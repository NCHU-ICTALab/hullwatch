"""Observed maintenance evidence and per-ship Speed Loss branch simulation.

The engineering series is derived only from normalized vt_fd fields. Calendar
surrogates, SOG, scored fuel residuals, and legacy ROI artifacts are intentionally
excluded from this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Mapping

import numpy as np
import pandas as pd

ACTION_TYPES = ("UWI", "PP", "UWI+PP", "UWC", "UWC+PP", "DD")
ACTION_LABELS = {
    "UWI": "水下檢查",
    "PP": "螺旋槳拋光",
    "UWI+PP": "水下檢查＋螺旋槳拋光",
    "UWC": "水下船殼清洗",
    "UWC+PP": "船殼清洗＋螺旋槳拋光",
    "DD": "進塢重塗裝",
}
DEFAULT_RECOVERY_PCT = {
    "UWI": 0.0,
    "PP": 15.0,
    "UWI+PP": 20.0,
    "UWC": 45.0,
    "UWC+PP": 58.0,
    "DD": 75.0,
}

REQUIRED_NOON_COLUMNS = frozenset(
    {
        "ship_id",
        "day",
        "stw",
        "horse_power",
        "displacement",
        "mid_draft",
        "wind_scale",
        "hours_full_speed",
        "hours_total",
        "me_consumption",
    }
)
REQUIRED_EVENT_COLUMNS = frozenset(
    {"ship_id", "event_day", "original_event_type"}
)
MIN_VALID_POINTS = 20
MIN_EVENT_WINDOW_BINS = 3
MIN_DIRTY_SPEED_LOSS_PCT = 1.5
MIN_DN_RATE_PCT_PER_MONTH = 0.3


@dataclass(frozen=True)
class PreparedShip:
    available: bool
    reason: str | None
    counts: dict[str, int]
    history: tuple[dict, ...]
    latest_day: float | None
    now_speed_loss_pct: float | None
    recent_rate_pct_per_month: float | None
    dn_rate_pct_per_month: float | None
    full_speed_daily_consumption_mt: float | None
    baseline: dict | None


@dataclass(frozen=True)
class MaintenanceBenefitContext:
    available: bool
    reason: str | None
    ships: dict[str, PreparedShip]
    evidence: tuple[dict, ...]
    events_by_ship: dict[str, tuple[dict, ...]]
    source_counts: dict[str, int]


def _finite(value: float | int | None, digits: int = 3) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), digits)


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _unavailable_ship(reason: str, source_rows: int = 0) -> PreparedShip:
    return PreparedShip(
        available=False,
        reason=reason,
        counts={"source_rows": int(source_rows)},
        history=(),
        latest_day=None,
        now_speed_loss_pct=None,
        recent_rate_pct_per_month=None,
        dn_rate_pct_per_month=None,
        full_speed_daily_consumption_mt=None,
        baseline=None,
    )


def _prepare_ship(records: pd.DataFrame, ship_id: str) -> PreparedShip:
    ship = records.loc[records["ship_id"].astype(str) == str(ship_id)].copy()
    if ship.empty:
        return _unavailable_ship("此船沒有 normalized noon-report 紀錄。")

    source_rows = len(ship)
    numeric_columns = REQUIRED_NOON_COLUMNS - {"ship_id"}
    for column in numeric_columns:
        ship[column] = _numeric(ship, column)

    positive_power = ship.loc[ship["horse_power"] > 0, "horse_power"]
    if positive_power.empty:
        return _unavailable_ship("HORSE_POWER 沒有正數資料。", source_rows)
    hp_low, hp_high = positive_power.quantile([0.02, 0.98]).tolist()

    hours_ratio = ship["hours_full_speed"] / ship["hours_total"]
    valid = ship.loc[
        ship["day"].notna()
        & ship["stw"].between(6.0, 25.0, inclusive="both")
        & ship["horse_power"].between(hp_low, hp_high, inclusive="both")
        & ship["wind_scale"].le(5.0)
        & ship["hours_total"].gt(0)
        & hours_ratio.ge(0.5)
    ].sort_values("day").copy()

    counts = {
        "source_rows": int(source_rows),
        "valid_filter_rows": int(len(valid)),
    }
    if len(valid) < MIN_VALID_POINTS:
        return PreparedShip(
            **{
                **_unavailable_ship(
                    f"有效點少於 {MIN_VALID_POINTS}（目前 {len(valid)}）。", source_rows
                ).__dict__,
                "counts": counts,
            }
        )

    displacement_median = float(valid["displacement"].median())
    draft_median = float(valid["mid_draft"].median())
    load = np.full(len(valid), 0.5, dtype=float)
    displacement_ok = valid["displacement"].notna() & np.isfinite(displacement_median)
    load[displacement_ok.to_numpy()] = (
        valid.loc[displacement_ok, "displacement"] >= displacement_median
    ).astype(float)
    draft_ok = (
        ~displacement_ok
        & valid["mid_draft"].notna()
        & np.isfinite(draft_median)
    )
    load[draft_ok.to_numpy()] = (
        valid.loc[draft_ok, "mid_draft"] >= draft_median
    ).astype(float)
    valid["load_indicator"] = load

    baseline_rows = max(3, ceil(len(valid) * 0.30))
    baseline_frame = valid.iloc[:baseline_rows]
    design = np.column_stack(
        [
            np.ones(len(baseline_frame)),
            np.cbrt(baseline_frame["horse_power"].to_numpy(dtype=float)),
            baseline_frame["load_indicator"].to_numpy(dtype=float),
        ]
    )
    coefficients = np.linalg.lstsq(
        design, baseline_frame["stw"].to_numpy(dtype=float), rcond=None
    )[0]
    full_design = np.column_stack(
        [
            np.ones(len(valid)),
            np.cbrt(valid["horse_power"].to_numpy(dtype=float)),
            valid["load_indicator"].to_numpy(dtype=float),
        ]
    )
    valid["stw_expected"] = full_design @ coefficients
    valid = valid.loc[valid["stw_expected"] > 0].copy()
    valid["speed_loss_pct"] = (
        (valid["stw_expected"] - valid["stw"]) / valid["stw_expected"] * 100.0
    )
    valid = valid.loc[valid["speed_loss_pct"].between(-6.0, 45.0)].copy()
    counts["speed_loss_rows"] = int(len(valid))
    if len(valid) < MIN_VALID_POINTS:
        return PreparedShip(
            **{
                **_unavailable_ship(
                    f"Speed Loss 外值排除後少於 {MIN_VALID_POINTS} 點（目前 {len(valid)}）。",
                    source_rows,
                ).__dict__,
                "counts": counts,
            }
        )

    valid["week"] = np.floor(valid["day"] / 7.0).astype(int)
    binned = (
        valid.groupby("week", as_index=False)
        .agg(
            day=("day", "median"),
            speed_loss_pct=("speed_loss_pct", "median"),
            observations=("speed_loss_pct", "size"),
        )
        .sort_values("day")
    )
    anchor_p5 = float(binned["speed_loss_pct"].quantile(0.05))
    binned["speed_loss_pct"] = np.maximum(
        binned["speed_loss_pct"] - anchor_p5, 0.0
    )
    counts["seven_day_bins"] = int(len(binned))
    if len(binned) < 3:
        return PreparedShip(
            **{
                **_unavailable_ship("7 日分箱少於 3 點，無法估計近期趨勢。", source_rows).__dict__,
                "counts": counts,
            }
        )

    recent = binned.tail(min(12, len(binned)))
    recent_slope, recent_intercept = np.polyfit(
        recent["day"].to_numpy(dtype=float),
        recent["speed_loss_pct"].to_numpy(dtype=float),
        1,
    )
    latest_day = float(binned["day"].iloc[-1])
    now_speed_loss = max(float(recent_intercept + recent_slope * latest_day), 0.0)
    recent_rate = float(recent_slope * 30.0)
    dn_rate = max(recent_rate, MIN_DN_RATE_PCT_PER_MONTH)

    fuel_rows = ship.loc[
        ship["hours_full_speed"].ge(20.0)
        & ship["me_consumption"].gt(0.0),
        "me_consumption",
    ]
    full_speed_daily_consumption = (
        float(fuel_rows.median()) if len(fuel_rows) else None
    )

    history = tuple(
        {
            "day": _finite(row.day, 2),
            "speed_loss_pct": _finite(row.speed_loss_pct),
            "observations": int(row.observations),
        }
        for row in binned.itertuples()
    )
    return PreparedShip(
        available=True,
        reason=None,
        counts=counts,
        history=history,
        latest_day=latest_day,
        now_speed_loss_pct=now_speed_loss,
        recent_rate_pct_per_month=recent_rate,
        dn_rate_pct_per_month=dn_rate,
        full_speed_daily_consumption_mt=full_speed_daily_consumption,
        baseline={
            "sample_fraction": 0.30,
            "rows": int(baseline_rows),
            "intercept": _finite(coefficients[0]),
            "horse_power_cuberoot_slope": _finite(coefficients[1]),
            "load_indicator_slope": _finite(coefficients[2]),
            "displacement_median": _finite(displacement_median),
            "draft_median": _finite(draft_median),
            "anchor_p5_pct": _finite(anchor_p5),
        },
    )


def _original_events(events: pd.DataFrame) -> pd.DataFrame:
    frame = events.copy()
    for column in REQUIRED_EVENT_COLUMNS:
        if column not in frame:
            frame[column] = np.nan
    frame["event_day"] = _numeric(frame, "event_day")
    frame["original_event_type"] = frame["original_event_type"].astype(str).str.upper()
    frame = frame.loc[
        frame["event_day"].notna()
        & frame["original_event_type"].isin(ACTION_TYPES)
    ].copy()
    identity = (
        ["source_event_id"]
        if "source_event_id" in frame and frame["source_event_id"].notna().any()
        else ["ship_id", "event_day", "original_event_type"]
    )
    return frame.drop_duplicates(identity).sort_values(["event_day", "ship_id"])


def _event_evidence(
    ships: dict[str, PreparedShip], original_events: pd.DataFrame
) -> tuple[dict, ...]:
    samples: dict[str, dict[str, list[float] | int]] = {
        action: {
            "n_total": 0,
            "n_window_valid": 0,
            "recoveries": [],
            "recurrences": [],
        }
        for action in ACTION_TYPES
    }
    for event in original_events.itertuples():
        action = str(event.original_event_type)
        bucket = samples[action]
        bucket["n_total"] = int(bucket["n_total"]) + 1
        prepared = ships.get(str(event.ship_id))
        if prepared is None or not prepared.available:
            continue
        history = pd.DataFrame(prepared.history)
        day = float(event.event_day)
        before = history.loc[
            history["day"].between(day - 60.0, day - 3.0, inclusive="both"),
            "speed_loss_pct",
        ]
        after = history.loc[
            history["day"].between(day + 7.0, day + 55.0, inclusive="both"),
            "speed_loss_pct",
        ]
        if len(before) >= MIN_EVENT_WINDOW_BINS and len(after) >= MIN_EVENT_WINDOW_BINS:
            bucket["n_window_valid"] = int(bucket["n_window_valid"]) + 1
            sl_before = float(before.median())
            if sl_before >= MIN_DIRTY_SPEED_LOSS_PCT:
                recoveries = bucket["recoveries"]
                assert isinstance(recoveries, list)
                recoveries.append(sl_before - float(after.median()))
        recurrence = history.loc[
            history["day"].between(day + 7.0, day + 150.0, inclusive="both"),
            ["day", "speed_loss_pct"],
        ]
        if len(recurrence) >= MIN_EVENT_WINDOW_BINS:
            slope = np.polyfit(
                recurrence["day"].to_numpy(dtype=float),
                recurrence["speed_loss_pct"].to_numpy(dtype=float),
                1,
            )[0]
            recurrences = bucket["recurrences"]
            assert isinstance(recurrences, list)
            recurrences.append(float(slope * 30.0))

    result = []
    for action in ACTION_TYPES:
        bucket = samples[action]
        recoveries = bucket["recoveries"]
        recurrences = bucket["recurrences"]
        assert isinstance(recoveries, list) and isinstance(recurrences, list)
        result.append(
            {
                "event_type": action,
                "label": ACTION_LABELS[action],
                "n_total": int(bucket["n_total"]),
                "n_window_valid": int(bucket["n_window_valid"]),
                "n_used": len(recoveries),
                "n_recurrence": len(recurrences),
                "observed_recovery_median_pp": (
                    _finite(float(np.median(recoveries))) if recoveries else None
                ),
                "observed_recurrence_median_pct_per_month": (
                    _finite(float(np.median(recurrences))) if recurrences else None
                ),
            }
        )
    return tuple(result)


def prepare_maintenance_benefit(
    records: pd.DataFrame, events: pd.DataFrame
) -> MaintenanceBenefitContext:
    """Prepare all fixed OLS series and event evidence once at service startup."""
    missing_noon = sorted(REQUIRED_NOON_COLUMNS - set(records.columns))
    missing_events = sorted(REQUIRED_EVENT_COLUMNS - set(events.columns))
    if missing_noon or missing_events:
        parts = []
        if missing_noon:
            parts.append("noon source 缺欄：" + ", ".join(missing_noon))
        if missing_events:
            parts.append("event source 缺欄：" + ", ".join(missing_events))
        return MaintenanceBenefitContext(
            available=False,
            reason="；".join(parts),
            ships={},
            evidence=(),
            events_by_ship={},
            source_counts={"noon_rows": len(records), "original_events": 0},
        )

    ship_ids = sorted(records["ship_id"].dropna().astype(str).unique())
    ships = {ship_id: _prepare_ship(records, ship_id) for ship_id in ship_ids}
    original_events = _original_events(events)
    evidence = _event_evidence(ships, original_events)
    condition_columns = (
        "propeller_condition",
        "hull_fouling_type",
        "hull_coating_condition",
        "cavitation_found",
    )
    events_by_ship: dict[str, tuple[dict, ...]] = {}
    for ship_id, group in original_events.groupby("ship_id"):
        events_by_ship[str(ship_id)] = tuple(
            {
                "day": _finite(float(row.event_day), 2),
                "event_type": str(row.original_event_type),
                "label": ACTION_LABELS[str(row.original_event_type)],
                "conditions": {
                    column: (
                        None
                        if not hasattr(row, column) or pd.isna(getattr(row, column))
                        else str(getattr(row, column))
                    )
                    for column in condition_columns
                },
            }
            for row in group.itertuples()
        )
    return MaintenanceBenefitContext(
        available=True,
        reason=None,
        ships=ships,
        evidence=evidence,
        events_by_ship=events_by_ship,
        source_counts={
            "noon_rows": int(len(records)),
            "ships": int(len(ships)),
            "original_events": int(len(original_events)),
        },
    )


def _base_response(
    context: MaintenanceBenefitContext,
    ship_id: str,
    parameters: dict,
) -> dict:
    return {
        "ship_id": ship_id,
        "method": "anchored-stw-power-ols-maintenance-branches",
        "time_axis": "NOON_UTC/event_day relative day",
        "day0_note": "原始兩檔沒有可驗證的日曆 Day 0；所有 day 僅供排序、事件窗與日距計算。",
        "available": False,
        "reason": context.reason,
        "source_counts": context.source_counts,
        "parameters": parameters,
        "evidence": list(context.evidence),
        "history": [],
        "past_events": [],
        "no_action": [],
        "actions": [],
    }


def simulate_maintenance_benefit(
    context: MaintenanceBenefitContext,
    ship_id: str,
    *,
    execution_delay_days: int = 0,
    horizon_days: int = 180,
    threshold_pct: float = 8.0,
    fuel_factor: float = 3.0,
    fuel_price_usd_per_mt: float = 600.0,
    sea_ratio: float = 0.65,
    recovery_pct: Mapping[str, float] | None = None,
) -> dict:
    """Simulate no-action and six maintenance branches for one prepared ship."""
    recoveries = {
        action: float((recovery_pct or {}).get(action, default))
        for action, default in DEFAULT_RECOVERY_PCT.items()
    }
    parameters = {
        "execution_delay_days": int(execution_delay_days),
        "horizon_days": int(horizon_days),
        "threshold_pct": float(threshold_pct),
        "fuel_factor": float(fuel_factor),
        "fuel_price_usd_per_mt": float(fuel_price_usd_per_mt),
        "sea_ratio": float(sea_ratio),
        "recovery_pct": recoveries,
        "minimum_dn_rate_pct_per_month": MIN_DN_RATE_PCT_PER_MONTH,
    }
    response = _base_response(context, ship_id, parameters)
    if not context.available:
        return response
    prepared = context.ships.get(str(ship_id))
    if prepared is None:
        return {**response, "reason": "未知船舶。"}
    if not prepared.available:
        return {
            **response,
            "reason": prepared.reason,
            "counts": prepared.counts,
        }
    if prepared.full_speed_daily_consumption_mt is None:
        return {
            **response,
            "reason": "HOURS_FULL_SPEED≥20 的 ME_CONSUMPTION 資料不足。",
            "counts": prepared.counts,
            "history": list(prepared.history),
        }

    latest_day = float(prepared.latest_day)
    now_sl = float(prepared.now_speed_loss_pct)
    dn_rate = float(prepared.dn_rate_pct_per_month)
    offsets = np.arange(horizon_days + 1, dtype=float)
    absolute_days = latest_day + offsets
    no_action_values = now_sl + dn_rate / 30.0 * offsets
    no_action = [
        {"day": _finite(day, 2), "speed_loss_pct": _finite(value)}
        for day, value in zip(absolute_days, no_action_values, strict=True)
    ]
    metric_no_action = no_action_values[:horizon_days]
    no_action_days_below = int(np.sum(metric_no_action < threshold_pct))
    evidence_by_action = {
        item["event_type"]: item for item in context.evidence
    }

    action_results = []
    for action in ACTION_TYPES:
        recovery = min(max(recoveries[action], 0.0), 100.0)
        execution_sl = now_sl + dn_rate / 30.0 * execution_delay_days
        if action == "UWI" and recovery == 0.0:
            branch_values = no_action_values.copy()
            branch_rate = dn_rate
            post_action_sl = execution_sl
            branch_points = [dict(point) for point in no_action]
        else:
            post_action_sl = max(execution_sl * (1.0 - recovery / 100.0), 0.1)
            branch_rate = dn_rate * (0.5 if action == "DD" else 1.0)
            branch_values = no_action_values.copy()
            after = offsets >= execution_delay_days
            branch_values[after] = (
                post_action_sl
                + branch_rate / 30.0 * (offsets[after] - execution_delay_days)
            )
            branch_points = []
            for offset, day, value in zip(
                offsets, absolute_days, branch_values, strict=True
            ):
                if offset == execution_delay_days:
                    branch_points.append(
                        {
                            "day": _finite(day, 2),
                            "speed_loss_pct": _finite(execution_sl),
                            "phase": "before_action",
                        }
                    )
                branch_points.append(
                    {
                        "day": _finite(day, 2),
                        "speed_loss_pct": _finite(value),
                        "phase": "after_action" if offset >= execution_delay_days else "waiting",
                    }
                )
        metric_branch = branch_values[:horizon_days]
        average_sl_difference = float(np.mean(metric_no_action - metric_branch))
        fuel_saving = max(
            average_sl_difference / 100.0
            * fuel_factor
            * float(prepared.full_speed_daily_consumption_mt)
            * (horizon_days * sea_ratio),
            0.0,
        )
        branch_days_below = int(np.sum(metric_branch < threshold_pct))
        action_results.append(
            {
                "event_type": action,
                "label": ACTION_LABELS[action],
                "recovery_pct": recovery,
                "branch_rate_pct_per_month": _finite(branch_rate),
                "post_action_speed_loss_pct": _finite(post_action_sl),
                "days_below_threshold_gain": branch_days_below - no_action_days_below,
                "fuel_saving_mt": _finite(fuel_saving),
                "cost_saving_usd": _finite(fuel_saving * fuel_price_usd_per_mt, 2),
                "co2_avoided_t": _finite(fuel_saving * 3.114),
                "evidence": evidence_by_action.get(action),
                "branch": branch_points,
            }
        )
    action_results.sort(
        key=lambda item: (-float(item["fuel_saving_mt"] or 0.0), ACTION_TYPES.index(item["event_type"]))
    )
    return {
        **response,
        "available": True,
        "reason": None,
        "counts": prepared.counts,
        "baseline": prepared.baseline,
        "latest_day": _finite(latest_day, 2),
        "now_speed_loss_pct": _finite(now_sl),
        "recent_rate_pct_per_month": _finite(prepared.recent_rate_pct_per_month),
        "dn_rate_pct_per_month": _finite(dn_rate),
        "full_speed_daily_consumption_mt": _finite(
            prepared.full_speed_daily_consumption_mt
        ),
        "history": list(prepared.history),
        "past_events": list(context.events_by_ship.get(str(ship_id), ())),
        "no_action": no_action,
        "actions": action_results,
    }
