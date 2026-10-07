import json
import random
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .core import SessionLocal, settings
from .models import LearningRewardEvent, ResearchRun, StrategyState, Trade
from .market_research import _patterns as research_patterns, analyze_structure

IST = ZoneInfo(settings.timezone)


@dataclass(frozen=True)
class StrategyArm:
    name: str
    ema_fast: int
    ema_slow: int
    rsi_bull: float
    rsi_bear: float
    breakout_lookback: int
    volume_ratio: float
    min_score: float
    style: str = "breakout"


ARMS = [
    StrategyArm("APEX_BREAKOUT_BALANCED", 9, 21, 56, 44, 15, 1.05, settings.signal_min_score),
    StrategyArm("APEX_BREAKOUT_FAST", 8, 18, 58, 42, 12, 1.10, min(0.82, settings.signal_min_score + 0.03)),
    StrategyArm("APEX_BREAKOUT_STABLE", 12, 26, 55, 45, 20, 1.00, max(0.55, settings.signal_min_score - 0.02)),
    StrategyArm("APEX_SCALP_MOMENTUM", 5, 13, 60, 40, 8, 1.20, max(0.72, settings.signal_min_score + 0.05), "scalp"),
]


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return values[-1]
    alpha = 2 / (period + 1)
    out = sum(values[:period]) / period
    for value in values[period:]:
        out = alpha * value + (1 - alpha) * out
    return out


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    recent = changes[-period:]
    gains = sum(max(x, 0) for x in recent) / period
    losses = sum(max(-x, 0) for x in recent) / period
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - (100 / (1 + rs))


def _atr(candles: list[dict], period: int = 14) -> float:
    if len(candles) <= period:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(candles)):
        high, low = float(candles[i]["high"]), float(candles[i]["low"])
        prev_close = float(candles[i - 1]["close"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs[-period:]) / period


def detect_candlestick_patterns(candles: list[dict]) -> dict:
    raw = research_patterns(candles)
    bullish = raw.get("bullish") or []
    bearish = raw.get("bearish") or []
    # Strong multi-candle / breakout patterns carry more live-scoring weight.
    strong = {
        "BULLISH_MARUBOZU", "BEARISH_MARUBOZU", "THREE_WHITE_SOLDIERS", "THREE_BLACK_CROWS",
        "BULLISH_ENGULFING", "BEARISH_ENGULFING", "BREAKOUT_CLOSE", "BREAKDOWN_CLOSE",
        "MORNING_STAR", "EVENING_STAR",
    }
    def score(rows: list[str]) -> float:
        value = 0.0
        for name in rows:
            value += 0.38 if name in strong else 0.24
        return min(1.0, value)
    return {
        "bullishScore": round(score(bullish), 4),
        "bearishScore": round(score(bearish), 4),
        "bullish": bullish,
        "bearish": bearish,
        "neutral": raw.get("neutral") or [],
    }

def market_context(candles: list[dict]) -> dict:
    if len(candles) < 22:
        return {"key": "UNKNOWN", "trend": "UNKNOWN", "volatility": "UNKNOWN", "timeBucket": "UNKNOWN"}
    closes = [float(c["close"]) for c in candles]
    fast, slow = _ema(closes, 9), _ema(closes, 21)
    atr = _atr(candles)
    close = max(closes[-1], 1e-9)
    trend_gap = (fast - slow) / close * 100
    recent_up = len(closes) >= 6 and closes[-1] > closes[-6] and float(candles[-1]["low"]) > float(candles[-4]["low"])
    recent_down = len(closes) >= 6 and closes[-1] < closes[-6] and float(candles[-1]["high"]) < float(candles[-4]["high"])
    if trend_gap > 0.04 or (trend_gap > -0.02 and recent_up):
        trend = "UP"
    elif trend_gap < -0.04 or (trend_gap < 0.02 and recent_down):
        trend = "DOWN"
    else:
        trend = "FLAT"
    atr_pct = atr / close * 100
    volatility = "HIGH" if atr_pct >= 0.35 else "LOW" if atr_pct <= 0.10 else "NORMAL"
    try:
        ts = datetime.fromisoformat(str(candles[-1]["timestamp"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        hm = ts.astimezone(IST).strftime("%H:%M")
        time_bucket = "OPEN" if hm < "10:30" else "MID" if hm < "14:00" else "LATE"
    except Exception:
        time_bucket = "UNKNOWN"
    patterns = detect_candlestick_patterns(candles)
    bias = "BULL" if patterns["bullishScore"] > patterns["bearishScore"] else "BEAR" if patterns["bearishScore"] > patterns["bullishScore"] else "NONE"
    return {
        "key": f"{trend}|{volatility}|{time_bucket}|{bias}",
        "trend": trend,
        "volatility": volatility,
        "timeBucket": time_bucket,
        "patternBias": bias,
        "atrPct": round(atr_pct, 4),
    }


def eligible_arms(capital: float, context: dict) -> list[StrategyArm]:
    arms = []
    for arm in ARMS:
        if arm.style == "scalp":
            if not settings.allow_scalp_strategy or capital < settings.min_capital_for_scalp:
                continue
            if (
                context.get("volatility") == "LOW"
                and context.get("trend") == "FLAT"
                and context.get("patternBias") in {None, "NONE"}
            ):
                continue
        arms.append(arm)
    return arms or [ARMS[0]]


def evaluate_signal(candles: list[dict], arm: StrategyArm) -> dict:
    minimum = max(arm.ema_slow + 5, arm.breakout_lookback + 3, 30)
    if len(candles) < minimum:
        return {"action": "NO_TRADE", "score": 0.0, "reason": f"need {minimum} candles", "strategy": arm.name}

    candles = sorted(candles, key=lambda x: x["timestamp"])
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    volumes = [float(c.get("volume", 0) or 0) for c in candles]
    close = closes[-1]
    fast, slow = _ema(closes, arm.ema_fast), _ema(closes, arm.ema_slow)
    rsi = _rsi(closes)
    atr = _atr(candles)
    atr_pct = (atr / close * 100) if close else 0
    ref_high = max(highs[-arm.breakout_lookback - 1:-1])
    ref_low = min(lows[-arm.breakout_lookback - 1:-1])
    volume_base = sum(volumes[-11:-1]) / max(1, len(volumes[-11:-1]))
    volume_ratio = volumes[-1] / volume_base if volume_base > 0 else 1.0
    patterns = detect_candlestick_patterns(candles)
    smc = analyze_structure(candles)
    volatility_ok = 0.04 <= atr_pct <= 2.5

    bull_smc = {
        "BOS": smc.get("bos") == "BULL",
        "CHOCH": smc.get("choch") == "BULL",
        "LIQUIDITY_SWEEP": smc.get("liquiditySweep") == "BULL",
    }
    bear_smc = {
        "BOS": smc.get("bos") == "BEAR",
        "CHOCH": smc.get("choch") == "BEAR",
        "LIQUIDITY_SWEEP": smc.get("liquiditySweep") == "BEAR",
    }
    strong_bull_smc = (
        bull_smc["CHOCH"]
        or bull_smc["LIQUIDITY_SWEEP"]
        or (bull_smc["BOS"] and patterns["bullishScore"] > 0)
    )
    strong_bear_smc = (
        bear_smc["CHOCH"]
        or bear_smc["LIQUIDITY_SWEEP"]
        or (bear_smc["BOS"] and patterns["bearishScore"] > 0)
    )

    bull = 0.0
    bull += 0.25 if fast > slow else 0
    bull += 0.15 if rsi >= arm.rsi_bull else 0
    bull += 0.20 if close > ref_high else 0
    bull += 0.10 if volume_ratio >= arm.volume_ratio else 0
    bull += 0.20 * patterns["bullishScore"]
    bull += 0.10 if volatility_ok else 0

    bear = 0.0
    bear += 0.25 if fast < slow else 0
    bear += 0.15 if rsi <= arm.rsi_bear else 0
    bear += 0.20 if close < ref_low else 0
    bear += 0.10 if volume_ratio >= arm.volume_ratio else 0
    bear += 0.20 * patterns["bearishScore"]
    bear += 0.10 if volatility_ok else 0

    action, score = "NO_TRADE", max(bull, bear)
    entry_reason = "NO_TRADE"
    entry_threshold = arm.min_score
    smc_override = False

    if score >= arm.min_score:
        if bull > bear:
            if not settings.candle_confirmation_required or patterns["bullishScore"] > 0:
                action, score = "CE", bull
                entry_reason = "STANDARD_SIGNAL"
        elif bear > bull:
            if not settings.candle_confirmation_required or patterns["bearishScore"] > 0:
                action, score = "PE", bear
                entry_reason = "STANDARD_SIGNAL"

    if action == "NO_TRADE" and score >= settings.smc_override_min_score:
        if bull > bear and strong_bull_smc:
            action, score = "CE", bull
            entry_reason = "SMC_OVERRIDE"
            entry_threshold = settings.smc_override_min_score
            smc_override = True
        elif bear > bull and strong_bear_smc:
            action, score = "PE", bear
            entry_reason = "SMC_OVERRIDE"
            entry_threshold = settings.smc_override_min_score
            smc_override = True

    return {
        "action": action,
        "score": round(score, 4),
        "underlyingPrice": close,
        "emaFast": round(fast, 2),
        "emaSlow": round(slow, 2),
        "rsi": round(rsi, 2),
        "atr": round(atr, 2),
        "atrPct": round(atr_pct, 4),
        "volumeRatio": round(volume_ratio, 2),
        "breakoutHigh": ref_high,
        "breakoutLow": ref_low,
        "previousHigh": highs[-2],
        "previousLow": lows[-2],
        "previousClose": closes[-2],
        "strategy": arm.name,
        "patterns": patterns,
        "smc": smc,
        "smcOverride": smc_override,
        "entryReason": entry_reason,
        "entryThreshold": round(entry_threshold, 4),
        "context": market_context(candles),
    }


def resample_candles(candles: list[dict], minutes: int) -> list[dict]:
    """Aggregate 1m OHLCV candles into deterministic N-minute bars without provider-specific APIs."""
    if minutes <= 1:
        return sorted(candles, key=lambda x: x["timestamp"])
    buckets: dict[str, dict] = {}
    for row in sorted(candles, key=lambda x: x["timestamp"]):
        try:
            ts = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
            ts = ts.astimezone(IST)
            minute = (ts.minute // minutes) * minutes
            bucket_ts = ts.replace(minute=minute, second=0, microsecond=0)
            key = bucket_ts.isoformat()
            o, h, l, close = map(float, (row["open"], row["high"], row["low"], row["close"]))
            volume = float(row.get("volume", 0) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if key not in buckets:
            buckets[key] = {
                "timestamp": key,
                "open": o,
                "high": h,
                "low": l,
                "close": close,
                "volume": volume,
                "oi": float(row.get("oi", 0) or 0),
            }
        else:
            bar = buckets[key]
            bar["high"] = max(float(bar["high"]), h)
            bar["low"] = min(float(bar["low"]), l)
            bar["close"] = close
            bar["volume"] = float(bar.get("volume", 0) or 0) + volume
    return list(buckets.values())


def _frame_direction(candles: list[dict]) -> dict:
    if len(candles) < 8:
        return {"trend": "UNKNOWN", "emaFast": 0.0, "emaSlow": 0.0, "rsi": 50.0}
    closes = [float(row["close"]) for row in candles]
    highs = [float(row["high"]) for row in candles]
    lows = [float(row["low"]) for row in candles]
    fast = _ema(closes, 5)
    slow = _ema(closes, 13)
    rsi = _rsi(closes, period=min(14, max(5, len(closes) - 1)))
    gap = (fast - slow) / max(closes[-1], 1e-9) * 100
    higher_structure = len(candles) >= 5 and highs[-1] >= highs[-3] and lows[-1] > lows[-3]
    lower_structure = len(candles) >= 5 and lows[-1] <= lows[-3] and highs[-1] < highs[-3]
    if gap > 0.025 or (closes[-1] > closes[-4] and higher_structure):
        trend = "UP"
    elif gap < -0.025 or (closes[-1] < closes[-4] and lower_structure):
        trend = "DOWN"
    else:
        trend = "FLAT"
    return {
        "trend": trend,
        "emaFast": round(fast, 2),
        "emaSlow": round(slow, 2),
        "rsi": round(rsi, 2),
        "higherStructure": bool(higher_structure),
        "lowerStructure": bool(lower_structure),
    }


def evaluate_mtf_continuation(candles: list[dict]) -> dict:
    """SMC/momentum continuation candidate using 1m source data plus derived 5m/15m structure."""
    one = sorted(candles, key=lambda x: x["timestamp"])
    five = resample_candles(one, 5)
    fifteen = resample_candles(one, 15)
    if len(one) < 30 or len(five) < 8 or len(fifteen) < 6:
        return {
            "action": "NO_TRADE",
            "score": 0.0,
            "qualified": False,
            "reason": "insufficient MTF candles",
            "frames": {},
        }

    d1, d5, d15 = _frame_direction(one), _frame_direction(five), _frame_direction(fifteen)
    smc = analyze_structure(one)
    patterns = detect_candlestick_patterns(one)
    closes = [float(x["close"]) for x in one]
    highs = [float(x["high"]) for x in one]
    lows = [float(x["low"]) for x in one]
    close = closes[-1]
    rsi = _rsi(closes)
    fast, slow = _ema(closes, 5), _ema(closes, 13)

    lower_low = lows[-1] < min(lows[-4:-1])
    lower_high = highs[-1] < max(highs[-4:-1])
    higher_high = highs[-1] > max(highs[-4:-1])
    higher_low = lows[-1] > min(lows[-4:-1])
    prev_low_break = close < lows[-2]
    prev_high_break = close > highs[-2]

    swing_low = min(lows[-21:])
    swing_high = max(highs[-21:])
    swing_range = max(1e-9, swing_high - swing_low)
    fib_382 = swing_high - swing_range * 0.382
    fib_500 = swing_high - swing_range * 0.500
    fib_618 = swing_high - swing_range * 0.618
    fib_zone = min(fib_382, fib_618) <= close <= max(fib_382, fib_618)

    bull_points = 0.0
    bear_points = 0.0
    evidence_bull: list[str] = []
    evidence_bear: list[str] = []

    for frame_name, frame, weight in (("15m", d15, 1.6), ("5m", d5, 1.8), ("1m", d1, 1.1)):
        if frame["trend"] == "UP":
            bull_points += weight
            evidence_bull.append(f"{frame_name}_UP")
        elif frame["trend"] == "DOWN":
            bear_points += weight
            evidence_bear.append(f"{frame_name}_DOWN")

    if fast > slow:
        bull_points += 0.7; evidence_bull.append("EMA_5_13_UP")
    elif fast < slow:
        bear_points += 0.7; evidence_bear.append("EMA_5_13_DOWN")
    if rsi >= 52:
        bull_points += 0.6; evidence_bull.append("RSI_BULL")
    elif rsi <= 48:
        bear_points += 0.6; evidence_bear.append("RSI_BEAR")

    if higher_high: bull_points += 0.7; evidence_bull.append("HIGHER_HIGH")
    if higher_low: bull_points += 0.5; evidence_bull.append("HIGHER_LOW")
    if lower_low: bear_points += 0.7; evidence_bear.append("LOWER_LOW")
    if lower_high: bear_points += 0.5; evidence_bear.append("LOWER_HIGH")
    if prev_high_break: bull_points += 0.8; evidence_bull.append("PREVIOUS_HIGH_BREAK")
    if prev_low_break: bear_points += 0.8; evidence_bear.append("PREVIOUS_LOW_BREAK")

    if smc.get("bos") == "BULL": bull_points += 1.1; evidence_bull.append("BOS_BULL")
    if smc.get("bos") == "BEAR": bear_points += 1.1; evidence_bear.append("BOS_BEAR")
    if smc.get("choch") == "BULL": bull_points += 1.2; evidence_bull.append("CHOCH_BULL")
    if smc.get("choch") == "BEAR": bear_points += 1.2; evidence_bear.append("CHOCH_BEAR")
    if smc.get("liquiditySweep") == "BULL": bull_points += 0.9; evidence_bull.append("SWEEP_BULL")
    if smc.get("liquiditySweep") == "BEAR": bear_points += 0.9; evidence_bear.append("SWEEP_BEAR")
    if smc.get("fairValueGap") == "BULL": bull_points += 0.45; evidence_bull.append("FVG_BULL")
    if smc.get("fairValueGap") == "BEAR": bear_points += 0.45; evidence_bear.append("FVG_BEAR")
    if patterns["bullishScore"] > 0:
        bull_points += min(0.7, patterns["bullishScore"]); evidence_bull.extend(patterns["bullish"][:2])
    if patterns["bearishScore"] > 0:
        bear_points += min(0.7, patterns["bearishScore"]); evidence_bear.extend(patterns["bearish"][:2])
    if fib_zone:
        if bull_points > bear_points:
            bull_points += 0.35; evidence_bull.append("FIB_382_618_ZONE")
        elif bear_points > bull_points:
            bear_points += 0.35; evidence_bear.append("FIB_382_618_ZONE")

    total_scale = 10.0
    bull_score = min(1.0, bull_points / total_scale)
    bear_score = min(1.0, bear_points / total_scale)
    action = "CE" if bull_score > bear_score else "PE" if bear_score > bull_score else "NO_TRADE"
    score = max(bull_score, bear_score)
    directional_frames = (
        sum(1 for x in (d5["trend"], d15["trend"]) if x == "UP")
        if action == "CE"
        else sum(1 for x in (d5["trend"], d15["trend"]) if x == "DOWN")
    )
    structure_ok = (
        action == "CE" and (higher_high or prev_high_break or smc.get("bos") == "BULL" or smc.get("choch") == "BULL")
    ) or (
        action == "PE" and (lower_low or prev_low_break or smc.get("bos") == "BEAR" or smc.get("choch") == "BEAR")
    )
    qualified = bool(action in {"CE", "PE"} and score >= 0.56 and directional_frames >= 1 and structure_ok)

    return {
        "action": action if qualified else "NO_TRADE",
        "candidateAction": action,
        "score": round(score, 4),
        "qualified": qualified,
        "reason": "MTF_SMC_CONTINUATION" if qualified else "MTF continuation confluence below gate",
        "underlyingPrice": close,
        "rsi": round(rsi, 2),
        "emaFast": round(fast, 2),
        "emaSlow": round(slow, 2),
        "previousHigh": highs[-2],
        "previousLow": lows[-2],
        "patterns": patterns,
        "smc": smc,
        "frames": {"1m": d1, "5m": d5, "15m": d15},
        "structure": {
            "lowerLow": lower_low,
            "lowerHigh": lower_high,
            "higherHigh": higher_high,
            "higherLow": higher_low,
            "previousLowBreak": prev_low_break,
            "previousHighBreak": prev_high_break,
        },
        "fibonacci": {
            "swingLow": round(swing_low, 2),
            "swingHigh": round(swing_high, 2),
            "fib382": round(fib_382, 2),
            "fib500": round(fib_500, 2),
            "fib618": round(fib_618, 2),
            "inRetracementZone": fib_zone,
        },
        "evidence": evidence_bull if action == "CE" else evidence_bear,
    }


def select_option(chain: list[dict], direction: str, spot: float, deployable_capital: float) -> dict | None:
    candidates: list[dict] = []
    strikes = sorted({float(row.get("strike_price") or 0) for row in chain if float(row.get("strike_price") or 0) > 0})
    if not strikes:
        return None
    atm_index = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    if direction == "CE":
        allowed_strikes = set(strikes[atm_index: atm_index + settings.max_otm_steps + 1])
    else:
        start = max(0, atm_index - settings.max_otm_steps)
        allowed_strikes = set(strikes[start: atm_index + 1])

    for row in chain:
        strike = float(row.get("strike_price") or 0)
        if strike not in allowed_strikes:
            continue
        side = row.get("call_options") if direction == "CE" else row.get("put_options")
        if not side:
            continue
        market, greeks = side.get("market_data") or {}, side.get("option_greeks") or {}
        ltp = float(market.get("ltp") or 0)
        bid, ask = float(market.get("bid_price") or 0), float(market.get("ask_price") or 0)
        volume, delta = int(market.get("volume") or 0), abs(float(greeks.get("delta") or 0))
        instrument_key = side.get("instrument_key")
        if not instrument_key or ltp <= 0:
            continue
        spread_pct = ((ask - bid) / ltp * 100) if ask > 0 and bid > 0 and ask >= bid else 999.0
        if spread_pct > settings.max_option_spread_pct or volume < settings.min_option_volume:
            continue
        distance_pct = abs(strike - spot) / max(spot, 1) * 100
        affordable = deployable_capital / ltp
        delta_score = max(0.0, 1 - abs(delta - settings.target_delta) / max(settings.target_delta, 0.01))
        spread_score = max(0.0, 1 - spread_pct / max(settings.max_option_spread_pct, 0.01))
        liquidity_score = min(1.0, volume / max(settings.min_option_volume * 5, 1))
        distance_score = max(0.0, 1 - distance_pct / 2.0)
        affordability_score = min(1.0, affordable / 100.0)
        score = 0.35 * delta_score + 0.25 * spread_score + 0.20 * liquidity_score + 0.10 * distance_score + 0.10 * affordability_score
        candidates.append({
            "instrumentKey": instrument_key, "strike": strike, "ltp": ltp, "bid": bid, "ask": ask,
            "volume": volume, "oi": int(market.get("oi") or 0), "delta": delta,
            "gamma": float(greeks.get("gamma") or 0), "theta": float(greeks.get("theta") or 0),
            "vega": float(greeks.get("vega") or 0), "iv": float(greeks.get("iv") or 0),
            "spreadPct": round(spread_pct, 3), "selectionScore": round(score, 4),
        })
    if not candidates:
        return None
    return max(candidates, key=lambda x: x["selectionScore"])


def _simulate_arm(candles: list[dict], arm: StrategyArm) -> dict:
    if len(candles) < 80:
        return {"trades": 0, "wins": 0, "winRate": 0.0, "expectancyR": 0.0}
    split = max(40, int(len(candles) * 0.70))
    rewards: list[float] = []
    i = split
    while i < len(candles) - 2:
        prefix = candles[: i + 1]
        signal = evaluate_signal(prefix, arm)
        if signal["action"] not in {"CE", "PE"}:
            i += 1
            continue
        entry = float(candles[i + 1]["open"])
        stop_distance = max(_atr(prefix) * settings.backtest_atr_stop_mult, entry * 0.001)
        direction = 1 if signal["action"] == "CE" else -1
        reward = None
        exit_index = i + 1
        for j in range(i + 1, min(len(candles), i + 2 + settings.backtest_max_hold_bars)):
            high, low = float(candles[j]["high"]), float(candles[j]["low"])
            if direction == 1:
                stop_hit = low <= entry - stop_distance
                target_hit = high >= entry + stop_distance * settings.reward_risk_ratio
            else:
                stop_hit = high >= entry + stop_distance
                target_hit = low <= entry - stop_distance * settings.reward_risk_ratio
            if stop_hit:
                reward = -1.0
                exit_index = j
                break
            if target_hit:
                reward = settings.reward_risk_ratio
                exit_index = j
                break
            exit_index = j
        if reward is None:
            last_close = float(candles[exit_index]["close"])
            reward = direction * (last_close - entry) / stop_distance
            reward = max(-1.0, min(settings.reward_risk_ratio, reward))
        rewards.append(reward)
        i = exit_index + 1
    wins = sum(1 for r in rewards if r > 0)
    return {
        "trades": len(rewards),
        "wins": wins,
        "winRate": round(wins / len(rewards) * 100, 2) if rewards else 0.0,
        "expectancyR": round(sum(rewards) / len(rewards), 4) if rewards else 0.0,
    }


class AdaptiveLearner:
    def _get_state(self, db) -> StrategyState:
        state = db.scalar(select(StrategyState).where(StrategyState.name == "APEX_ADAPTIVE_V1"))
        if not state:
            state = StrategyState(name="APEX_ADAPTIVE_V1")
            db.add(state); db.commit(); db.refresh(state)
        return state

    def _latest_research(self, db) -> dict:
        row = db.scalar(select(ResearchRun).order_by(ResearchRun.run_date.desc()).limit(1))
        if not row:
            return {}
        try:
            return json.loads(row.results_json or "{}")
        except json.JSONDecodeError:
            return {}

    def snapshot(self, context: str | None = None) -> dict:
        with SessionLocal() as db:
            state = self._get_state(db)
            q_values = json.loads(state.q_values_json or "{}")
            counts = json.loads(state.counts_json or "{}")
            global_q, global_counts = {}, {}
            for arm in ARMS:
                global_q[arm.name] = float(q_values.get(arm.name, 0.0))
                global_counts[arm.name] = int(counts.get(arm.name, 0))
            contextual = {}
            if context:
                contextual = {arm.name: float(q_values.get(f"{context}::{arm.name}", global_q[arm.name])) for arm in ARMS}
            best = max(ARMS, key=lambda a: global_q[a.name]).name
            reward_rows = list(db.execute(
                select(LearningRewardEvent).order_by(LearningRewardEvent.id.desc()).limit(10)
            ).scalars().all())
            total_rewards = sum(global_counts.values())
            return {
                "enabled": settings.adaptive_learning_enabled,
                "method": "contextual epsilon-greedy reinforcement bandit + shaped net-R reward + daily OOS research prior",
                "qValues": global_q,
                "counts": global_counts,
                "contextQ": contextual,
                "bestArm": best,
                "explorationRate": settings.exploration_rate,
                "learningRate": settings.learning_rate,
                "rewardUpdates": int(total_rewards),
                "recentRewards": [{
                    "tradeId": row.trade_id,
                    "strategy": row.strategy,
                    "context": row.context_key,
                    "status": row.status,
                    "pnl": row.pnl,
                    "rawReward": row.raw_reward,
                    "shapedReward": row.shaped_reward,
                    "createdAt": row.created_at.isoformat() if row.created_at else None,
                } for row in reward_rows],
                "latestResearch": self._latest_research(db),
                "arms": [asdict(a) for a in ARMS],
            }

    def choose_arm(self, context: dict, capital: float) -> StrategyArm:
        available = eligible_arms(capital, context)
        with SessionLocal() as db:
            state = self._get_state(db)
            q_values = json.loads(state.q_values_json or "{}")
            counts = json.loads(state.counts_json or "{}")
            research = self._latest_research(db).get("arms", {})
        key = context.get("key", "UNKNOWN")
        total = sum(int(counts.get(f"{key}::{arm.name}", 0)) for arm in available)
        if not settings.adaptive_learning_enabled:
            return next((a for a in available if a.name == "APEX_BREAKOUT_BALANCED"), available[0])
        if total < settings.minimum_learning_trades:
            researched = [
                (arm, research.get(arm.name) or {}) for arm in available
                if int((research.get(arm.name) or {}).get("trades", 0)) >= 5
            ]
            if researched:
                best_arm, best_stats = max(researched, key=lambda item: float(item[1].get("expectancyR", 0.0)))
                if float(best_stats.get("expectancyR", 0.0)) > 0:
                    return best_arm
            return next((a for a in available if a.name == "APEX_BREAKOUT_BALANCED"), available[0])
        if random.random() < settings.exploration_rate:
            return random.choice(available)

        def blended(arm: StrategyArm) -> float:
            contextual = float(q_values.get(f"{key}::{arm.name}", q_values.get(arm.name, 0.0)))
            prior = float((research.get(arm.name) or {}).get("expectancyR", 0.0))
            w = max(0.0, min(0.5, settings.research_prior_weight))
            return contextual * (1 - w) + prior * w

        return max(available, key=blended)

    def learn(self) -> dict:
        with SessionLocal() as db:
            state = self._get_state(db)
            q_values = json.loads(state.q_values_json or "{}")
            counts = json.loads(state.counts_json or "{}")
            rows = db.execute(select(Trade).where(
                Trade.status != "OPEN",
                Trade.status != "INVALID_CONTRACT",
                Trade.id > state.last_processed_trade_id,
            ).order_by(Trade.id.asc())).scalars().all()
            processed = 0
            for trade in rows:
                state.last_processed_trade_id = max(state.last_processed_trade_id, trade.id)
                try:
                    meta = json.loads(trade.reason or "{}")
                except json.JSONDecodeError:
                    continue
                if meta.get("manualDoTrade"):
                    # Explicit user-triggered aggressive paper trades must not bias autonomous arm preferences.
                    continue
                arm = meta.get("learningArm") or meta.get("strategy")
                context = meta.get("contextKey", "UNKNOWN")
                initial_risk = float(meta.get("initialRisk") or 0)
                if arm not in {a.name for a in ARMS} or initial_risk <= 0:
                    continue
                raw_reward = trade.pnl / initial_risk
                if not isfinite(raw_reward):
                    continue
                outcome_adjustment = 0.10 if trade.status == "TARGET" else -0.10 if trade.status == "STOPPED" else -0.03 if trade.status == "FORCED_EXIT" else 0.0
                late_penalty = -0.05 if "|LATE|" in str(context) else 0.0
                reward = max(-2.0, min(2.0, raw_reward + outcome_adjustment + late_penalty))
                for key in (arm, f"{context}::{arm}"):
                    old = float(q_values.get(key, 0.0))
                    q_values[key] = round(old + settings.learning_rate * (reward - old), 6)
                    counts[key] = int(counts.get(key, 0)) + 1
                meta["learningReward"] = {
                    "raw": round(raw_reward, 6),
                    "shaped": round(reward, 6),
                    "outcomeAdjustment": outcome_adjustment,
                    "latePenalty": late_penalty,
                    "learnedAt": datetime.now(IST).isoformat(),
                }
                trade.reason = json.dumps(meta, default=str)
                if not db.scalar(select(LearningRewardEvent.id).where(LearningRewardEvent.trade_id == trade.id)):
                    db.add(LearningRewardEvent(
                        trade_id=trade.id,
                        strategy=arm,
                        context_key=str(context),
                        status=trade.status,
                        pnl=float(trade.pnl),
                        initial_risk=initial_risk,
                        raw_reward=round(raw_reward, 6),
                        shaped_reward=round(reward, 6),
                        detail_json=json.dumps(meta["learningReward"]),
                    ))
                processed += 1
            if rows:
                state.q_values_json = json.dumps(q_values)
                state.counts_json = json.dumps(counts)
                state.updated_at = datetime.now(timezone.utc)
                db.commit()
            return {"processed": processed, "qValues": q_values, "counts": counts}

    def daily_research(self, provider) -> dict:
        if not settings.daily_research_enabled or not provider.market_ready:
            return {"status": "disabled"}
        today = datetime.now(IST).date()
        with SessionLocal() as db:
            existing = db.scalar(select(ResearchRun).where(ResearchRun.run_date == today.isoformat()))
            if existing:
                return json.loads(existing.results_json or "{}")
        from_date = today - timedelta(days=settings.backtest_lookback_days)
        aggregate = {arm.name: {"trades": 0, "wins": 0, "rewardSum": 0.0} for arm in ARMS}
        markets = {}
        for name, key in settings.underlying_keys.items():
            candles = provider.historical_candles(key, from_date, today)
            market_result = {}
            for arm in ARMS:
                result = _simulate_arm(candles, arm)
                market_result[arm.name] = result
                agg = aggregate[arm.name]
                agg["trades"] += result["trades"]
                agg["wins"] += result["wins"]
                agg["rewardSum"] += result["expectancyR"] * result["trades"]
            markets[name] = market_result
        arms = {}
        for arm in ARMS:
            agg = aggregate[arm.name]
            trades = agg["trades"]
            arms[arm.name] = {
                "trades": trades,
                "wins": agg["wins"],
                "winRate": round(agg["wins"] / trades * 100, 2) if trades else 0.0,
                "expectancyR": round(agg["rewardSum"] / trades, 4) if trades else 0.0,
            }
        result = {
            "status": "completed",
            "runDate": today.isoformat(),
            "lookbackDays": settings.backtest_lookback_days,
            "note": "Underlying directional OOS research only; not an options-PnL backtest.",
            "arms": arms,
            "markets": markets,
        }
        with SessionLocal() as db:
            db.add(ResearchRun(run_date=today.isoformat(), results_json=json.dumps(result)))
            db.commit()
        return result


adaptive_learner = AdaptiveLearner()
