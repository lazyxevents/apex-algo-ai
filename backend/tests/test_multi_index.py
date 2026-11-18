from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.multi_index import assess_index_candidate


def _signal(index="NIFTY", action="CE", volatility="NORMAL", atr_pct=0.20,
            previous_low=9990, previous_high=10005):
    return {
        "index": index,
        "action": action,
        "score": 0.70,
        "chosenStrategy": "APEX_EMA9_21_PULLBACK_SCALP",
        "underlyingPrice": 10000,
        "previousLow": previous_low,
        "previousHigh": previous_high,
        "dataFreshness": {"fresh": True, "state": "FRESH"},
        "marketContext": {"volatility": volatility, "atrPct": atr_pct},
        "marketIntelligence": {
            "regime": "BULLISH_TREND" if action == "CE" else "BEARISH_TREND",
            "tradeSuitability": "FAVOURABLE",
            "timeframes": {"1m": "UP" if action == "CE" else "DOWN",
                           "5m": "UP" if action == "CE" else "DOWN",
                           "15m": "UP" if action == "CE" else "DOWN"},
            "patterns": {"active": ["BULLISH_ENGULFING"]},
            "newsContext": {"status": "FRESH"},
        },
        "mtfAnalysis": {
            "frames": {
                "5m": {"trend": "UP" if action == "CE" else "DOWN"},
                "15m": {"trend": "UP" if action == "CE" else "DOWN"},
            },
        },
    }


def _option(selection_score=0.8, structure_risk=8, theta_burn=0.5,
            spread=0.5, volume=6000, tier="STRICT"):
    return {
        "instrumentKey": "NSE_FO|TEST",
        "displayName": "TEST INDEX CE",
        "ltp": 100,
        "selectionScore": selection_score,
        "structureRiskPct": structure_risk,
        "thetaBurnPct": theta_burn,
        "spreadPct": spread,
        "volume": volume,
        "delta": 0.45,
        "gamma": 0.001,
        "theta": -1,
        "iv": 14,
        "filterTier": tier,
    }


def test_multi_index_candidate_passes_market_and_option_gates():
    result = assess_index_candidate(_signal(), _option(), min_ranking_score=0.55)
    assert result["status"] == "READY"
    assert result["rankingScore"] > 0.55
    assert result["index"] == "NIFTY"


def test_multi_index_rejects_high_volatility_before_option_selection():
    result = assess_index_candidate(_signal(volatility="HIGH"), option_required=False)
    assert result["status"] == "BLOCKED"
    assert "HIGH_VOLATILITY_STOP_OUT_RISK" in result["blockers"]


def test_multi_index_rejects_wide_previous_structure_stop():
    result = assess_index_candidate(
        _signal(previous_low=9960, previous_high=10005),
        option_required=False,
        max_structure_risk_atr=1.75,
    )
    assert result["status"] == "BLOCKED"
    assert "PREVIOUS_STRUCTURE_STOP_TOO_WIDE" in result["blockers"]


def test_multi_index_rejects_both_higher_timeframes_opposing_signal():
    signal = _signal()
    signal["mtfAnalysis"]["frames"]["5m"]["trend"] = "DOWN"
    signal["mtfAnalysis"]["frames"]["15m"]["trend"] = "DOWN"
    result = assess_index_candidate(signal, option_required=False)
    assert result["status"] == "BLOCKED"
    assert "BOTH_HIGHER_TIMEFRAMES_OPPOSE_SIGNAL" in result["blockers"]


def test_safer_option_candidate_ranks_above_high_decay_contract():
    signal = _signal()
    safer = assess_index_candidate(signal, _option(selection_score=0.82, structure_risk=5, theta_burn=0.3, spread=0.4, volume=9000))
    risky = assess_index_candidate(signal, _option(selection_score=0.65, structure_risk=16, theta_burn=2.5, spread=2.4, volume=1200, tier="PAPER_FALLBACK"))
    assert safer["status"] == "READY"
    assert risky["status"] in {"READY", "BLOCKED"}
    assert safer["rankingScore"] > (risky["rankingScore"] or 0)
