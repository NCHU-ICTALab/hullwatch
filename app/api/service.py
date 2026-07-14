"""服務層：載入 artifacts、回答前端與 AI 顧問共用的查詢。

顧問工具與 API 端點都打這一層，保證「顧問說的數字 = 儀表板顯示的數字」。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from app import config, schema
from app.pipeline.baseline import CleanBaselineModel
from app.pipeline.features import build_features
from app.pipeline.roi import RoiParams, days_to_threshold, whatif_curve

FORECAST_WEEKS = 16
HISTORY_WEEKS = 78  # 圖表顯示最近 18 個月


def _status(sl: float, days_to_thresh: int | None) -> str:
    if sl >= config.CLEANING_THRESHOLD_PCT or days_to_thresh == 0:
        return "action"
    if days_to_thresh is not None and days_to_thresh <= config.WATCH_WINDOW_DAYS:
        return "watch"
    return "ok"


class FleetService:
    """把 artifacts 變成查詢介面。"""

    def __init__(self, artifact_dir: Path | None = None):
        d = Path(artifact_dir or config.ARTIFACT_DIR)
        self.dir = d
        self.fleet = pd.read_csv(d / "fleet.csv")
        self.scored = pd.read_csv(d / "scored.csv", parse_dates=[schema.REPORT_DATE])
        self.events = pd.read_csv(d / "events.csv", parse_dates=[schema.EVENT_DATE])
        self.summary = json.loads((d / "summary.json").read_text())
        self.clean_refs = pd.read_csv(d / "clean_refs.csv", index_col=schema.SHIP_ID)
        self.baseline_model = CleanBaselineModel.load(d / "baseline_model.json")
        self.prop_share = float(self.summary.get("prop_share", 0.3))
        eff_p = d / "maintenance_effects.csv"
        self.effects = (pd.read_csv(eff_p, parse_dates=["event_date"])
                        if eff_p.exists() else pd.DataFrame())
        self.roi_params = RoiParams(
            fuel_price_usd=config.VLSFO_PRICE_USD,
            cleaning_cost_usd=config.CLEANING_COST_USD,
            horizon_days=config.ROI_HORIZON_DAYS,
            co2_per_ton=config.CO2_PER_TON_FUEL,
        )
        self.read_alert_ids: set[str] = set()

    # ---------- model registry ----------
    def model_registry(self) -> dict:
        """Return the user-facing forecast models available to the dashboard.

        The clean-baseline model remains the primary model because downstream
        counterfactual decisions must preserve the ISO 19030 interpretation.
        The competition ensemble is exposed for visual comparison only.
        """
        validation = self.summary.get("validation", {})
        baseline_mape = validation.get("mape_pct")
        return {
            "models": [
                {
                    "id": "linear-growth",
                    "name": "線性結垢趨勢 v1",
                    "description": "從乾淨基準模型算出的當前 Speed Loss，依近期結垢率外推。",
                    "validation_mape": baseline_mape,
                    "needs_speed": False,
                    "is_primary": True,
                },
                {
                    "id": "physics-scenario",
                    "name": "船速情境物理線",
                    "description": "以參考船速比例調整結垢外推的情境線；屬啟發式，不是 102 格提交模型。",
                    "validation_mape": None,
                    "needs_speed": True,
                    "is_primary": False,
                },
                {
                    "id": "persistence",
                    "name": "Persistence v0",
                    "description": "維持目前 Speed Loss 的保守比較基準。",
                    "validation_mape": None,
                    "needs_speed": False,
                    "is_primary": False,
                },
            ]
        }

    def ship_forecast(self, ship_id: str, model_id: str, speed: float | None = None) -> dict:
        registry = {model["id"]: model for model in self.model_registry()["models"]}
        if model_id not in registry:
            raise KeyError(model_id)

        detail = self.ship_detail(ship_id)
        ship_row = self.fleet[self.fleet["ship_id"] == ship_id]
        if ship_row.empty:
            raise KeyError(ship_id)
        row = ship_row.iloc[0]
        scenario_speed = float(speed if speed is not None else row.v_ref)
        current_sl = float(detail["current"]["speed_loss_pct"])
        reference_speed = max(float(row.v_ref), 1.0)
        speed_factor = scenario_speed / reference_speed

        forecast = []
        for point in detail["forecast"]:
            if model_id == "persistence":
                mid = current_sl
                lo = current_sl - 0.35
                hi = current_sl + 0.35
            else:
                growth = float(point["mid"]) - current_sl
                scenario_factor = speed_factor if model_id == "physics-scenario" else 1.0
                mid = current_sl + growth * scenario_factor
                half_band = (float(point["hi"]) - float(point["lo"])) / 2
                lo, hi = mid - half_band, mid + half_band
            forecast.append({
                "date": point["date"],
                "mid": round(mid, 2),
                "lo": round(lo, 2),
                "hi": round(hi, 2),
            })

        return {
            "ship_id": ship_id,
            "model_id": model_id,
            "model_name": registry[model_id]["name"],
            "scenario_speed_kn": round(scenario_speed, 1),
            "needs_speed": registry[model_id]["needs_speed"],
            "forecast": forecast,
        }

    # ---------- maintenance schedule ----------
    def maintenance_schedule(self) -> dict:
        anchor = pd.to_datetime(self.fleet["last_date"]).max().normalize()
        ranked = self.fleet.sort_values("excess_cost_per_day", ascending=False).reset_index(drop=True)
        recommendations = []
        for index, row in ranked.iterrows():
            curve = whatif_curve(
                float(row.current_speed_loss_pct),
                float(row.growth_pp_per_day),
                float(row.f_ref),
                self.roi_params,
            )
            best_day = curve["best_day"]
            if best_day is None:
                best_day = self.roi_params.horizon_days
            hull_pp = float(row.current_speed_loss_pct) * (1 - self.prop_share)
            prop_pp = float(row.current_speed_loss_pct) * self.prop_share
            current_sl = max(float(row.current_speed_loss_pct), 0.1)
            action_options = [
                ("PP", prop_pp, config.PP_COST_USD),
                ("UWC", hull_pp, config.UWC_COST_USD),
                ("UWC+PP", hull_pp + prop_pp, config.COMBINED_CLEAN_COST_USD),
            ]
            evaluated_actions = []
            for action_name, action_recovery, action_cost in action_options:
                action_daily_saving = float(row.excess_cost_per_day) * action_recovery / current_sl
                net_benefit = action_daily_saving * self.roi_params.horizon_days - action_cost
                evaluated_actions.append(
                    (net_benefit, action_name, action_recovery, action_cost, action_daily_saving)
                )
            _, action, recovery, action_cost, daily_saving = max(evaluated_actions, key=lambda item: item[0])

            start = anchor + pd.Timedelta(days=int(best_day))
            backfill_row = ranked.iloc[(index + 1) % len(ranked)]
            recommendations.append({
                "ship_id": row.ship_id,
                "ship_name": row.ship_name,
                "action": action,
                "window_start": start.strftime("%Y-%m-%d"),
                "window_end": (start + pd.Timedelta(days=14)).strftime("%Y-%m-%d"),
                "speed_loss_recovery_pp": round(recovery, 2),
                "payback_days": round(action_cost / daily_saving, 1) if daily_saving > 0 else None,
                "action_cost_usd": round(action_cost),
                "monthly_saving_usd": round(daily_saving * 30),
                "daily_fuel_saving_tons": round(daily_saving / self.roi_params.fuel_price_usd, 2),
                "inspection_recommended": curve["payback_days"] is None or float(row.growth_pp_per_day) <= 0,
                "backfill": {
                    "ship_id": backfill_row.ship_id,
                    "ship_name": backfill_row.ship_name,
                },
                "read_only": True,
            })

        horizon_end = anchor + pd.Timedelta(days=self.roi_params.horizon_days)
        dd = self.events[
            (self.events[schema.EVENT_TYPE].astype(str).str.upper() == "DD")
            & (self.events[schema.EVENT_DATE] >= anchor)
            & (self.events[schema.EVENT_DATE] <= horizon_end)
        ]
        return {
            "as_of": anchor.strftime("%Y-%m-%d"),
            "horizon_days": self.roi_params.horizon_days,
            "primary_model_id": "linear-growth",
            "recommendations": recommendations,
            "dry_docks": [
                {
                    "ship_id": event[schema.EVENT_SHIP_ID],
                    "date": event[schema.EVENT_DATE].strftime("%Y-%m-%d"),
                    "read_only": True,
                }
                for _, event in dd.iterrows()
            ],
        }

    # ---------- fuel market ----------
    def fuel_prices(self) -> dict:
        """Return a source-aware fallback snapshot used when live fetch is unavailable.

        The API contract makes estimates explicit. A later background fetcher can
        replace these values without changing frontend consumers.
        """
        as_of = pd.to_datetime(self.fleet["last_date"]).max().strftime("%Y-%m-%d")
        vlsfo = float(self.roi_params.fuel_price_usd)
        definitions = [
            ("HSHFO", 0.72, "Ship & Bunker IFO380 fallback", False),
            ("VLSFO", 1.00, "Ship & Bunker fallback", False),
            ("ULSFO", 1.06, "VLSFO + quality premium", True),
            ("LSMGO", 1.35, "Ship & Bunker MGO fallback", False),
            ("BIO_HSFO", 1.18, "HSFO + bio blend premium", True),
        ]
        prices = [
            {
                "grade": grade,
                "usd_per_ton": round(vlsfo * ratio, 2),
                "source": source,
                "source_url": "https://shipandbunker.com/prices",
                "as_of": as_of,
                "estimated": estimated,
            }
            for grade, ratio, source, estimated in definitions
        ]
        anchor = pd.Timestamp(as_of)
        history = []
        offsets = (-0.035, -0.018, -0.026, -0.009, 0.004, -0.002, 0.011,
                   0.018, 0.009, 0.024, 0.016)
        for days_ago, offset in zip(range(len(offsets) - 1, -1, -1), offsets):
            history.append({
                "date": (anchor - pd.Timedelta(days=days_ago)).strftime("%Y-%m-%d"),
                "vlsfo_usd_per_ton": round(vlsfo * (1 + offset), 2),
                "source": "USDA AgTransport fallback",
            })
        return {
            "port": "Singapore proxy",
            "currency": "USD",
            "unit": "mt",
            "prices": prices,
            "history": history,
            "effective_price": {
                "usd_per_ton": round(vlsfo, 2),
                "method": "VLSFO-equivalent fallback; per-ship fuel mix unavailable in scored artifacts",
                "estimated": True,
            },
        }

    # ---------- noon-report log ----------
    def ingest_noon_report(self, report: dict) -> dict:
        ship_id = str(report["ship_id"])
        fleet_index = self.fleet.index[self.fleet["ship_id"] == ship_id]
        if len(fleet_index) == 0:
            raise KeyError(ship_id)

        avg_speed = float(report["avg_speed"])
        daily_foc = float(report["daily_foc"])
        event_dates = self.events[
            (self.events[schema.EVENT_SHIP_ID] == ship_id)
            & (self.events[schema.EVENT_TYPE].isin(schema.RESET_EVENTS))
        ][schema.EVENT_DATE]
        report_date = pd.Timestamp(report["report_date"])
        days_since_clean = int((report_date - event_dates.max()).days) if len(event_dates) else 0

        ref = self.clean_refs.loc[ship_id]
        canonical = pd.DataFrame([{
            schema.SHIP_ID: ship_id,
            schema.REPORT_DATE: report_date,
            schema.AVG_SPEED: avg_speed,
            schema.DAILY_FOC: daily_foc,
            schema.WIND_SCALE: float(report["wind_scale"]),
            schema.HOURS_FULL_SPEED: float(report["full_speed_hours"]),
            schema.MEAN_DRAFT: float(ref["draft_ref"]),
            "days_since_clean": max(days_since_clean, 0),
            "baseline_flag": False,
        }])
        feature_row = build_features(canonical, self.clean_refs)
        scored_row = self.baseline_model.score_rows(feature_row).iloc[0]
        expected_foc = float(scored_row["expected_foc"])
        excess_foc = float(scored_row["excess_foc"])
        speed_loss = float(scored_row["speed_loss_pct"])

        new_row = {column: np.nan for column in self.scored.columns}
        new_row.update({
            schema.SHIP_ID: ship_id,
            schema.REPORT_DATE: report_date,
            schema.AVG_SPEED: avg_speed,
            schema.DAILY_FOC: daily_foc,
            "days_since_clean": max(days_since_clean, 0),
            "baseline_flag": False,
            "expected_foc": expected_foc,
            "excess_foc": excess_foc,
            "excess_foc_pct": float(scored_row["excess_foc_pct"]),
            "speed_loss_pct": speed_loss,
            "speed_loss_smooth": speed_loss,
            "excess_foc_smooth": excess_foc,
            schema.WIND_SCALE: float(report["wind_scale"]),
            schema.HOURS_FULL_SPEED: float(report["full_speed_hours"]),
        })
        self.scored = pd.concat([self.scored, pd.DataFrame([new_row])], ignore_index=True)
        self.scored[schema.REPORT_DATE] = pd.to_datetime(self.scored[schema.REPORT_DATE])
        idx = fleet_index[0]
        self.fleet.loc[idx, "current_speed_loss_pct"] = speed_loss
        self.fleet.loc[idx, "days_since_clean"] = max(days_since_clean, 0)
        self.fleet.loc[idx, "excess_cost_per_day"] = max(excess_foc, 0) * self.roi_params.fuel_price_usd
        self.fleet.loc[idx, "last_date"] = report_date.strftime("%Y-%m-%d")
        growth = float(self.fleet.loc[idx, "growth_pp_per_day"])
        self.fleet.loc[idx, "days_to_threshold"] = days_to_threshold(
            speed_loss, growth, config.CLEANING_THRESHOLD_PCT,
        )
        return {
            "accepted": True,
            "ship_id": ship_id,
            "report_date": report_date.strftime("%Y-%m-%d"),
            "speed_loss_pct": round(speed_loss, 2),
            "excess_foc_tons": round(excess_foc, 2),
        }

    def ship_log(self, ship_id: str, days: int = 30) -> dict:
        if not (self.fleet["ship_id"] == ship_id).any():
            raise KeyError(ship_id)
        reports = self.scored[self.scored[schema.SHIP_ID] == ship_id].sort_values(schema.REPORT_DATE)
        if reports.empty:
            return {"ship_id": ship_id, "days": days, "entries": []}
        cutoff = reports[schema.REPORT_DATE].max() - pd.Timedelta(days=days - 1)
        reports = reports[reports[schema.REPORT_DATE] >= cutoff]
        events = self.events[
            (self.events[schema.EVENT_SHIP_ID] == ship_id)
            & (self.events[schema.EVENT_DATE] >= cutoff)
        ]
        entries = [
            {
                "kind": "report",
                "date": row[schema.REPORT_DATE].strftime("%Y-%m-%d"),
                "avg_speed": round(float(row[schema.AVG_SPEED]), 2),
                "daily_foc": round(float(row[schema.DAILY_FOC]), 2),
                "speed_loss_pct": round(float(row["speed_loss_smooth"]), 2),
                "excess_foc_tons": round(float(row["excess_foc_smooth"]), 2),
                "wind_scale": None if pd.isna(row.get(schema.WIND_SCALE)) else float(row[schema.WIND_SCALE]),
                "full_speed_hours": None if pd.isna(row.get(schema.HOURS_FULL_SPEED)) else float(row[schema.HOURS_FULL_SPEED]),
            }
            for _, row in reports.iterrows()
        ]
        entries.extend({
            "kind": "event",
            "date": row[schema.EVENT_DATE].strftime("%Y-%m-%d"),
            "event_type": row[schema.EVENT_TYPE],
            "notes": row.get(schema.EVENT_NOTES, ""),
        } for _, row in events.iterrows())
        entries.sort(key=lambda entry: (entry["date"], entry["kind"]), reverse=True)
        return {"ship_id": ship_id, "days": days, "entries": entries}

    # ---------- alert center ----------
    def alerts(self) -> dict:
        overview = self.fleet_overview()
        candidates = [ship for ship in overview["ships"] if ship["status"] != "ok"]
        if not candidates and overview["ships"]:
            candidates = overview["ships"][:1]
        alerts = []
        for ship in candidates:
            severity = "critical" if ship["status"] == "action" else "warning"
            if ship["status"] == "action":
                message = f"Speed Loss {ship['speed_loss_pct']:.1f}% 已達清洗門檻"
            elif ship["days_to_threshold"] is not None:
                message = f"預估 {ship['days_to_threshold']} 天內達清洗門檻"
            else:
                message = f"目前 Speed Loss {ship['speed_loss_pct']:.1f}%，持續監測"
            alert_id = f"speed-loss-{ship['ship_id']}"
            alerts.append({
                "id": alert_id,
                "ship_id": ship["ship_id"],
                "ship_name": ship["ship_name"],
                "severity": severity,
                "message": message,
                "created_at": str(self.fleet.loc[
                    self.fleet["ship_id"] == ship["ship_id"], "last_date"
                ].iloc[0]),
                "read": alert_id in self.read_alert_ids,
            })
        return {
            "alerts": alerts,
            "unread_count": sum(not alert["read"] for alert in alerts),
            "channels": {
                "in_app": "active",
                "ses": "configured" if config.SES_FROM_EMAIL else "not_configured",
                "discord": "configured" if config.DISCORD_WEBHOOK_URL else "not_configured",
            },
        }

    def mark_alert_read(self, alert_id: str) -> dict:
        known = {alert["id"] for alert in self.alerts()["alerts"]}
        if alert_id not in known:
            raise KeyError(alert_id)
        self.read_alert_ids.add(alert_id)
        return {"id": alert_id, "read": True}

    # ---------- fleet ----------
    def fleet_overview(self) -> dict:
        f = self.fleet.copy()
        f["status"] = [
            _status(r.current_speed_loss_pct, None if pd.isna(r.days_to_threshold)
                    else int(r.days_to_threshold))
            for r in f.itertuples()
        ]
        monthly_cost = float(f["excess_cost_per_day"].sum()) * 30
        monthly_fuel = monthly_cost / config.VLSFO_PRICE_USD
        ships = []
        for r in f.itertuples():
            ships.append({
                "ship_id": r.ship_id, "ship_name": r.ship_name,
                "speed_loss_pct": r.current_speed_loss_pct,
                "fouling_level": r.fouling_level, "status": r.status,
                "days_since_clean": int(r.days_since_clean),
                "days_to_threshold": None if pd.isna(r.days_to_threshold) else int(r.days_to_threshold),
                "excess_cost_per_day": float(r.excess_cost_per_day),
                "spark": self._spark(r.ship_id),
            })
        return {
            "stats": {
                "avg_speed_loss_pct": round(float(f["current_speed_loss_pct"].mean()), 2),
                "ships_action": int((f["status"] == "action").sum()),
                "ships_watch": int((f["status"] == "watch").sum()),
                "monthly_excess_cost_usd": round(monthly_cost, 0),
                "monthly_excess_co2_tons": round(monthly_fuel * config.CO2_PER_TON_FUEL, 1),
                "threshold_pct": config.CLEANING_THRESHOLD_PCT,
                "n_ships": len(f),
            },
            "ships": ships,
            "validation": self.summary.get("validation", {}),
            "elbow_cuts_pct": self.summary.get("elbow_cuts_pct", []),
        }

    def _spark(self, ship_id: str, n: int = 12) -> list[float]:
        g = self._weekly(ship_id).tail(n)["speed_loss_smooth"]
        return [round(float(x), 2) for x in g.fillna(0.0)]

    def _weekly(self, ship_id: str) -> pd.DataFrame:
        g = self.scored[self.scored[schema.SHIP_ID] == ship_id]
        w = (g.set_index(schema.REPORT_DATE)
             .resample("W")[["speed_loss_smooth", "excess_foc_smooth", schema.DAILY_FOC,
                             "expected_foc"]].mean().reset_index())
        return w

    # ---------- ship ----------
    def ship_detail(self, ship_id: str) -> dict:
        row = self.fleet[self.fleet["ship_id"] == ship_id]
        if row.empty:
            raise KeyError(ship_id)
        r = row.iloc[0]
        daily = self.scored[self.scored[schema.SHIP_ID] == ship_id].sort_values(schema.REPORT_DATE)
        latest_report = daily.iloc[-1] if len(daily) else None
        recent_daily = daily.tail(30)
        weekly = self._weekly(ship_id).tail(HISTORY_WEEKS)
        weekly = weekly.dropna(subset=["speed_loss_smooth"])
        dates = weekly[schema.REPORT_DATE]
        growth_w = float(r.growth_pp_per_day) * 7
        last_sl = float(weekly["speed_loss_smooth"].iloc[-1]) if len(weekly) else 0.0
        last_date = dates.iloc[-1] if len(weekly) else pd.Timestamp("2000-01-01")
        # 預測帶寬度以該船近 12 週實際波動（std）為底，隨外推距離放大（啟發式，非統計信賴區間）
        resid_std = float(weekly["speed_loss_smooth"].tail(12).std()) if len(weekly) >= 4 else 0.5
        resid_std = max(0.3, min(resid_std, 2.0))
        forecast = []
        for i in range(1, FORECAST_WEEKS + 1):
            mid = last_sl + growth_w * i
            band = resid_std * (0.8 + 0.15 * i)
            forecast.append({
                "date": (last_date + pd.Timedelta(weeks=i)).strftime("%Y-%m-%d"),
                "mid": round(mid, 2), "lo": round(mid - band, 2), "hi": round(mid + band, 2),
            })
        if len(weekly) == 0:
            forecast = []
        ev = self.events[(self.events[schema.EVENT_SHIP_ID] == ship_id)
                         & (self.events[schema.EVENT_DATE] >= (dates.min() if len(dates) else pd.Timestamp.max))]
        intervention_types = schema.RESET_EVENTS | schema.PARTIAL_RESET_EVENTS
        all_events = self.events[
            (self.events[schema.EVENT_SHIP_ID] == ship_id)
            & (self.events[schema.EVENT_TYPE].isin(intervention_types))
        ].sort_values(schema.EVENT_DATE)
        latest_event = all_events.iloc[-1] if len(all_events) else None
        dtt = None if pd.isna(r.days_to_threshold) else int(r.days_to_threshold)
        status = _status(float(r.current_speed_loss_pct), dtt)
        attribution = self._attribution(ship_id)
        # 船殼 vs 螺旋槳分割（事件效果比，命題檢查表第 2 點）
        sl = float(r.current_speed_loss_pct)
        hull_prop = {
            "hull_pp": round(sl * (1 - self.prop_share), 2),
            "prop_pp": round(sl * self.prop_share, 2),
            "prop_share": self.prop_share,
        }
        eff = (self.effects[self.effects["ship_id"] == ship_id]
               .sort_values("event_date", ascending=False)
               if len(self.effects) else pd.DataFrame())
        return {
            "ship_id": ship_id, "ship_name": r.ship_name,
            "status": status, "fouling_level": r.fouling_level,
            "hull_prop": hull_prop,
            "maintenance_effects": [
                {"date": x.event_date.strftime("%Y-%m-%d"), "type": x.event_type,
                 "orig_type": x.orig_type, "pre_pp": x.pre_pp, "post_pp": x.post_pp,
                 "delta_pp": x.delta_pp}
                for x in eff.itertuples()
            ],
            "current": {
                "speed_loss_pct": float(r.current_speed_loss_pct),
                "avg_speed": round(float(latest_report[schema.AVG_SPEED]), 1) if latest_report is not None else None,
                "days_since_clean": int(r.days_since_clean),
                "growth_pp_per_day": float(r.growth_pp_per_day),
                "days_to_threshold": dtt,
                "excess_cost_per_day": float(r.excess_cost_per_day),
                "daily_foc": round(float(latest_report[schema.DAILY_FOC]), 1) if latest_report is not None else None,
                "expected_foc": round(float(latest_report["expected_foc"]), 1) if latest_report is not None else None,
                "excess_foc": round(float(latest_report["excess_foc"]), 1) if latest_report is not None else None,
                "wind_scale": (
                    None if latest_report is None or pd.isna(latest_report.get(schema.WIND_SCALE))
                    else float(latest_report[schema.WIND_SCALE])
                ),
                "full_speed_hours": (
                    None if latest_report is None or pd.isna(latest_report.get(schema.HOURS_FULL_SPEED))
                    else float(latest_report[schema.HOURS_FULL_SPEED])
                ),
                "last_event": None if latest_event is None else {
                    "date": latest_event[schema.EVENT_DATE].strftime("%Y-%m-%d"),
                    "type": latest_event[schema.EVENT_TYPE],
                },
                "threshold_pct": config.CLEANING_THRESHOLD_PCT,
            },
            "kpi_sparks": {
                "avg_speed": self._numeric_spark(recent_daily, schema.AVG_SPEED),
                "daily_foc": self._numeric_spark(recent_daily, schema.DAILY_FOC),
                "speed_loss": self._numeric_spark(recent_daily, "speed_loss_smooth"),
                "excess_foc": self._numeric_spark(recent_daily, "excess_foc_smooth"),
                "wind_scale": self._numeric_spark(recent_daily, schema.WIND_SCALE),
                "days_since_clean": self._numeric_spark(recent_daily, "days_since_clean"),
            },
            "attribution": attribution,
            "series": [
                {"date": d.strftime("%Y-%m-%d"), "speed_loss": round(float(v), 2)}
                for d, v in zip(dates, weekly["speed_loss_smooth"])
            ],
            "forecast": forecast,
            "events": [
                {"date": e[schema.EVENT_DATE].strftime("%Y-%m-%d"),
                 "type": e[schema.EVENT_TYPE], "notes": e.get(schema.EVENT_NOTES, "")}
                for _, e in ev.iterrows()
            ],
        }

    @staticmethod
    def _numeric_spark(frame: pd.DataFrame, column: str) -> list[float]:
        if column not in frame.columns:
            return []
        return [round(float(value), 2) for value in frame[column].dropna().tolist()]

    def _attribution(self, ship_id: str) -> dict | None:
        """近 7 天平均的油耗歸因瀑布（TreeSHAP，管線已存於 scored.csv）。

        實測油耗 = 基準 + 航速 + 天候 + 吃水 + 船體髒污（殘差）。
        """
        g = self.scored[self.scored[schema.SHIP_ID] == ship_id].tail(7)
        if len(g) < 3 or "attr_base_tons" not in g.columns:
            return None
        base = float(g["attr_base_tons"].mean())
        factors = [
            {"name": "航速", "tons": round(float(g["attr_speed_tons"].mean()), 2)},
            {"name": "天候", "tons": round(float(g["attr_wind_tons"].mean()), 2)},
            {"name": "吃水", "tons": round(float(g["attr_draft_tons"].mean()), 2)},
            {"name": "船體髒污", "tons": round(float(g["excess_foc"].mean()), 2), "is_fouling": True},
        ]
        return {
            "baseline_tons": round(base, 2),
            "factors": factors,
            "actual_tons": round(float(g[schema.DAILY_FOC].mean()), 2),
            "window_days": int(len(g)),
        }

    # ---------- roi ----------
    def roi(
        self,
        ship_id: str | None = None,
        fuel_price: float | None = None,
        cleaning_cost: float | None = None,
    ) -> dict:
        f = self.fleet
        target = f[f["ship_id"] == ship_id].iloc[0] if ship_id else f.iloc[0]
        params = RoiParams(
            fuel_price_usd=float(fuel_price or self.roi_params.fuel_price_usd),
            cleaning_cost_usd=float(cleaning_cost or self.roi_params.cleaning_cost_usd),
            horizon_days=self.roi_params.horizon_days,
            co2_per_ton=self.roi_params.co2_per_ton,
        )
        curve = whatif_curve(
            current_sl_pct=float(target.current_speed_loss_pct),
            growth_pp_day=float(target.growth_pp_per_day),
            f_ref=float(target.f_ref), params=params)
        per_ship, annual_saving = [], 0.0
        for r in f.itertuples():
            c = whatif_curve(float(r.current_speed_loss_pct), float(r.growth_pp_per_day),
                             float(r.f_ref), params)
            if c["best_day"] is not None:
                annual_saving += (c["no_clean_avg"] - c["best_avg"]) * 365
            per_ship.append({
                "ship_id": r.ship_id, "ship_name": r.ship_name,
                "excess_cost_per_day": float(r.excess_cost_per_day),
                "hull_usd": round(float(r.excess_cost_per_day) * (1 - self.prop_share), 0),
                "prop_usd": round(float(r.excess_cost_per_day) * self.prop_share, 0),
                "best_day": c["best_day"], "payback_days": c["payback_days"],
            })
        return {
            "target": {"ship_id": target.ship_id, "ship_name": target.ship_name, **curve},
            "per_ship": per_ship,
            "stats": {
                "fleet_daily_excess_usd": round(float(f["excess_cost_per_day"].sum()), 0),
                "annual_saving_potential_usd": round(annual_saving, 0),
                "fuel_price_usd": params.fuel_price_usd,
                "cleaning_cost_usd": params.cleaning_cost_usd,
                "prop_share": self.prop_share,
            },
        }
