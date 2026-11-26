from apex_forex.backtest import run_bracket_backtest
from apex_forex.domain import PaperOrderRequest, PaperTrade, Side
from apex_forex.paper import PaperBroker
from apex_forex.risk import RiskEngine
from apex_forex.storage import PaperTradeStore


def make_broker():
    risk = RiskEngine(500, 1000, 2)
    return PaperBroker(10_000, risk, slippage_ticks=0, fee_per_order=2)


def make_trade():
    return make_broker().open_trade(PaperOrderRequest(
        instrument_key="OANDA:EUR_USD", side=Side.LONG, quantity=1,
        entry_price=1.15, stop_loss=1.145, target=1.16, tick_size=0.0001,
    ))


def test_sqlite_trade_ledger_survives_store_reopen(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'apex.db'}"
    store = PaperTradeStore(database_url)
    trade = make_trade()
    store.save_trade(trade)
    store.close()
    reopened = PaperTradeStore(database_url)
    restored = [PaperTrade.model_validate_json(payload) for payload in reopened.load_trades()]
    assert len(restored) == 1
    assert restored[0].id == trade.id
    assert restored[0].status == "OPEN"
    reopened.close()


def test_paper_broker_restores_balance_and_open_positions():
    broker = make_broker()
    trade = make_trade()
    broker.restore_trades([trade])
    assert broker.open_trades[0].id == trade.id
    assert broker.balance == 10_000


def test_backtest_is_conservative_when_stop_and_target_touch_same_bar():
    trades = run_bracket_backtest(
        [
            {"open": 1.15, "high": 1.151, "low": 1.149, "close": 1.15},
            {"open": 1.15, "high": 1.162, "low": 1.144, "close": 1.155},
        ],
        side="LONG", quantity=2, stop_loss=1.145, target=1.16,
        fee_per_side=0.00002, slippage=0.00005,
    )
    assert len(trades) == 1
    assert trades[0].exit_reason == "STOP_LOSS"
    assert trades[0].net_pnl < 0
