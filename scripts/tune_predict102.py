"""Optuna 調參 — 102 格預測模型。

目標函數＝遮蔽窗口模擬的整體 micro MAPE（與真實任務同分佈），
搜尋結果寫入 data/artifacts/best_params_102.json，predict102 會自動讀取。

    python scripts/tune_predict102.py --trials 40
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import optuna

from app import config
from app.pipeline.predict102 import PARAMS, build_dataset, masked_window_validation

optuna.logging.set_verbosity(optuna.logging.WARNING)


def main(trials: int) -> None:
    df, _ = build_dataset()

    def objective(trial: optuna.Trial) -> float:
        params = dict(PARAMS)
        params.update(
            n_estimators=800,
            max_depth=trial.suggest_int("max_depth", 4, 9),
            learning_rate=trial.suggest_float("learning_rate", 0.015, 0.1, log=True),
            min_child_weight=trial.suggest_int("min_child_weight", 2, 40, log=True),
            subsample=trial.suggest_float("subsample", 0.6, 1.0),
            colsample_bytree=trial.suggest_float("colsample_bytree", 0.5, 1.0),
            reg_lambda=trial.suggest_float("reg_lambda", 0.5, 20.0, log=True),
            reg_alpha=trial.suggest_float("reg_alpha", 1e-3, 5.0, log=True),
        )
        val = masked_window_validation(df, params=params)
        return float(val.attrs["micro_mape_pct"])

    study = optuna.create_study(direction="minimize",
                                sampler=optuna.samplers.TPESampler(seed=42))
    # 先把目前手設參數當 baseline trial
    study.enqueue_trial({"max_depth": 6, "learning_rate": 0.03, "min_child_weight": 8,
                         "subsample": 0.85, "colsample_bytree": 0.8,
                         "reg_lambda": 3.0, "reg_alpha": 1e-3})
    study.optimize(objective, n_trials=trials, show_progress_bar=False)

    best = dict(PARAMS)
    best.update(study.best_params, n_estimators=800)
    out = config.ARTIFACT_DIR / "best_params_102.json"
    out.write_text(json.dumps(best, indent=2))
    print(f"[OK] best micro MAPE = {study.best_value:.3f}%（baseline trial 0 = "
          f"{study.trials[0].value:.3f}%），參數已寫入 {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=40)
    args = ap.parse_args()
    main(args.trials)
