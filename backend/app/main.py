import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .core import db_health, init_db, settings
from .dataset_model import dataset_model_service
from .kite import kite_service
from .learning_worker import learning_worker
from .live_learning import live_learning_service
from .research_engine import research_engine
from .strategy import adaptive_learner
from .trading import trading_engine
from .upstox import upstox_service
from .yahoo import yahoo_service


def get_market_service():
    provider = settings.market_data_provider.strip().lower()
    if provider in {"yfinance", "yahoo"}:
        return yahoo_service
    if provider == "upstox":
        return upstox_service
    raise RuntimeError(f"Unsupported MARKET_DATA_PROVIDER={settings.market_data_provider}")


market_service = get_market_service()


async def automation_loop():
    trading_engine.state.automation_running = True
    try:
        while True:
            await asyncio.to_thread(trading_engine.automation_cycle, market_service)
            await asyncio.sleep(max(15, settings.auto_scan_interval_seconds))
    finally:
        trading_engine.state.automation_running = False


async def learning_loop():
    while True:
        await asyncio.to_thread(learning_worker.run_cycle, market_service)
        await asyncio.sleep(max(300, settings.learning_worker_interval_minutes * 60))


async def position_monitor_loop():
    trading_engine.state.position_monitor_running = True
    try:
        while True:
            try:
                await asyncio.to_thread(trading_engine.monitor_open_positions, market_service)
            except Exception as exc:
                trading_engine.state.last_error = f"position monitor: {exc}"
            await asyncio.sleep(max(1.0, settings.position_monitor_interval_seconds))
    finally:
        trading_engine.state.position_monitor_running = False


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    automation_task = asyncio.create_task(automation_loop())
    position_task = asyncio.create_task(position_monitor_loop())
    learning_task = asyncio.create_task(learning_loop()) if settings.learning_worker_enabled else None
    yield
    automation_task.cancel()
    position_task.cancel()
    if learning_task:
        learning_task.cancel()
    for task in (automation_task, position_task, learning_task):
        if task is None:
            continue
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="APEX Algo AI", version="0.6.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ModeRequest(BaseModel):
    mode: Literal["OFF", "PAPER", "SAFE", "KILLED"]


class TradePlanRequest(BaseModel):
    stop: float = Field(gt=0)
    target: float = Field(gt=0)


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
        "marketProvider": market_service.status(),
    }


@app.get("/api/system/status")
def system_status():
    return trading_engine.status(kite_service.connection_status(), market_service.status())


@app.post("/api/system/mode")
def set_mode(body: ModeRequest):
    flatten = []
    if body.mode == "KILLED":
        trading_engine.kill_switch("manual mode request")
        flatten = trading_engine.force_flatten(market_service, "manual KILLED mode")
    else:
        try:
            trading_engine.set_mode(body.mode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": system_status(), "flatten": flatten}


@app.post("/api/risk/kill-switch")
def kill_switch():
    trading_engine.kill_switch("manual dashboard kill switch")
    flatten = trading_engine.force_flatten(market_service, "manual kill switch")
    return {"status": system_status(), "flatten": flatten}


@app.post("/api/risk/flatten")
def flatten_positions():
    return {"flatten": trading_engine.force_flatten(market_service, "manual emergency flatten"), "status": system_status()}


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


@app.get("/api/learning/status")
def learning_status():
    return learning_worker.snapshot()


@app.post("/api/learning/run-once")
def learning_run_once():
    return learning_worker.run_cycle(market_service, force=True)


@app.get("/api/learning/live")
def live_learning_status(limit: int = 20):
    return live_learning_service.snapshot(limit=min(max(limit, 1), 50))


@app.get("/api/learning/dataset")
def learning_dataset():
    return dataset_model_service.snapshot()


@app.post("/api/learning/evaluate-candidate")
def learning_evaluate_candidate():
    return dataset_model_service.evaluate_candidate()


@app.get("/api/research/intelligence")
def research_intelligence():
    return research_engine.snapshot()


@app.post("/api/research/run-once")
def research_run_once():
    try:
        return adaptive_learner.daily_research(market_service)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/research/premarket")
def premarket_research_run_once():
    try:
        return trading_engine.run_premarket_research(market_service, force=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/automation/run-once")
def automation_run_once():
    return trading_engine.automation_cycle(market_service)


@app.get("/api/trades")
def list_trades(limit: int = 100):
    return trading_engine.list_trades(limit=min(max(limit, 1), 500))


@app.post("/api/paper/orders")
def paper_order(body: PaperOrderRequest):
    try:
        return trading_engine.open_paper_trade(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/paper/trades/{trade_id}/plan")
def update_trade_plan(trade_id: int, body: TradePlanRequest):
    try:
        return trading_engine.update_trade_plan(trade_id, body.stop, body.target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/paper/trades/{trade_id}/exit")
def manual_exit_trade(trade_id: int):
    try:
        return trading_engine.manual_exit_trade(market_service, trade_id)
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
