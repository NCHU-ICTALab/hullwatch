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
from app.pipeline.features import build_features, ensure_baselines
from app.pipeline.labeling import fouling_levels, label
from app.pipeline.roi import days_to_threshold, excess_cost_per_day, fit_growth_rate
from app.pipeline.validation import time_blocked
from app.synth.generator import GeneratorConfig, generate


def maintenance_effects(scored: pd.DataFrame, events: pd.DataFrame,
                        window_days: int = 60, min_n: int = 5) -> pd.DataFrame:
    """每個養護事件前後 window 天的平滑 Speed Loss 中位數比較（ISO 19030 維護成效 KPI）。

    notes 開頭若帶原始事件類型（如 "UWC+PP; ..."，ingest_yangming 的格式）則一併保留，
    供船殼/螺旋槳效果比估計；合成資料無此格式時 orig_type 退回 canonical。
    """
    rows = []
    for _, e in events.iterrows():
        g = scored[scored[schema.SHIP_ID] == e[schema.EVENT_SHIP_ID]]
        d = e[schema.EVENT_DATE]
        pre = g[(g[schema.REPORT_DATE] < d)
                & (g[schema.REPORT_DATE] >= d - pd.Timedelta(days=window_days))]["speed_loss_smooth"].dropna()
        post = g[(g[schema.REPORT_DATE] > d)
                 & (g[schema.REPORT_DATE] <= d + pd.Timedelta(days=window_days))]["speed_loss_smooth"].dropna()
        if len(pre) < min_n or len(post) < min_n:
            continue
        notes = str(e.get(schema.EVENT_NOTES, "") or "")
        orig = notes.split(";")[0].strip() if ";" in notes else e[schema.EVENT_TYPE]
        rows.append({
            "ship_id": e[schema.EVENT_SHIP_ID],
            "event_date": d,
            "event_type": e[schema.EVENT_TYPE],
            "orig_type": orig,
            "pre_pp": round(float(pre.median()), 2),
            "post_pp": round(float(post.median()), 2),
            "delta_pp": round(float(post.median() - pre.median()), 2),
        })
    out = pd.DataFrame(rows, columns=["ship_id", "event_date", "event_type", "orig_type",
                                      "pre_pp", "post_pp", "delta_pp"])
    # 複合事件（UWI+PP 等）在 canonical events 拆成兩列，效果只算一次
    return out.drop_duplicates(subset=["ship_id", "event_date", "orig_type"]).reset_index(drop=True)


def estimate_prop_share(effects: pd.DataFrame, default: float = 0.3) -> float:
    """船殼 vs 螺旋槳的效果占比：清洗類 vs 純拋光的中位改善幅度比。"""
    if effects.empty:
        return default
    uwc = effects[effects["orig_type"].isin(["UWC", "UWC+PP", "cleaning"])]["delta_pp"]
    pp = effects[effects["orig_type"].isin(["PP", "propeller_polish"])]["delta_pp"]
    uwc_drop = max(float(-uwc.median()) if len(uwc) else 0.0, 0.1)
    pp_drop = max(float(-pp.median()) if len(pp) else 0.0, 0.0)
    share = pp_drop / (pp_drop + uwc_drop)
    return round(float(np.clip(share, 0.0, 0.6)), 3)


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


def prepare_features(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    """raw → 正規化 → 篩選 → 事件對齊 → 相對化特徵。

    Returns:
        (feat, refs, events, truth)。tuning 與 run_pipeline 共用。
    """
    noon_raw, events_raw, truth = load_raw(raw_dir)
    noon = schema.normalize_noon_reports(noon_raw)
    filtered = schema.apply_quality_filter(
        noon, config.GOOD_WEATHER_MAX_WIND, config.MIN_FULL_SPEED_HOURS)
    events = events_raw.copy()
    events[schema.EVENT_DATE] = pd.to_datetime(events[schema.EVENT_DATE])
    aligned = align_events(filtered, events, config.BASELINE_WINDOW_DAYS)
    aligned, refs = ensure_baselines(aligned, config.BASELINE_MIN_ROWS)
    feat = build_features(aligned, refs)
    return feat, refs, events, truth


def run_pipeline(raw_dir: Path | None = None, artifact_dir: Path | None = None,
                 with_loso: bool = False) -> dict:
    """執行完整管線並輸出 artifacts。回傳 summary dict。"""
    raw_dir = raw_dir or (config.DATA_DIR / "raw")
    artifact_dir = artifact_dir or config.ARTIFACT_DIR
    artifact_dir.mkdir(parents=True, exist_ok=True)

    feat, refs, events, truth = prepare_features(raw_dir)

    model = CleanBaselineModel().fit(feat)
    scored = smooth_speed_loss(model.score_rows(feat))

    # 油耗歸因（TreeSHAP，換算成噸/天）：實測 = 基準 + 航速 + 天候 + 吃水 + 髒污殘差
    contribs = model.contributions(scored)
    for col, name in [("contrib_v_rel", "attr_speed_tons"), ("contrib_wind", "attr_wind_tons"),
                      ("contrib_draft_rel", "attr_draft_tons"), ("contrib_bias", "attr_base_tons")]:
        scored[name] = (contribs[col] * scored["f_ref"]).round(3)

    # 肘點法分級（掛在平滑殘差上）
    cuts = fouling_levels(scored["speed_loss_smooth"].dropna().to_numpy())

    # 維護成效表 + 船殼/螺旋槳分割（事件前後 60 天 Speed Loss 中位數比較）
    effects = maintenance_effects(scored, events)
    prop_share = estimate_prop_share(effects)

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

    # 驗證指標（時間分塊 + 可選 LOSO；有 ground truth 才算得出來）
    metrics: dict = {}
    if truth is not None:
        cutoff = str(feat[schema.REPORT_DATE].quantile(0.8).date())
        metrics = time_blocked(feat, truth, cutoff=cutoff)
        if with_loso:
            from app.pipeline.validation import leave_one_ship_out

            loso = leave_one_ship_out(feat, truth)
            metrics["loso"] = {
                "worst_mae_pp": round(float(loso["mae_pp"].max()), 3),
                "mean_mae_pp": round(float(loso["mae_pp"].mean()), 3),
                "worst_corr": round(float(loso["corr"].min()), 3),
            }

    # 輸出
    model.save(artifact_dir / "baseline_model.json")
    refs.to_csv(artifact_dir / "clean_refs.csv")
    effects.to_csv(artifact_dir / "maintenance_effects.csv", index=False)
    keep_cols = [schema.SHIP_ID, schema.REPORT_DATE, schema.AVG_SPEED, schema.DAILY_FOC,
                 schema.WIND_SCALE, schema.HOURS_FULL_SPEED,
                 "days_since_clean", "baseline_flag", "expected_foc", "excess_foc",
                 "excess_foc_pct", "speed_loss_pct", "speed_loss_smooth", "excess_foc_smooth",
                 "attr_base_tons", "attr_speed_tons", "attr_wind_tons", "attr_draft_tons"]
    scored[keep_cols].to_csv(artifact_dir / "scored.csv", index=False)
    events.to_csv(artifact_dir / "events.csv", index=False)
    fleet.to_csv(artifact_dir / "fleet.csv", index=False)
    summary = {
        "n_ships": int(fleet.shape[0]),
        "n_rows_scored": int(len(scored)),
        "elbow_cuts_pct": [round(c, 2) for c in cuts],
        "prop_share": prop_share,
        "validation": metrics,
    }
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--synth", action="store_true", help="先產生合成資料再跑管線")
    ap.add_argument("--loso", action="store_true", help="附帶 Leave-One-Ship-Out 驗證（較慢）")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    raw = config.DATA_DIR / "raw"
    if args.synth or not (raw / "noon_reports.csv").exists():
        print("[*] 產生合成資料 ...")
        generate_and_save(raw, GeneratorConfig(seed=args.seed))
    print("[*] 執行管線 ...")
    print(json.dumps(run_pipeline(with_loso=args.loso), indent=2, ensure_ascii=False))
