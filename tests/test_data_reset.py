"""資料重置端點：弄髒 → 重置 → 從來源重建並熱替換服務（背景執行＋輪詢）。"""

import time

import pytest
from fastapi.testclient import TestClient

from app import config
from app.pipeline.run import generate_and_save, run_pipeline
from app.synth.generator import GeneratorConfig


@pytest.fixture()
def client(tmp_path):
    # 現行站台 4 艘、重置來源 3 艘（canonical 目錄）——船數差異證明真的換了資料
    data_dir = tmp_path / "data"
    raw, art = data_dir / "raw", data_dir / "artifacts"
    generate_and_save(raw, GeneratorConfig(n_ships=4, start="2021-01-01",
                                           end="2022-06-30", seed=5))
    run_pipeline(raw, art)
    source = tmp_path / "dataset"
    generate_and_save(source, GeneratorConfig(n_ships=3, start="2021-01-01",
                                              end="2022-06-30", seed=7))

    old = (config.DATA_DIR, config.ARTIFACT_DIR,
           config.RESET_DATASET_URI, config.FUEL_LIVE_ENABLED)
    config.DATA_DIR, config.ARTIFACT_DIR = data_dir, art
    config.RESET_DATASET_URI = str(source)
    config.FUEL_LIVE_ENABLED = False
    from app.api.main import app

    with TestClient(app) as c:
        yield c
    (config.DATA_DIR, config.ARTIFACT_DIR,
     config.RESET_DATASET_URI, config.FUEL_LIVE_ENABLED) = old


def _wait_done(client, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get("/api/data/reset/status").json()
        if status["state"] in {"done", "error"}:
            return status
        time.sleep(0.5)
    return client.get("/api/data/reset/status").json()


def test_reset_clears_and_reimports(client):
    assert client.get("/api/data/reset/status").json()["state"] == "idle"
    fleet = client.get("/api/fleet").json()
    assert fleet["stats"]["n_ships"] == 4

    # 先弄髒：介面上傳一筆日報（只進記憶體）
    r = client.post("/api/noon-report", json={
        "ship_id": fleet["ships"][0]["ship_id"], "report_date": "2022-07-15",
        "avg_speed": 14.5, "daily_foc": 55.0, "wind_scale": 3, "full_speed_hours": 24})
    assert r.status_code == 201

    r = client.post("/api/data/reset")
    assert r.status_code == 202
    assert r.json()["state"] == "running"
    # 進行中重複觸發要被擋下
    assert client.post("/api/data/reset").status_code == 409

    status = _wait_done(client)
    assert status["state"] == "done", status
    assert status["summary"]["n_ships"] == 3
    # 服務已熱替換成來源資料；上傳的髒資料一併消失
    assert client.get("/api/fleet").json()["stats"]["n_ships"] == 3
    assert client.get("/api/health").json()["artifacts_loaded"] is True


def test_reset_error_keeps_old_service(client, tmp_path):
    config.RESET_DATASET_URI = str(tmp_path / "no-such-dir")
    assert client.post("/api/data/reset").status_code == 202
    status = _wait_done(client, timeout=30)
    assert status["state"] == "error"
    assert "不存在" in status["error"]
    # 舊服務仍在供應
    assert client.get("/api/fleet").json()["stats"]["n_ships"] == 4
