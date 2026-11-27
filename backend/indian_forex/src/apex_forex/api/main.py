import asyncio
import hmac
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from apex_forex.backtest import run_bracket_backtest
from apex_forex.config import get_settings
from apex_forex.domain import MarketTick, PaperOrderRequest, PaperTrade, Side
from apex_forex.paper import PaperBroker
from apex_forex.market.mt5_bridge import MT5TickBatch, normalize_mt5_tick
from apex_forex.research.agents import AGENT_CATALOG, plan_agents
from apex_forex.research.strategy_lab import evaluate_strategy, metrics_dict
from apex_forex.research.strategy_catalog import generate_signal, strategy_catalog
from apex_forex.risk_sizing import calculate_position_size, position_size_dict
from apex_forex.providers.oanda import OandaForexClient, OandaProviderError
from apex_forex.providers.twelvedata import TwelveDataForexClient, TwelveDataProviderError
from apex_forex.risk import RiskEngine
from apex_forex.storage import PaperTradeStore

settings = get_settings()
risk = RiskEngine(
    settings.paper_max_risk_per_trade,
    settings.paper_max_daily_loss,
    settings.paper_max_open_positions,
)
paper = PaperBroker(
    settings.paper_starting_balance, risk,
    settings.paper_default_slippage_ticks, settings.paper_fee_per_order,
)
latest_ticks: dict[str, MarketTick] = {}
trade_store: PaperTradeStore | None = None
market_stream_task: asyncio.Task | None = None
market_stream_error: str | None = None
stream_instruments: list[str] = []


class StreamRequest(BaseModel):
    instruments: list[str] = Field(min_length=1, max_length=100)


class MT5IngestRequest(BaseModel):
    ticks: list[MT5TickBatch] = Field(min_length=1, max_length=200)


class PositionSizeRequest(BaseModel):
    risk_budget_account_ccy: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    pip_size: float = Field(gt=0)
    quote_to_account_rate: float = Field(gt=0)
    round_trip_cost_pips: float = Field(default=0, ge=0)
    contract_units_per_lot: float = Field(default=100000, gt=0)
    lot_step: float = Field(default=0.01, gt=0)
    min_lot: float = Field(default=0.01, gt=0)
    max_lot: float = Field(default=100, gt=0)


class StrategySignalRequest(BaseModel):
    strategy_id: str
    candles: list[dict] = Field(min_length=1, max_length=10000)


class StrategyEvaluationRequest(BaseModel):
    in_sample_pnls: list[float] = Field(min_length=1, max_length=100000)
    out_of_sample_pnls: list[float] = Field(default_factory=list, max_length=100000)
    initial_equity: float = Field(gt=0)


class PricingRequest(BaseModel):
    instruments: list[str] = Field(min_length=1, max_length=100)


class CandleRequest(BaseModel):
    instrument: str
    granularity: str = "M5"
    count: int = Field(default=500, ge=1, le=5000)
    price: str = "MBA"


class BracketBacktestRequest(BaseModel):
    candles: list[dict] = Field(min_length=2)
    side: Side
    quantity: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    target: float = Field(gt=0)
    fee_per_side: float = Field(default=0, ge=0)
    slippage: float = Field(default=0, ge=0)


def twelve_data_client() -> TwelveDataForexClient:
    return TwelveDataForexClient(settings.twelve_data_api_key, settings.twelve_data_timeout_seconds)


def oanda_client() -> OandaForexClient:
    return OandaForexClient(
        settings.oanda_access_token,
        settings.oanda_account_id,
        settings.oanda_environment,
        settings.oanda_timeout_seconds,
    )


def on_oanda_price(event: dict) -> None:
    normalized = OandaForexClient.normalize_price(event)
    if not normalized:
        return
    timestamp = normalized["timestamp"]
    tick = MarketTick(
        instrument_key=f"OANDA:{normalized['instrument']}",
        ltp=normalized["mid"],
        bid=normalized["bid"],
        ask=normalized["ask"],
        exchange_timestamp_ms=int(timestamp.timestamp() * 1000),
    )
    latest_ticks[tick.instrument_key] = tick
    for closed_trade in paper.on_tick(tick):
        if trade_store:
            trade_store.save_trade(closed_trade)


async def consume_oanda_stream(instruments: list[str]) -> None:
    global market_stream_error
    client = oanda_client()
    try:
        async for event in client.stream_prices(instruments):
            if event.get("type") == "PRICE":
                on_oanda_price(event)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        market_stream_error = str(exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global trade_store
    trade_store = PaperTradeStore(
        settings.forex_database_url,
        mongo_uri=settings.mongodb_uri,
        mongo_database=settings.mongodb_database,
    )
    paper.restore_trades([
        PaperTrade.model_validate_json(payload)
        for payload in trade_store.load_trades()
    ])
    yield
    await stop_market_stream()
    if trade_store:
        trade_store.close()


app = FastAPI(
    title="APEX Forex Engine",
    version="0.4.0",
    description="Provider-adaptable Forex research and paper trading. OANDA practice data adapter; live execution disabled.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


async def stop_market_stream() -> None:
    global market_stream_task, stream_instruments
    if market_stream_task:
        market_stream_task.cancel()
        with suppress(asyncio.CancelledError):
            await market_stream_task
    market_stream_task = None
    stream_instruments = []


@app.get("/health")
async def health():
    return {
        "status": "ok" if trade_store else "starting",
        "product": "FOREX",
        "mode": "PAPER_ONLY",
        "live_order_execution": False,
        "database_ready": trade_store is not None,
        "market_stream_connected": bool(market_stream_task and not market_stream_task.done()),
        "latest_tick_count": len(latest_ticks),
        "mt5_bridge_configured": bool(settings.mt5_bridge_token),
        "mt5_tick_count": sum(1 for key in latest_ticks if key.startswith("MT5:")),
        "twelve_data_configured": bool(settings.twelve_data_api_key),
    }


@app.get("/api/v1/config")
async def get_public_config():
    return {
        "product": "FOREX",
        "execution": "internal paper broker only",
        "paper_only": True,
        "live_order_execution": False,
        "database_configured": bool(settings.database_url),
        "providers": {
            "metatrader5": {
                "bridge_configured": bool(settings.mt5_bridge_token),
                "role": "MT5 terminal Expert Advisor market-data bridge; paper execution only",
                "live_order_execution": False,
            },
            "twelve_data": {
                "configured": bool(settings.twelve_data_api_key),
                "role": "Forex market-data prices and historical OHLC candles; no bid/ask or broker execution",
            },
            "oanda": {
                "configured": bool(settings.oanda_access_token and settings.oanda_account_id),
                "environment": "practice",
                "role": "Forex pricing stream, instruments and historical candles",
            },
        },
    }


@app.get("/api/v1/providers")
async def provider_status():
    return {
        "providers": [
            {
                "name": "Twelve Data",
                "configured": bool(settings.twelve_data_api_key),
                "connected": False,
                "environment": "market-data REST API",
                "capabilities": ["on_demand_prices", "historical_ohlc_candles"],
                "quote_type": "price-only; bid/ask unavailable from this adapter",
                "execution": "market data only; paper execution requires a separate bid/ask source",
            },
            {
                "name": "MetaTrader 5 terminal bridge",
                "configured": bool(settings.mt5_bridge_token),
                "connected": any(key.startswith("MT5:") and tick.age_seconds <= settings.market_stale_after_seconds for key, tick in latest_ticks.items()),
                "environment": "broker terminal selected by user; market-data bridge only",
                "capabilities": ["market_watch_symbols", "bid_ask_ticks", "paper_trade_price_feed"],
                "execution": "MT5 order_send is not implemented; internal paper only",
            },
            {
                "name": "OANDA v20",
                "configured": bool(settings.oanda_access_token and settings.oanda_account_id),
                "connected": bool(market_stream_task and not market_stream_task.done()),
                "environment": "practice",
                "capabilities": ["account", "instruments", "pricing_snapshot", "pricing_stream", "candles"],
                "execution": "internal paper only; broker order placement disabled",
            },
            {
                "name": "Dukascopy JForex/FIX",
                "configured": False,
                "connected": False,
                "environment": "research/demo candidate",
                "capabilities": ["streaming_quotes", "historical_ticks", "historical_candles"],
                "execution": "not integrated",
            },
            {
                "name": "Interactive Brokers TWS",
                "configured": False,
                "connected": False,
                "environment": "research/paper candidate",
                "capabilities": ["streaming_quotes", "historical_ticks", "historical_candles", "paper_account"],
                "execution": "not integrated",
            },
        ],
        "live_order_execution": False,
    }


@app.get("/api/v1/twelvedata/status")
async def twelve_data_status():
    return {
        "provider": "TWELVE_DATA",
        "configured": bool(settings.twelve_data_api_key),
        "mode": "MARKET_DATA_ONLY",
        "capabilities": ["on_demand_prices", "historical_ohlc_candles"],
        "bid_ask_available": False,
        "live_order_execution": False,
    }


@app.post("/api/v1/twelvedata/pricing")
async def twelve_data_pricing(request: PricingRequest):
    try:
        quotes = await twelve_data_client().prices(request.instruments)
        received = []
        for quote in quotes:
            symbol_key = quote["symbol"].replace("/", "_")
            tick = MarketTick(
                instrument_key=f"TWELVE_DATA:{symbol_key}",
                ltp=quote["price"],
                exchange_timestamp_ms=None,
            )
            latest_ticks[tick.instrument_key] = tick
            received.append({
                **quote,
                "instrument_key": tick.instrument_key,
                "age_seconds": round(tick.age_seconds, 3),
            })
        return {
            "provider": "TWELVE_DATA",
            "count": len(received),
            "quotes": received,
            "quote_type": "price_only_no_bid_ask",
            "paper_execution_eligible": False,
        }
    except TwelveDataProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/twelvedata/candles")
async def twelve_data_candles(request: CandleRequest):
    try:
        return await twelve_data_client().candles(
            request.instrument, request.granularity, request.count
        )
    except TwelveDataProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/forex/accounts")
async def forex_accounts():
    try:
        return await oanda_client().accounts()
    except OandaProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/forex/account")
async def forex_account():
    try:
        return await oanda_client().account_summary()
    except OandaProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/forex/instruments")
async def forex_instruments():
    try:
        payload = await oanda_client().instruments()
        return {
            "provider": "OANDA",
            "count": len(payload.get("instruments", [])),
            "instruments": payload.get("instruments", []),
        }
    except OandaProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/forex/pricing")
async def forex_pricing(request: PricingRequest):
    try:
        return await oanda_client().pricing(request.instruments)
    except OandaProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/forex/candles")
async def forex_candles(request: CandleRequest):
    try:
        return await oanda_client().candles(
            request.instrument, request.granularity, request.count, request.price
        )
    except OandaProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/mt5/ingest")
async def ingest_mt5_ticks(request: MT5IngestRequest, x_apex_mt5_key: str | None = Header(default=None, alias="X-APEX-MT5-KEY")):
    if not settings.mt5_bridge_token:
        raise HTTPException(status_code=503, detail="MT5 bridge token is not configured on APEX")
    if not x_apex_mt5_key or not hmac.compare_digest(x_apex_mt5_key, settings.mt5_bridge_token):
        raise HTTPException(status_code=401, detail="Invalid MT5 bridge credential")
    accepted = 0
    closed_trades = []
    for payload in request.ticks:
        tick = normalize_mt5_tick(payload)
        latest_ticks[tick.instrument_key] = tick
        accepted += 1
        for closed in paper.on_tick(tick):
            if trade_store:
                trade_store.save_trade(closed)
            closed_trades.append(closed.id)
    return {"accepted": accepted, "provider": "MT5", "closed_paper_trades": closed_trades}



@app.get("/api/v1/mt5/status")
async def mt5_status():
    ticks = {key.removeprefix("MT5:"): tick for key, tick in latest_ticks.items() if key.startswith("MT5:")}
    return {"bridge_configured": bool(settings.mt5_bridge_token),
            "connected": any(tick.age_seconds <= settings.market_stale_after_seconds for tick in ticks.values()),
            "symbols": {symbol: {"bid": tick.bid, "ask": tick.ask, "last": tick.ltp, "age_seconds": round(tick.age_seconds, 3)} for symbol, tick in ticks.items()},
            "live_order_execution": False}

@app.get("/api/v1/research/agents")
async def research_agents(task: str = "strategy research and backtesting"):
    selected = plan_agents(task)
    return {"catalog_size": len(AGENT_CATALOG), "selected_for_task": len(selected),
            "max_concurrent_specialists": 12,
            "catalog": [{"id": a.agent_id, "domain": a.domain, "specialty": a.specialty} for a in AGENT_CATALOG],
            "selected": [{"id": a.agent_id, "domain": a.domain, "specialty": a.specialty} for a in selected],
            "note": "Role catalogue and deterministic task router; not 120 running LLM instances."}

@app.post("/api/v1/risk/position-size")
async def forex_position_size(request: PositionSizeRequest):
    try:
        result = calculate_position_size(**request.model_dump())
        return position_size_dict(result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.get("/api/v1/research/strategies")
async def list_research_strategies():
    return {"count": len(strategy_catalog()), "strategies": strategy_catalog()}

@app.post("/api/v1/research/strategy/signal")
async def research_strategy_signal(request: StrategySignalRequest):
    try:
        return {"strategy_id": request.strategy_id,
                "signal": generate_signal(request.strategy_id, request.candles),
                "status": "RESEARCH_ONLY_NOT_APPROVED_FOR_EXECUTION"}
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.post("/api/v1/research/strategy/evaluate")
async def evaluate_strategy_candidate(request: StrategyEvaluationRequest):
    metrics = evaluate_strategy(request.in_sample_pnls, request.out_of_sample_pnls, request.initial_equity)
    return metrics_dict(metrics)

@app.post("/api/v1/market/connect")
async def connect_market(request: StreamRequest):
    global market_stream_task, market_stream_error, stream_instruments
    try:
        instruments = OandaForexClient.validate_instruments(request.instruments)
        if market_stream_task and not market_stream_task.done():
            if instruments == stream_instruments:
                return {"status": "already_connected", "instruments": stream_instruments}
            await stop_market_stream()
        # Validate credentials and account before starting a long-lived stream.
        await oanda_client().account_summary()
        market_stream_error = None
        stream_instruments = instruments
        market_stream_task = asyncio.create_task(consume_oanda_stream(instruments))
        return {"status": "connecting", "provider": "OANDA", "environment": "practice",
                "instruments": instruments}
    except OandaProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/market/disconnect")
async def disconnect_market():
    await stop_market_stream()
    return {"status": "disconnected"}


@app.get("/api/v1/market/status")
async def market_status():
    return {
        "provider": "OANDA + MetaTrader 5 bridge",
        "connected": bool(market_stream_task and not market_stream_task.done()) or any(key.startswith("MT5:") and tick.age_seconds <= settings.market_stale_after_seconds for key, tick in latest_ticks.items()),
        "mt5_symbols": [key.removeprefix("MT5:") for key in latest_ticks if key.startswith("MT5:")],
        "instruments": stream_instruments,
        "last_error": market_stream_error,
        "ticks": {key: tick.model_dump(mode="json") for key, tick in latest_ticks.items()},
        "stale_after_seconds": settings.market_stale_after_seconds,
    }


@app.post("/api/v1/backtest/bracket")
async def bracket_backtest(request: BracketBacktestRequest):
    try:
        return run_bracket_backtest(
            candles=request.candles, side=request.side.value, quantity=request.quantity,
            stop_loss=request.stop_loss, target=request.target,
            fee_per_side=request.fee_per_side, slippage=request.slippage,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/paper/trades")
async def create_paper_trade(order: PaperOrderRequest):
    if trade_store is None:
        raise HTTPException(status_code=503, detail="Persistent trade store is not ready")
    tick = latest_ticks.get(order.instrument_key)
    if tick is None or tick.age_seconds > settings.market_stale_after_seconds:
        raise HTTPException(status_code=409, detail="No fresh MT5/OANDA quote for this Forex pair")
    if tick.bid is None or tick.ask is None:
        raise HTTPException(status_code=409, detail="Bid/ask quote is required for Forex paper execution")
    executable_entry = tick.ask if order.side == Side.LONG else tick.bid
    order = order.model_copy(update={"entry_price": executable_entry})
    try:
        trade = paper.open_trade(order)
        trade_store.save_trade(trade)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return trade


@app.post("/api/v1/paper/trades/{trade_id}/close")
async def close_paper_trade(trade_id: str):
    if trade_store is None:
        raise HTTPException(status_code=503, detail="Persistent trade store is not ready")
    trade = next((t for t in paper.open_trades if t.id == trade_id), None)
    if not trade:
        raise HTTPException(status_code=404, detail="Open paper trade not found")
    tick = latest_ticks.get(trade.instrument_key)
    if tick is None or tick.age_seconds > settings.market_stale_after_seconds:
        raise HTTPException(status_code=409, detail="Cannot close against stale/missing Forex quote")
    exit_price = (
        tick.bid if trade.side == Side.LONG and tick.bid else
        tick.ask if trade.side == Side.SHORT and tick.ask else tick.ltp
    )
    closed = paper.close_trade(trade_id, exit_price)
    trade_store.save_trade(closed)
    return closed


@app.get("/api/v1/paper/summary")
async def paper_summary():
    return {
        "mode": "PAPER_ONLY",
        "starting_balance": paper.starting_balance,
        "balance": round(paper.balance, 2),
        "realized_daily_pnl": round(paper.realized_daily_pnl, 2),
        "open_positions": len(paper.open_trades),
        "closed_trades": len([t for t in paper.trades if t.status == "CLOSED"]),
        "live_order_execution": False,
    }


@app.get("/api/v1/paper/trades")
async def list_paper_trades():
    return paper.trades
