"""乾淨基準模型（ADR-0001）。

單一油耗方向模型 f_rel = g(v_rel, wind, draft_rel)，以 monotone constraint
強制油耗隨航速單調遞增；同一條曲線讀出兩個指標：
- 超額油耗：excess_foc = (f_rel_measured − g(v_rel)) × f_ref
- Speed Loss：以向量化二分搜尋反解 g，得同油耗下的預期航速 v_exp，
  speed_loss = (v_exp − v_obs) / v_exp。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from app import schema
from app.pipeline.features import MODEL_FEATURES, MONOTONE_CONSTRAINTS, TARGET

DEFAULT_PARAMS = dict(
    n_estimators=500,
    learning_rate=0.05,
    max_depth=4,
    min_child_weight=20,
    subsample=0.8,
    colsample_bytree=0.9,
    reg_lambda=5.0,
    tree_method="hist",
    monotone_constraints=str(MONOTONE_CONSTRAINTS),
    early_stopping_rounds=50,
)

V_REL_LO, V_REL_HI = 0.5, 1.7  # 反演搜尋範圍（相對航速）


class CleanBaselineModel:
    """乾淨基準油耗模型 + 曲線反演。"""

    def __init__(self, params: dict | None = None):
        self.params = {**DEFAULT_PARAMS, **(params or {})}
        self.model: XGBRegressor | None = None

    def fit(self, feat: pd.DataFrame, seed: int = 42) -> "CleanBaselineModel":
        """以乾淨基準期資料訓練。feat 需含 baseline_flag 與模型特徵。"""
        base = feat[feat["baseline_flag"]]
        if len(base) < 50:
            raise ValueError(f"基準樣本僅 {len(base)} 筆，不足以訓練")
        rng = np.random.default_rng(seed)
        mask = rng.random(len(base)) < 0.85
        train, val = base[mask], base[~mask]
        self.model = XGBRegressor(**self.params, random_state=seed)
        self.model.fit(
            train[MODEL_FEATURES], train[TARGET],
            eval_set=[(val[MODEL_FEATURES], val[TARGET])], verbose=False,
        )
        return self

    def predict_f_rel(self, feat: pd.DataFrame) -> np.ndarray:
        return self.model.predict(feat[MODEL_FEATURES])

    def invert_v_rel(self, feat: pd.DataFrame, iters: int = 30) -> np.ndarray:
        """二分反解：給定實測 f_rel 與天候/吃水條件，求乾淨船達到此油耗的航速。

        monotone constraint 保證曲線對 v_rel 單調，故二分收斂。
        目標超出曲線範圍時收斂到邊界（極端值於下游裁剪）。
        """
        target = feat[TARGET].to_numpy()
        lo = np.full(len(feat), V_REL_LO)
        hi = np.full(len(feat), V_REL_HI)
        probe = feat[MODEL_FEATURES].copy()
        for _ in range(iters):
            mid = (lo + hi) / 2
            probe["v_rel"] = mid
            pred = self.model.predict(probe)
            too_high = pred >= target
            hi = np.where(too_high, mid, hi)
            lo = np.where(too_high, lo, mid)
        return (lo + hi) / 2

    def score_rows(self, feat: pd.DataFrame) -> pd.DataFrame:
        """對每列產出 expected_foc / excess_foc / excess_foc_pct / speed_loss_pct。"""
        out = feat.copy()
        f_rel_hat = self.predict_f_rel(feat)
        out["expected_foc"] = f_rel_hat * out["f_ref"]
        out["excess_foc"] = out[schema.DAILY_FOC] - out["expected_foc"]
        out["excess_foc_pct"] = (out[schema.DAILY_FOC] / out["expected_foc"] - 1.0) * 100
        v_rel_exp = self.invert_v_rel(feat)
        v_exp = v_rel_exp * out["v_ref"]
        raw_sl = (v_exp - out[schema.AVG_SPEED]) / v_exp * 100
        out["speed_loss_pct"] = raw_sl.clip(-10, 35)
        return out

    def save(self, path: str | Path) -> None:
        self.model.save_model(str(path))

    @classmethod
    def load(cls, path: str | Path) -> "CleanBaselineModel":
        inst = cls()
        params = {k: v for k, v in inst.params.items() if k != "early_stopping_rounds"}
        inst.model = XGBRegressor(**params)
        inst.model.load_model(str(path))
        return inst


def smooth_speed_loss(scored: pd.DataFrame, window: int = 7) -> pd.DataFrame:
    """儀表板用的滾動中位數平滑（抗單日雜訊）。"""
    scored = scored.sort_values([schema.SHIP_ID, schema.REPORT_DATE]).copy()
    scored["speed_loss_smooth"] = scored.groupby(schema.SHIP_ID)["speed_loss_pct"].transform(
        lambda s: s.rolling(window, min_periods=3).median()
    )
    scored["excess_foc_smooth"] = scored.groupby(schema.SHIP_ID)["excess_foc"].transform(
        lambda s: s.rolling(window, min_periods=3).median()
    )
    return scored
