"""服務層：載入 artifacts、回答前端與 AI 顧問共用的查詢。

顧問工具與 API 端點都打這一層，保證「顧問說的數字 = 儀表板顯示的數字」。
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

import numpy as np
import pandas as pd
import xgboost as xgb

from app import config, schema
from app.api.fuel_market import FuelMarketService
from app.api.model_packages import ModelPackageStore
from app.api.notifications import NotificationSubscriptionStore
from app.pipeline.baseline import CleanBaselineModel
from app.pipeline.features import build_features
from app.pipeline.ingest_yangming import EPOCH as DAY_EPOCH
from app.pipeline.maintenance_benefit import (
    MaintenanceBenefitContext,
    prepare_maintenance_benefit,
    simulate_maintenance_benefit,
)
from app.pipeline.roi import POST_CLEAN_SL_PCT, RoiParams, days_to_threshold, whatif_curve
from app.pipeline.speed_loss_prediction import (
    REQUIRED_SOURCE_COLUMNS,
    LoadCondition,
    predict_speed_loss,
)

FORECAST_WEEKS = 16
HISTORY_WEEKS = 78  # 圖表顯示最近 18 個月


def classify_operational_status(sl: float, days_to_thresh: int | None) -> str:
    """依固定 Speed Loss 門檻與達清洗門檻預測判斷營運狀態。"""
    if sl >= config.CLEANING_THRESHOLD_PCT or days_to_thresh == 0:
        return "action"
    if sl >= config.WATCH_THRESHOLD_PCT:
        return "watch"
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
        self.fuel_market = FuelMarketService(d / "fuel-market-cache.json")
        self.model_packages = ModelPackageStore(d / "model-packages")
        self.notification_subscriptions = NotificationSubscriptionStore(
            d / "notification-subscriptions.json"
        )
        self.speed_loss_source_path = d.parent / "raw" / "noon_reports.csv"
        self.maintenance_source_path = d.parent / "raw" / "events.csv"
        self._maintenance_source_lock = Lock()
        self._maintenance_source_signature: tuple[
            tuple[int, int] | None, tuple[int, int] | None
        ] | None = None
        self._load_maintenance_sources()

    @staticmethod
    def _file_signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _current_maintenance_source_signature(
        self,
    ) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        return (
            self._file_signature(self.speed_loss_source_path),
            self._file_signature(self.maintenance_source_path),
        )

    def _load_maintenance_sources(self) -> None:
        speed_loss_source = (
            pd.read_csv(self.speed_loss_source_path)
            if self.speed_loss_source_path.exists()
            else pd.DataFrame()
        )
        maintenance_source = (
            pd.read_csv(self.maintenance_source_path)
            if self.maintenance_source_path.exists()
            else pd.DataFrame()
        )
        missing_columns = sorted(
            REQUIRED_SOURCE_COLUMNS - set(speed_loss_source.columns)
        )
        context = prepare_maintenance_benefit(
            speed_loss_source, maintenance_source
        )

        self.speed_loss_source = speed_loss_source
        self.speed_loss_source_missing_columns = missing_columns
        self.speed_loss_source_ready = bool(
            len(speed_loss_source) and not missing_columns
        )
        self.maintenance_source = maintenance_source
        self.maintenance_benefit_context: MaintenanceBenefitContext = context
        self.maintenance_benefit_ready = context.available
        self._maintenance_source_signature = (
            self._current_maintenance_source_signature()
        )

    def refresh_maintenance_sources_if_changed(self) -> bool:
        """Reload raw source/context when a pipeline run replaces either CSV."""
        signature = self._current_maintenance_source_signature()
        if signature == self._maintenance_source_signature:
            return False
        with self._maintenance_source_lock:
            signature = self._current_maintenance_source_signature()
            if signature == self._maintenance_source_signature:
                return False
            self._load_maintenance_sources()
        return True

    # ---------- model registry ----------
    def model_registry(self) -> dict:
        """Return the user-facing forecast models available to the dashboard.

        The clean-baseline model remains the primary model because downstream
        counterfactual decisions must preserve the ISO 19030 interpretation.
        The competition ensemble is exposed for visual comparison only.
        """
        validation = self.summary.get("validation", {})
        baseline_mape = validation.get("mape_pct")
        builtins = [
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
        active_id = self.model_packages.active_id()
        for model in builtins:
            model["is_primary"] = model["id"] == active_id
            model["status"] = "active" if model["is_primary"] else "available"
            model["version"] = "1.0.0"
            model["model_format"] = "builtin"
        uploaded = self.model_packages.list()
        for model in uploaded:
            model["is_primary"] = model["id"] == active_id
        return {
            "models": builtins + uploaded,
            "active_model_id": active_id,
            "supported_formats": ["xgboost-json"],
            "planned_formats": ["onnx"],
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
            elif model_id in {"linear-growth", "physics-scenario"}:
                growth = float(point["mid"]) - current_sl
                scenario_factor = speed_factor if model_id == "physics-scenario" else 1.0
                mid = current_sl + growth * scenario_factor
                half_band = (float(point["hi"]) - float(point["lo"])) / 2
                lo, hi = mid - half_band, mid + half_band
            else:
                booster, package = self.model_packages.load_booster(model_id)
                features = package["features"]
                week = len(forecast) + 1
                values = {
                    "week": week,
                    "current_speed_loss_pct": current_sl,
                    "growth_pp_per_day": float(row.growth_pp_per_day),
                    "scenario_speed_kn": scenario_speed,
                    "reference_speed_kn": reference_speed,
                }
                matrix = xgb.DMatrix([[values[name] for name in features]], feature_names=features)
                mid = float(booster.predict(matrix)[0])
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

    def speed_loss_prediction(
        self,
        ship_id: str,
        forecast_days: int = 180,
        threshold_pct: float = 8.0,
        max_wind_scale: float = 4.0,
        load_condition: LoadCondition = "all",
    ) -> dict:
        """Recompute the strict STW/power OLS prediction from normalized raw."""
        self.refresh_maintenance_sources_if_changed()
        if self.fleet[self.fleet[schema.SHIP_ID] == ship_id].empty:
            raise KeyError(ship_id)
        return predict_speed_loss(
            self.speed_loss_source,
            ship_id,
            forecast_days=forecast_days,
            threshold_pct=threshold_pct,
            max_wind_scale=max_wind_scale,
            load_condition=load_condition,
        )

    def fleet_speed_loss_windows(
        self,
        forecast_days: int = 180,
        threshold_pct: float = 8.0,
        max_wind_scale: float = 4.0,
    ) -> dict:
        """全船隊 strict 預測門檻窗口摘要：只回傳每船每載況的交叉窗口與現況純量，
        不含逐點 history/trend/forecast 序列（總覽時間軸用，避免 15 船全序列 payload）。"""
        self.refresh_maintenance_sources_if_changed()
        ships = []
        day0_note = None
        for _, row in self.fleet.sort_values(schema.SHIP_ID).iterrows():
            ship_id = str(row[schema.SHIP_ID])
            result = predict_speed_loss(
                self.speed_loss_source,
                ship_id,
                forecast_days=forecast_days,
                threshold_pct=threshold_pct,
                max_wind_scale=max_wind_scale,
                load_condition="all",
            )
            day0_note = result.get("day0_note") or day0_note
            ships.append({
                "ship_id": ship_id,
                "ship_name": str(row["ship_name"]),
                "available": result["available"],
                "reason": result["reason"],
                "groups": [
                    {
                        "load_condition": group["load_condition"],
                        "load_label": group["load_label"],
                        "available": group["available"],
                        "reason": group["reason"],
                        "current_speed_loss_pct": group["current_speed_loss_pct"],
                        "deterioration_rate_pct_per_month": group["deterioration_rate_pct_per_month"],
                        "latest_day": group["latest_day"],
                        "threshold_crossing": group["threshold_crossing"],
                    }
                    for group in result["groups"]
                ],
            })
        return {
            "method": "per-ship-load-stw-horsepower-ols",
            "day0_note": day0_note,
            "parameters": {
                "forecast_days": forecast_days,
                "threshold_pct": threshold_pct,
                "max_wind_scale": max_wind_scale,
            },
            "ships": ships,
        }

    def maintenance_benefit(
        self,
        ship_id: str,
        *,
        execution_delay_days: int,
        horizon_days: int,
        threshold_pct: float,
        fuel_factor: float,
        fuel_price_usd_per_mt: float,
        sea_ratio: float,
        recovery_pct: dict[str, float],
    ) -> dict:
        """Return evidence plus no-action and six physical-prior branches."""
        self.refresh_maintenance_sources_if_changed()
        if self.fleet[self.fleet[schema.SHIP_ID] == ship_id].empty:
            raise KeyError(ship_id)
        return simulate_maintenance_benefit(
            self.maintenance_benefit_context,
            ship_id,
            execution_delay_days=execution_delay_days,
            horizon_days=horizon_days,
            threshold_pct=threshold_pct,
            fuel_factor=fuel_factor,
            fuel_price_usd_per_mt=fuel_price_usd_per_mt,
            sea_ratio=sea_ratio,
            recovery_pct=recovery_pct,
        )

    def register_model_package(self, manifest: str, artifact: bytes) -> dict:
        record = self.model_packages.register(manifest, artifact)
        booster, package = self.model_packages.load_booster(record["id"])
        features = package["features"]
        rows = []
        targets = []
        current_predictions = []
        for ship_id, frame in self.scored.groupby(schema.SHIP_ID):
            frame = frame.sort_values(schema.REPORT_DATE).dropna(subset=["speed_loss_smooth", schema.AVG_SPEED])
            if len(frame) < 8:
                continue
            fleet_row = self.fleet[self.fleet[schema.SHIP_ID] == ship_id]
            if fleet_row.empty:
                continue
            fleet_row = fleet_row.iloc[0]
            for index in range(len(frame) - 7):
                current = frame.iloc[index]
                max_week = min(FORECAST_WEEKS, (len(frame) - index - 1) // 7)
                for week in range(1, max_week + 1):
                    target = frame.iloc[index + week * 7]
                    values = {
                        "week": float(week),
                        "current_speed_loss_pct": float(current["speed_loss_smooth"]),
                        "growth_pp_per_day": float(fleet_row.growth_pp_per_day),
                        "scenario_speed_kn": float(current[schema.AVG_SPEED]),
                        "reference_speed_kn": float(fleet_row.v_ref),
                    }
                    rows.append([values[name] for name in features])
                    targets.append(float(target["speed_loss_smooth"]))
                    current_predictions.append(
                        values["current_speed_loss_pct"] + values["growth_pp_per_day"] * 7 * week
                    )
        if not rows:
            raise ValueError("歷史資料不足，無法建立共同驗證集")
        candidate = booster.predict(xgb.DMatrix(rows, feature_names=features))
        candidate_mae = float(np.mean(np.abs(candidate - np.asarray(targets))))
        current_mae = float(np.mean(np.abs(np.asarray(current_predictions) - np.asarray(targets))))
        finite = bool(np.isfinite(candidate).all())
        in_range = bool(((candidate >= 0) & (candidate <= 100)).all())
        passed = finite and in_range and candidate_mae <= current_mae * 1.05
        validation = {
            "rows": len(rows),
            "candidate_mae": round(candidate_mae, 4),
            "current_model_mae": round(current_mae, 4),
            "max_allowed_mae": round(current_mae * 1.05, 4),
            "finite": finite,
            "in_range": in_range,
            "passed": passed,
        }
        return self.model_packages.update_validation(record["id"], validation)

    def activate_model(self, model_id: str) -> dict:
        builtins = {
            model["id"]: model
            for model in self.model_registry()["models"]
            if model.get("model_format") == "builtin"
        }
        if model_id in builtins:
            self.model_packages.activate_builtin(model_id)
            return next(
                model for model in self.model_registry()["models"]
                if model["id"] == model_id
            )
        return self.model_packages.activate(model_id)

    def restore_builtin_model(self) -> dict:
        return self.model_packages.restore()

    def _decision_growth(self, row) -> float:
        """Convert the active trend model's first week into a daily decision slope."""
        active_id = self.model_packages.active_id()
        if active_id == "linear-growth":
            return float(row.growth_pp_per_day)
        forecast = self.ship_forecast(str(row.ship_id), active_id, float(row.v_ref))["forecast"]
        if not forecast:
            return float(row.growth_pp_per_day)
        return (float(forecast[0]["mid"]) - float(row.current_speed_loss_pct)) / 7

    # ---------- maintenance schedule ----------
    def maintenance_schedule(self, past_days: int = 90, future_days: int = 180) -> dict:
        anchor = pd.to_datetime(self.fleet["last_date"]).max().normalize()
        ranked = self.fleet.sort_values("excess_cost_per_day", ascending=False).reset_index(drop=True)
        recommendations = []
        for index, row in ranked.iterrows():
            decision_growth = self._decision_growth(row)
            curve = whatif_curve(
                float(row.current_speed_loss_pct),
                decision_growth,
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
                bounded_recovery = min(max(float(action_recovery), 0.0), current_sl)
                action_daily_saving = (
                    float(row.excess_cost_per_day) * bounded_recovery / current_sl
                )
                evaluated_actions.append({
                    "action": action_name,
                    "speed_loss_recovery_pp": round(bounded_recovery, 2),
                    "post_clean_speed_loss_pct": round(
                        max(float(row.current_speed_loss_pct) - bounded_recovery, 0.0), 2
                    ),
                    "action_cost_usd": round(action_cost),
                    "payback_days": (
                        round(action_cost / action_daily_saving, 1)
                        if action_daily_saving > 0 else None
                    ),
                    "daily_fuel_saving_tons": round(
                        action_daily_saving / self.roi_params.fuel_price_usd, 2
                    ),
                    "monthly_saving_usd": round(action_daily_saving * 30),
                    "net_benefit_usd": round(
                        action_daily_saving * self.roi_params.horizon_days - action_cost
                    ),
                })
            selected_action = max(
                evaluated_actions, key=lambda item: item["net_benefit_usd"]
            )
            action = selected_action["action"]
            recovery = selected_action["speed_loss_recovery_pp"]
            action_cost = selected_action["action_cost_usd"]
            daily_saving = (
                selected_action["daily_fuel_saving_tons"]
                * self.roi_params.fuel_price_usd
            )

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
                "action_options": [
                    {key: value for key, value in option.items() if key != "net_benefit_usd"}
                    for option in evaluated_actions
                ],
                "inspection_recommended": curve["payback_days"] is None or decision_growth <= 0,
                "backfill": {
                    "ship_id": backfill_row.ship_id,
                    "ship_name": backfill_row.ship_name,
                },
                "read_only": True,
                "speed_loss_pct": round(float(row.current_speed_loss_pct), 2),
                "excess_cost_per_day": round(float(row.excess_cost_per_day), 2),
                "risk_rank": 0 if classify_operational_status(float(row.current_speed_loss_pct), None if pd.isna(row.days_to_threshold) else int(row.days_to_threshold)) == "action" else 1,
            })

        timeline_start = anchor - pd.Timedelta(days=past_days)
        horizon_end = anchor + pd.Timedelta(days=future_days)
        dd = self.events[
            (self.events[schema.EVENT_TYPE].astype(str).str.upper() == "DD")
            & (self.events[schema.EVENT_DATE] >= anchor)
            & (self.events[schema.EVENT_DATE] <= horizon_end)
        ]
        return {
            "as_of": anchor.strftime("%Y-%m-%d"),
            "horizon_days": future_days,
            "past_days": past_days,
            "future_days": future_days,
            "timeline_start": timeline_start.strftime("%Y-%m-%d"),
            "timeline_end": horizon_end.strftime("%Y-%m-%d"),
            "primary_model_id": self.model_packages.active_id(),
            "recommendations": recommendations,
            "dry_docks": [
                {
                    "ship_id": event[schema.EVENT_SHIP_ID],
                    "date": event[schema.EVENT_DATE].strftime("%Y-%m-%d"),
                    "read_only": True,
                }
                for _, event in dd.iterrows()
            ],
            "maintenance_events": [
                {
                    "ship_id": event[schema.EVENT_SHIP_ID],
                    "date": event[schema.EVENT_DATE].strftime("%Y-%m-%d"),
                    "type": event[schema.EVENT_TYPE],
                    "notes": event.get(schema.EVENT_NOTES, ""),
                }
                for _, event in self.events[
                    (self.events[schema.EVENT_DATE] >= timeline_start)
                    & (self.events[schema.EVENT_DATE] <= horizon_end)
                ].iterrows()
            ],
        }

    # ---------- fuel market ----------
    def fuel_prices(self) -> dict:
        return self.fuel_market.snapshot()

    # ---------- noon-report log ----------
    def _notify_noon_report(self, updates: list[dict]) -> dict:
        """規則 1：上傳新正午日報才觸發訂閱通知；失敗不可拖垮上傳本身。"""
        try:
            return self.notification_subscriptions.notify_noon_report_updates(
                updates, self.fleet_overview()["ships"], config.WATCH_THRESHOLD_PCT)
        except Exception as exc:  # noqa: BLE001
            return {"updates": len(updates), "notified": 0, "error": str(exc)[:120]}

    def ingest_noon_report(self, report: dict, notify: bool = True) -> dict:
        ship_id = str(report["ship_id"])
        fleet_index = self.fleet.index[self.fleet["ship_id"] == ship_id]
        if len(fleet_index) == 0:
            raise KeyError(ship_id)

        avg_speed = float(report["avg_speed"])
        daily_foc = float(report["daily_foc"])
        report_date = pd.Timestamp(report["report_date"])
        event_dates = self.events[
            (self.events[schema.EVENT_SHIP_ID] == ship_id)
            & (self.events[schema.EVENT_TYPE].isin(schema.RESET_EVENTS))
            & (self.events[schema.EVENT_DATE] <= report_date)
        ][schema.EVENT_DATE]
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
        duplicate = (
            (self.scored[schema.SHIP_ID] == ship_id)
            & (pd.to_datetime(self.scored[schema.REPORT_DATE]).dt.normalize() == report_date.normalize())
        )
        updated = bool(duplicate.any())
        if updated:
            self.scored = self.scored.loc[~duplicate].copy()
        self.scored = pd.concat([self.scored, pd.DataFrame([new_row])], ignore_index=True)
        self.scored[schema.REPORT_DATE] = pd.to_datetime(self.scored[schema.REPORT_DATE])
        idx = fleet_index[0]
        previous_last = pd.Timestamp(self.fleet.loc[idx, "last_date"])
        if report_date >= previous_last:
            self.fleet.loc[idx, "current_speed_loss_pct"] = speed_loss
            self.fleet.loc[idx, "days_since_clean"] = max(days_since_clean, 0)
            self.fleet.loc[idx, "excess_cost_per_day"] = max(excess_foc, 0) * self.roi_params.fuel_price_usd
            self.fleet.loc[idx, "last_date"] = report_date.strftime("%Y-%m-%d")
            growth = float(self.fleet.loc[idx, "growth_pp_per_day"])
            self.fleet.loc[idx, "days_to_threshold"] = days_to_threshold(
                speed_loss, growth, config.CLEANING_THRESHOLD_PCT,
            )
        response = {
            "accepted": True,
            "updated": updated,
            "ship_id": ship_id,
            "report_date": report_date.strftime("%Y-%m-%d"),
            "speed_loss_pct": round(speed_loss, 2),
            "excess_foc_tons": round(excess_foc, 2),
        }
        if notify:
            ship_name = str(self.fleet.loc[idx, "ship_name"])
            response["notifications"] = self._notify_noon_report([{
                "ship_id": ship_id, "ship_name": ship_name,
                "report_date": response["report_date"],
                "speed_loss_pct": response["speed_loss_pct"],
            }])
        return response

    # 上傳 CSV 的選配完整欄位：整列齊備才併入 strict 原始檔（速損預測／決策窗口／養護效益）
    STRICT_UPLOAD_COLUMNS = ["stw", "horse_power", "displacement",
                             "me_consumption", "mid_draft", "hours_total"]

    def _parse_strict_upload_columns(self, frame: pd.DataFrame, row: pd.Series) -> dict | None:
        """選配完整欄位：整列齊備回 dict、整列留空回 None、填一半就報錯（避免半套資料進引擎）。"""
        present = [c for c in self.STRICT_UPLOAD_COLUMNS if c in frame.columns]
        if not present:
            return None
        values = {c: row[c] for c in present}
        filled = {c: v for c, v in values.items()
                  if pd.notna(v) and str(v).strip() != ""}
        if not filled:
            return None
        if len(present) < len(self.STRICT_UPLOAD_COLUMNS) or len(filled) < len(self.STRICT_UPLOAD_COLUMNS):
            raise ValueError(
                "完整欄位（stw／horse_power／displacement／me_consumption／mid_draft／hours_total）"
                "需整列齊備，或整列留空只走基本 6 欄")
        numeric = {c: float(v) for c, v in filled.items()}
        if not all(np.isfinite(v) for v in numeric.values()):
            raise ValueError("完整欄位不得為 NaN 或無限大")
        # me_consumption 允許 0（真實資料的低活動日就有 0），其餘必須為正
        if not all(numeric[c] > 0 for c in
                   ("stw", "horse_power", "displacement", "mid_draft", "hours_total")):
            raise ValueError("stw／horse_power／displacement／mid_draft／hours_total 必須大於 0")
        if numeric["me_consumption"] < 0:
            raise ValueError("me_consumption 不得為負")
        if not 1 <= numeric["stw"] <= 35:
            raise ValueError("STW 必須介於 1–35 kn")
        if numeric["hours_total"] > 24:
            raise ValueError("hours_total 不得超過 24 小時")
        return numeric

    def _append_speed_loss_source(self, rows: list[dict]) -> None:
        """完整欄位上傳列併入 strict 原始檔（同船同日 upsert）。

        寫檔改變檔案簽章——strict 端點下次請求經 refresh_maintenance_sources_if_changed
        自動重載並重建效益 context。資料重置會以原始資料集覆寫還原本檔。
        """
        add = pd.DataFrame(rows)
        with self._maintenance_source_lock:
            src = (pd.read_csv(self.speed_loss_source_path)
                   if self.speed_loss_source_path.exists() else pd.DataFrame())
            if len(src) and {"ship_id", "day"}.issubset(src.columns):
                new_keys = set(zip(add["ship_id"].astype(str), add["day"].astype(float)))
                keep = [(str(s), float(d)) not in new_keys
                        for s, d in zip(src["ship_id"], src["day"])]
                src = src.loc[keep]
            merged = pd.concat([src, add], ignore_index=True)
            self.speed_loss_source_path.parent.mkdir(parents=True, exist_ok=True)
            merged.to_csv(self.speed_loss_source_path, index=False)

    def ingest_noon_report_csv(self, frame: pd.DataFrame) -> dict:
        required = ["ship_id", "report_date", "avg_speed", "daily_foc", "wind_scale", "full_speed_hours"]
        missing = [column for column in required if column not in frame.columns]
        if missing:
            raise ValueError(f"CSV 缺少欄位：{', '.join(missing)}")
        results = []
        errors = []
        strict_rows: list[dict] = []
        updated = 0
        for index, row in frame.iterrows():
            line = int(index) + 2
            try:
                report = {column: row[column] for column in required}
                if not str(report["ship_id"]).strip():
                    raise ValueError("ship_id 不可空白")
                numeric = {
                    column: float(report[column])
                    for column in ["avg_speed", "daily_foc", "wind_scale", "full_speed_hours"]
                }
                if not all(np.isfinite(value) for value in numeric.values()):
                    raise ValueError("數值欄位不得為空白、NaN 或無限大")
                if not 1 <= numeric["avg_speed"] <= 35:
                    raise ValueError("均速必須介於 1–35 kn")
                if numeric["daily_foc"] <= 0:
                    raise ValueError("DailyFOC 必須大於 0")
                if not 0 <= numeric["wind_scale"] <= 12:
                    raise ValueError("風級必須介於 0–12 Bft")
                if not 0 < numeric["full_speed_hours"] <= 24:
                    raise ValueError("全速時數必須介於 0–24 小時")
                pd.Timestamp(report["report_date"])
                strict_values = self._parse_strict_upload_columns(frame, row)
                result = self.ingest_noon_report(report, notify=False)  # 批次結束才一次通知
                updated += int(result["updated"])
                if strict_values is not None:
                    rd = pd.Timestamp(report["report_date"]).normalize()
                    strict_rows.append({
                        "ship_id": str(report["ship_id"]).strip(),
                        "report_date": rd.strftime("%Y-%m-%d"),
                        "day": float((rd - DAY_EPOCH).days),
                        "wind_scale": numeric["wind_scale"],
                        **strict_values,
                    })
                results.append({"row": line, **result})
            except (KeyError, ValueError, TypeError) as exc:
                message = f"未知船舶 {report.get('ship_id')}" if isinstance(exc, KeyError) else str(exc)
                errors.append({"row": line, "message": message})
        if strict_rows:  # 寫檔改簽章 → strict 引擎（預測/窗口/效益）下次請求自動重載
            self._append_speed_loss_source(strict_rows)
        # 規則 1：整批只寄一次（每艘船取本批最新一筆的 Speed Loss）
        latest_by_ship: dict[str, dict] = {}
        names = dict(zip(self.fleet["ship_id"].astype(str), self.fleet["ship_name"].astype(str)))
        for item in sorted(results, key=lambda r: r["report_date"]):
            latest_by_ship[item["ship_id"]] = {
                "ship_id": item["ship_id"],
                "ship_name": names.get(item["ship_id"], item["ship_id"]),
                "report_date": item["report_date"],
                "speed_loss_pct": item["speed_loss_pct"],
            }
        notifications = (self._notify_noon_report(list(latest_by_ship.values()))
                         if latest_by_ship else {"updates": 0, "notified": 0, "results": []})
        return {
            "summary": {
                "rows": len(frame),
                "accepted": len(results),
                "rejected": len(errors),
                "updated": updated,
                "strict_appended": len(strict_rows),
            },
            "results": results,
            "errors": errors,
            "notifications": notifications,
        }

    def ship_log(self, ship_id: str, days: int = 30) -> dict:
        if not (self.fleet["ship_id"] == ship_id).any():
            raise KeyError(ship_id)
        reports = self.scored[self.scored[schema.SHIP_ID] == ship_id].sort_values(schema.REPORT_DATE)
        if reports.empty:
            return {"ship_id": ship_id, "days": days, "entries": []}
        as_of = reports[schema.REPORT_DATE].max().normalize()
        cutoff = as_of - pd.Timedelta(days=days - 1)
        reports = reports[reports[schema.REPORT_DATE] >= cutoff]
        events = self.events[
            (self.events[schema.EVENT_SHIP_ID] == ship_id)
            & (self.events[schema.EVENT_DATE] >= cutoff)
            & (self.events[schema.EVENT_DATE] <= as_of)
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
                **self.notification_subscriptions.channel_status(),
            },
        }

    def list_notification_subscriptions(self) -> dict:
        ships = self.fleet[["ship_id", "ship_name"]].sort_values("ship_id").to_dict("records")
        return {
            "subscriptions": self.notification_subscriptions.list_public(),
            "available_ships": ships,
            "channels": self.notification_subscriptions.channel_status(),
            "watch_threshold_pct": config.WATCH_THRESHOLD_PCT,
        }

    def create_notification_subscription(
        self, channel: str, destination: str | None, ship_ids: list[str],
        kind: str = "digest",
    ) -> dict:
        known = set(self.fleet["ship_id"].astype(str))
        unknown = sorted(set(ship_ids) - known)
        if unknown:
            raise ValueError(f"未知船舶：{', '.join(unknown)}")
        if not ship_ids:
            raise ValueError("請至少選擇一艘船")
        created = self.notification_subscriptions.create(channel, destination, ship_ids, kind)
        # 規則 2：訂閱當下寄確認通知（失敗不阻擋訂閱成立，狀態如實回報）
        try:
            welcome = self.notification_subscriptions.send_welcome_or_request_verification(
                created["id"], self.fleet_overview()["ships"], config.WATCH_THRESHOLD_PCT)
        except Exception as exc:  # noqa: BLE001
            welcome = {"delivered": False, "status": "error", "reason": str(exc)[:120]}
        return {**created, "welcome": welcome}

    def delete_notification_subscription(self, subscription_id: str) -> dict:
        self.notification_subscriptions.delete(subscription_id)
        return {"id": subscription_id, "deleted": True}

    def send_notification_digest(self, subscription_id: str) -> dict:
        return self.notification_subscriptions.send_digest(
            subscription_id, self.fleet_overview()["ships"]
        )

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
            classify_operational_status(r.current_speed_loss_pct, None if pd.isna(r.days_to_threshold)
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
                "watch_threshold_pct": config.WATCH_THRESHOLD_PCT,
                "watch_window_days": config.WATCH_WINDOW_DAYS,
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
        gi = g.set_index(schema.REPORT_DATE)
        w = (gi.resample("W")[["speed_loss_smooth", "excess_foc_smooth", schema.DAILY_FOC,
                               "expected_foc"]].mean().reset_index())
        # 週點標「該週實際最後一筆日報日」——resample('W') 預設的日曆週日標籤
        # 會落在資料截止日之後（圖尾超出 as_of，映射座標截至對不上）；
        # 空週為 NaT，由下游 dropna(speed_loss_smooth) 一併濾除
        w[schema.REPORT_DATE] = gi.index.to_series().resample("W").max().to_numpy()
        return w

    # ---------- ship ----------
    def ship_detail(self, ship_id: str) -> dict:
        row = self.fleet[self.fleet["ship_id"] == ship_id]
        if row.empty:
            raise KeyError(ship_id)
        r = row.iloc[0]
        daily = self.scored[self.scored[schema.SHIP_ID] == ship_id].sort_values(schema.REPORT_DATE)
        latest_report = daily.iloc[-1] if len(daily) else None
        as_of = (
            pd.Timestamp(latest_report[schema.REPORT_DATE]).normalize()
            if latest_report is not None
            else pd.Timestamp(r.last_date).normalize()
        )
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
        ev = self.events[
            (self.events[schema.EVENT_SHIP_ID] == ship_id)
            & (self.events[schema.EVENT_DATE] >= (
                dates.min() if len(dates) else pd.Timestamp.max
            ))
            & (self.events[schema.EVENT_DATE] <= as_of)
        ]
        ship_events = self.events[
            (self.events[schema.EVENT_SHIP_ID] == ship_id)
            & (self.events[schema.EVENT_DATE] <= as_of)
        ].sort_values(schema.EVENT_DATE)
        intervention_types = schema.RESET_EVENTS | schema.PARTIAL_RESET_EVENTS
        maintenance_events = ship_events[
            ship_events[schema.EVENT_TYPE].isin(intervention_types)
        ]
        clean_events = ship_events[
            ship_events[schema.EVENT_TYPE].isin(schema.RESET_EVENTS)
        ]
        latest_event = maintenance_events.iloc[-1] if len(maintenance_events) else None
        latest_clean_event = clean_events.iloc[-1] if len(clean_events) else None
        if latest_clean_event is not None:
            days_since_clean = max(
                int((as_of - latest_clean_event[schema.EVENT_DATE].normalize()).days), 0
            )
            days_since_clean_basis = "event"
        else:
            dataset_start = (
                pd.Timestamp(daily[schema.REPORT_DATE].min()).normalize()
                if len(daily) else as_of
            )
            days_since_clean = max(int((as_of - dataset_start).days), 0)
            days_since_clean_basis = "dataset_start"
        dtt = None if pd.isna(r.days_to_threshold) else int(r.days_to_threshold)
        status = classify_operational_status(float(r.current_speed_loss_pct), dtt)
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
            "date_provenance": {
                "source_time_axis": "NOON_UTC/event_day relative day",
                "display_mapping": "Day 0 = 2021-01-01",
                "real_calendar_dates": False,
            },
            "status": status, "fouling_level": r.fouling_level,
            "hull_prop": hull_prop,
            "maintenance_effects": [
                {"date": x.event_date.strftime("%Y-%m-%d"), "type": x.event_type,
                 "orig_type": x.orig_type, "pre_pp": x.pre_pp, "post_pp": x.post_pp,
                 "delta_pp": x.delta_pp}
                for x in eff.itertuples()
            ],
            "current": {
                "as_of": as_of.strftime("%Y-%m-%d"),
                "speed_loss_pct": float(r.current_speed_loss_pct),
                "avg_speed": round(float(latest_report[schema.AVG_SPEED]), 1) if latest_report is not None else None,
                "days_since_clean": days_since_clean,
                "days_since_clean_basis": days_since_clean_basis,
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
                "last_clean_event": None if latest_clean_event is None else {
                    "date": latest_clean_event[schema.EVENT_DATE].strftime("%Y-%m-%d"),
                    "type": latest_clean_event[schema.EVENT_TYPE],
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
        speed_loss_recovery_pp: float | None = None,
    ) -> dict:
        f = self.fleet
        target = f[f["ship_id"] == ship_id].iloc[0] if ship_id else f.iloc[0]
        params = RoiParams(
            fuel_price_usd=float(fuel_price or self.roi_params.fuel_price_usd),
            cleaning_cost_usd=float(cleaning_cost or self.roi_params.cleaning_cost_usd),
            horizon_days=self.roi_params.horizon_days,
            co2_per_ton=self.roi_params.co2_per_ton,
        )
        post_clean_sl = max(
            0.0,
            float(target.current_speed_loss_pct) - float(speed_loss_recovery_pp),
        ) if speed_loss_recovery_pp is not None else POST_CLEAN_SL_PCT
        curve = whatif_curve(
            current_sl_pct=float(target.current_speed_loss_pct),
            growth_pp_day=self._decision_growth(target),
            f_ref=float(target.f_ref), params=params, post_clean_sl_pct=post_clean_sl)
        per_ship, annual_saving = [], 0.0
        for r in f.itertuples():
            c = whatif_curve(float(r.current_speed_loss_pct), self._decision_growth(r),
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
                "primary_model_id": self.model_packages.active_id(),
            },
        }
