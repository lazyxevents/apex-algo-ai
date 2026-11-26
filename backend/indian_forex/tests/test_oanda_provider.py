import pytest

from apex_forex.providers.oanda import OandaForexClient, OandaProviderError


def test_oanda_defaults_to_practice_and_never_accepts_live():
    client = OandaForexClient("token", "account")
    assert client.PRACTICE_REST == "https://api-fxpractice.oanda.com"
    with pytest.raises(OandaProviderError, match="practice"):
        OandaForexClient("token", "account", environment="live")


def test_oanda_requires_token_and_account():
    client = OandaForexClient("")
    with pytest.raises(OandaProviderError, match="OANDA_ACCESS_TOKEN"):
        client._headers()
    with pytest.raises(OandaProviderError, match="OANDA_ACCOUNT_ID"):
        client._account()


def test_oanda_instrument_validation_is_dynamic_and_deduplicates():
    assert OandaForexClient.validate_instruments(["eur_usd", "GBP_USD", "EUR_USD"]) == [
        "EUR_USD", "GBP_USD"
    ]
    with pytest.raises(ValueError, match="Invalid"):
        OandaForexClient.validate_instruments(["NIFTY"])
    with pytest.raises(ValueError):
        OandaForexClient.validate_instruments([])


def test_oanda_candle_validation():
    client = OandaForexClient("token", "account")
    with pytest.raises(ValueError, match="granularity"):
        import asyncio
        asyncio.run(client.candles("EUR_USD", "2MIN", 100))
    with pytest.raises(ValueError, match="count"):
        import asyncio
        asyncio.run(client.candles("EUR_USD", "M5", 6000))


def test_oanda_normalizes_bid_ask_price_event():
    event = {
        "type": "PRICE",
        "instrument": "EUR_USD",
        "time": "2026-11-26T08:30:00.000000000Z",
        "bids": [{"price": "1.15000", "liquidity": 1000000}],
        "asks": [{"price": "1.15020", "liquidity": 1000000}],
        "tradeable": True,
    }
    normalized = OandaForexClient.normalize_price(event)
    assert normalized["instrument"] == "EUR_USD"
    assert normalized["bid"] == 1.15
    assert normalized["ask"] == 1.1502
    assert normalized["mid"] == pytest.approx(1.1501)
    assert normalized["tradeable"] is True


def test_oanda_rejects_crossed_quotes():
    event = {
        "type": "PRICE", "instrument": "EUR_USD",
        "time": "2026-11-26T08:30:00Z",
        "bids": [{"price": "1.2"}], "asks": [{"price": "1.1"}],
    }
    assert OandaForexClient.normalize_price(event) is None
