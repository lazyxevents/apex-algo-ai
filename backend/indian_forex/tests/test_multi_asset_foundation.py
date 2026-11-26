import pytest
from datetime import datetime, timezone, timedelta

from apex_forex.market.contracts import AssetClass, ProductType, Instrument, Quote, Candle
from apex_forex.market.registry import default_registry, ProviderEnvironment
from apex_forex.intelligence.features import sma, ema, atr
from apex_forex.intelligence.regime import classify_regime, MarketRegime
from apex_forex.execution.safety import validate_execution_mode


def test_instrument_contract_is_provider_neutral_forex_spot():
    item = Instrument(
        instrument_id="OANDA:EUR_USD", provider="OANDA",
        provider_instrument_id="EUR_USD", venue="OTC",
        asset_class=AssetClass.FOREX, product_type=ProductType.SPOT,
        symbol="EUR_USD", base_currency="EUR", quote_currency="USD",
        tick_size=0.0001,
    )
    assert item.asset_class == AssetClass.FOREX
    assert item.product_type == ProductType.SPOT


def test_quote_rejects_crossed_and_naive_timestamps():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="Crossed"):
        Quote(instrument_id="x", event_time=now, bid=1.2, ask=1.1, provider="test")
    with pytest.raises(ValueError, match="timezone-aware"):
        Quote(instrument_id="x", event_time=datetime.now(), bid=1.1, ask=1.2, provider="test")


def test_candle_ohlc_validation():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="high"):
        Candle(instrument_id="x", timeframe="M1", start_time=now,
               end_time=now + timedelta(minutes=1), open=10, high=9,
               low=8, close=9, source="test")


def test_registry_contains_only_forex_providers_and_no_live_approval():
    registry = default_registry()
    assert registry.get("OANDA v20").streaming_quotes
    assert ProviderEnvironment.DEMO in registry.get("Dukascopy JForex/FIX").environments
    assert all(p.asset_classes == frozenset({"FOREX"}) for p in registry.list_all())
    assert all(not p.live_execution_approved for p in registry.list_all())


def test_features_and_regime_baseline():
    assert sma([1, 2, 3, 4], 3) == pytest.approx(3)
    assert ema([1, 2, 3, 4], 3) is not None
    assert atr([10, 11, 12], [9, 10, 11], [9.5, 10.5, 11.5], 2) is not None
    assert classify_regime([1, 2], [2, 3], [0.5, 1.5]) == MarketRegime.INSUFFICIENT_DATA


def test_live_mode_is_fail_closed():
    assert validate_execution_mode("paper").value == "PAPER"
    with pytest.raises(PermissionError, match="not implemented"):
        validate_execution_mode("live")
