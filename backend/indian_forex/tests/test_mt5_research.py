from apex_forex.market.mt5_bridge import MT5TickBatch, normalize_mt5_tick
from apex_forex.research.agents import AGENT_CATALOG, plan_agents
from apex_forex.research.strategy_lab import evaluate_strategy


def test_mt5_tick_normalization_preserves_symbol_and_bid_ask():
    tick = normalize_mt5_tick(MT5TickBatch(
        symbol="EURUSD.a", bid=1.1, ask=1.1002, last=0, time_msc=1_700_000_000_000,
    ))
    assert tick.instrument_key == "MT5:EURUSD.A"
    assert tick.bid == 1.1
    assert tick.ask == 1.1002
    assert tick.ltp == 1.1001


def test_mt5_rejects_crossed_quotes():
    payload = MT5TickBatch(symbol="EURUSD", bid=1.101, ask=1.1, time_msc=1_700_000_000_000)
    try:
        normalize_mt5_tick(payload)
        assert False, "crossed quote should fail"
    except ValueError as exc:
        assert "crossed" in str(exc).lower()


def test_specialist_catalog_has_more_than_100_roles_and_task_router_selects():
    assert len(AGENT_CATALOG) >= 100
    chosen = plan_agents("run walk forward backtest and portfolio risk validation")
    assert chosen
    assert len(chosen) <= 12
    assert any(agent.domain == "backtesting" for agent in chosen)


def test_strategy_gate_rejects_small_sample_even_with_high_win_rate():
    metrics = evaluate_strategy(
        [10.0] * 9 + [-1.0], [5.0] * 4,
        10_000,
    )
    assert metrics.win_rate_pct == 90
    assert not metrics.eligible
    assert any("in-sample trades" in reason for reason in metrics.rejection_reasons)


def test_strategy_gate_requires_positive_out_of_sample_expectancy():
    metrics = evaluate_strategy(
        [10.0] * 100, [-5.0] * 30,
        10_000,
    )
    assert not metrics.eligible
    assert metrics.out_of_sample_expectancy < 0
    assert any("out-of-sample" in reason for reason in metrics.rejection_reasons)
