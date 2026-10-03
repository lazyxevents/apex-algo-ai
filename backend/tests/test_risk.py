from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import init_db
from app.strategy import ARMS, detect_candlestick_patterns, evaluate_signal, market_context
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
    assert "BULLISH_PIN_BAR" in patterns["bullish"]


def test_market_context_has_key():
    candles = [
        {"timestamp": f"2026-10-01T10:{i%60:02d}:00+05:30", "open": 100+i*0.1, "high": 101+i*0.1, "low": 99+i*0.1, "close": 100.5+i*0.1, "volume": 1000+i}
        for i in range(40)
    ]
    assert "|" in market_context(candles)["key"]
