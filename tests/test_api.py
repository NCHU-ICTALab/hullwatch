"""FastAPI 端點整合測試（stub LLM、本地檢索、小型合成資料）。"""

import json
from datetime import date

import numpy as np
import pytest
import xgboost as xgb
from fastapi.testclient import TestClient

from app import config
from app.pipeline.run import generate_and_save, run_pipeline
from app.synth.generator import GeneratorConfig


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("hw")
    raw, art = tmp / "raw", tmp / "artifacts"
    generate_and_save(raw, GeneratorConfig(n_ships=4, start="2021-01-01",
                                           end="2023-06-30", seed=5))
    run_pipeline(raw, art)
    old = config.ARTIFACT_DIR
    old_fuel_live = config.FUEL_LIVE_ENABLED
    config.ARTIFACT_DIR = art
    config.FUEL_LIVE_ENABLED = False
    from app.api.main import app

    with TestClient(app) as c:
        yield c
    config.ARTIFACT_DIR = old
    config.FUEL_LIVE_ENABLED = old_fuel_live


def test_health(client):
    r = client.get("/api/health").json()
    assert r["artifacts_loaded"] is True
    assert r["advisor_mode"] == "scripted"  # 賽前預設 stub


def test_fleet(client):
    r = client.get("/api/fleet")
    assert r.status_code == 200
    body = r.json()
    assert body["stats"]["n_ships"] == 4
    assert body["stats"]["threshold_pct"] == config.CLEANING_THRESHOLD_PCT
    assert body["stats"]["watch_threshold_pct"] == config.WATCH_THRESHOLD_PCT
    assert body["stats"]["watch_window_days"] == config.WATCH_WINDOW_DAYS
    ship = body["ships"][0]
    for key in ["ship_id", "speed_loss_pct", "fouling_level", "status",
                "days_since_clean", "excess_cost_per_day", "spark"]:
        assert key in ship
    # 依 speed loss 由高到低排序
    sls = [s["speed_loss_pct"] for s in body["ships"]]
    assert sls == sorted(sls, reverse=True)


def test_ship_attribution_waterfall(client):
    ships = client.get("/api/fleet").json()["ships"]
    d = client.get(f"/api/ship/{ships[0]['ship_id']}").json()
    a = d["attribution"]
    assert a is not None
    parts = a["baseline_tons"] + sum(f["tons"] for f in a["factors"])
    assert abs(parts - a["actual_tons"]) < 0.6  # 近 7 天平均，允許平滑誤差
    assert any(f.get("is_fouling") for f in a["factors"])


def test_ship_detail(client):
    ship_id = client.get("/api/fleet").json()["ships"][0]["ship_id"]
    r = client.get(f"/api/ship/{ship_id}")
    assert r.status_code == 200
    d = r.json()
    assert len(d["series"]) > 20
    assert len(d["forecast"]) == 16
    assert d["forecast"][0]["lo"] < d["forecast"][0]["mid"] < d["forecast"][0]["hi"]
    current = d["current"]
    assert current["as_of"]
    assert current["wind_scale"] is not None
    assert current["last_event"] is None or current["last_event"]["date"] <= current["as_of"]
    if current["last_clean_event"]:
        assert current["last_clean_event"]["type"] in {"cleaning", "drydock"}
        assert current["days_since_clean"] == (
            date.fromisoformat(current["as_of"])
            - date.fromisoformat(current["last_clean_event"]["date"])
        ).days
        assert current["days_since_clean_basis"] == "event"
    else:
        assert current["days_since_clean_basis"] == "dataset_start"
    assert client.get("/api/ship/NOPE").status_code == 404


def test_roi(client):
    r = client.get("/api/roi").json()
    assert len(r["target"]["days"]) == config.ROI_HORIZON_DAYS + 1
    assert r["stats"]["fleet_daily_excess_usd"] >= 0
    assert len(r["per_ship"]) == 4


def test_roi_accepts_fuel_price_override_for_scenario_analysis(client):
    ship_id = client.get("/api/fleet").json()["ships"][0]["ship_id"]
    baseline = client.get("/api/roi", params={"ship_id": ship_id}).json()

    override = client.get("/api/roi", params={"ship_id": ship_id, "fuel_price": 900}).json()

    assert override["stats"]["fuel_price_usd"] == 900
    assert override["target"]["current_excess_cost"] > baseline["target"]["current_excess_cost"]


def test_roi_accepts_recommended_action_cost_for_consistent_decision_curve(client):
    recommendation = client.get("/api/schedule").json()["recommendations"][0]

    body = client.get("/api/roi", params={
        "ship_id": recommendation["ship_id"],
        "cleaning_cost": recommendation["action_cost_usd"],
        "speed_loss_recovery_pp": recommendation["speed_loss_recovery_pp"],
    }).json()

    assert body["target"]["ship_id"] == recommendation["ship_id"]
    assert body["stats"]["cleaning_cost_usd"] == recommendation["action_cost_usd"]
    assert body["target"]["post_clean_sl_pct"] == pytest.approx(
        max(0, recommendation["speed_loss_pct"] - recommendation["speed_loss_recovery_pp"]),
        abs=0.02,
    )


def test_model_registry_exposes_one_primary_and_comparison_models(client):
    response = client.get("/api/models")

    assert response.status_code == 200
    models = response.json()["models"]
    assert len(models) >= 2
    assert sum(model["is_primary"] for model in models) == 1
    assert all(
        {"id", "name", "description", "validation_mape", "needs_speed"} <= model.keys()
        for model in models
    )


def test_builtin_forecast_model_can_become_the_decision_model(client):
    response = client.post("/api/models/persistence/activate")

    assert response.status_code == 200
    registry = client.get("/api/models").json()
    assert registry["active_model_id"] == "persistence"
    assert [
        model["id"] for model in registry["models"] if model["is_primary"]
    ] == ["persistence"]
    assert client.post("/api/models/restore").status_code == 200


def test_ship_forecast_supports_registered_model_and_scenario_speed(client):
    ship_id = client.get("/api/fleet").json()["ships"][0]["ship_id"]

    response = client.get(
        f"/api/ship/{ship_id}/forecast",
        params={"model": "linear-growth", "speed": 16.5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ship_id"] == ship_id
    assert body["model_id"] == "linear-growth"
    assert body["scenario_speed_kn"] == 16.5
    assert len(body["forecast"]) == 16
    assert body["forecast"][0]["lo"] < body["forecast"][0]["mid"] < body["forecast"][0]["hi"]
    assert client.get(f"/api/ship/{ship_id}/forecast?model=unknown").status_code == 404


def test_schedule_returns_read_only_recommendations_for_the_fleet(client):
    response = client.get("/api/schedule")

    assert response.status_code == 200
    body = response.json()
    assert body["horizon_days"] == 180
    assert len(body["recommendations"]) == 4
    recommendation = body["recommendations"][0]
    assert recommendation["action"] in {"PP", "UWC", "UWC+PP"}
    assert recommendation["action_cost_usd"] > 0
    assert recommendation["speed_loss_recovery_pp"] >= 0
    assert recommendation["read_only"] is True
    assert "backfill" in recommendation
    assert {option["action"] for option in recommendation["action_options"]} == {
        "PP", "UWC", "UWC+PP",
    }
    assert all(
        option["post_clean_speed_loss_pct"] <= recommendation["speed_loss_pct"]
        and option["daily_fuel_saving_tons"] >= 0
        and option["monthly_saving_usd"] >= 0
        for option in recommendation["action_options"]
    )


def test_schedule_exposes_ninety_day_history_and_sort_fields(client):
    body = client.get("/api/schedule", params={"past_days": 90, "future_days": 180}).json()

    assert body["past_days"] == 90
    assert body["future_days"] == 180
    assert body["timeline_start"] < body["as_of"] < body["timeline_end"]
    assert all(
        {"risk_rank", "speed_loss_pct", "excess_cost_per_day"} <= item.keys()
        for item in body["recommendations"]
    )
    assert "maintenance_events" in body


def test_fuel_prices_expose_five_grades_sources_and_history(client):
    response = client.get("/api/fuel-prices")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["prices"], list)
    assert isinstance(body["history"], list)
    assert body["effective_price"]["estimated"] is True
    assert "manual scenario" in body["effective_price"]["method"]
    assert body["market_status"] in {"live", "cached", "stale", "unavailable"}
    assert body["refresh_interval_hours"] == 6
    assert body["stale_after_hours"] == 24


def test_fuel_history_can_drive_a_grade_selector(client):
    body = client.get("/api/fuel-prices").json()

    grades = {price["grade"] for price in body["prices"]}
    history_by_grade = body["history_by_grade"]
    assert grades <= history_by_grade.keys()
    assert all(
        points and {"date", "usd_per_ton", "source"} <= points[0].keys()
        for points in history_by_grade.values()
    )


def test_noon_report_upload_updates_ship_log_and_current_kpi(client):
    ship_id = client.get("/api/fleet").json()["ships"][0]["ship_id"]
    payload = {
        "ship_id": ship_id,
        "report_date": "2026-07-15",
        "avg_speed": 16.2,
        "daily_foc": 42.3,
        "wind_scale": 3,
        "full_speed_hours": 24,
    }

    response = client.post("/api/noon-report", json=payload)

    assert response.status_code == 201
    assert response.json()["accepted"] is True
    detail = client.get(f"/api/ship/{ship_id}").json()
    assert detail["current"]["daily_foc"] == 42.3
    assert detail["current"]["last_event"] is None or detail["current"]["last_event"]["type"] != "inspection"
    log = client.get(f"/api/ship/{ship_id}/log?days=30").json()
    assert any(row["date"] == "2026-07-15" and row["kind"] == "report" for row in log["entries"])


def test_noon_report_rejects_unknown_ship(client):
    response = client.post("/api/noon-report", json={
        "ship_id": "NOPE",
        "report_date": "2026-07-15",
        "avg_speed": 16,
        "daily_foc": 40,
        "wind_scale": 2,
        "full_speed_hours": 24,
    })

    assert response.status_code == 404


def test_noon_report_csv_template_has_canonical_columns(client):
    response = client.get("/api/noon-report/template")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        'attachment; filename="hullwatch-noon-report-template.csv"'
    )
    assert response.text.splitlines()[0] == (
        "ship_id,report_date,avg_speed,daily_foc,wind_scale,full_speed_hours"
    )


def test_noon_report_csv_import_partially_accepts_and_overwrites(client):
    ship_id = client.get("/api/fleet").json()["ships"][0]["ship_id"]
    csv_body = (
        "ship_id,report_date,avg_speed,daily_foc,wind_scale,full_speed_hours\n"
        f"{ship_id},2026-07-16,15.8,41.0,3,24\n"
        "NOPE,2026-07-16,15.8,41.0,3,24\n"
    )
    first = client.post(
        "/api/noon-report/file",
        files={"file": ("noon.csv", csv_body.encode(), "text/csv")},
    )

    assert first.status_code == 200
    assert first.json()["summary"] == {"rows": 2, "accepted": 1, "rejected": 1, "updated": 0}
    assert first.json()["errors"][0]["row"] == 3

    replacement = csv_body.splitlines()[0] + f"\n{ship_id},2026-07-16,15.8,43.0,3,24\n"
    second = client.post(
        "/api/noon-report/file",
        files={"file": ("noon.csv", replacement.encode(), "text/csv")},
    )
    assert second.json()["summary"]["updated"] == 1
    detail = client.get(f"/api/ship/{ship_id}").json()
    assert detail["current"]["daily_foc"] == 43.0


def test_noon_report_csv_rejects_out_of_range_rows_without_replacing_current(client):
    ship_id = client.get("/api/fleet").json()["ships"][0]["ship_id"]
    before = client.get(f"/api/ship/{ship_id}").json()["current"]["daily_foc"]
    csv_body = (
        "ship_id,report_date,avg_speed,daily_foc,wind_scale,full_speed_hours\n"
        f"{ship_id},2020-01-01,15.8,99.0,-1,25\n"
    )
    response = client.post(
        "/api/noon-report/file",
        files={"file": ("noon.csv", csv_body.encode(), "text/csv")},
    )

    assert response.json()["summary"]["rejected"] == 1
    assert client.get(f"/api/ship/{ship_id}").json()["current"]["daily_foc"] == before


def test_model_manifest_template_and_weights_only_upload_rejection(client):
    template = client.get("/api/models/template")

    assert template.status_code == 200
    body = template.json()
    assert body["model_format"] == "xgboost-json"
    assert body["target"] == "speed_loss_pct"
    assert body["features"]
    response = client.post(
        "/api/models/upload",
        files={"artifact": ("model.json", b"{}", "application/json")},
        data={"manifest": "{}"},
    )
    assert response.status_code == 422
    non_object = client.post(
        "/api/models/upload",
        files={"artifact": ("model.json", b"{}", "application/json")},
        data={"manifest": "[]"},
    )
    assert non_object.status_code == 422


def test_valid_xgboost_package_is_registered_as_validated_or_rejected_candidate(client, tmp_path):
    template = client.get("/api/models/template").json()
    features = template["features"]
    rng = np.random.default_rng(4)
    train_x = rng.normal(size=(80, len(features)))
    train_y = np.clip(4 + train_x[:, 0] * 0.1, 0, 100)
    booster = xgb.train({"max_depth": 2, "eta": 0.2}, xgb.DMatrix(train_x, label=train_y, feature_names=features), num_boost_round=4)
    artifact_path = tmp_path / "model.json"
    booster.save_model(artifact_path)

    response = client.post(
        "/api/models/upload",
        files={"artifact": ("model.json", artifact_path.read_bytes(), "application/json")},
        data={"manifest": json.dumps(template)},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] in {"validated", "rejected"}
    assert body["validation"]["rows"] > 0
    registry = client.get("/api/models").json()
    assert any(model["id"] == template["id"] for model in registry["models"])
    duplicate = client.post(
        "/api/models/upload",
        files={"artifact": ("model.json", artifact_path.read_bytes(), "application/json")},
        data={"manifest": json.dumps(template)},
    )
    assert duplicate.status_code == 422
    assert "不可變" in duplicate.json()["detail"]


def test_alert_center_supports_read_state(client):
    body = client.get("/api/alerts").json()

    assert body["alerts"]
    alert = body["alerts"][0]
    assert {"id", "ship_id", "severity", "message", "read"} <= alert.keys()
    response = client.post(f"/api/alerts/{alert['id']}/read")
    assert response.status_code == 200
    refreshed = client.get("/api/alerts").json()
    updated = next(item for item in refreshed["alerts"] if item["id"] == alert["id"])
    assert updated["read"] is True


def test_notification_subscriptions_support_ship_selection_and_safe_crud(client):
    ship_ids = [ship["ship_id"] for ship in client.get("/api/fleet").json()["ships"][:2]]
    response = client.post("/api/notification-subscriptions", json={
        "channel": "email",
        "destination": "owner@example.com",
        "ship_ids": ship_ids,
    })

    assert response.status_code == 201
    created = response.json()
    assert created["ship_ids"] == ship_ids
    assert created["destination_masked"] == "o***@example.com"
    assert "destination" not in created
    listing = client.get("/api/notification-subscriptions").json()
    assert any(item["id"] == created["id"] for item in listing["subscriptions"])
    assert {ship["ship_id"] for ship in listing["available_ships"]} >= set(ship_ids)

    deleted = client.delete(f"/api/notification-subscriptions/{created['id']}")
    assert deleted.status_code == 200
    assert all(
        item["id"] != created["id"]
        for item in client.get("/api/notification-subscriptions").json()["subscriptions"]
    )


def test_notification_subscription_rejects_unknown_ship_and_invalid_email(client):
    invalid_ship = client.post("/api/notification-subscriptions", json={
        "channel": "email", "destination": "owner@example.com", "ship_ids": ["NOPE"],
    })
    invalid_email = client.post("/api/notification-subscriptions", json={
        "channel": "email", "destination": "not-an-email", "ship_ids": [],
    })

    assert invalid_ship.status_code == 422
    assert invalid_email.status_code == 422


def test_advisor_scripted(client):
    r = client.post("/api/advisor", json={"question": "這一季哪幾艘該優先清洗？為什麼？"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "scripted"
    assert body["steps"] and body["citations"]
    assert "US$" in body["answer"]


def test_advisor_stream_scripted(client):
    """SSE 串流端點：stub 模式下也要吐 token + done 事件，格式與 agent 模式一致。"""
    r = client.post("/api/advisor/stream", json={"question": "哪幾艘該優先清洗？"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[len("data: "):])
              for line in r.text.split("\n\n") if line.startswith("data: ")]
    kinds = [e["type"] for e in events]
    assert kinds[-1] == "done" and "token" in kinds
    done = events[-1]
    assert done["mode"] == "scripted" and done["steps"] and done["citations"]
    assert "US$" in "".join(e["text"] for e in events if e["type"] == "token")


def test_advisor_ship_cost(client):
    ship = client.get("/api/fleet").json()["ships"][0]
    r = client.post("/api/advisor",
                    json={"question": f"{ship['ship_name']} 的髒污成本一天多少？"}).json()
    assert ship["ship_name"] in r["answer"] or "US$" in r["answer"]


def test_advisor_ten_demo_questions_hit_their_required_answer_terms(client):
    ship = client.get("/api/fleet").json()["ships"][0]
    cases = [
        ("目前全船隊哪些船需要優先處置？請比較 Speed Loss 與每日超額成本。", ["Speed Loss", "US$"]),
        (f"{ship['ship_id']} 現在的 Speed Loss、狀態與每日超額成本是多少？", ["Speed Loss", "US$"]),
        (f"{ship['ship_id']} 建議做船殼清洗還是螺旋槳拋光？依據是什麼？", [ship["ship_id"], "建議"]),
        ("密切留意與立即處置的 Speed Loss 標準是什麼？", ["5%", "10%"]),
        ("PP、UWC、UWI 與 DD 有什麼差別？哪些會改善效能？", ["UWI", "不會改善"]),
        ("Speed Loss 是怎麼計算的？符合 ISO 19030 嗎？", ["ISO 19030", "乾淨基準"]),
        ("每月超額成本與超額碳排怎麼計算？單位是什麼？", ["US$", "CO₂"]),
        ("市場行情多久更新一次？決策情境價會改寫行情嗎？", ["6 小時", "24 小時"]),
        ("目前有哪些模型？油耗預測與 Speed Loss 模型分別用在哪裡？", ["P0", "Speed Loss"]),
        ("正午日報需要哪些欄位？STW、SOG 與風級怎麼解讀？", ["STW", "SOG"]),
    ]

    for question, expected_terms in cases:
        response = client.post("/api/advisor", json={"question": question})
        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "scripted"
        assert body["citations"]
        answer = body["answer"]
        assert all(term in answer for term in expected_terms), (question, answer)
    cost_answer = client.post("/api/advisor", json={"question": cases[6][0]}).json()["answer"]
    assert "30 天" in cost_answer and "不是帳務實際月份" in cost_answer
    model_answer = client.post("/api/advisor", json={"question": cases[8][0]}).json()["answer"]
    assert "尚未完成" in model_answer and "no-action／UWC／PP" in model_answer


def test_underwater_image_interpretation_is_not_exposed(client):
    r = client.post("/api/inspect", files={"file": ("hull.jpg", b"\xff\xd8fakejpeg", "image/jpeg")},
                    data={"ship_id": "S1"})
    assert r.status_code == 404


def test_legacy_frontend_does_not_expose_underwater_image_interpretation():
    html = (config.LEGACY_FRONTEND_DIR / "index.html").read_text(encoding="utf-8")

    assert "水下判讀" not in html
    assert "/api/inspect" not in html


def test_fleet_speed_loss_windows(client):
    r = client.get("/api/fleet/speed-loss-windows")
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "per-ship-load-stw-horsepower-ols"
    assert body["parameters"]["threshold_pct"] == 8.0
    assert len(body["ships"]) == 4
    for ship in body["ships"]:
        assert {"ship_id", "ship_name", "available", "reason", "groups"} <= ship.keys()
        # all 載況固定回傳兩組，且不含逐點序列（總覽輕量 payload）
        assert [g["load_condition"] for g in ship["groups"]] == ["laden", "ballast"]
        for group in ship["groups"]:
            assert {"load_label", "available", "latest_day", "threshold_crossing"} <= group.keys()
            assert {"eta_days", "earliest_days", "latest_days"} <= group["threshold_crossing"].keys()
            assert "history" not in group and "forecast" not in group

    assert client.get("/api/fleet/speed-loss-windows",
                      params={"threshold_pct": 0}).status_code == 422
