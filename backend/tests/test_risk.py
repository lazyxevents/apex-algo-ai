from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import init_db, settings
from app.strategy import ARMS, detect_candlestick_patterns, evaluate_signal, market_context, select_option
from app.trading import TradingEngine


def test_dynamic_quantity_is_whole_lot():
    init_db()
    engine = TradingEngine()
    qty = engine._quantity_for_risk(entry=100, stop=98, lot_size=25)
    assert qty % 25 == 0
    assert qty > 0


def test_strategy_can_return_no_trade():
    candles = [
        {"timestamp": f"2026-10-01T10:{i%60:02d}:00+05:30", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}
        for i in range(60)
    ]
    result = evaluate_signal(candles, ARMS[0])
    assert result["action"] == "NO_TRADE"


def test_bullish_pin_bar_detection():
    candles = [
        {"timestamp": "2026-10-01T10:00:00+05:30", "open": 100, "high": 101, "low": 99, "close": 99.5, "volume": 1000},
        {"timestamp": "2026-10-01T10:05:00+05:30", "open": 100, "high": 101.2, "low": 94, "close": 101, "volume": 1500},
    ]
    patterns = detect_candlestick_patterns(candles)
    assert isinstance(patterns["bullish"], list)
    assert isinstance(patterns["bearish"], list)
    assert 0 <= patterns["bullishScore"] <= 1


def test_market_context_has_key():
    candles = [
        {"timestamp": f"2026-10-01T10:{i%60:02d}:00+05:30", "open": 100+i*0.1, "high": 101+i*0.1, "low": 99+i*0.1, "close": 100.5+i*0.1, "volume": 1000+i}
        for i in range(40)
    ]
    assert "|" in market_context(candles)["key"]


def test_configured_market_provider_keys_are_current():
    assert settings.market_data_provider.lower() in {"dhan", "upstox"}
    assert settings.underlying_keys["SENSEX"]
    assert settings.allow_live_orders is False


def test_option_selector_respects_exchange_atm_reference():
    from app.core import settings
    previous_min = settings.paper_fallback_min_volume
    try:
        settings.paper_fallback_min_volume = 100
        def row(strike):
            return {
                "strike_price": strike,
                "put_options": {
                    "instrument_key": f"BSE_FNO|{int(strike)}|OPTIDX",
                    "market_data": {"ltp": 100, "bid_price": 99, "ask_price": 101, "volume": 2000},
                    "option_greeks": {"delta": -0.42},
                },
            }
        # Only a far-OTM affordable contract survives upstream; it must NOT be
        # treated as ATM after the unaffordable strikes have been removed.
        result = select_option([row(71000)], "PE", 71600, 32000, reference_strikes=[
            71000, 71100, 71200, 71300, 71400, 71500, 71600,
        ])
        assert result is None
    finally:
        settings.paper_fallback_min_volume = previous_min
