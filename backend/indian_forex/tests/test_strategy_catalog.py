from apex_forex.research.strategy_catalog import STRATEGIES, generate_signal, strategy_catalog


def test_strategy_catalog_has_multiple_research_candidates():
    assert len(STRATEGIES) >= 5
    assert all(item.strategy_id for item in STRATEGIES)
    assert all(item["status"] == "RESEARCH_CANDIDATE_NOT_PROMOTED" for item in strategy_catalog())


def test_strategy_signal_waits_for_sufficient_closed_bars():
    assert generate_signal("ema_trend", [{"close": 1.1, "high": 1.2, "low": 1.0}] * 20) is None


def test_unknown_strategy_is_rejected():
    try:
        generate_signal("random_magic", [])
        assert False, "unknown strategy should fail closed"
    except ValueError as exc:
        assert "Unknown strategy" in str(exc)
