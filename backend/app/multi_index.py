from __future__ import annotations

from math import isfinite

from .risk import calculate_underlying_stop_distance


def _number(value, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _frame_trend(frames: dict, frame: str) -> str:
    value = frames.get(frame)
    if isinstance(value, dict):
        value = value.get("trend")
    return str(value or "UNKNOWN").upper()


def assess_index_candidate(
    signal: dict,
    option: dict | None = None,
    *,
    option_required: bool = True,
    option_error: str | None = None,
    synthetic_demo: bool = False,
    stop_buffer_points: float = 2.0,
    max_structure_risk_atr: float = 1.75,
    min_stop_atr: float = 0.55,
    max_previous_range_atr: float = 2.25,
    max_option_structure_risk_pct: float = 18.0,
    max_option_theta_burn_pct: float = 2.0,
    max_option_spread_pct: float = 2.5,
    min_option_volume: int = 1000,
    min_ranking_score: float = 0.56,
) -> dict:
    """Relative cross-index score; never a calibrated probability or execution authority."""
    index = str(signal.get("index") or "UNKNOWN").upper()
    action = str(signal.get("action") or "NO_TRADE").upper()
    context = signal.get("marketContext") or {}
    intelligence = signal.get("marketIntelligence") or {}
    mtf = signal.get("mtfAnalysis") or {}
    frames = mtf.get("frames") or intelligence.get("timeframes") or {}
    freshness = signal.get("dataFreshness") or {}
    volatility = str(context.get("volatility") or intelligence.get("volatility") or "UNKNOWN").upper()
    regime = str(intelligence.get("regime") or "UNKNOWN").upper()
    suitability = str(intelligence.get("tradeSuitability") or "UNKNOWN").upper()
    blockers: list[str] = []

    base = {
        "index": index,
        "action": action,
        "signalScore": round(_clamp(_number(signal.get("score"))), 4),
        "volatility": volatility,
        "regime": regime,
        "tradeSuitability": suitability,
        "timeframes": {
            "1m": _frame_trend(frames, "1m"),
            "5m": _frame_trend(frames, "5m"),
            "15m": _frame_trend(frames, "15m"),
        },
        "patterns": (intelligence.get("patterns") or {}).get("active") or [],
        "newsStatus": (intelligence.get("newsContext") or {}).get("status") or "UNAVAILABLE",
        "rankingScore": None,
        "rankingScorePct": None,
        "optionScore": None,
        "structureStopPoints": None,
        "rawStructureStopPoints": None,
        "minimumAtrStopPoints": None,
        "structureRiskAtr": None,
        "previousRangeAtr": None,
        "previousCandleRangeAtr": None,
        "previousCandleStopAtr": None,
        "previousCandleRangePoints": None,
        "previousCandleStopPoints": None,
        "atrPoints": None,
        "thetaBurnPct": None,
        "structureRiskPct": None,
        "spreadPct": None,
        "premium": None,
        "contract": None,
        "lotSize": None,
        "lotValue": None,
        "delta": None,
        "gamma": None,
        "theta": None,
        "iv": None,
        "filterTier": None,
        "blockers": blockers,
    }

    if action not in {"CE", "PE"}:
        base.update({
            "status": "NO_SETUP",
            "reason": str(signal.get("reason") or "NO_DIRECTIONAL_SETUP"),
            "blockers": [str(x) for x in (signal.get("blockedBy") or [signal.get("reason") or "NO_DIRECTIONAL_SETUP"])],
        })
        return base

    strategy_name = str(signal.get("chosenStrategy") or signal.get("strategy") or "").upper()
    if "SCALP" not in strategy_name:
        blockers.append("SCALPING_ONLY_POLICY")
    if freshness.get("fresh") is not True:
        blockers.append("STALE_OR_UNVERIFIED_CANDLES")
    if volatility == "HIGH" or regime == "HIGH_VOLATILITY":
        blockers.append("HIGH_VOLATILITY_STOP_OUT_RISK")
    if suitability == "AVOID" or regime in {"SIDEWAYS", "DATA_UNAVAILABLE"}:
        blockers.append("UNFAVOURABLE_MARKET_REGIME")

    expected_trend = "UP" if action == "CE" else "DOWN"
    opposite_trend = "DOWN" if action == "CE" else "UP"
    known_trends = [_frame_trend(frames, frame) for frame in ("5m", "15m")]
    known_trends = [trend for trend in known_trends if trend in {"UP", "DOWN", "FLAT"}]
    aligned = sum(1 for trend in known_trends if trend == expected_trend)
    opposed = sum(1 for trend in known_trends if trend == opposite_trend)
    neutral = sum(1 for trend in known_trends if trend == "FLAT")
    trend_alignment = (aligned + neutral * 0.5) / len(known_trends) if known_trends else 0.5
    if len(known_trends) == 2 and opposed == 2:
        blockers.append("BOTH_HIGHER_TIMEFRAMES_OPPOSE_SIGNAL")

    spot = _number(signal.get("underlyingPrice"))
    previous_low = _number(signal.get("previousLow"), spot)
    previous_high = _number(signal.get("previousHigh"), spot)
    stop_buffer = max(0.0, _number(stop_buffer_points))
    atr_pct = _number(context.get("atrPct"))
    atr_points = spot * atr_pct / 100.0 if spot > 0 and atr_pct > 0 else 0.0
    stop_plan = calculate_underlying_stop_distance(
        action, spot, previous_low, previous_high, stop_buffer,
        atr_points, min_stop_atr,
    )
    structure_stop = float(stop_plan["distancePoints"])
    previous_range = max(0.0, previous_high - previous_low)
    structure_risk_atr = structure_stop / atr_points if atr_points > 0 else None
    previous_range_atr = previous_range / atr_points if atr_points > 0 else None
    previous_candle = signal.get("previousCandle") or {}
    previous_candle_range_atr = None
    previous_candle_stop_atr = None
    previous_candle_range = None
    previous_candle_stop = None
    if previous_candle.get("low") is not None and previous_candle.get("high") is not None:
        candle_low = _number(previous_candle.get("low"))
        candle_high = _number(previous_candle.get("high"))
        previous_candle_range = max(0.0, candle_high - candle_low)
        previous_candle_stop = (
            max(0.0, spot - candle_low + stop_buffer)
            if action == "CE" else max(0.0, candle_high - spot + stop_buffer)
        )
        if atr_points > 0:
            previous_candle_range_atr = previous_candle_range / atr_points
            previous_candle_stop_atr = previous_candle_stop / atr_points
        if previous_candle_range_atr is not None and previous_candle_range_atr > max_previous_range_atr:
            blockers.append("PREVIOUS_CANDLE_RANGE_TOO_WIDE")
        if previous_candle_stop_atr is not None and previous_candle_stop_atr > max_structure_risk_atr:
            blockers.append("PREVIOUS_CANDLE_STOP_TOO_WIDE")
    if structure_risk_atr is not None and structure_risk_atr > max_structure_risk_atr:
        blockers.append("PREVIOUS_STRUCTURE_STOP_TOO_WIDE")
    if previous_range_atr is not None and previous_range_atr > max_previous_range_atr:
        blockers.append("RECENT_CANDLE_RANGE_TOO_WIDE")

    base.update({
        "trendAlignment": round(trend_alignment, 4),
        "structureStopPoints": round(structure_stop, 2),
        "rawStructureStopPoints": round(stop_plan["structuralPoints"], 2),
        "minimumAtrStopPoints": round(stop_plan["atrFloorPoints"], 2),
        "previousRangePoints": round(previous_range, 2),
        "atrPoints": round(atr_points, 2) if atr_points > 0 else None,
        "structureRiskAtr": round(structure_risk_atr, 3) if structure_risk_atr is not None else None,
        "previousRangeAtr": round(previous_range_atr, 3) if previous_range_atr is not None else None,
        "previousCandleRangeAtr": round(previous_candle_range_atr, 3) if previous_candle_range_atr is not None else None,
        "previousCandleStopAtr": round(previous_candle_stop_atr, 3) if previous_candle_stop_atr is not None else None,
        "previousCandleRangePoints": round(previous_candle_range, 2) if previous_candle_range is not None else None,
        "previousCandleStopPoints": round(previous_candle_stop, 2) if previous_candle_stop is not None else None,
    })

    if blockers:
        base.update({"status": "BLOCKED", "reason": blockers[0], "blockers": blockers})
        return base

    if not option_required:
        base.update({"status": "SIGNAL_READY", "reason": "SIGNAL_PASSED_MARKET_RISK_PREFLIGHT"})
        return base

    if not option:
        reason = str(option_error or "NO_LIQUID_AFFORDABLE_OPTION_CONTRACT")
        base.update({"status": "OPTION_BLOCKED", "reason": reason, "blockers": [reason]})
        return base

    structure_risk_pct = _number(option.get("structureRiskPct"))
    theta_burn_pct = _number(option.get("thetaBurnPct"))
    spread_pct = _number(option.get("spreadPct"), 999.0)
    volume = max(0, int(_number(option.get("volume"))))
    option_score = 0.5 if synthetic_demo else _clamp(_number(option.get("selectionScore")))
    if structure_risk_pct > max_option_structure_risk_pct:
        blockers.append("OPTION_STRUCTURE_RISK_TOO_HIGH")
    if theta_burn_pct > max_option_theta_burn_pct * 1.5:
        blockers.append("OPTION_THETA_DECAY_TOO_HIGH")
    if not synthetic_demo and spread_pct > max_option_spread_pct and str(option.get("filterTier") or "").upper() != "PAPER_FALLBACK":
        blockers.append("OPTION_SPREAD_TOO_WIDE")

    base.update({
        "optionScore": round(option_score, 4),
        "thetaBurnPct": round(theta_burn_pct, 4),
        "structureRiskPct": round(structure_risk_pct, 3),
        "spreadPct": round(spread_pct, 3) if not synthetic_demo else None,
        "premium": round(_number(option.get("ltp")), 2),
        "contract": str(option.get("displayName") or option.get("instrumentKey") or "Synthetic demo contract"),
        "delta": _number(option.get("delta")) if option.get("delta") is not None else None,
        "gamma": _number(option.get("gamma")) if option.get("gamma") is not None else None,
        "theta": _number(option.get("theta")) if option.get("theta") is not None else None,
        "iv": _number(option.get("iv")) if option.get("iv") is not None else None,
        "filterTier": str(option.get("filterTier") or ("SYNTHETIC_DEMO" if synthetic_demo else "UNKNOWN")),
    })
    if blockers:
        base.update({"status": "BLOCKED", "reason": blockers[0], "blockers": blockers})
        return base

    volatility_score = {"LOW": 1.0, "NORMAL": 0.85, "UNKNOWN": 0.55}.get(volatility, 0.55)
    structure_score = (
        _clamp(1.0 - structure_risk_atr / max(max_structure_risk_atr, 0.01))
        if structure_risk_atr is not None else 0.5
    )
    range_score = (
        _clamp(1.0 - previous_range_atr / max(max_previous_range_atr, 0.01))
        if previous_range_atr is not None else 0.5
    )
    theta_score = _clamp(1.0 - theta_burn_pct / max(max_option_theta_burn_pct, 0.1))
    spread_score = 0.5 if synthetic_demo else _clamp(1.0 - spread_pct / max(max_option_spread_pct, 0.1))
    liquidity_score = 0.5 if synthetic_demo else _clamp(volume / max(min_option_volume * 3, 1))
    score = (
        0.17 * base["signalScore"]
        + 0.18 * trend_alignment
        + 0.10 * volatility_score
        + 0.15 * structure_score
        + 0.08 * range_score
        + 0.18 * option_score
        + 0.07 * theta_score
        + 0.05 * spread_score
        + 0.02 * liquidity_score
    )
    if str(option.get("filterTier") or "").upper() == "PAPER_FALLBACK":
        score -= 0.05
    if synthetic_demo:
        score -= 0.03
    score = _clamp(score)
    base.update({
        "rankingScore": round(score, 4),
        "rankingScorePct": round(score * 100, 1),
        "status": "READY" if score >= min_ranking_score else "BLOCKED",
        "reason": "BEST_RISK_ADJUSTED_INDEX_CANDIDATE" if score >= min_ranking_score else "RANKING_SCORE_BELOW_THRESHOLD",
        "blockers": [] if score >= min_ranking_score else ["RANKING_SCORE_BELOW_THRESHOLD"],
    })
    return base



def build_multi_index_selection(engine, provider, ranked: list[dict], capital: float, settings, updated_at) -> tuple[dict, dict | None]:
    """Fetch eligible option chains and return one risk-adjusted PAPER candidate."""
    deployable = max(0.0, capital * min(90.0, max(0.0, settings.capital_usage_pct)) / 100)
    synthetic_demo = not bool(getattr(provider, "supports_option_chain", True))
    candidate_rows: list[dict] = []
    executable: list[dict] = []

    # Candle/SMC/MTF agents already ran concurrently. Keep option-chain requests
    # sequential so the broker does not receive a burst of parallel chain calls.
    ordered = sorted(ranked, key=lambda row: float(row.get("score") or 0), reverse=True)
    for signal in ordered:
        kwargs = {
            "stop_buffer_points": settings.paper_stop_buffer_points,
            "max_structure_risk_atr": settings.multi_index_max_structure_risk_atr,
            "min_stop_atr": settings.multi_index_min_stop_atr,
            "max_previous_range_atr": settings.multi_index_max_previous_range_atr,
            "max_option_structure_risk_pct": settings.max_option_structure_risk_pct,
            "max_option_theta_burn_pct": settings.max_option_theta_burn_pct,
            "max_option_spread_pct": settings.max_option_spread_pct,
            "min_option_volume": settings.min_option_volume,
            "min_ranking_score": settings.multi_index_min_ranking_score,
        }
        preflight = assess_index_candidate(signal, option_required=False, **kwargs)
        if preflight.get("status") != "SIGNAL_READY":
            candidate_rows.append(preflight)
            continue

        option = None
        lot_size = 0
        expiry = None
        diagnostics: dict = {}
        option_error = None
        try:
            if synthetic_demo:
                option = provider.synthetic_option_candidate(
                    signal["index"], signal["underlyingKey"],
                    signal["action"], signal["underlyingPrice"],
                )
                lot_size = int(option.get("lotSize") or 1)
                expiry = option.get("expiry")
            else:
                option, lot_size, expiry = engine._real_option_candidate(provider, signal, deployable)
                diagnostics = dict(getattr(engine, "_last_option_filter_diagnostics", {}) or {})
        except Exception as exc:
            option_error = f"OPTION_CHAIN_ERROR: {str(exc)[:180]}"
            diagnostics = {
                "reason": "OPTION_CHAIN_ERROR", "error": str(exc)[:240],
                "index": signal.get("index"), "direction": signal.get("action"),
            }

        if not option or lot_size <= 0:
            if option_error is None:
                option_error = str(diagnostics.get("reason") or "NO_LIQUID_AFFORDABLE_OPTION_CONTRACT")
            assessment = assess_index_candidate(
                signal, option_required=True, option_error=option_error,
                synthetic_demo=synthetic_demo, **kwargs,
            )
            assessment["diagnostics"] = diagnostics
            candidate_rows.append(assessment)
            continue

        assessment = assess_index_candidate(
            signal, option, option_required=True, synthetic_demo=synthetic_demo, **kwargs,
        )
        exchange = "BSE" if str(signal.get("index") or "").upper() == "SENSEX" else "NSE"
        assessment.update({
            "exchange": exchange,
            "expiry": expiry or option.get("expiry"),
            "strike": option.get("strike"),
            "moneyness": option.get("moneyness"),
            "lotSize": int(lot_size),
            "lotValue": round(float(option.get("ltp") or 0) * int(lot_size), 2),
            "bid": option.get("bid"),
            "ask": option.get("ask"),
            "oi": option.get("oi"),
            "diagnostics": diagnostics,
            "quoteTime": option.get("quoteTime") or updated_at.isoformat(),
            "priceSource": option.get("priceSource") or ("synthetic_estimate" if synthetic_demo else "exchange_option_chain"),
        })
        candidate_rows.append(assessment)
        if assessment.get("status") == "READY":
            executable.append({
                "signal": signal, "option": option, "lotSize": int(lot_size),
                "expiry": expiry or option.get("expiry"), "syntheticDemo": synthetic_demo,
                "assessment": assessment,
            })

    candidate_rows.sort(
        key=lambda row: (
            row.get("status") == "READY",
            float(row.get("rankingScore") or 0),
            float(row.get("signalScore") or 0),
        ),
        reverse=True,
    )
    selected = max(
        executable, key=lambda row: float(row["assessment"].get("rankingScore") or 0)
    ) if executable else None
    selection = {
        "status": "SELECTED" if selected else "NO_ELIGIBLE_INDEX",
        "selectedIndex": selected["signal"].get("index") if selected else None,
        "selectedDirection": selected["signal"].get("action") if selected else None,
        "selectedContract": selected["assessment"].get("contract") if selected else None,
        "rankingScore": selected["assessment"].get("rankingScore") if selected else None,
        "rankingScorePct": selected["assessment"].get("rankingScorePct") if selected else None,
        "analyzedIndices": [str(row.get("index") or "UNKNOWN") for row in ranked],
        "agentMode": "PARALLEL_CANDLE_ANALYSIS_CENTRAL_RISK_RANKING",
        "optionChainMode": "SEQUENTIAL_RATE_LIMIT_SAFE" if not synthetic_demo else "SYNTHETIC_DEMO",
        "candidates": candidate_rows,
        "reason": (
            f"{selected['signal'].get('index')} {selected['signal'].get('action')} selected by risk-adjusted cross-index ranking."
            if selected else
            "No index passed fresh-candle, market-regime, structure-risk, option-liquidity and capital filters."
        ),
        "updatedAt": updated_at.isoformat(),
    }
    return selection, selected
