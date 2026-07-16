"""正午報表 schema 對應 — 全專案唯一的欄位知識來源。

比賽當天拿到真資料時，只改這個檔案：
1. 修改 ``COLUMN_ALIASES``，把真實欄位名對應到 canonical 名。
2. 若有新欄位要進特徵，加入 ``OPTIONAL_NUMERIC``。
下游（產生器、特徵工程、模型、API）一律使用 canonical 名。
"""

from __future__ import annotations

import pandas as pd

# canonical 欄位名（内部統一使用）
SHIP_ID = "ship_id"
SHIP_NAME = "ship_name"
REPORT_DATE = "report_date"
WIND_SCALE = "wind_scale"
HOURS_FULL_SPEED = "hours_full_speed"
ME_CONSUMP_VLSFO = "me_fullspeed_consump_vlsfo"
AVG_SPEED = "avg_speed"          # 節；STW 優先、缺值退 SOG（ingest_yangming；SOG 另存欄）
MEAN_DRAFT = "mean_draft"        # 公尺
DAILY_FOC = "daily_foc"          # 產出欄位：噸/天

REQUIRED = [SHIP_ID, REPORT_DATE, WIND_SCALE, HOURS_FULL_SPEED, ME_CONSUMP_VLSFO, AVG_SPEED]
OPTIONAL_NUMERIC = [MEAN_DRAFT]

# 真實資料欄位名 → canonical 名。當天照實際 CSV 標頭增修。
COLUMN_ALIASES: dict[str, str] = {
    "SHIP_ID": SHIP_ID,
    "VESSEL_CODE": SHIP_ID,
    "SHIP_NAME": SHIP_NAME,
    "REPORT_DATE": REPORT_DATE,
    "DATE": REPORT_DATE,
    "WIND_SCALE": WIND_SCALE,
    "HOURS_FULL_SPEED": HOURS_FULL_SPEED,
    "ME_FULLSPEED_CONSUMP_VLSFO": ME_CONSUMP_VLSFO,
    "AVG_SPEED": AVG_SPEED,
    "SPEED": AVG_SPEED,
    "MEAN_DRAFT": MEAN_DRAFT,
}

# 水下事件 canonical
EVENT_SHIP_ID = "ship_id"
EVENT_DATE = "event_date"
EVENT_TYPE = "event_type"        # inspection | cleaning | propeller_polish | drydock
EVENT_NOTES = "notes"

RESET_EVENTS = {"cleaning", "drydock"}          # 完全重置乾淨基準
PARTIAL_RESET_EVENTS = {"propeller_polish"}     # 部分重置（不開新基準窗口）


def normalize_noon_reports(df: pd.DataFrame) -> pd.DataFrame:
    """把任意來源的正午報表轉成 canonical schema 並計算 DailyFOC。

    Args:
        df: 原始正午報表（欄名可為真實系統名或 canonical 名）。

    Returns:
        canonical 欄名的 DataFrame，含 ``daily_foc``，依 ship_id、date 排序。

    Raises:
        ValueError: 缺少必要欄位時。
    """
    renamed = df.rename(columns={c: COLUMN_ALIASES.get(c, c) for c in df.columns})
    missing = [c for c in REQUIRED if c not in renamed.columns]
    if missing:
        raise ValueError(f"正午報表缺少必要欄位: {missing}；請更新 schema.COLUMN_ALIASES")
    out = renamed.copy()
    out[REPORT_DATE] = pd.to_datetime(out[REPORT_DATE])
    hours = out[HOURS_FULL_SPEED].astype(float)
    out[DAILY_FOC] = (out[ME_CONSUMP_VLSFO].astype(float) / hours.where(hours > 0) * 24.0)
    return out.sort_values([SHIP_ID, REPORT_DATE]).reset_index(drop=True)


def apply_quality_filter(df: pd.DataFrame, max_wind: float, min_hours: float) -> pd.DataFrame:
    """套用命題規定的良好天氣 + 全速航行篩選。輸入須為 canonical schema。"""
    mask = (
        (df[WIND_SCALE].astype(float) <= max_wind)
        & (df[HOURS_FULL_SPEED].astype(float) >= min_hours)
        & df[DAILY_FOC].notna()
        & (df[AVG_SPEED].astype(float) > 0)
    )
    return df.loc[mask].reset_index(drop=True)
