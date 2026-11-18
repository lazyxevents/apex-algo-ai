from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.intelligence import build_scorecard


def test_scorecard_groups_valid_closed_trades_and_computes_expectancy():
    rows = [
        {"id": 1, "symbol": "NSE_FO|NIFTY", "index": "NIFTY", "side": "CE", "strategy": "BREAKOUT", "regime": "BULLISH_TREND", "pnl": 100.0, "status": "TARGET", "closedAt": None},
        {"id": 2, "symbol": "BSE_FO|SENSEX", "index": "SENSEX", "side": "PE", "strategy": "PULLBACK", "regime": "BEARISH_TREND", "pnl": -40.0, "status": "STOP", "closedAt": None},
        {"id": 3, "symbol": "NSE_FO|NIFTY", "index": "NIFTY", "side": "CE", "strategy": "BREAKOUT", "regime": "BULLISH_TREND", "pnl": 60.0, "status": "TARGET", "closedAt": None},
    ]
    result = build_scorecard(rows)
    assert result["summary"]["trades"] == 3
    assert result["summary"]["netPnl"] == 120.0
    assert result["summary"]["expectancy"] == 40.0
    assert result["summary"]["winRatePct"] == 66.67
    assert result["byIndex"][0]["name"] == "NIFTY"
    assert result["byStrategy"][0]["name"] == "BREAKOUT"
    assert result["dataQuality"]["regimeCoverage"] == 3


def test_scorecard_empty_history_is_safe():
    result = build_scorecard([])
    assert result["summary"]["trades"] == 0
    assert result["summary"]["profitFactor"] == 0.0
    assert result["byIndex"] == []
