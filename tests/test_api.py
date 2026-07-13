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
