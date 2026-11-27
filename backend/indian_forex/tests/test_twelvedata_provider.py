import pytest

from apex_forex.providers.twelvedata import (
    GRANULARITY_MAP,
    TwelveDataForexClient,
    TwelveDataProviderError,
)


def test_twelve_data_requires_api_key():
    client = TwelveDataForexClient("")
    with pytest.raises(TwelveDataProviderError, match="TWELVE_DATA_API_KEY"):
        client._require_key()


def test_twelve_data_symbol_validation_and_normalization():
    assert TwelveDataForexClient.validate_instruments(["eur_usd", "GBP/USD", "EUR/USD"]) == [
        "EUR/USD", "GBP/USD"
    ]
    assert TwelveDataForexClient.normalize_symbol("eur_usd") == "EUR/USD"
    with pytest.raises(ValueError, match="Invalid"):
        TwelveDataForexClient.validate_instruments(["NIFTY"])
    with pytest.raises(ValueError, match="between 1 and 8"):
        TwelveDataForexClient.validate_instruments(["EUR/USD"] * 9)


def test_twelve_data_timeframe_mapping():
    assert GRANULARITY_MAP["M1"] == "1min"
    assert GRANULARITY_MAP["H1"] == "1h"
    assert GRANULARITY_MAP["D"] == "1day"
