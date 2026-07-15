"""Strict per-ship STW/power Speed Loss prediction.

This module intentionally does not depend on the production fuel model or on SOG.
It implements the dashboard decision method directly from normalized noon-report
fields so adjustable weather and load filters can be recomputed on request.
"""

from __future__ import annotations

from math import ceil
from typing import Literal

import numpy as np
import pandas as pd

LoadCondition = Literal["all", "laden", "ballast"]

STW_COLUMN = "stw"
POWER_COLUMN = "horse_power"
DAY_COLUMN = "day"
DISPLACEMENT_COLUMN = "displacement"
HOURS_TOTAL_COLUMN = "hours_total"
REQUIRED_SOURCE_COLUMNS = frozenset(
    {
        "ship_id",
        DAY_COLUMN,
        STW_COLUMN,
        POWER_COLUMN,
        "wind_scale",
        DISPLACEMENT_COLUMN,
    }
)
Z_90 = 1.645
OUTLIER_LOW_PCT = -8.0
OUTLIER_HIGH_PCT = 45.0
CLEANING_DROP_PCT = 3.0

_LABELS = {"laden": "重載", "ballast": "壓艙"}


def _ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    design = np.column_stack([np.ones(len(x)), x])
    intercept, slope = np.linalg.lstsq(design, y, rcond=None)[0]
    return float(intercept), float(slope)


def _rounded(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def _requested_groups(load_condition: LoadCondition) -> tuple[str, ...]:
    if load_condition == "all":
        return ("laden", "ballast")
    return (load_condition,)


def _unavailable_group(
    condition: str,
    reason: str,
    counts: dict[str, int] | None = None,
) -> dict:
    return {
        "load_condition": condition,
        "load_label": _LABELS[condition],
        "available": False,
        "reason": reason,
        "counts": counts or {},
        "baseline": None,
        "current_speed_loss_pct": None,
        "deterioration_rate_pct_per_month": None,
        "latest_day": None,
        "latest_cleaning_day": None,
        "cleaning_days": [],
        "threshold_crossing": {
            "eta_days": None,
            "earliest_days": None,
            "latest_days": None,
        },
        "history": [],
        "trend": [],
        "forecast": [],
    }


def _first_crossing_days(values: np.ndarray, threshold: float) -> int | None:
    """Return ceil(days from the first point), with linear interpolation."""
    if len(values) == 0 or not np.isfinite(values[0]):
        return None
    if values[0] >= threshold:
        return 0
    for index in range(1, len(values)):
        previous = float(values[index - 1])
        current = float(values[index])
        if not np.isfinite(previous) or not np.isfinite(current):
            continue
        if current >= threshold and current > previous:
            fraction = (threshold - previous) / (current - previous)
            return max(0, int(ceil((index - 1) + fraction - 1e-12)))
    return None


def _predict_group(
    group: pd.DataFrame,
    condition: str,
    forecast_days: int,
    threshold_pct: float,
) -> dict:
    counts = {"load_rows": int(len(group))}
    if len(group) < 2:
        return _unavailable_group(condition, "載況有效紀錄不足，無法建立功率基準。", counts)

    ordered = group.sort_values(DAY_COLUMN, kind="stable").reset_index(drop=True).copy()
    baseline_rows = max(2, int(ceil(len(ordered) * 0.30)))
    baseline = ordered.iloc[:baseline_rows]
    power_root = np.cbrt(baseline[POWER_COLUMN].to_numpy(dtype=float))
    if len(power_root) < 2 or float(np.ptp(power_root)) <= 1e-12:
        counts["baseline_rows"] = int(len(baseline))
        return _unavailable_group(condition, "最早 30% 紀錄的功率沒有足夠變化，OLS 不可識別。", counts)

    baseline_stw = baseline[STW_COLUMN].to_numpy(dtype=float)
    baseline_intercept, baseline_slope = _ols(power_root, baseline_stw)

    all_power_root = np.cbrt(ordered[POWER_COLUMN].to_numpy(dtype=float))
    expected_stw = baseline_intercept + baseline_slope * all_power_root
    measured_stw = ordered[STW_COLUMN].to_numpy(dtype=float)
    valid_expected = np.isfinite(expected_stw) & (expected_stw > 0)
    speed_loss = np.full(len(ordered), np.nan, dtype=float)
    speed_loss[valid_expected] = (
        (expected_stw[valid_expected] - measured_stw[valid_expected])
        / expected_stw[valid_expected]
        * 100.0
    )
    inlier = (
        np.isfinite(speed_loss)
        & (speed_loss >= OUTLIER_LOW_PCT)
        & (speed_loss <= OUTLIER_HIGH_PCT)
    )
    points = ordered.loc[inlier, [DAY_COLUMN]].copy()
    points["speed_loss_pct"] = speed_loss[inlier]
    counts.update({
        "baseline_rows": int(len(baseline)),
        "speed_loss_rows": int(valid_expected.sum()),
        "inlier_rows": int(inlier.sum()),
        "outlier_rows": int((valid_expected & ~inlier).sum()),
    })
    if points.empty:
        return _unavailable_group(condition, "Speed Loss 計算後沒有落在 -8% 至 45% 的有效值。", counts)

    points["bin_start"] = np.floor(points[DAY_COLUMN] / 7.0) * 7.0
    binned = (
        points.groupby("bin_start", as_index=False)
        .agg(speed_loss_pct=("speed_loss_pct", "mean"), observations=("speed_loss_pct", "size"))
        .sort_values("bin_start")
        .reset_index(drop=True)
    )
    binned[DAY_COLUMN] = binned["bin_start"] + 3.5
    counts["bins"] = int(len(binned))
    if len(binned) < 3:
        return _unavailable_group(condition, "7 天分箱後少於 3 點，無法估計趨勢與信賴帶。", counts)

    binned_y = binned["speed_loss_pct"].to_numpy(dtype=float)
    drops = np.flatnonzero(np.diff(binned_y) < -CLEANING_DROP_PCT) + 1
    # Ignore only a drop into the final bin: there is no observation after it
    # to corroborate a cleaning. A later non-terminal drop remains the latest
    # cleaning even when it leaves too few bins; that group must then report
    # unavailable instead of silently fitting an older segment.
    cleaning_indices = drops[drops < len(binned) - 1]
    cleaning_days = binned.iloc[cleaning_indices][DAY_COLUMN].to_numpy(dtype=float)
    trend_start_index = int(cleaning_indices[-1]) if len(cleaning_indices) else 0
    trend_data = binned.iloc[trend_start_index:].reset_index(drop=True)
    counts["trend_bins"] = int(len(trend_data))
    if len(trend_data) < 3:
        result = _unavailable_group(
            condition,
            "最近一次清洗偵測後少於 3 個分箱點，無法估計趨勢與信賴帶。",
            counts,
        )
        result["cleaning_days"] = [_rounded(day, 1) for day in cleaning_days]
        result["latest_cleaning_day"] = (
            _rounded(cleaning_days[-1], 1) if len(cleaning_days) else None
        )
        result["history"] = [
            {
                "day": _rounded(row[DAY_COLUMN], 1),
                "speed_loss_pct": _rounded(row["speed_loss_pct"]),
                "observations": int(row["observations"]),
            }
            for _, row in binned.iterrows()
        ]
        return result

    trend_x = trend_data[DAY_COLUMN].to_numpy(dtype=float)
    trend_y = trend_data["speed_loss_pct"].to_numpy(dtype=float)
    x_bar = float(trend_x.mean())
    sxx = float(np.square(trend_x - x_bar).sum())
    if sxx <= 1e-12:
        return _unavailable_group(condition, "趨勢分箱時間沒有變化，OLS 不可識別。", counts)

    trend_intercept, trend_slope = _ols(trend_x, trend_y)
    fitted = trend_intercept + trend_slope * trend_x
    residual_s = float(np.sqrt(np.square(trend_y - fitted).sum() / (len(trend_x) - 2)))
    last_day = float(binned.iloc[-1][DAY_COLUMN])
    forecast_x = last_day + np.arange(forecast_days + 1, dtype=float)
    forecast_mid = trend_intercept + trend_slope * forecast_x
    half_width = Z_90 * residual_s * np.sqrt(
        1.0 / len(trend_x) + np.square(forecast_x - x_bar) / sxx
    )
    forecast_low = forecast_mid - half_width
    forecast_high = forecast_mid + half_width

    ss_total = float(np.square(baseline_stw - baseline_stw.mean()).sum())
    baseline_fit = baseline_intercept + baseline_slope * power_root
    baseline_r2 = (
        1.0 - float(np.square(baseline_stw - baseline_fit).sum()) / ss_total
        if ss_total > 1e-12
        else None
    )

    return {
        "load_condition": condition,
        "load_label": _LABELS[condition],
        "available": True,
        "reason": None,
        "counts": counts,
        "baseline": {
            "sample_fraction": 0.30,
            "rows": int(len(baseline)),
            "intercept": _rounded(baseline_intercept, 6),
            "horse_power_cuberoot_slope": _rounded(baseline_slope, 6),
            "r_squared": _rounded(baseline_r2, 4) if baseline_r2 is not None else None,
        },
        "current_speed_loss_pct": _rounded(binned_y[-1]),
        "deterioration_rate_pct_per_month": _rounded(trend_slope * 30.0),
        "latest_day": _rounded(last_day, 1),
        "latest_cleaning_day": _rounded(cleaning_days[-1], 1) if len(cleaning_days) else None,
        "cleaning_days": [_rounded(day, 1) for day in cleaning_days],
        "threshold_crossing": {
            "eta_days": _first_crossing_days(forecast_mid, threshold_pct),
            "earliest_days": _first_crossing_days(forecast_high, threshold_pct),
            "latest_days": _first_crossing_days(forecast_low, threshold_pct),
        },
        "history": [
            {
                "day": _rounded(row[DAY_COLUMN], 1),
                "speed_loss_pct": _rounded(row["speed_loss_pct"]),
                "observations": int(row["observations"]),
            }
            for _, row in binned.iterrows()
        ],
        "trend": [
            {
                "day": _rounded(day, 1),
                "mid": _rounded(trend_intercept + trend_slope * day),
            }
            for day in trend_x
        ],
        "forecast": [
            {
                "day": _rounded(day, 1),
                "mid": _rounded(mid),
                "lo": _rounded(lo),
                "hi": _rounded(hi),
            }
            for day, mid, lo, hi in zip(
                forecast_x, forecast_mid, forecast_low, forecast_high, strict=True
            )
        ],
    }


def predict_speed_loss(
    records: pd.DataFrame,
    ship_id: str,
    *,
    forecast_days: int = 180,
    threshold_pct: float = 8.0,
    max_wind_scale: float = 4.0,
    load_condition: LoadCondition = "all",
) -> dict:
    """Run the exact STW/HORSE_POWER dashboard pipeline for one ship.

    ``all`` requests still fit laden and ballast independently and return two
    groups. Missing STW, power, displacement, or NOON_UTC day never falls back
    to SOG, draft, cargo, report date, or the legacy fuel-residual model.
    """
    requested = _requested_groups(load_condition)
    parameters = {
        "forecast_days": int(forecast_days),
        "threshold_pct": float(threshold_pct),
        "max_wind_scale": float(max_wind_scale),
        "load_condition": load_condition,
        "confidence_level": 0.90,
        "confidence_z": Z_90,
    }
    base_response = {
        "ship_id": ship_id,
        "method": "per-ship-load-stw-horsepower-ols",
        "time_axis": "NOON_UTC relative day",
        "day0_note": "原始兩檔只提供相對日，無法自行還原真實日曆日期；ETA 以距最新紀錄天數呈現，若另有外部 Day 0 對照表才能換算日曆日。",
        "parameters": parameters,
        "displacement_median": None,
    }

    missing = sorted(REQUIRED_SOURCE_COLUMNS - set(records.columns))
    if missing:
        reason = "strict source 缺少必要欄位：" + ", ".join(missing)
        return {
            **base_response,
            "available": False,
            "reason": reason,
            "filter_counts": {},
            "groups": [_unavailable_group(condition, reason) for condition in requested],
        }

    frame = records.loc[records["ship_id"].astype(str) == str(ship_id)].copy()
    if frame.empty:
        reason = "此船在 strict normalized raw 中沒有紀錄。"
        return {
            **base_response,
            "available": False,
            "reason": reason,
            "filter_counts": {"source_rows": 0},
            "groups": [_unavailable_group(condition, reason) for condition in requested],
        }

    numeric_columns = [
        DAY_COLUMN,
        STW_COLUMN,
        POWER_COLUMN,
        "wind_scale",
        DISPLACEMENT_COLUMN,
        "hours_full_speed",
        HOURS_TOTAL_COLUMN,
    ]
    for column in numeric_columns:
        if column not in frame.columns:
            frame[column] = np.nan
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    positive = (frame[STW_COLUMN] > 0) & (frame[POWER_COLUMN] > 0) & frame[DAY_COLUMN].notna()
    weather = frame["wind_scale"].notna() & (frame["wind_scale"] <= max_wind_scale)
    hours_known = frame["hours_full_speed"].notna() & frame[HOURS_TOTAL_COLUMN].notna()
    steady = (~hours_known) | (
        (frame[HOURS_TOTAL_COLUMN] > 0)
        & (frame["hours_full_speed"] / frame[HOURS_TOTAL_COLUMN] >= 0.5)
    )
    valid = positive & weather & steady
    filtered = frame.loc[valid].copy()
    with_displacement = filtered[DISPLACEMENT_COLUMN].notna()
    model_source = filtered.loc[with_displacement].copy()
    filter_counts = {
        "source_rows": int(len(frame)),
        "positive_stw_power_day_rows": int(positive.sum()),
        "weather_rows": int((positive & weather).sum()),
        "steady_full_speed_rows": int(valid.sum()),
        "hours_ratio_checked_rows": int((positive & weather & hours_known).sum()),
        "with_displacement_rows": int(len(model_source)),
    }
    if model_source.empty:
        reason = "篩選後沒有含 DISPLACEMENT 的有效紀錄；不得以吃水或貨量替代載況。"
        return {
            **base_response,
            "available": False,
            "reason": reason,
            "filter_counts": filter_counts,
            "groups": [
                _unavailable_group(condition, reason, {"load_rows": 0})
                for condition in requested
            ],
        }

    displacement_median = float(model_source[DISPLACEMENT_COLUMN].median())
    model_source["load_condition"] = np.where(
        model_source[DISPLACEMENT_COLUMN] >= displacement_median,
        "laden",
        "ballast",
    )
    groups = [
        _predict_group(
            model_source.loc[model_source["load_condition"] == condition],
            condition,
            int(forecast_days),
            float(threshold_pct),
        )
        for condition in requested
    ]
    available = any(group["available"] for group in groups)
    return {
        **base_response,
        "available": available,
        "reason": None if available else "所選載況沒有足夠資料完成 strict OLS 預測。",
        "filter_counts": filter_counts,
        "displacement_median": _rounded(displacement_median, 3),
        "groups": groups,
    }
