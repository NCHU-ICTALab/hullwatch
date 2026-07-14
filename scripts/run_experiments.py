"""油耗預測實驗套件（competition-plan §2 的完整矩陣）。

步驟：
  0. 遮蔽窗口結構分析（PREDICT/HIDDEN 包夾、前後最近可見油耗距離）
  1. 資訊集實驗：同日特徵 / +窗口前錨點 / +前後錨點（錨點於驗證時排除被遮列，防洩漏）
  2. 分組實驗：全隊+船別one-hot / 全隊僅船型 / W1、W2 分訓
  3. 模型比較：物理基準(k·RPM³) / Ridge / RandomForest / XGBoost(tuned) / LightGBM
     + 隨機 5-fold 反面教材（時序洩漏的虛胖對照）
  4. 相似度分析：S21–23 vs 訓練船的特徵向量相關
  5. 勝出配置（允許 W1/W2 混用）→ 5-seed 中位數 → predictions_final.csv

用法：
  python scripts/run_experiments.py --out results/            # 全套（EC2 上跑）
  python scripts/run_experiments.py --quick --out results/    # 本地煙霧測試
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from app import config, schema
from app.pipeline.predict102 import (FEATURES, PARAMS, TARGET, MIN_SANE_STW,
                                     _load_tuned_params, _trainable, build_dataset)

RESET = list(schema.RESET_EVENTS)
W1 = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S21"]


# ---------------------------------------------------------------- step 0
def window_structure(raw_dir: Path) -> pd.DataFrame:
    """遮蔽區塊結構：每塊的範圍、PREDICT 位置、前後最近可見油耗日距離。"""
    noon = pd.read_csv(raw_dir / "noon_reports.csv")
    targets = pd.read_csv(raw_dir / "predict_targets.csv")
    noon["known"] = noon[schema.ME_CONSUMP_VLSFO].notna()
    blocks = []
    for ship, g in noon.groupby(schema.SHIP_ID):
        g = g.sort_values("day")
        masked = g[~g.known]
        if masked.empty:
            continue
        # 連續遮蔽日聚成塊（允許 3 天內的空缺視為同塊）
        days = masked["day"].to_numpy()
        splits = np.where(np.diff(days) > 3)[0] + 1
        for chunk in np.split(days, splits):
            lo, hi = int(chunk[0]), int(chunk[-1])
            tk = targets[(targets.ship_id == ship) & targets.day.between(lo, hi)]
            vis = g[g.known]
            pre = vis[vis.day < lo]["day"]
            post = vis[vis.day > hi]["day"]
            blocks.append({
                "ship": ship, "start": lo, "end": hi, "len_days": hi - lo + 1,
                "n_masked_rows": len(chunk), "n_predict": len(tk),
                "gap_to_prev_visible": int(lo - pre.max()) if len(pre) else None,
                "gap_to_next_visible": int(post.min() - hi) if len(post) else None,
            })
    return pd.DataFrame(blocks).sort_values(["ship", "start"]).reset_index(drop=True)


# ---------------------------------------------------------------- anchors
def add_anchor_features(df: pd.DataFrame, exclude: pd.Index | None = None,
                        k: int = 5) -> pd.DataFrame:
    """窗口前/後錨點：該船最近 k 個「可見油耗日」的均值（排除 exclude 的列）。

    對訓練列＝自身鄰域（shift 避開當日）；對被遮列＝遮蔽窗口外的最近可見日，
    與真實 PREDICT 情境一致。錨點以 f_anchor / rpm³ 正規化後再交給模型比較公平，
    這裡同時給原值與比值。
    """
    df = df.sort_values([schema.SHIP_ID, "day"]).copy()
    known = df[TARGET].notna()
    if exclude is not None:
        known = known & ~df.index.isin(exclude)
    df["_known_foc"] = df[TARGET].where(known)
    g = df.groupby(schema.SHIP_ID)["_known_foc"]
    pre = g.transform(lambda s: s.shift(1).rolling(k, min_periods=1).mean())
    post = g.transform(lambda s: s[::-1].shift(1).rolling(k, min_periods=1).mean()[::-1])
    # 被遮列（或被排除列）的 shift 鄰域可能仍是 NaN → 向前/向後補到最近可見值
    pre = pre.groupby(df[schema.SHIP_ID]).ffill()
    post = post.groupby(df[schema.SHIP_ID]).bfill()
    df["anchor_pre"] = pre
    df["anchor_post"] = post
    df["anchor_pre_per_rpm3"] = df["anchor_pre"] / df["rpm3"].where(df["rpm3"] > 0)
    df["anchor_post_per_rpm3"] = df["anchor_post"] / df["rpm3"].where(df["rpm3"] > 0)
    return df.drop(columns="_known_foc")


ANCHOR_PRE = ["anchor_pre", "anchor_pre_per_rpm3"]
ANCHOR_POST = ["anchor_post", "anchor_post_per_rpm3"]


# ---------------------------------------------------------------- models
def make_model(name: str, seed: int = 42):
    if name == "xgb":
        from xgboost import XGBRegressor

        p = {**PARAMS, **_load_tuned_params()}
        p.pop("early_stopping_rounds", None)
        return XGBRegressor(**p, random_state=seed)
    if name == "lgbm":
        from lightgbm import LGBMRegressor

        return LGBMRegressor(n_estimators=800, learning_rate=0.03, num_leaves=63,
                             min_child_samples=8, subsample=0.85, colsample_bytree=0.8,
                             reg_lambda=3.0, random_state=seed, verbose=-1)
    if name == "rf":
        from sklearn.ensemble import RandomForestRegressor

        return RandomForestRegressor(n_estimators=400, min_samples_leaf=3,
                                     n_jobs=-1, random_state=seed)
    if name == "ridge":
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.impute import SimpleImputer
        from sklearn.preprocessing import StandardScaler

        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                             Ridge(alpha=1.0, random_state=seed))
    raise ValueError(name)


class PhysicsBaseline:
    """每船校準 k = median(FOC/RPM³)，預測 = k·RPM³。無 ML 參考線。"""

    def fit(self, X: pd.DataFrame, y: pd.Series):
        d = X.assign(_y=y)
        self.k_ = d.groupby("_ship")["_y"].sum() * 0  # placeholder index
        self.k_ = d.groupby("_ship").apply(lambda g: (g["_y"] / g["rpm3"]).median())
        self.k_global_ = float((d["_y"] / d["rpm3"]).median())
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        k = X["_ship"].map(self.k_).fillna(self.k_global_)
        return (k * X["rpm3"]).to_numpy()


# ---------------------------------------------------------------- eval core
def masked_folds(df: pd.DataFrame, window_days: int = 12):
    """回傳 [(hold_index, meta), ...]——訓練船重置事件後窗口。"""
    events = pd.read_csv(config.DATA_DIR / "raw" / "events.csv", parse_dates=[schema.EVENT_DATE])
    resets = events[events[schema.EVENT_TYPE].isin(RESET)]
    train_all = _trainable(df)
    folds = []
    for _, e in resets.iterrows():
        sid = e[schema.EVENT_SHIP_ID]
        if sid in ("S21", "S22", "S23"):
            continue
        lo, hi = e[schema.EVENT_DATE], e[schema.EVENT_DATE] + pd.Timedelta(days=window_days)
        hold = train_all[(train_all[schema.SHIP_ID] == sid)
                         & train_all[schema.REPORT_DATE].between(lo, hi)]
        if len(hold) >= 2:
            folds.append((hold.index, {"ship": sid, "event": e[schema.EVENT_TYPE],
                                       "date": str(e[schema.EVENT_DATE].date())}))
    return folds


def evaluate(df: pd.DataFrame, feats: list[str], model_name: str,
             ships: list[str] | None = None, use_anchor_exclude: bool = True,
             window_days: int = 12) -> dict:
    """遮蔽窗口模擬 → micro MAPE。ships 限制訓練/評估範圍（分組實驗用）。"""
    d = df if ships is None else df[df[schema.SHIP_ID].isin(ships)]
    folds = masked_folds(d, window_days)
    errs = []
    for hold_idx, _meta in folds:
        dd = d
        if use_anchor_exclude and any(f.startswith("anchor") for f in feats):
            dd = add_anchor_features(d, exclude=hold_idx)
        train = _trainable(dd).drop(hold_idx, errors="ignore")
        hold = dd.loc[[i for i in hold_idx if i in dd.index]]
        X_tr, X_ho = train[feats].copy(), hold[feats].copy()
        if model_name == "physics":
            X_tr["_ship"], X_ho["_ship"] = train[schema.SHIP_ID], hold[schema.SHIP_ID]
        m = make_model(model_name) if model_name != "physics" else PhysicsBaseline()
        m.fit(X_tr, train[TARGET])
        pred = m.predict(X_ho)
        errs.extend((np.asarray(pred) - hold[TARGET]) / hold[TARGET])
    errs = np.abs(np.asarray(errs, dtype=float))
    return {"micro_mape_pct": round(float(errs.mean()) * 100, 3), "n_rows": len(errs)}


def random_kfold_mape(df: pd.DataFrame, feats: list[str], model_name: str) -> float:
    """反面教材：隨機 5-fold（時序洩漏 → 虛胖）。"""
    from sklearn.model_selection import KFold

    train = _trainable(df)
    errs = []
    for tr, te in KFold(5, shuffle=True, random_state=0).split(train):
        t, h = train.iloc[tr], train.iloc[te]
        m = make_model(model_name)
        m.fit(t[feats], t[TARGET])
        errs.extend(np.abs((m.predict(h[feats]) - h[TARGET]) / h[TARGET]))
    return round(float(np.mean(errs)) * 100, 3)


# ---------------------------------------------------------------- step 4
def ship_similarity(df: pd.DataFrame) -> pd.DataFrame:
    """每船特徵向量（油耗-轉速係數、營運剖面、滑差水準）→ 相關矩陣。"""
    rows = {}
    for ship, g in _trainable(df).groupby(schema.SHIP_ID):
        rows[ship] = {
            "k_rpm3": float((g[TARGET] / g["rpm3"]).median()),
            "stw_med": float(g["stw"].median()),
            "stw_p90": float(g["stw"].quantile(0.9)),
            "rpm_med": float(g["me_rpm"].median()),
            "draft_med": float(g["mean_draft"].median()),
            "slip_med": float(g["slip_full_spd"].median()),
            "foc_med": float(g[TARGET].median()),
            "hours_med": float(g[schema.HOURS_FULL_SPEED].median()),
        }
    sig = pd.DataFrame(rows).T
    z = (sig - sig.mean()) / sig.std()
    return z.T.corr().round(3)


# ---------------------------------------------------------------- main
def main(out_dir: Path, quick: bool):
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = config.DATA_DIR / "raw"
    report: dict = {}

    print("[0] 遮蔽窗口結構 ...")
    win = window_structure(raw_dir)
    win.to_csv(out_dir / "window_structure.csv", index=False)
    s2x = win[win.ship.isin(["S21", "S22", "S23"])]
    report["window_structure"] = {
        "n_blocks_s2x": int(len(s2x)),
        "len_days": s2x.len_days.describe().round(1).to_dict(),
        "gap_prev": s2x.gap_to_prev_visible.describe().round(1).to_dict(),
        "gap_next": s2x.gap_to_next_visible.describe().round(1).to_dict(),
    }
    print(s2x.to_string(index=False))

    df, targets = build_dataset()
    df = add_anchor_features(df)  # 全量錨點（提交用；驗證時逐 fold 重算）
    base_feats = FEATURES + [c for c in df.columns if c.startswith("ship_S")]

    infosets = {"A_same_day": base_feats,
                "B_pre_anchor": base_feats + ANCHOR_PRE,
                "C_pre_post_anchor": base_feats + ANCHOR_PRE + ANCHOR_POST}
    models = ["physics", "ridge", "rf", "xgb", "lgbm"]
    groupings = {"pooled_onehot": None, "W1_only": W1,
                 "W2_only": [s for s in df[schema.SHIP_ID].unique() if s not in W1]}
    if quick:
        infosets = {k: infosets[k] for k in ["A_same_day", "C_pre_post_anchor"]}
        models = ["physics", "xgb"]
        groupings = {"pooled_onehot": None}

    print("[1] 資訊集 × 模型（pooled）...")
    grid = []
    for iname, feats in infosets.items():
        for mname in models:
            r = evaluate(df, feats, mname)
            grid.append({"grouping": "pooled_onehot", "infoset": iname, "model": mname, **r})
            print(f"  {iname:20s} {mname:8s} MAPE={r['micro_mape_pct']}%")
    grid_df = pd.DataFrame(grid)

    best_pooled = grid_df.loc[grid_df.micro_mape_pct.idxmin()]
    print(f"[2] 分組實驗（用最佳資訊集 {best_pooled.infoset} × {best_pooled.model}）...")
    best_feats = infosets[best_pooled.infoset]
    for gname, ships in groupings.items():
        if gname == "pooled_onehot":
            continue
        r = evaluate(df, best_feats, best_pooled.model, ships=ships)
        grid.append({"grouping": gname, "infoset": best_pooled.infoset,
                     "model": best_pooled.model, **r})
        print(f"  {gname:14s} MAPE={r['micro_mape_pct']}%")
    grid_df = pd.DataFrame(grid)
    grid_df.to_csv(out_dir / "benchmark.csv", index=False)
    report["benchmark"] = grid_df.to_dict("records")

    print("[3] 隨機 5-fold 反面教材 ...")
    report["random_kfold_mape_pct"] = random_kfold_mape(df, best_feats, best_pooled.model)
    report["masked_window_mape_pct"] = float(best_pooled.micro_mape_pct)
    print(f"  隨機 k-fold={report['random_kfold_mape_pct']}% vs 遮蔽窗口={best_pooled.micro_mape_pct}%")

    print("[4] 相似度分析 ...")
    sim = ship_similarity(df)
    sim.to_csv(out_dir / "ship_similarity.csv")
    report["similarity_top"] = {s: sim[s].drop(s).nlargest(3).round(3).to_dict()
                                for s in ["S21", "S22", "S23"] if s in sim}

    print("[5] 最終提交：勝出配置 5-seed 中位數 ...")
    w1_ships = W1
    w2_ships = [s for s in df[schema.SHIP_ID].unique() if s not in W1]
    # 分組 vs pooled 以 benchmark 決定；預測用全量錨點特徵
    use_group = {}
    for grp, ships in [("W1", w1_ships), ("W2", w2_ships)]:
        gname = f"{grp}_only"
        grow = grid_df[grid_df.grouping == gname]
        pooled = float(best_pooled.micro_mape_pct)
        use_group[grp] = (len(grow) > 0
                          and float(grow.micro_mape_pct.iloc[0]) < pooled)
    report["use_group_models"] = use_group

    key = df.set_index([schema.SHIP_ID, "day"])
    preds = {}
    for seed in ([42] if quick else [42, 7, 2024, 555, 31337]):
        for grp, ships in [("W1", w1_ships), ("W2", w2_ships)]:
            sub = df[df[schema.SHIP_ID].isin(ships)] if use_group[grp] else df
            train = _trainable(sub)
            m = make_model(best_pooled.model, seed=seed)
            m.fit(train[best_feats], train[TARGET])
            for _, t in targets.iterrows():
                if (t.ship_id in ships) != (grp == ("W1" if t.ship_id in W1 else "W2")):
                    continue
                if t.ship_id not in ships:
                    continue
                r = key.loc[(t.ship_id, t.day)]
                if isinstance(r, pd.DataFrame):
                    r = r.iloc[0]
                if float(r["stw"]) <= MIN_SANE_STW:
                    continue  # 漂航日走 predict102 的專用估計器（沿用現有提交值）
                rate = float(m.predict(pd.DataFrame([r[best_feats].astype(float)]))[0])
                preds.setdefault((t.ship_id, int(t.day), t.fuel_type), []).append(
                    rate * float(r[schema.HOURS_FULL_SPEED]) / 24.0)
    # 併回（漂航日沿用既有 predictions.csv）
    existing = pd.read_csv(config.DATA_DIR / "submission" / "predictions.csv")
    rows = []
    for _, t in targets.iterrows():
        kk = (t.ship_id, int(t.day), t.fuel_type)
        if kk in preds:
            v = float(np.median(preds[kk]))
        else:
            v = float(existing[(existing.ship_id == t.ship_id) & (existing.day == t.day)
                               ]["predicted_value"].iloc[0])
        rows.append({"ship_id": t.ship_id, "day": int(t.day),
                     "fuel_type": t.fuel_type, "predicted_value": round(v, 2)})
    final = pd.DataFrame(rows)
    final.to_csv(out_dir / "predictions_final.csv", index=False)
    report["n_predictions"] = len(final)
    report["elapsed_min"] = round((time.time() - t0) / 60, 1)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    print(f"[DONE] {report['elapsed_min']} min → {out_dir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    main(args.out, args.quick)
