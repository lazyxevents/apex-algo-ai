import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .core import db_health, init_db, settings
from .kite import kite_service
from .strategy import adaptive_learner
from .trading import trading_engine
from .upstox import upstox_service


async def automation_loop():
    trading_engine.state.automation_running = True
    try:
        while True:
            await asyncio.to_thread(trading_engine.automation_cycle, upstox_service)
            await asyncio.sleep(max(15, settings.auto_scan_interval_seconds))
    finally:
        trading_engine.state.automation_running = False


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    task = asyncio.create_task(automation_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="APEX Algo AI", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ModeRequest(BaseModel):
    mode: Literal["OFF", "PAPER", "SAFE", "KILLED"]


class PaperOrderRequest(BaseModel):
    symbol: str = Field(min_length=2, max_length=128)
    direction: Literal["CE", "PE"]
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    target: float = Field(gt=0)
    lot_size: int = Field(gt=0)
    quantity: int | None = Field(default=None, gt=0)
    reason: str = Field(default="{}", max_length=8000)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "database": db_health(),
        "mode": trading_engine.state.mode,
        "automation": trading_engine.state.automation_running,
    }


@app.get("/api/system/status")
def system_status():
    return trading_engine.status(kite_service.connection_status(), upstox_service.status())


@app.post("/api/system/mode")
def set_mode(body: ModeRequest):
    flatten = []
    if body.mode == "KILLED":
        trading_engine.kill_switch("manual mode request")
        flatten = trading_engine.force_flatten(upstox_service, "manual KILLED mode")
    else:
        try:
            trading_engine.set_mode(body.mode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": system_status(), "flatten": flatten}


@app.post("/api/risk/kill-switch")
def kill_switch():
    trading_engine.kill_switch("manual dashboard kill switch")
    flatten = trading_engine.force_flatten(upstox_service, "manual kill switch")
    return {"status": system_status(), "flatten": flatten}


@app.post("/api/risk/flatten")
def flatten_positions():
    return {"flatten": trading_engine.force_flatten(upstox_service, "manual emergency flatten"), "status": system_status()}


@app.post("/api/risk/reset-kill-switch")
def reset_kill_switch():
    trading_engine.reset_kill_switch()
    return system_status()


@app.get("/api/risk/status")
def risk_status():
    return trading_engine.risk_snapshot()


@app.get("/api/performance")
def performance():
    return trading_engine.performance_snapshot()


@app.get("/api/strategy/status")
def strategy_status():
    return adaptive_learner.snapshot()


@app.post("/api/research/run-once")
def research_run_once():
    try:
        return adaptive_learner.daily_research(upstox_service)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/automation/run-once")
def automation_run_once():
    return trading_engine.automation_cycle(upstox_service)


@app.get("/api/trades")
def list_trades(limit: int = 100):
    return trading_engine.list_trades(limit=min(max(limit, 1), 500))


@app.post("/api/paper/orders")
def paper_order(body: PaperOrderRequest):
    try:
        return trading_engine.open_paper_trade(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/paper/mark/{trade_id}")
def mark_trade(trade_id: int, ltp: float):
    try:
        return trading_engine.mark_trade(trade_id, ltp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/kite/login-url")
def kite_login_url():
    return {"configured": kite_service.configured, "url": kite_service.login_url()}


class KiteSessionRequest(BaseModel):
    request_token: str


@app.post("/api/kite/session")
def kite_session(body: KiteSessionRequest):
    return kite_service.generate_session(body.request_token)


@app.get("/api/kite/profile")
def kite_profile():
    return kite_service.profile()


@app.websocket("/ws")
async def ws_status(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json(system_status())
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        return
