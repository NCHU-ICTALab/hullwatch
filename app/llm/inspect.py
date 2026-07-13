"""水下判讀：船殼照片 → 附著等級 → 與數據面 Speed Loss 交叉驗證。

bedrock 模式用 Converse 多模態（Claude 視覺），stub 模式回傳標記清楚的
示意結果——兩者皆與資料面做同一套交叉驗證，介面一致。
"""

from __future__ import annotations

import json
import re

from app import config
from app.llm.provider import get_bedrock_runtime

VISION_PROMPT = """你是船體檢驗專家。分析這張船殼水下照片，僅回傳 JSON（不要其他文字）：
{"fouling_level": "light|moderate|heavy", "coverage_pct": 0-100 的整數,
 "organisms": ["觀察到的生物類型（繁體中文）"], "notes": "一句繁體中文觀察摘要"}"""

# 附著等級 ↔ 合理的 speed loss 區間（%），供交叉驗證
LEVEL_SL_RANGE = {"light": (0.0, 4.0), "moderate": (2.5, 10.0), "heavy": (7.0, 100.0)}


def cross_check(fouling_level: str, data_speed_loss_pct: float | None) -> dict:
    """照片判讀 vs 數據推得 Speed Loss 的一致性。"""
    if data_speed_loss_pct is None:
        return {"consistent": None, "detail": "無資料面 Speed Loss 可比對"}
    lo, hi = LEVEL_SL_RANGE.get(fouling_level, (0, 100))
    ok = lo <= data_speed_loss_pct <= hi
    return {
        "consistent": ok,
        "detail": (f"照片判讀為 {fouling_level}（合理區間 {lo}–{hi}%），"
                   f"數據推得 Speed Loss {data_speed_loss_pct:.1f}%，"
                   f"{'兩個獨立證據鏈互相印證' if ok else '不一致，建議安排水下檢查確認'}"),
    }


def analyze_hull_image(image_bytes: bytes, image_format: str = "jpeg",
                       data_speed_loss_pct: float | None = None) -> dict:
    """回傳 {mode, fouling_level, coverage_pct, organisms, notes, cross_check}。"""
    rt = get_bedrock_runtime()
    if rt is None:
        result = _stub_analysis(data_speed_loss_pct)
        mode = "stub"
    else:
        resp = rt.converse(
            modelId=config.BEDROCK_MODEL_ID,
            messages=[{"role": "user", "content": [
                {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
                {"text": VISION_PROMPT},
            ]}],
            inferenceConfig={"maxTokens": 500, "temperature": 0},
        )
        text = resp["output"]["message"]["content"][0]["text"]
        m = re.search(r"\{.*\}", text, re.DOTALL)
        result = json.loads(m.group(0)) if m else _stub_analysis(data_speed_loss_pct)
        mode = "bedrock"
    return {
        "mode": mode,
        **result,
        "cross_check": cross_check(result.get("fouling_level", ""), data_speed_loss_pct),
    }


def _stub_analysis(data_speed_loss_pct: float | None) -> dict:
    """開發用示意判讀：從資料面 Speed Loss 推一組自洽的假結果，並明確標示。"""
    sl = data_speed_loss_pct if data_speed_loss_pct is not None else 5.0
    level = "heavy" if sl >= 8 else ("moderate" if sl >= 3 else "light")
    return {
        "fouling_level": level,
        "coverage_pct": int(min(95, max(3, sl * 5))),
        "organisms": ["硬殼藤壺", "絲狀藻類"] if level != "light" else ["黏液膜"],
        "notes": "（示意結果：stub 模式，比賽環境將由 Bedrock 視覺模型判讀）",
    }
