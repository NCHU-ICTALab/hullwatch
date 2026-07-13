"""Optuna 調參 — 學長「自訂穩定性目標」的船舶版。

論文目標：maximize Mean(MCC) − 0.5·Std(MCC)（跨刀具穩定）。
船舶版（回歸）：minimize Mean(per-ship RMSE) + 0.5·Std(per-ship RMSE)，
RMSE 以「各船基準期的時間尾段」為驗證（時間分塊，不隨機切）。
逼出對每艘船都穩、而非只對平均好的參數。

合成資料上調參無意義（P2 備忘）；此模組供比賽當天真資料到手後執行：
    python -m app.pipeline.tuning --trials 50
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app import schema
from app.pipeline.baseline import CleanBaselineModel
from app.pipeline.features import MODEL_FEATURES, TARGET


def _time_split_baseline(feat: pd.DataFrame, holdout_ratio: float = 0.25):
    """每艘船基準期的時間尾段作驗證（防時序洩漏）。"""
    base = feat[feat["baseline_flag"]].sort_values([schema.SHIP_ID, schema.REPORT_DATE])
    train_parts, val_parts = [], []
    for _, grp in base.groupby(schema.SHIP_ID):
        k = int(len(grp) * (1 - holdout_ratio))
        train_parts.append(grp.iloc[:k])
        val_parts.append(grp.iloc[k:])
    return pd.concat(train_parts), pd.concat(val_parts)


def stability_objective(feat: pd.DataFrame, params: dict) -> float:
    """回傳 Mean + 0.5·Std 的跨船 RMSE（越小越好）。"""
    train, val = _time_split_baseline(feat)
    model = CleanBaselineModel(params)
    model.fit(train.assign(baseline_flag=True))
    rmses = []
    for _, grp in val.groupby(schema.SHIP_ID):
        if len(grp) < 5:
            continue
        pred = model.predict_f_rel(grp)
        rmses.append(float(np.sqrt(np.mean((pred - grp[TARGET]) ** 2))))
    rmses = np.array(rmses)
    return float(rmses.mean() + 0.5 * rmses.std())


def tune(feat: pd.DataFrame, n_trials: int = 50, seed: int = 42) -> dict:
    """執行 Optuna 搜尋，回傳最佳參數（可直接餵 CleanBaselineModel）。"""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial: "optuna.Trial") -> float:
        params = {
            "max_depth": trial.suggest_int("max_depth", 3, 7),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
            "min_child_weight": trial.suggest_int("min_child_weight", 5, 100, log=True),
            "subsample": trial.suggest_float("subsample", 0.5, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "reg_lambda": trial.suggest_float("reg_lambda", 0.5, 30.0, log=True),
        }
        return stability_objective(feat, params)

    study = optuna.create_study(direction="minimize",
                                sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    return {"best_params": study.best_params, "best_value": study.best_value}


if __name__ == "__main__":
    import argparse
    import json

    from app import config
    from app.pipeline.run import prepare_features

    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=50)
    args = ap.parse_args()
    feat, _, _, _ = prepare_features(config.DATA_DIR / "raw")
    print(json.dumps(tune(feat, n_trials=args.trials), indent=2))
