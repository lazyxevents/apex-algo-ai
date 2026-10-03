from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import init_db
from app.strategy import ARMS, evaluate_signal
from app.trading import TradingEngine


def test_dynamic_quantity_is_whole_lot():
    init_db()
    engine = TradingEngine()
    qty = engine._quantity_for_risk(entry=100, stop=98, lot_size=25)
    assert qty % 25 == 0
    assert qty == 100


def test_strategy_can_return_no_trade():
    candles = [
        {"timestamp": str(i), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}
        for i in range(60)
    ]
    result = evaluate_signal(candles, ARMS[0])
    assert result["action"] == "NO_TRADE"
