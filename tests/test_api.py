"""FastAPI 端點整合測試（stub LLM、本地檢索、小型合成資料）。"""

import pytest
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
    config.ARTIFACT_DIR = art
    from app.api.main import app

    with TestClient(app) as c:
        yield c
    config.ARTIFACT_DIR = old


def test_health(client):
    r = client.get("/api/health").json()
    assert r["artifacts_loaded"] is True
    assert r["advisor_mode"] == "scripted"  # 賽前預設 stub


def test_fleet(client):
    r = client.get("/api/fleet")
    assert r.status_code == 200
    body = r.json()
    assert body["stats"]["n_ships"] == 4
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


def test_fuel_prices_expose_five_grades_sources_and_history(client):
    response = client.get("/api/fuel-prices")

    assert response.status_code == 200
    body = response.json()
    assert {price["grade"] for price in body["prices"]} == {
        "HSHFO", "VLSFO", "ULSFO", "LSMGO", "BIO_HSFO"
    }
    assert all(price["source"] and price["as_of"] for price in body["prices"])
    assert any(price["estimated"] for price in body["prices"])
    assert len(body["history"]) >= 7


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


def test_advisor_scripted(client):
    r = client.post("/api/advisor", json={"question": "這一季哪幾艘該優先清洗？為什麼？"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "scripted"
    assert body["steps"] and body["citations"]
    assert "US$" in body["answer"]


def test_advisor_ship_cost(client):
    ship = client.get("/api/fleet").json()["ships"][0]
    r = client.post("/api/advisor",
                    json={"question": f"{ship['ship_name']} 的髒污成本一天多少？"}).json()
    assert ship["ship_name"] in r["answer"] or "US$" in r["answer"]


def test_inspect_stub(client):
    ship_id = client.get("/api/fleet").json()["ships"][0]["ship_id"]
    r = client.post("/api/inspect", files={"file": ("hull.jpg", b"\xff\xd8fakejpeg", "image/jpeg")},
                    data={"ship_id": ship_id})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "stub"
    assert body["fouling_level"] in {"light", "moderate", "heavy"}
    assert body["cross_check"]["consistent"] is True  # stub 從資料面推，必一致
