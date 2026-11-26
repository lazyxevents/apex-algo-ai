import pytest
from apex_forex.domain import PaperOrderRequest, Side, MarketTick
from apex_forex.paper import PaperBroker
from apex_forex.risk import RiskEngine


def make_broker():
    risk = RiskEngine(max_risk_per_trade=500, max_daily_loss=1000, max_open_positions=1)
    return PaperBroker(100_000, risk, slippage_ticks=1, fee_per_order=10)


def test_long_trade_opens_with_adverse_slippage():
    broker = make_broker()
    order = PaperOrderRequest(
        instrument_key="NCD_FO|test", side=Side.LONG, quantity=2,
        entry_price=100, stop_loss=95, target=110, tick_size=0.05,
    )
    trade = broker.open_trade(order)
    assert trade.entry_price == pytest.approx(100.05)
    assert trade.status == "OPEN"


def test_risk_limit_rejects_oversized_trade():
    broker = make_broker()
    order = PaperOrderRequest(
        instrument_key="NCD_FO|test", side=Side.LONG, quantity=200,
        entry_price=100, stop_loss=95, target=110, tick_size=0.05,
    )
    with pytest.raises(ValueError, match="risk"):
        broker.open_trade(order)


def test_long_stop_closes_trade_and_books_costs():
    broker = make_broker()
    order = PaperOrderRequest(
        instrument_key="NCD_FO|test", side=Side.LONG, quantity=1,
        entry_price=100, stop_loss=95, target=110, tick_size=0.05,
    )
    trade = broker.open_trade(order)
    broker.on_tick(MarketTick(instrument_key="NCD_FO|test", ltp=94.9))
    assert trade.status == "CLOSED"
    assert trade.net_pnl < 0
    assert broker.balance < broker.starting_balance
