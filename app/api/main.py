"""FastAPI 單體（ADR-0003）：五條 API + 直接 serve 前端靜態檔。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config
from app.llm import inspect as inspect_mod
from app.llm.advisor import Advisor
from app.llm.provider import get_chat_model
from app.llm.retrieval import get_retriever


class AskBody(BaseModel):
    question: str


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


@app.get("/api/ship/{ship_id}")
def ship(ship_id: str):
    try:
        return _svc(app.state).ship_detail(ship_id)
    except KeyError:
        raise HTTPException(404, f"未知船舶 {ship_id}")


@app.get("/api/roi")
def roi(ship_id: str | None = None):
    try:
        return _svc(app.state).roi(ship_id)
    except IndexError:
        raise HTTPException(404, f"未知船舶 {ship_id}")


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
    return inspect_mod.analyze_hull_image(content, image_format=fmt, data_speed_loss_pct=data_sl)


@app.get("/")
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")
