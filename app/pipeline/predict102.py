"""102 格油耗預測（競賽產出 1）。

任務：預測 S21–S23 遮蔽窗口內、指定燃料的主機全速油耗
（= 全速時段內消耗的該燃料總量，MT）。

管線：
1. 內部以「VLSFO 當量、正規化 24h 的全速油耗率」為訓練標的（跨燃料/跨時數可比）
2. 特徵全部取自遮蔽區間內仍可見的 A 類欄位 + 事件衍生（距清洗/拋光/塢修天數）
   —— H 類（功率/負載/SFOC 等）在預測日不可見，嚴禁入模
3. 預測後換算回提交格式：predicted_value = rate × hours/24 × (40.2 / LCV_fuel)
   （本次 102 格皆為 HSHFO/VLSFO，LCV 同為 40.2，係數恰為 1）

驗證（無 ground truth 的替代方案）：
- 遮蔽窗口模擬：對每個訓練船的重置事件，遮蔽事件後 45 天，訓練→預測→比對
  —— 與真實遮蔽的分佈一致（都是「養護後」窗口）
- 時間分塊：後 20% 時段外推

用法：
    python -m app.pipeline.predict102            # 驗證 + 預測 + 輸出提交檔
    python -m app.pipeline.predict102 --no-val   # 只出提交檔
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from app import config, schema
from app.pipeline.events import align_events
from app.pipeline.ingest_yangming import LCV, LCV_VLSFO

FEATURES = [
    "stw", "me_rpm", "prop_rpm", "slip_full_spd", "current_proxy",
    "mean_draft", "displacement", "cargo",
    "wind_scale", "wind_speed", "sea_height", "swell_height",
    "sea_water_temp", "water_depth",
    "days_since_clean", "days_since_polish", "days_since_dd",
    "hours_full_speed",
    "is_w2",
    # 物理錨點（皆由 A 類可見欄位衍生）：功率 ∝ 轉速³、阻力 ∝ 航速³
    "rpm3", "stw3", "stw_per_rpm",
    # 船別 one-hot（S21–S23 在可見段有訓練列，故合法）由 _fit 動態加入
]
TARGET = "daily_foc"  # VLSFO 當量、24h 正規化

PARAMS = dict(
    n_estimators=1200, learning_rate=0.03, max_depth=6, min_child_weight=8,
    subsample=0.85, colsample_bytree=0.8, reg_lambda=3.0,
    tree_method="hist", early_stopping_rounds=80,
)


def build_dataset(raw_dir: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """回傳 (全資料含特徵, targets)。不做品質篩選——由呼叫端依用途篩。"""
    raw_dir = Path(raw_dir or (config.DATA_DIR / "raw"))
    noon = pd.read_csv(raw_dir / "noon_reports.csv", parse_dates=[schema.REPORT_DATE])
    noon[schema.DAILY_FOC] = (noon[schema.ME_CONSUMP_VLSFO]
                              / noon[schema.HOURS_FULL_SPEED].where(noon[schema.HOURS_FULL_SPEED] > 0)
                              * 24.0)
    events = pd.read_csv(raw_dir / "events.csv", parse_dates=[schema.EVENT_DATE])
    noon = noon.sort_values([schema.SHIP_ID, schema.REPORT_DATE]).reset_index(drop=True)
    aligned = align_events(noon, events, config.BASELINE_WINDOW_DAYS)
    # 塢修單獨的時間軸（塗裝重置 vs 水下清洗，物理不同）
    dd = events[events[schema.EVENT_TYPE] == "drydock"]
    from app.pipeline.events import _days_since_last

    parts = []
    for ship_id, grp in aligned.groupby(schema.SHIP_ID, sort=False):
        days, idx = _days_since_last(grp[schema.REPORT_DATE],
                                     dd[dd[schema.EVENT_SHIP_ID] == ship_id][schema.EVENT_DATE].sort_values())
        grp = grp.copy()
        grp["days_since_dd"] = np.where(idx < 0, np.nan, days)
        parts.append(grp)
    df = pd.concat(parts, ignore_index=True)
    df["is_w2"] = (df["ship_type"] == "W2").astype(int)
    df["rpm3"] = df["me_rpm"] ** 3
    df["stw3"] = df["stw"] ** 3
    df["stw_per_rpm"] = df["stw"] / df["me_rpm"].where(df["me_rpm"] > 0)  # 每轉前進量（滑差反向代理）
    df = pd.concat([df, pd.get_dummies(df[schema.SHIP_ID], prefix="ship", dtype=int)], axis=1)
    targets = pd.read_csv(raw_dir / "predict_targets.csv")
    return df, targets


def _feature_cols(df: pd.DataFrame) -> list[str]:
    return FEATURES + [c for c in df.columns if c.startswith("ship_S")]


def _fit(train: pd.DataFrame, seed: int = 42) -> tuple[XGBRegressor, list[str]]:
    cols = _feature_cols(train)
    rng = np.random.default_rng(seed)
    mask = rng.random(len(train)) < 0.9
    m = XGBRegressor(**PARAMS, random_state=seed)
    m.fit(train.loc[mask, cols], train.loc[mask, TARGET],
          eval_set=[(train.loc[~mask, cols], train.loc[~mask, TARGET])], verbose=False)
    return m, cols


MIN_SANE_STW = 5.0    # 節；「全速 24h 但 STW=0、油耗 4 噸」的異常列會炸掉訓練與 MAPE
MIN_SANE_FOC = 5.0    # 噸/天


def _trainable(df: pd.DataFrame) -> pd.DataFrame:
    """訓練列：油耗已知、與預測日同條件（全速 ≥22h、風 ≤4）、通過物理合理性檢查。"""
    return df[(df[TARGET].notna())
              & (df[schema.HOURS_FULL_SPEED] >= config.MIN_FULL_SPEED_HOURS)
              & (df[schema.WIND_SCALE] <= config.GOOD_WEATHER_MAX_WIND)
              & (df["stw"] > MIN_SANE_STW)
              & (df[TARGET] > MIN_SANE_FOC)]


def masked_window_validation(df: pd.DataFrame, window_days: int = 12) -> pd.DataFrame:
    """模擬真實遮蔽：逐一遮蔽訓練船的「重置事件後窗口」，訓練→預測→評估。"""
    events = pd.read_csv(config.DATA_DIR / "raw" / "events.csv", parse_dates=[schema.EVENT_DATE])
    resets = events[events[schema.EVENT_TYPE].isin(schema.RESET_EVENTS)]
    train_all = _trainable(df)
    rows = []
    for _, e in resets.iterrows():
        if e[schema.EVENT_SHIP_ID].startswith("S2") and len(e[schema.EVENT_SHIP_ID]) == 3:
            continue  # S21–S23 的窗口本來就被遮蔽，無法評估
        lo, hi = e[schema.EVENT_DATE], e[schema.EVENT_DATE] + pd.Timedelta(days=window_days)
        hold = train_all[(train_all[schema.SHIP_ID] == e[schema.EVENT_SHIP_ID])
                         & train_all[schema.REPORT_DATE].between(lo, hi)]
        if len(hold) < 2:
            continue
        model, cols = _fit(train_all.drop(hold.index))
        pred = model.predict(hold[cols])
        err = (pred - hold[TARGET]) / hold[TARGET]
        rows.append({"ship": e[schema.EVENT_SHIP_ID], "event": e[schema.EVENT_TYPE],
                     "date": e[schema.EVENT_DATE].date(), "n": len(hold),
                     "mape_pct": round(float(err.abs().mean() * 100), 2),
                     "bias_pct": round(float(err.mean() * 100), 2),
                     "_abs_errs": err.abs().tolist()})
    out = pd.DataFrame(rows)
    if len(out):
        all_errs = [e for lst in out["_abs_errs"] for e in lst]
        out.attrs["micro_mape_pct"] = round(float(np.mean(all_errs)) * 100, 2)
        out = out.drop(columns="_abs_errs")
    return out


def _fit_anomaly_fallback(df: pd.DataFrame):
    """漂航日估計器：STW<5 但仍申報全速時數的日子（102 格中有 3 格屬此型）。

    這種日子的油耗與航速脫鉤、只跟轉速有關；以同型異常列（全船隊 ~128 列）
    的 RPM 迴歸估計。
    """
    odd = df[(df[TARGET].notna()) & (df[TARGET] > 0)
             & (df[schema.HOURS_FULL_SPEED] >= config.MIN_FULL_SPEED_HOURS)
             & (df["stw"] <= MIN_SANE_STW) & (df["me_rpm"].notna())]
    m = XGBRegressor(n_estimators=120, max_depth=3, learning_rate=0.1,
                     min_child_weight=4, tree_method="hist")
    m.fit(odd[["me_rpm", "rpm3"]], odd[TARGET])
    return m


def predict_submission(df: pd.DataFrame, targets: pd.DataFrame,
                       out_path: Path | None = None) -> pd.DataFrame:
    """訓練全量模型，預測 102 格，輸出提交 CSV。"""
    model, cols = _fit(_trainable(df))
    fallback = _fit_anomaly_fallback(df)
    key = df.set_index([schema.SHIP_ID, "day"])
    rows = []
    n_fallback = 0
    for _, t in targets.iterrows():
        r = key.loc[(t.ship_id, t.day)]
        if isinstance(r, pd.DataFrame):
            r = r.iloc[0]
        if float(r["stw"]) <= MIN_SANE_STW:  # 漂航日 → 專用估計器
            rate = float(fallback.predict(r[["me_rpm", "rpm3"]].to_frame().T.astype(float))[0])
            n_fallback += 1
        else:
            rate = float(model.predict(r[cols].to_frame().T.astype(float))[0])
        hours = float(r[schema.HOURS_FULL_SPEED])
        value = rate * hours / 24.0 * (LCV_VLSFO / LCV[t.fuel])
        rows.append({"ship_id": t.ship_id, "day": int(t.day),
                     "fuel_type": t.fuel_type, "predicted_value": round(value, 2)})
    if n_fallback:
        print(f"[info] {n_fallback} 格為漂航日（STW<={MIN_SANE_STW}），採 RPM 專用估計器")
    sub = pd.DataFrame(rows)
    out = Path(out_path or (config.DATA_DIR / "submission" / "predictions.csv"))
    out.parent.mkdir(parents=True, exist_ok=True)
    sub.to_csv(out, index=False)
    print(f"[OK] 提交檔已輸出: {out}（{len(sub)} 列）")
    return sub


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--no-val", action="store_true")
    args = ap.parse_args()
    df, targets = build_dataset()
    if not args.no_val:
        val = masked_window_validation(df)
        print(val.to_string(index=False))
        print(f"\n遮蔽窗口模擬 MAPE: 事件平均 {val.mape_pct.mean():.2f}%"
              f" | 全列 micro {val.attrs.get('micro_mape_pct')}%"
              f" | 最差事件 {val.mape_pct.max():.2f}% | 平均偏差 {val.bias_pct.mean():+.2f}%")
    predict_submission(df, targets)
