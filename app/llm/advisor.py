"""AI 顧問 — 學長 wiki_agent 的船舶版移植（工具迴圈 + 唯讀工具集）。

兩種模式，回應格式相同（steps / answer / citations / mode）：
- ``agent``：LangGraph create_react_agent + Bedrock Claude（比賽正式環境）。
- ``scripted``：無 LLM 時的決定性回答器——意圖比對 → 直接呼叫同一組工具 →
  模板組稿。數字與 agent 模式同源（都來自 FleetService），也是 demo 現場
  Bedrock 掛掉時的保命 fallback。

工具一律唯讀（Q8 決策）：顧問引用的數字 = 儀表板顯示的數字。
"""

from __future__ import annotations

import json

from app import config
from app.api.service import FleetService

SYSTEM_PROMPT = """你是陽明海運的船體能效顧問，服務缺乏資深船岸工程師的營運團隊。
回答一律使用繁體中文，白話、直接、給得出行動建議。
你只能引用工具回傳的數字，不可捏造。金額一律 USD。
回答結構：結論先行 → 依據（引用具體數字）→ 建議行動。
Speed Loss 清洗門檻為 {threshold}%。

工具欄位字典（嚴格照此解讀，勿混淆）：
- excess_cost_per_day / current_excess_cost：因髒污「每天多燒」的油錢
- hull_usd / prop_usd：上述每日超額成本中，船殼髒污 vs 螺旋槳的分量
  （執行清洗/拋光後每天可拿回的節省，不是清洗費用）
- cleaning_cost_usd：單次水下清潔的固定費用
- payback_days：清潔費用 ÷ 每日超額成本 = 回本天數
- delta_pp（maintenance_effects）：事件後 − 事件前的 Speed Loss（負值=改善）"""

STATUS_ZH = {"action": "待清洗", "watch": "留意", "ok": "良好"}


def _fmt_usd(x: float) -> str:
    return f"US${x:,.0f}"


class Advisor:
    def __init__(self, service: FleetService, retriever, chat_model=None):
        self.service = service
        self.retriever = retriever
        self.chat_model = chat_model
        self.mode = "agent" if chat_model is not None else "scripted"
        self._agent = self._build_agent() if chat_model is not None else None

    # ---------- 工具（兩種模式共用同一批函式） ----------
    def _tool_fleet(self) -> dict:
        return self.service.fleet_overview()

    def _tool_ship(self, ship_id: str) -> dict:
        d = self.service.ship_detail(ship_id)
        d.pop("series", None); d.pop("forecast", None)  # 給 LLM 的摘要不需長序列
        return d

    def _tool_roi(self, ship_id: str | None = None) -> dict:
        r = self.service.roi(ship_id)
        r["target"].pop("days", None); r["target"].pop("avg_cost", None)
        return r

    def _tool_kb(self, query: str) -> list[dict]:
        return self.retriever.search(query, k=3)

    # ---------- agent 模式 ----------
    def _build_agent(self):
        from langchain_core.tools import tool
        from langgraph.prebuilt import create_react_agent

        advisor = self

        @tool
        def get_fleet_status() -> str:
            """取得全船隊當前狀態：每艘船的 Speed Loss、髒污等級、距上次清洗天數、每日超額成本。"""
            return json.dumps(advisor._tool_fleet(), ensure_ascii=False, default=str)

        @tool
        def get_ship_detail(ship_id: str) -> str:
            """取得單船詳情（ship_id 如 YM-9001）：當前指標、水下事件史、清洗建議。"""
            return json.dumps(advisor._tool_ship(ship_id), ensure_ascii=False, default=str)

        @tool
        def compute_roi(ship_id: str = "") -> str:
            """計算清洗經濟效益：最佳清洗日、回本天數、每日超額成本。ship_id 留空看全隊。"""
            return json.dumps(advisor._tool_roi(ship_id or None), ensure_ascii=False, default=str)

        @tool
        def retrieve_knowledge(query: str) -> str:
            """查詢知識庫（ISO 19030 方法論、清洗成本行情、命題背景）。"""
            return json.dumps(advisor._tool_kb(query), ensure_ascii=False, default=str)

        return create_react_agent(
            self.chat_model,
            [get_fleet_status, get_ship_detail, compute_roi, retrieve_knowledge],
            prompt=SYSTEM_PROMPT.format(threshold=config.CLEANING_THRESHOLD_PCT),
        )

    def ask(self, question: str) -> dict:
        if self._agent is not None:
            try:
                return self._ask_agent(question)
            except Exception as e:  # Bedrock 掛掉時保命：退回 scripted
                out = self._ask_scripted(question)
                out["mode"] = f"scripted (agent 失敗: {type(e).__name__})"
                return out
        return self._ask_scripted(question)

    def _ask_agent(self, question: str) -> dict:
        result = self._agent.invoke({"messages": [("user", question)]})
        msgs = result["messages"]
        steps = []
        for m in msgs:
            for call in getattr(m, "tool_calls", None) or []:
                args = ", ".join(f"{k}={v}" for k, v in call.get("args", {}).items())
                steps.append(f"{call['name']}({args})")
        answer = msgs[-1].content
        if isinstance(answer, list):  # Claude 內容區塊
            answer = "".join(b.get("text", "") for b in answer if isinstance(b, dict))
        cites = [c["source"] for c in self._tool_kb(question)]
        return {"mode": "agent", "steps": steps, "answer": answer, "citations": cites}

    # ---------- scripted 模式 ----------
    def _ask_scripted(self, question: str) -> dict:
        q = question.lower()
        ship = self._match_ship(question)
        if any(w in question for w in ["優先", "先洗", "哪幾艘", "排程"]):
            return self._scripted_priority()
        if ship is not None and any(w in question for w in ["建議做", "船殼清洗", "螺旋槳拋光"]):
            return self._scripted_ship_action(ship)
        if ship is not None and any(w in question for w in ["成本", "多少", "錢", "花費"]):
            return self._scripted_ship_cost(ship)
        if any(w in question for w in ["門檻", "標準", "密切留意", "立即處置"]) or "30 天" in q or "30天" in q:
            return self._scripted_threshold()
        if any(w in q for w in ["uwc", "uwi", "pp、", "dd ", "維護動作"]):
            return self._scripted_maintenance_actions()
        if "speed loss" in q and any(w in question for w in ["計算", "ISO", "定義"]):
            return self._scripted_speed_loss()
        if "每月" in question and ("碳排" in question or "超額成本" in question):
            return self._scripted_cost_carbon()
        if any(w in question for w in ["市場行情", "情境價", "多久更新", "油價更新"]):
            return self._scripted_fuel_market()
        if "模型" in question:
            return self._scripted_models()
        if "正午日報" in question or "stw" in q or "sog" in q:
            return self._scripted_noon_report()
        return self._scripted_priority()  # 預設給最有行動價值的答案

    def _match_ship(self, question: str):
        for r in self.service.fleet.itertuples():
            if r.ship_id.lower() in question.lower() or str(r.ship_name).lower() in question.lower():
                return r
        return None

    def _cites(self, query: str) -> list[str]:
        seen = list(dict.fromkeys(c["source"] for c in self._tool_kb(query)))
        return seen or ["fleet artifacts"]

    def _scripted_priority(self) -> dict:
        ov = self._tool_fleet()
        act = [s for s in ov["ships"] if s["status"] == "action"]
        watch = [s for s in ov["ships"] if s["status"] == "watch"]
        lines = ["排序依據是各船 Speed Loss、越過門檻時間與每日超額成本。"]
        if act:
            names = "、".join(f"{s['ship_name']}（Speed Loss {s['speed_loss_pct']}%，"
                              f"每日多燒 {_fmt_usd(s['excess_cost_per_day'])}）" for s in act[:3])
            lines.append(f"建議優先安排清洗：{names}。已超過 {ov['stats']['threshold_pct']}% 門檻或即將越過。")
        if watch:
            names = "、".join(f"{s['ship_name']}（約 {s['days_to_threshold']} 天後越過門檻）"
                              for s in watch[:3])
            lines.append(f"留意名單：{names}，建議納入下一波排程以節省動員成本。")
        if not lines:
            lines.append("目前全隊皆在門檻之下，維持每週監測即可。")
        lines.append(f"全隊船體髒污每月超額成本約 {_fmt_usd(ov['stats']['monthly_excess_cost_usd'])}"
                     f"、超額碳排約 {ov['stats']['monthly_excess_co2_tons']} 噸 CO₂。")
        return {"mode": "scripted",
                "steps": ["get_fleet_status()", "compute_roi()", "retrieve_knowledge(清洗排程)"],
                "answer": " ".join(lines), "citations": self._cites("清洗 優先 排程 門檻")}

    def _scripted_ship_action(self, ship) -> dict:
        schedule = self.service.maintenance_schedule()
        recommendation = next(
            (item for item in schedule["recommendations"] if item["ship_id"] == ship.ship_id),
            None,
        )
        detail = self._tool_ship(ship.ship_id)
        hull = detail["hull_prop"]["hull_pp"]
        prop = detail["hull_prop"]["prop_pp"]
        if recommendation is None:
            answer = f"{ship.ship_id} 目前沒有可用的維護建議，請先確認排程資料。"
            steps = [f"get_ship_detail({ship.ship_id})"]
        else:
            action = recommendation["action"]
            answer = (
                f"{ship.ship_id} 的唯讀系統建議為 {action}；目前估計船殼歸因 {hull}pp、"
                f"螺旋槳歸因 {prop}pp，建議窗口為 {recommendation['window_start']} 至 "
                f"{recommendation['window_end']}。這是依 180 天淨效益比較產生的建議，"
                "仍需港口規範、船期與工程檢查確認後才能執行。"
            )
            steps = [f"get_ship_detail({ship.ship_id})", "get_maintenance_schedule()"]
        return {
            "mode": "scripted",
            "steps": steps,
            "answer": answer,
            "citations": self._cites("PP UWC 維護建議 船殼 螺旋槳"),
        }

    def _scripted_ship_cost(self, ship) -> dict:
        roi = self._tool_roi(ship.ship_id)["target"]
        ans = (f"{ship.ship_name} 目前 Speed Loss {ship.current_speed_loss_pct}%，"
               f"因船體髒污每天多花約 {_fmt_usd(roi['current_excess_cost'])}"
               f"（超額碳排 {roi['excess_co2_per_day']} 噸 CO₂/天）。")
        if roi["best_day"] is not None:
            ans += (f" 依 180 天成本掃描，最佳清洗日為第 {roi['best_day']} 天，"
                    f"清潔費 {_fmt_usd(self.service.roi_params.cleaning_cost_usd)} "
                    f"約 {roi['payback_days']} 天回本。")
        else:
            ans += " 目前清洗不划算，建議持續監測。"
        return {"mode": "scripted",
                "steps": [f"get_ship_detail({ship.ship_id})", f"compute_roi({ship.ship_id})"],
                "answer": ans, "citations": self._cites("清洗成本 回本")}

    def _scripted_threshold(self) -> dict:
        ov = self._tool_fleet()
        soon = [s for s in ov["ships"]
                if s["days_to_threshold"] is not None and s["days_to_threshold"] <= 30]
        if soon:
            names = "、".join(
                f"{s['ship_name']}（{'已超過' if s['days_to_threshold'] == 0 else '約 ' + str(s['days_to_threshold']) + ' 天'}）"
                for s in soon)
            forecast = f"未來 30 天內會達到 {ov['stats']['threshold_pct']}% 門檻的船：{names}。"
        else:
            forecast = "未來 30 天內沒有船會越過清洗門檻。"
        watch_threshold = ov["stats"].get("watch_threshold_pct", 5)
        action_threshold = ov["stats"]["threshold_pct"]
        watch_label = f"{watch_threshold:g}"
        action_label = f"{action_threshold:g}"
        ans = (
            f"狀態標準是：正常低於 {watch_label}%；密切留意為 {watch_label}%–"
            f"小於 {action_label}%，或預估 60 天內達 {action_label}%；立即處置為 "
            f"{action_label}% 以上。{forecast}"
        )
        return {"mode": "scripted", "steps": ["get_fleet_status()", "外推各船結垢趨勢"],
                "answer": ans, "citations": self._cites("門檻 speed loss 預測")}

    def _scripted_maintenance_actions(self) -> dict:
        answer = (
            "PP 是螺旋槳拋光；UWC 是水下船殼清洗；UWI 是水下檢查；DD 是進乾塢大修。"
            "PP、UWC 與 DD 有實體介入，通常可改善對應部位效能；UWI 只檢查、不清洗，"
            "因此不會改善 Speed Loss，也不應重置乾淨基準。"
        )
        return {
            "mode": "scripted",
            "steps": ["retrieve_knowledge(PP UWC UWI DD)"],
            "answer": answer,
            "citations": self._cites("PP UWC UWI DD 維護動作"),
        }

    def _scripted_speed_loss(self) -> dict:
        answer = (
            "Speed Loss 是在相同功率條件下，乾淨基準預期船速與實測船速的百分比差。"
            "HullWatch 採 ISO 19030 的基準法精神，以每艘船最近清潔後的乾淨基準追蹤趨勢；"
            "目前是營運決策指標，不宣稱已完成第三方 ISO 19030 認證。"
        )
        return {
            "mode": "scripted",
            "steps": ["retrieve_knowledge(ISO 19030 Speed Loss)"],
            "answer": answer,
            "citations": self._cites("ISO 19030 Speed Loss 乾淨基準"),
        }

    def _scripted_cost_carbon(self) -> dict:
        stats = self._tool_fleet()["stats"]
        answer = (
            "固定 30 天情境的超額成本估算，是全船隊每日超額燃油成本合計乘以 30，"
            "單位為 US$／30 天；目前模型估算約 "
            + _fmt_usd(stats["monthly_excess_cost_usd"]) + "／30 天。"
            "固定 30 天情境的超額碳排估算，是超額燃油量乘燃油排放係數再乘以 30，"
            f"單位為 tCO₂／30 天；目前模型估算約 {stats['monthly_excess_co2_tons']} tCO₂／30 天。"
            "這不是帳務實際月份或法規申報值。"
        )
        return {
            "mode": "scripted",
            "steps": ["get_fleet_status()", "retrieve_knowledge(成本 碳排 公式)"],
            "answer": answer,
            "citations": self._cites("超額成本 碳排 CO2 公式"),
        }

    def _scripted_fuel_market(self) -> dict:
        market = self.service.fuel_prices()
        refresh = market.get("refresh_interval_hours", 6)
        stale = market.get("stale_after_hours", 24)
        answer = (
            f"市場行情不是持續串流：後端快取 {refresh} 小時，超過後要等下一次 API 請求才嘗試更新；"
            f"來源時間超過 {stale} 小時會標示資料延遲。前端目前在進入或重新載入頁面時請求，"
            "不會自動每 6 小時輪詢。決策情境價只是 What-if 輸入，不會改寫行情或來源。"
        )
        return {
            "mode": "scripted",
            "steps": ["get_fuel_market_status()"],
            "answer": answer,
            "citations": ["HullWatch fuel market configuration"],
        }

    def _scripted_models(self) -> dict:
        registry = self.service.model_registry()
        names = "、".join(model["name"] for model in registry["models"])
        answer = (
            "P0 Fuel 102 ensemble 用來預測主機全速每日油耗 DailyFOC，供競賽客觀評分；"
            f"Dashboard 的 {names} 用來比較與預測 Speed Loss 趨勢。兩類模型的目標不同，"
            "不能把 Speed Loss 趨勢模型當成油耗 P0 權重。目前尚未完成由同一 P0 模型執行 "
            "no-action／UWC／PP 的反事實整合流程。"
        )
        return {
            "mode": "scripted",
            "steps": ["get_model_registry()", "retrieve_knowledge(P0 Speed Loss 模型)"],
            "answer": answer,
            "citations": ["HullWatch model registry", *self._cites("P0 Speed Loss 模型")],
        }

    def _scripted_noon_report(self) -> dict:
        answer = (
            "目前 CSV 匯入欄位是 ship_id、report_date、avg_speed、daily_foc、wind_scale、"
            "full_speed_hours。STW 是相對水速，較適合船體效能分析；SOG 是對地速度，會受海流影響；"
            "風級使用 Beaufort scale，良好天氣篩選目前要求風力不高於 4 級且全速航行至少 22 小時。"
        )
        return {
            "mode": "scripted",
            "steps": ["retrieve_knowledge(正午日報 STW SOG Beaufort)"],
            "answer": answer,
            "citations": self._cites("正午日報 STW SOG 風級"),
        }
