"""肘點法與 ROI 引擎的單元測試。"""

import numpy as np
import pandas as pd
import pytest

from app.pipeline.labeling import elbow_cut, fouling_levels, label
from app.pipeline.roi import (RoiParams, days_to_threshold, excess_fuel_tons_per_day,
                              fit_growth_rate, whatif_curve)


def test_elbow_finds_knee_between_clusters():
    rng = np.random.default_rng(3)
    vals = np.concatenate([rng.normal(0.5, 0.4, 700), rng.normal(6.0, 0.8, 300)])
    cut, strength = elbow_cut(vals)
    assert 1.2 < cut < 5.0
    assert strength >= 0.15


def test_smooth_distribution_falls_back_to_quantiles():
    """近乎均勻的分佈沒有膝點——應退回分位數且門檻不得為負。"""
    rng = np.random.default_rng(5)
    vals = np.concatenate([rng.uniform(-2, 18, 5000)])
    cut1, cut2 = fouling_levels(vals)
    assert 0 <= cut1 < cut2 <= 18
    labels = label(pd.Series(np.clip(vals, 0, None)), (cut1, cut2))
    shares = labels.value_counts(normalize=True)
    assert shares.min() > 0.15  # 三級都要有合理佔比


def test_fouling_levels_three_way():
    rng = np.random.default_rng(3)
    vals = np.concatenate([rng.normal(0.5, 0.4, 700), rng.normal(4.0, 0.6, 200),
                           rng.normal(9.0, 0.8, 100)])
    cut1, cut2 = fouling_levels(vals)
    assert cut1 < cut2
    labels = label(pd.Series(vals), (cut1, cut2))
    assert set(labels.unique()) == {"low", "medium", "high"}
    # 大致的群組還原
    assert (labels[vals < 1.5] == "low").mean() > 0.9
    assert (labels[vals > 7.5] == "high").mean() > 0.8


def test_elbow_degenerate_inputs():
    assert elbow_cut(np.array([1.0, 1.0, 1.0]))[0] == 1.0
    assert elbow_cut(np.array([]))[0] == 0.0


def test_cube_law_excess_fuel():
    # s=10%：1/0.9³ − 1 ≈ 37.2%
    assert excess_fuel_tons_per_day(10.0, 30.0) == pytest.approx(30 * 0.372, rel=0.01)
    assert excess_fuel_tons_per_day(0.0, 30.0) == 0.0


def test_growth_rate_fit():
    days = np.arange(200.0)
    sl = 1.0 + 0.03 * days + np.random.default_rng(0).normal(0, 0.2, 200)
    assert fit_growth_rate(days, sl) == pytest.approx(0.03, abs=0.01)
    # 負趨勢（剛清洗完）不得回報負結垢率
    assert fit_growth_rate(days, 5.0 - 0.02 * days) == 0.0


def test_whatif_dirty_ship_should_clean_now():
    p = RoiParams(fuel_price_usd=600, cleaning_cost_usd=20000, horizon_days=180)
    r = whatif_curve(current_sl_pct=8.0, growth_pp_day=0.03, f_ref=30.0, params=p)
    assert r["best_day"] is not None and r["best_day"] <= 10
    assert r["best_avg"] < r["no_clean_avg"]
    assert r["payback_days"] < 40


def test_whatif_clean_ship_should_not_clean():
    p = RoiParams(fuel_price_usd=600, cleaning_cost_usd=20000, horizon_days=180)
    r = whatif_curve(current_sl_pct=0.4, growth_pp_day=0.0, f_ref=30.0, params=p)
    assert r["best_day"] is None


def test_days_to_threshold():
    assert days_to_threshold(12.0, 0.05, 10.0) == 0
    assert days_to_threshold(7.0, 0.05, 10.0) == 60
    assert days_to_threshold(7.0, 0.0, 10.0) is None
