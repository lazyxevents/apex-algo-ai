import pytest

from apex_forex.risk_sizing import calculate_position_size


def test_forex_position_size_uses_account_currency_conversion_and_costs():
    result = calculate_position_size(
        risk_budget_account_ccy=100,
        entry_price=1.1050,
        stop_price=1.1000,
        pip_size=0.0001,
        quote_to_account_rate=1.0,
        round_trip_cost_pips=1.5,
    )
    assert result.accepted
    assert result.stop_pips == 50
    assert result.estimated_risk_account_ccy <= 100
    assert result.units > 0


def test_position_size_rejects_too_small_risk_budget():
    result = calculate_position_size(
        risk_budget_account_ccy=0.01,
        entry_price=1.1,
        stop_price=1.09,
        pip_size=0.0001,
        quote_to_account_rate=1.0,
    )
    assert not result.accepted
    assert result.lots == 0


def test_position_size_requires_explicit_conversion_rate():
    with pytest.raises(ValueError):
        calculate_position_size(
            risk_budget_account_ccy=100,
            entry_price=150,
            stop_price=149,
            pip_size=0.01,
            quote_to_account_rate=0,
        )
