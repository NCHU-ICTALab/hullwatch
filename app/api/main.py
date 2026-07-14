"""FastAPI 單體（ADR-0003）：五條 API + 直接 serve 前端靜態檔。"""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from datetime import date

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config
from app.llm import inspect as inspect_mod
from app.llm.advisor import Advisor
from app.llm.provider import get_chat_model
from app.llm.retrieval import get_retriever
from app.api.model_packages import manifest_template


class AskBody(BaseModel):
    question: str


class NoonReportBody(BaseModel):
    ship_id: str
    report_date: date
    avg_speed: float
    daily_foc: float
    wind_scale: float
    full_speed_hours: float


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.api.service import FleetService

    try:
        app.state.service = FleetService()
    except FileNotFoundError:
        app.state.service = None  # artifacts 未產生：跑 python -m app.pipeline.run --synth
    if app.state.service is not None:
        app.state.advisor = Advisor(app.state.service, get_retriever(), get_chat_model())
    else:
        app.state.advisor = None
    yield


app = FastAPI(title="HullWatch", lifespan=lifespan)


def _svc(app_state) -> "FleetService":
    if app_state.service is None:
        raise HTTPException(503, "artifacts 未載入：請先執行 python -m app.pipeline.run --synth")
    return app_state.service


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "artifacts_loaded": app.state.service is not None,
        "llm_provider": config.LLM_PROVIDER,
        "retriever": config.RETRIEVER,
        "advisor_mode": app.state.advisor.mode if app.state.advisor else None,
    }


@app.get("/api/fleet")
def fleet():
    return _svc(app.state).fleet_overview()


@app.get("/api/models")
def models():
    return _svc(app.state).model_registry()


@app.get("/api/models/template")
def model_template():
    return manifest_template()


@app.post("/api/models/upload", status_code=201)
async def upload_model(artifact: UploadFile = File(...), manifest: str = Form(...)):
    max_bytes = 20 * 1024 * 1024
    content = await artifact.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(413, "模型檔案不得超過 20MB")
    try:
        return _svc(app.state).register_model_package(manifest, content)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/models/{model_id}/activate")
def activate_model(model_id: str):
    try:
        return _svc(app.state).activate_model(model_id)
    except KeyError:
        raise HTTPException(404, f"未知模型 {model_id}")
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/models/restore")
def restore_model():
    return _svc(app.state).restore_builtin_model()


@app.get("/api/ship/{ship_id}")
def ship(ship_id: str):
    try:
        return _svc(app.state).ship_detail(ship_id)
    except KeyError:
        raise HTTPException(404, f"未知船舶 {ship_id}")


@app.get("/api/ship/{ship_id}/forecast")
def ship_forecast(ship_id: str, model: str = "clean-baseline", speed: float | None = None):
    try:
        return _svc(app.state).ship_forecast(ship_id, model, speed)
    except KeyError as exc:
        raise HTTPException(404, f"未知船舶或模型 {exc.args[0]}")


@app.get("/api/ship/{ship_id}/log")
def ship_log(ship_id: str, days: int = 30):
    try:
        return _svc(app.state).ship_log(ship_id, max(1, min(days, 365)))
    except KeyError:
        raise HTTPException(404, f"未知船舶 {ship_id}")


@app.post("/api/noon-report", status_code=201)
def noon_report(body: NoonReportBody):
    if body.avg_speed <= 0 or body.daily_foc <= 0 or body.full_speed_hours <= 0:
        raise HTTPException(422, "航速、DailyFOC 與全速時數必須大於 0")
    try:
        return _svc(app.state).ingest_noon_report(body.model_dump(mode="json"))
    except KeyError:
        raise HTTPException(404, f"未知船舶 {body.ship_id}")


NOON_REPORT_COLUMNS = "ship_id,report_date,avg_speed,daily_foc,wind_scale,full_speed_hours\n"


@app.get("/api/noon-report/template")
def noon_report_template():
    example = "HW-001,2026-07-15,15.8,41.2,3,24\n"
    return Response(
        content=NOON_REPORT_COLUMNS + example,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="hullwatch-noon-report-template.csv"'},
    )


@app.post("/api/noon-report/file")
async def noon_report_file(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(415, "第一版只接受 CSV 檔案")
    max_bytes = 5 * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(413, "CSV 檔案請小於 5MB")
    try:
        frame = pd.read_csv(io.BytesIO(content))
        return _svc(app.state).ingest_noon_report_csv(frame)
    except (UnicodeDecodeError, pd.errors.ParserError, ValueError) as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/roi")
def roi(
    ship_id: str | None = None,
    fuel_price: float | None = None,
    cleaning_cost: float | None = None,
):
    if fuel_price is not None and not 100 <= fuel_price <= 3000:
        raise HTTPException(422, "油價必須介於 100–3000 USD/mt")
    if cleaning_cost is not None and not 0 <= cleaning_cost <= 10_000_000:
        raise HTTPException(422, "清潔成本超出允許範圍")
    try:
        return _svc(app.state).roi(ship_id, fuel_price, cleaning_cost)
    except IndexError:
        raise HTTPException(404, f"未知船舶 {ship_id}")


@app.get("/api/schedule")
def schedule(past_days: int = 90, future_days: int = 180):
    return _svc(app.state).maintenance_schedule(
        max(0, min(past_days, 365)),
        max(30, min(future_days, 365)),
    )


@app.get("/api/fuel-prices")
def fuel_prices():
    return _svc(app.state).fuel_prices()


@app.get("/api/alerts")
def alerts():
    return _svc(app.state).alerts()


@app.post("/api/alerts/{alert_id}/read")
def mark_alert_read(alert_id: str):
    try:
        return _svc(app.state).mark_alert_read(alert_id)
    except KeyError:
        raise HTTPException(404, f"未知警報 {alert_id}")


@app.post("/api/advisor")
def advisor(body: AskBody):
    if app.state.advisor is None:
        raise HTTPException(503, "顧問未初始化")
    return app.state.advisor.ask(body.question)


@app.post("/api/inspect")
async def inspect(file: UploadFile = File(...), ship_id: str = Form("")):
    data_sl = None
    if ship_id and app.state.service is not None:
        try:
            data_sl = app.state.service.ship_detail(ship_id)["current"]["speed_loss_pct"]
        except KeyError:
            pass
    fmt = (file.content_type or "image/jpeg").split("/")[-1]
    if fmt == "jpg":
        fmt = "jpeg"
    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(413, "影像請小於 8MB")
    return inspect_mod.analyze_hull_image(content, image_format=fmt, data_speed_loss_pct=data_sl)


@app.get("/")
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


if (config.FRONTEND_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIR / "assets"), name="assets")
app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")
