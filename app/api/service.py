"""服務層：載入 artifacts、回答前端與 AI 顧問共用的查詢。

顧問工具與 API 端點都打這一層，保證「顧問說的數字 = 儀表板顯示的數字」。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from app import config, schema
from app.pipeline.roi import RoiParams, whatif_curve

FORECAST_WEEKS = 16
HISTORY_WEEKS = 78  # 圖表顯示最近 18 個月


def _status(sl: float, days_to_thresh: int | None) -> str:
    if sl >= config.CLEANING_THRESHOLD_PCT or days_to_thresh == 0:
        return "action"
    if days_to_thresh is not None and days_to_thresh <= config.WATCH_WINDOW_DAYS:
        return "watch"
    return "ok"


class FleetService:
    """把 artifacts 變成查詢介面。"""

    def __init__(self, artifact_dir: Path | None = None):
        d = Path(artifact_dir or config.ARTIFACT_DIR)
        self.dir = d
        self.fleet = pd.read_csv(d / "fleet.csv")
        self.scored = pd.read_csv(d / "scored.csv", parse_dates=[schema.REPORT_DATE])
        self.events = pd.read_csv(d / "events.csv", parse_dates=[schema.EVENT_DATE])
        self.summary = json.loads((d / "summary.json").read_text())
        self.prop_share = float(self.summary.get("prop_share", 0.3))
        eff_p = d / "maintenance_effects.csv"
        self.effects = (pd.read_csv(eff_p, parse_dates=["event_date"])
                        if eff_p.exists() else pd.DataFrame())
        self.roi_params = RoiParams(
            fuel_price_usd=config.VLSFO_PRICE_USD,
            cleaning_cost_usd=config.CLEANING_COST_USD,
            horizon_days=config.ROI_HORIZON_DAYS,
            co2_per_ton=config.CO2_PER_TON_FUEL,
        )

    # ---------- fleet ----------
    def fleet_overview(self) -> dict:
        f = self.fleet.copy()
        f["status"] = [
            _status(r.current_speed_loss_pct, None if pd.isna(r.days_to_threshold)
                    else int(r.days_to_threshold))
            for r in f.itertuples()
        ]
        monthly_cost = float(f["excess_cost_per_day"].sum()) * 30
        monthly_fuel = monthly_cost / config.VLSFO_PRICE_USD
        ships = []
        for r in f.itertuples():
            ships.append({
                "ship_id": r.ship_id, "ship_name": r.ship_name,
                "speed_loss_pct": r.current_speed_loss_pct,
                "fouling_level": r.fouling_level, "status": r.status,
                "days_since_clean": int(r.days_since_clean),
                "days_to_threshold": None if pd.isna(r.days_to_threshold) else int(r.days_to_threshold),
                "excess_cost_per_day": float(r.excess_cost_per_day),
                "spark": self._spark(r.ship_id),
            })
        return {
            "stats": {
                "avg_speed_loss_pct": round(float(f["current_speed_loss_pct"].mean()), 2),
                "ships_action": int((f["status"] == "action").sum()),
                "ships_watch": int((f["status"] == "watch").sum()),
                "monthly_excess_cost_usd": round(monthly_cost, 0),
                "monthly_excess_co2_tons": round(monthly_fuel * config.CO2_PER_TON_FUEL, 1),
                "threshold_pct": config.CLEANING_THRESHOLD_PCT,
                "n_ships": len(f),
            },
            "ships": ships,
            "validation": self.summary.get("validation", {}),
            "elbow_cuts_pct": self.summary.get("elbow_cuts_pct", []),
        }

    def _spark(self, ship_id: str, n: int = 12) -> list[float]:
        g = self._weekly(ship_id).tail(n)["speed_loss_smooth"]
        return [round(float(x), 2) for x in g.fillna(0.0)]

    def _weekly(self, ship_id: str) -> pd.DataFrame:
        g = self.scored[self.scored[schema.SHIP_ID] == ship_id]
        w = (g.set_index(schema.REPORT_DATE)
             .resample("W")[["speed_loss_smooth", "excess_foc_smooth", schema.DAILY_FOC,
                             "expected_foc"]].mean().reset_index())
        return w

    # ---------- ship ----------
    def ship_detail(self, ship_id: str) -> dict:
        row = self.fleet[self.fleet["ship_id"] == ship_id]
        if row.empty:
            raise KeyError(ship_id)
        r = row.iloc[0]
        weekly = self._weekly(ship_id).tail(HISTORY_WEEKS)
        weekly = weekly.dropna(subset=["speed_loss_smooth"])
        dates = weekly[schema.REPORT_DATE]
        growth_w = float(r.growth_pp_per_day) * 7
        last_sl = float(weekly["speed_loss_smooth"].iloc[-1]) if len(weekly) else 0.0
        last_date = dates.iloc[-1] if len(weekly) else pd.Timestamp("2000-01-01")
        # 預測帶寬度以該船近 12 週實際波動（std）為底，隨外推距離放大（啟發式，非統計信賴區間）
        resid_std = float(weekly["speed_loss_smooth"].tail(12).std()) if len(weekly) >= 4 else 0.5
        resid_std = max(0.3, min(resid_std, 2.0))
        forecast = []
        for i in range(1, FORECAST_WEEKS + 1):
            mid = last_sl + growth_w * i
            band = resid_std * (0.8 + 0.15 * i)
            forecast.append({
                "date": (last_date + pd.Timedelta(weeks=i)).strftime("%Y-%m-%d"),
                "mid": round(mid, 2), "lo": round(mid - band, 2), "hi": round(mid + band, 2),
            })
        if len(weekly) == 0:
            forecast = []
        ev = self.events[(self.events[schema.EVENT_SHIP_ID] == ship_id)
                         & (self.events[schema.EVENT_DATE] >= (dates.min() if len(dates) else pd.Timestamp.max))]
        dtt = None if pd.isna(r.days_to_threshold) else int(r.days_to_threshold)
        status = _status(float(r.current_speed_loss_pct), dtt)
        attribution = self._attribution(ship_id)
        # 船殼 vs 螺旋槳分割（事件效果比，命題檢查表第 2 點）
        sl = float(r.current_speed_loss_pct)
        hull_prop = {
            "hull_pp": round(sl * (1 - self.prop_share), 2),
            "prop_pp": round(sl * self.prop_share, 2),
            "prop_share": self.prop_share,
        }
        eff = (self.effects[self.effects["ship_id"] == ship_id]
               .sort_values("event_date", ascending=False)
               if len(self.effects) else pd.DataFrame())
        return {
            "ship_id": ship_id, "ship_name": r.ship_name,
            "status": status, "fouling_level": r.fouling_level,
            "hull_prop": hull_prop,
            "maintenance_effects": [
                {"date": x.event_date.strftime("%Y-%m-%d"), "type": x.event_type,
                 "orig_type": x.orig_type, "pre_pp": x.pre_pp, "post_pp": x.post_pp,
                 "delta_pp": x.delta_pp}
                for x in eff.itertuples()
            ],
            "current": {
                "speed_loss_pct": float(r.current_speed_loss_pct),
                "days_since_clean": int(r.days_since_clean),
                "growth_pp_per_day": float(r.growth_pp_per_day),
                "days_to_threshold": dtt,
                "excess_cost_per_day": float(r.excess_cost_per_day),
                "daily_foc": round(float(weekly[schema.DAILY_FOC].iloc[-1]), 1) if len(weekly) else None,
                "expected_foc": round(float(weekly["expected_foc"].iloc[-1]), 1) if len(weekly) else None,
                "threshold_pct": config.CLEANING_THRESHOLD_PCT,
            },
            "attribution": attribution,
            "series": [
                {"date": d.strftime("%Y-%m-%d"), "speed_loss": round(float(v), 2)}
                for d, v in zip(dates, weekly["speed_loss_smooth"])
            ],
            "forecast": forecast,
            "events": [
                {"date": e[schema.EVENT_DATE].strftime("%Y-%m-%d"),
                 "type": e[schema.EVENT_TYPE], "notes": e.get(schema.EVENT_NOTES, "")}
                for _, e in ev.iterrows()
            ],
        }

    def _attribution(self, ship_id: str) -> dict | None:
        """近 7 天平均的油耗歸因瀑布（TreeSHAP，管線已存於 scored.csv）。

        實測油耗 = 基準 + 航速 + 天候 + 吃水 + 船體髒污（殘差）。
        """
        g = self.scored[self.scored[schema.SHIP_ID] == ship_id].tail(7)
        if len(g) < 3 or "attr_base_tons" not in g.columns:
            return None
        base = float(g["attr_base_tons"].mean())
        factors = [
            {"name": "航速", "tons": round(float(g["attr_speed_tons"].mean()), 2)},
            {"name": "天候", "tons": round(float(g["attr_wind_tons"].mean()), 2)},
            {"name": "吃水", "tons": round(float(g["attr_draft_tons"].mean()), 2)},
            {"name": "船體髒污", "tons": round(float(g["excess_foc"].mean()), 2), "is_fouling": True},
        ]
        return {
            "baseline_tons": round(base, 2),
            "factors": factors,
            "actual_tons": round(float(g[schema.DAILY_FOC].mean()), 2),
            "window_days": int(len(g)),
        }

    # ---------- roi ----------
    def roi(self, ship_id: str | None = None) -> dict:
        f = self.fleet
        target = f[f["ship_id"] == ship_id].iloc[0] if ship_id else f.iloc[0]
        curve = whatif_curve(
            current_sl_pct=float(target.current_speed_loss_pct),
            growth_pp_day=float(target.growth_pp_per_day),
            f_ref=float(target.f_ref), params=self.roi_params)
        per_ship, annual_saving = [], 0.0
        for r in f.itertuples():
            c = whatif_curve(float(r.current_speed_loss_pct), float(r.growth_pp_per_day),
                             float(r.f_ref), self.roi_params)
            if c["best_day"] is not None:
                annual_saving += (c["no_clean_avg"] - c["best_avg"]) * 365
            per_ship.append({
                "ship_id": r.ship_id, "ship_name": r.ship_name,
                "excess_cost_per_day": float(r.excess_cost_per_day),
                "hull_usd": round(float(r.excess_cost_per_day) * (1 - self.prop_share), 0),
                "prop_usd": round(float(r.excess_cost_per_day) * self.prop_share, 0),
                "best_day": c["best_day"], "payback_days": c["payback_days"],
            })
        return {
            "target": {"ship_id": target.ship_id, "ship_name": target.ship_name, **curve},
            "per_ship": per_ship,
            "stats": {
                "fleet_daily_excess_usd": round(float(f["excess_cost_per_day"].sum()), 0),
                "annual_saving_potential_usd": round(annual_saving, 0),
                "fuel_price_usd": config.VLSFO_PRICE_USD,
                "cleaning_cost_usd": config.CLEANING_COST_USD,
                "prop_share": self.prop_share,
            },
        }
