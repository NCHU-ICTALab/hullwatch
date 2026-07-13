"""經濟決策引擎：超額成本、What-if 清洗日掃描、最佳清洗日。

立方定律：維持同航速時，油耗放大係數 = 1/(1−s)³。
超額油耗(噸/天) = f_ref × (1/(1−s)³ − 1)；成本 = 超額油耗 × 油價。
What-if：若第 D 天清洗（成本 C），D 之前照目前結垢率繼續長、之後從殘留值重長，
在視野 H 天內求平均每日總成本最低的 D。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

POST_CLEAN_SL_PCT = 0.5  # 清洗後殘留 speed loss（%）


@dataclass
class RoiParams:
    fuel_price_usd: float
    cleaning_cost_usd: float
    horizon_days: int = 180
    co2_per_ton: float = 3.114


def excess_fuel_tons_per_day(speed_loss_pct: float, f_ref: float) -> float:
    """同航速下因髒污每天多燒的燃油（噸）。"""
    s = np.clip(speed_loss_pct / 100.0, 0.0, 0.35)
    return float(f_ref * (1.0 / (1.0 - s) ** 3 - 1.0))


def excess_cost_per_day(speed_loss_pct: float, f_ref: float, fuel_price: float) -> float:
    return excess_fuel_tons_per_day(speed_loss_pct, f_ref) * fuel_price


def fit_growth_rate(days: np.ndarray, speed_loss_pct: np.ndarray, lookback: int = 120) -> float:
    """以最近 lookback 天的平滑 speed loss 擬合線性結垢率（pp/天），下限 0。"""
    mask = np.isfinite(speed_loss_pct)
    d, s = np.asarray(days, float)[mask], np.asarray(speed_loss_pct, float)[mask]
    if len(d) < 10:
        return 0.0
    recent = d >= d.max() - lookback
    if recent.sum() >= 10:
        d, s = d[recent], s[recent]
    slope = float(np.polyfit(d, s, 1)[0])
    return max(slope, 0.0)


def whatif_curve(current_sl_pct: float, growth_pp_day: float, f_ref: float,
                 params: RoiParams) -> dict:
    """掃描 0..H 天各清洗日的平均每日總成本。

    Returns:
        dict：days、avg_cost（各清洗日的平均每日總成本）、no_clean_avg、
        best_day、best_avg、current_excess_cost、payback_days。
        永不清洗比任何清洗日都便宜時 best_day 為 None。
    """
    H = params.horizon_days
    t = np.arange(H, dtype=float)
    sl_no_clean = current_sl_pct + growth_pp_day * t
    cost_no_clean = np.array([excess_cost_per_day(s, f_ref, params.fuel_price_usd)
                              for s in sl_no_clean])
    no_clean_avg = float(cost_no_clean.mean())

    days = np.arange(0, H + 1)
    avg_costs = np.empty(len(days))
    for D in days:
        pre = cost_no_clean[:D].sum()
        t_post = np.arange(H - D, dtype=float)
        sl_post = POST_CLEAN_SL_PCT + growth_pp_day * t_post
        post = sum(excess_cost_per_day(s, f_ref, params.fuel_price_usd) for s in sl_post)
        avg_costs[D] = (pre + post + params.cleaning_cost_usd) / H

    best_idx = int(np.argmin(avg_costs))
    beats_no_clean = avg_costs[best_idx] < no_clean_avg
    current_cost = excess_cost_per_day(current_sl_pct, f_ref, params.fuel_price_usd)
    payback = params.cleaning_cost_usd / current_cost if current_cost > 1e-9 else float("inf")
    return {
        "days": days.tolist(),
        "avg_cost": np.round(avg_costs, 2).tolist(),
        "no_clean_avg": round(no_clean_avg, 2),
        "best_day": int(days[best_idx]) if beats_no_clean else None,
        "best_avg": round(float(avg_costs[best_idx]), 2),
        "current_excess_cost": round(current_cost, 2),
        "payback_days": round(payback, 1) if np.isfinite(payback) else None,
        "excess_co2_per_day": round(
            excess_fuel_tons_per_day(current_sl_pct, f_ref) * params.co2_per_ton, 2),
    }


def days_to_threshold(current_sl_pct: float, growth_pp_day: float, threshold_pct: float) -> int | None:
    """預估幾天後越過清洗門檻；已越過回 0，永不越過回 None。"""
    if current_sl_pct >= threshold_pct:
        return 0
    if growth_pp_day <= 1e-9:
        return None
    return int(np.ceil((threshold_pct - current_sl_pct) / growth_pp_day))
