"""陽明真資料轉接器：vt_fd.csv + maintenance.csv → canonical schema。

比賽當天唯一的資料側新增模組。產出與合成資料完全相同的介面
（data/raw/noon_reports.csv + events.csv），下游管線/API/前端零修改。

關鍵轉換：
- NOON_UTC 相對天數 → 供既有日曆型元件使用的映射日期（錨點 2021-01-01）；
  原始兩檔沒有絕對日期，此日期只能保留順序與間隔，不能宣稱真實日曆日
- 多燃料 → 熱值折算 VLSFO 當量（LCV 表照 README）
- 航速採 STW（對水航速，ISO 19030 正規做法；SOG 另存）
- HIDDEN/PREDICT → NaN；PREDICT 儲存格另出 targets 表（102 項提交用）
- 養護類型對應：UWC/UWC+PP/DD → cleaning/drydock（重置基準）；
  PP/UWI+PP → propeller_polish；UWI → inspection

用法：
    python -m app.pipeline.ingest_yangming data/yangming-aws-summit-hackathon
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from app import config, schema

# Display-only surrogate anchor for legacy date-based charts. The source files
# contain relative days only; this must never be presented as a real calendar date.
EPOCH = pd.Timestamp("2021-01-01")

LCV = {"HSHFO": 40.2, "ULSFO": 41.2, "VLSFO": 40.2, "LSMGO": 42.7, "BIO_HSFO": 39.4}
LCV_VLSFO = 40.2
FUEL_COLS = {f: f"ME_FULLSPEED_CONSUMP_{f}" for f in LCV}

EVENT_TYPE_MAP = {
    "UWC": "cleaning",
    "UWC+PP": "cleaning",          # 清洗＋拋光：以重置為主，拋光另補一列
    "DD": "drydock",
    "PP": "propeller_polish",
    "UWI+PP": "propeller_polish",  # 檢查＋拋光：以拋光為主，檢查另補一列
    "UWI": "inspection",
}

# 這批資料比命題最小集多出的可用特徵（帶進 canonical 表供特徵工程用）
EXTRA_NUMERIC = {
    "SPEED_THROUGH_WATER": "stw",
    "AVG_SPEED": "sog",
    "HORSE_POWER": "horse_power",
    "HOURS_TOTAL": "hours_total",
    "SEA_HEIGHT": "sea_height",
    "SWELL_HEIGHT": "swell_height",
    "SEA_WATER_TEMP": "sea_water_temp",
    "DISPLACEMENT": "displacement",
    "ME_AVG_RPM": "me_rpm",
    "PROPELLER_SPEED": "prop_rpm",
    "WATER_DEPTH": "water_depth",
    "CARGO_ON_BOARD": "cargo",
    "WIND_SPEED": "wind_speed",
    "FULL_SPD_STW_SLIP": "slip_full_spd",     # A 類可見：全速時段對水滑差（髒污代理）
    "DIFF_STW_SOG_SLIP": "current_proxy",      # A 類可見：對水-對地速差（洋流代理）
}

W2_SHIPS = {"S9", "S10", "S11", "S12", "S22", "S23"}  # 其餘為 W1 型


def _num(s: pd.Series) -> pd.Series:
    """HIDDEN / PREDICT / 空字串 一律轉 NaN。"""
    return pd.to_numeric(s, errors="coerce")


def load_vt_fd(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """回傳 (canonical noon reports, predict_targets)。"""
    raw = pd.read_csv(path, dtype=str).drop_duplicates()
    # 同船同日近重複列（數值微差）：優先保留含 PREDICT 的列（S21 day1018 一日兩列，
    # 一列全 HIDDEN 一列含 PREDICT——keep="first" 會弄丟第 102 個預測格）
    has_predict = raw[list(FUEL_COLS.values())].eq("PREDICT").any(axis=1)
    raw = (raw.assign(_p=has_predict.astype(int))
           .sort_values("_p", ascending=False, kind="stable")
           .drop_duplicates(subset=["De-identification Name", "NOON_UTC"], keep="first")
           .drop(columns="_p"))

    out = pd.DataFrame()
    out[schema.SHIP_ID] = raw["De-identification Name"]
    out[schema.SHIP_NAME] = raw["De-identification Name"]
    day = _num(raw["NOON_UTC"])
    out[schema.REPORT_DATE] = EPOCH + pd.to_timedelta(day, unit="D")
    out["day"] = day
    out[schema.WIND_SCALE] = _num(raw["WIND_SCALE"])
    out[schema.HOURS_FULL_SPEED] = _num(raw["HOURS_FULL_SPEED"])

    # 多燃料 → VLSFO 當量質量
    energy = None
    for f, col in FUEL_COLS.items():
        mass = _num(raw[col]).fillna(0.0)
        e = mass * LCV[f]
        energy = e if energy is None else energy + e
    vlsfo_eq = energy / LCV_VLSFO
    out[schema.ME_CONSUMP_VLSFO] = vlsfo_eq.where(vlsfo_eq > 0)  # 全零＝遮蔽或缺失

    # 航速：STW 為主（ISO 19030），缺值退回 SOG
    stw = _num(raw["SPEED_THROUGH_WATER"])
    sog = _num(raw["AVG_SPEED"])
    out[schema.AVG_SPEED] = stw.fillna(sog)

    # 吃水：舯吃水缺值時取艏艉平均
    mid = _num(raw["MID_DRAFT"])
    fore, aft = _num(raw["FORE_DRAFT"]), _num(raw["AFTER_DRAFT"])
    out[schema.MEAN_DRAFT] = mid.fillna((fore + aft) / 2)

    for src, dst in EXTRA_NUMERIC.items():
        out[dst] = _num(raw[src])
    out["ship_type"] = np.where(out[schema.SHIP_ID].isin(W2_SHIPS), "W2", "W1")

    # PREDICT 目標表（102 項）
    trows = []
    for f, col in FUEL_COLS.items():
        mask = raw[col] == "PREDICT"
        for i in raw.index[mask]:
            trows.append({
                "ship_id": raw.at[i, "De-identification Name"],
                "day": int(float(raw.at[i, "NOON_UTC"])),
                "fuel_type": col,
                "fuel": f,
            })
    targets = pd.DataFrame(trows).sort_values(["ship_id", "day"]).reset_index(drop=True)
    return out, targets


def load_maintenance(path: Path) -> pd.DataFrame:
    """→ canonical events（複合事件拆成主事件＋附屬事件兩列）。"""
    mt = pd.read_csv(path)
    if "event_date" in mt.columns:
        mt["event_date"] = pd.to_datetime(mt["event_date"])
    elif "event_day" in mt.columns:
        mt["event_date"] = EPOCH + pd.to_timedelta(_num(mt["event_day"]), unit="D")
    else:
        raise ValueError("maintenance.csv 缺少 event_date 或 event_day")
    rows = []
    for _, r in mt.iterrows():
        notes_parts = []
        for col in ["propeller_condition", "hull_fouling_type", "hull_coating_condition",
                    "cavitation_found"]:
            if pd.notna(r.get(col)) and str(r[col]).strip():
                notes_parts.append(f"{col.split('_')[0]}:{r[col]}")
        notes = f"{r.event_type}; " + ", ".join(notes_parts)
        main = EVENT_TYPE_MAP.get(r.event_type, "inspection")
        rows.append({"ship_id": r.ship_id, "event_date": r.event_date,
                     "event_type": main, "notes": notes})
        # 複合事件補第二列，讓拋光/檢查的時間軸完整
        if r.event_type == "UWC+PP":
            rows.append({"ship_id": r.ship_id, "event_date": r.event_date,
                         "event_type": "propeller_polish", "notes": notes})
        elif r.event_type == "UWI+PP":
            rows.append({"ship_id": r.ship_id, "event_date": r.event_date,
                         "event_type": "inspection", "notes": notes})
    return pd.DataFrame(rows)


def ingest(dataset_dir: Path, raw_out: Path | None = None) -> dict:
    """轉出 data/raw/（noon_reports.csv + events.csv + predict_targets.csv）。"""
    dataset_dir = Path(dataset_dir)
    raw_out = Path(raw_out or (config.DATA_DIR / "raw"))
    raw_out.mkdir(parents=True, exist_ok=True)
    noon, targets = load_vt_fd(dataset_dir / "vt_fd.csv")
    events = load_maintenance(dataset_dir / "maintenance.csv")
    # 下游 normalize 需要原始欄名嗎？—canonical 欄名已在 COLUMN_ALIASES 的目標端，直接可用
    noon.to_csv(raw_out / "noon_reports.csv", index=False)
    events.to_csv(raw_out / "events.csv", index=False)
    targets.to_csv(raw_out / "predict_targets.csv", index=False)
    truth = raw_out / "truth.csv"
    if truth.exists():
        truth.unlink()  # 真資料沒有 ground truth，移除殘留的合成 truth
    return {"noon_rows": len(noon), "events": len(events), "targets": len(targets),
            "ships": noon[schema.SHIP_ID].nunique()}


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("dataset_dir", type=Path)
    args = ap.parse_args()
    print(json.dumps(ingest(args.dataset_dir), indent=2, ensure_ascii=False))
