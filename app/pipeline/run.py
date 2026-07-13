"""端到端管線：資料 → 特徵 → 模型 → 評分 → 分級 → artifacts。

賽前跑合成資料；比賽當天把真實 CSV 放進 data/raw/ 後以 --real 重跑，
下游（API、前端、顧問）完全不變。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from app import config, schema
from app.pipeline.baseline import CleanBaselineModel, smooth_speed_loss
from app.pipeline.events import align_events
from app.pipeline.features import build_features, clean_reference_stats
from app.pipeline.labeling import fouling_levels, label
from app.pipeline.roi import days_to_threshold, excess_cost_per_day, fit_growth_rate
from app.pipeline.validation import time_blocked
from app.synth.generator import GeneratorConfig, generate


def load_raw(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    """讀取 data/raw/（noon_reports.csv + events.csv [+ truth.csv]）。"""
    noon = pd.read_csv(raw_dir / "noon_reports.csv")
    events = pd.read_csv(raw_dir / "events.csv")
    truth_p = raw_dir / "truth.csv"
    truth = pd.read_csv(truth_p, parse_dates=["report_date"]) if truth_p.exists() else None
    return noon, events, truth


def generate_and_save(raw_dir: Path, cfg: GeneratorConfig | None = None) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    data = generate(cfg)
    data["noon_reports"].to_csv(raw_dir / "noon_reports.csv", index=False)
    data["events"].to_csv(raw_dir / "events.csv", index=False)
    data["truth"].to_csv(raw_dir / "truth.csv", index=False)


def run_pipeline(raw_dir: Path | None = None, artifact_dir: Path | None = None) -> dict:
    """執行完整管線並輸出 artifacts。回傳 summary dict。"""
    raw_dir = raw_dir or (config.DATA_DIR / "raw")
    artifact_dir = artifact_dir or config.ARTIFACT_DIR
    artifact_dir.mkdir(parents=True, exist_ok=True)

    noon_raw, events_raw, truth = load_raw(raw_dir)
    noon = schema.normalize_noon_reports(noon_raw)
    filtered = schema.apply_quality_filter(
        noon, config.GOOD_WEATHER_MAX_WIND, config.MIN_FULL_SPEED_HOURS)
    events = events_raw.copy()
    events[schema.EVENT_DATE] = pd.to_datetime(events[schema.EVENT_DATE])
    aligned = align_events(filtered, events, config.BASELINE_WINDOW_DAYS)
    refs = clean_reference_stats(aligned)
    weak = refs[refs["n_baseline_rows"] < config.BASELINE_MIN_ROWS]
    if len(weak):
        print(f"[warn] 基準樣本不足的船（仍照常評分，但基準較不可靠）: {list(weak.index)}")
    feat = build_features(aligned, refs)

    model = CleanBaselineModel().fit(feat)
    scored = smooth_speed_loss(model.score_rows(feat))

    # 肘點法分級（掛在平滑殘差上）
    cuts = fouling_levels(scored["speed_loss_smooth"].dropna().to_numpy())

    # 每船摘要
    ships = []
    for ship_id, grp in scored.groupby(schema.SHIP_ID):
        grp = grp.sort_values(schema.REPORT_DATE)
        recent = grp.tail(1).iloc[0]
        # 對外顯示的 Speed Loss 下限為 0（負值＝比基準期更乾淨，僅是雜訊）
        cur_sl = max(0.0, float(np.nan_to_num(recent["speed_loss_smooth"])))
        day_num = (grp[schema.REPORT_DATE] - grp[schema.REPORT_DATE].min()).dt.days.to_numpy()
        growth = fit_growth_rate(day_num, grp["speed_loss_smooth"].to_numpy())
        f_ref = float(grp["f_ref"].iloc[-1])
        lvl = label(pd.Series([cur_sl]), cuts).iloc[0]
        ships.append({
            "ship_id": ship_id,
            "ship_name": str(grp[schema.SHIP_NAME].iloc[-1]) if schema.SHIP_NAME in grp else ship_id,
            "current_speed_loss_pct": round(cur_sl, 2),
            "fouling_level": lvl,
            "days_since_clean": int(recent["days_since_clean"]),
            "growth_pp_per_day": round(growth, 4),
            "days_to_threshold": days_to_threshold(cur_sl, growth, config.CLEANING_THRESHOLD_PCT),
            "f_ref": round(f_ref, 2),
            "v_ref": round(float(grp["v_ref"].iloc[-1]), 2),
            "excess_cost_per_day": round(
                excess_cost_per_day(cur_sl, f_ref, config.VLSFO_PRICE_USD), 0),
            "last_date": recent[schema.REPORT_DATE].strftime("%Y-%m-%d"),
        })
    fleet = pd.DataFrame(ships).sort_values("current_speed_loss_pct", ascending=False)

    # 驗證指標（時間分塊；有 ground truth 才算得出來）
    metrics = {}
    if truth is not None:
        cutoff = str(noon[schema.REPORT_DATE].quantile(0.8).date())
        metrics = time_blocked(feat, truth, cutoff=cutoff)

    # 輸出
    model.save(artifact_dir / "baseline_model.json")
    refs.to_csv(artifact_dir / "clean_refs.csv")
    keep_cols = [schema.SHIP_ID, schema.REPORT_DATE, schema.AVG_SPEED, schema.DAILY_FOC,
                 "days_since_clean", "baseline_flag", "expected_foc", "excess_foc",
                 "excess_foc_pct", "speed_loss_pct", "speed_loss_smooth", "excess_foc_smooth"]
    scored[keep_cols].to_csv(artifact_dir / "scored.csv", index=False)
    events.to_csv(artifact_dir / "events.csv", index=False)
    fleet.to_csv(artifact_dir / "fleet.csv", index=False)
    summary = {
        "n_ships": int(fleet.shape[0]),
        "n_rows_scored": int(len(scored)),
        "elbow_cuts_pct": [round(c, 2) for c in cuts],
        "validation": metrics,
    }
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--synth", action="store_true", help="先產生合成資料再跑管線")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    raw = config.DATA_DIR / "raw"
    if args.synth or not (raw / "noon_reports.csv").exists():
        print("[*] 產生合成資料 ...")
        generate_and_save(raw, GeneratorConfig(seed=args.seed))
    print("[*] 執行管線 ...")
    print(json.dumps(run_pipeline(), indent=2, ensure_ascii=False))
