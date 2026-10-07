from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .core import settings

IST = ZoneInfo(settings.timezone)


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return float(values[-1])
    alpha = 2 / (period + 1)
    out = sum(values[:period]) / period
    for value in values[period:]:
        out = alpha * value + (1 - alpha) * out
    return float(out)


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    recent = changes[-period:]
    gains = sum(max(v, 0.0) for v in recent) / period
    losses = sum(max(-v, 0.0) for v in recent) / period
    if losses <= 0:
        return 100.0
    rs = gains / losses
    return 100 - (100 / (1 + rs))


def _atr(candles: list[dict], period: int = 14) -> float:
    if len(candles) <= period:
        return 0.0
    values: list[float] = []
    for i in range(1, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        prev_close = float(candles[i - 1]["close"])
        values.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return float(sum(values[-period:]) / period)


def _trend(candles: list[dict]) -> dict:
    if len(candles) < 24:
        return {"trend": "UNKNOWN", "emaFast": 0.0, "emaSlow": 0.0, "rsi": 50.0, "atr": 0.0}
    closes = [float(row["close"]) for row in candles]
    highs = [float(row["high"]) for row in candles]
    lows = [float(row["low"]) for row in candles]
    fast = _ema(closes, 9)
    slow = _ema(closes, 21)
    close = max(closes[-1], 1e-9)
    gap_pct = (fast - slow) / close * 100
    recent_higher = len(candles) >= 6 and highs[-1] >= highs[-4] and lows[-1] > lows[-4]
    recent_lower = len(candles) >= 6 and lows[-1] <= lows[-4] and highs[-1] < highs[-4]
    if gap_pct > 0.025 or (gap_pct > -0.01 and recent_higher):
        trend = "UP"
    elif gap_pct < -0.025 or (gap_pct < 0.01 and recent_lower):
        trend = "DOWN"
    else:
        trend = "FLAT"
    return {
        "trend": trend,
        "emaFast": round(fast, 2),
        "emaSlow": round(slow, 2),
        "emaGapPct": round(gap_pct, 4),
        "rsi": round(_rsi(closes), 2),
        "atr": round(_atr(candles), 2),
        "lastPrice": round(closes[-1], 2),
        "recentStructure": "HH_HL" if recent_higher else "LL_LH" if recent_lower else "MIXED",
    }


def _levels(candles: list[dict], lookback: int = 24) -> dict:
    if len(candles) < 8:
        return {"support": 0.0, "resistance": 0.0}
    sample = candles[-lookback - 1:-1] if len(candles) > lookback else candles[:-1]
    return {
        "support": round(min(float(row["low"]) for row in sample), 2),
        "resistance": round(max(float(row["high"]) for row in sample), 2),
    }


def _patterns(candles: list[dict]) -> dict:
    if len(candles) < 3:
        return {"bullish": [], "bearish": [], "neutral": []}
    older, prev, cur = candles[-3], candles[-2], candles[-1]
    o, h, l, c = map(float, (cur["open"], cur["high"], cur["low"], cur["close"]))
    po, ph, pl, pc = map(float, (prev["open"], prev["high"], prev["low"], prev["close"]))
    oo, oh, ol, oc = map(float, (older["open"], older["high"], older["low"], older["close"]))
    body = max(abs(c - o), 1e-9)
    prev_body = max(abs(pc - po), 1e-9)
    rng = max(h - l, 1e-9)
    upper = h - max(o, c)
    lower = min(o, c) - l
    bullish: list[str] = []
    bearish: list[str] = []
    neutral: list[str] = []

    body_ratio = body / rng
    if body_ratio <= 0.10:
        neutral.append("DOJI")
    elif body_ratio <= 0.28 and upper >= body * 0.7 and lower >= body * 0.7:
        neutral.append("SPINNING_TOP")
    if h < ph and l > pl:
        neutral.append("INSIDE_BAR")
    if h > ph and l < pl:
        neutral.append("OUTSIDE_BAR")

    if lower >= body * 1.8 and upper <= body and c >= o:
        bullish.extend(["BULLISH_PIN_BAR", "HAMMER"])
    if upper >= body * 1.8 and lower <= body and c <= o:
        bearish.extend(["BEARISH_PIN_BAR", "SHOOTING_STAR"])

    if c > o and pc < po and o <= pc and c >= po:
        bullish.append("BULLISH_ENGULFING")
    if c < o and pc > po and o >= pc and c <= po:
        bearish.append("BEARISH_ENGULFING")

    if c > o and pc < po and o >= pc and c <= po and body < prev_body:
        bullish.append("BULLISH_HARAMI")
    if c < o and pc > po and o <= pc and c >= po and body < prev_body:
        bearish.append("BEARISH_HARAMI")

    if pc < po and c > o and o < pc and c > (po + pc) / 2 and c < po:
        bullish.append("PIERCING_LINE")
    if pc > po and c < o and o > pc and c < (po + pc) / 2 and c > po:
        bearish.append("DARK_CLOUD_COVER")

    if abs(l - pl) <= max(rng, ph - pl) * 0.08 and pc < po and c > o:
        bullish.append("TWEEZER_BOTTOM")
    if abs(h - ph) <= max(rng, ph - pl) * 0.08 and pc > po and c < o:
        bearish.append("TWEEZER_TOP")

    if c > ph and c > o:
        bullish.append("BREAKOUT_CLOSE")
    if c < pl and c < o:
        bearish.append("BREAKDOWN_CLOSE")

    if oc < oo and abs(pc - po) <= max((ph - pl) * 0.35, 1e-9) and c > o and c > (oo + oc) / 2:
        bullish.append("MORNING_STAR")
    if oc > oo and abs(pc - po) <= max((ph - pl) * 0.35, 1e-9) and c < o and c < (oo + oc) / 2:
        bearish.append("EVENING_STAR")

    if c > o and body_ratio >= 0.68:
        bullish.append("STRONG_BULL_BODY")
    if c < o and body_ratio >= 0.68:
        bearish.append("STRONG_BEAR_BODY")
    if c > o and body_ratio >= 0.88 and upper <= rng * 0.06 and lower <= rng * 0.06:
        bullish.append("BULLISH_MARUBOZU")
    if c < o and body_ratio >= 0.88 and upper <= rng * 0.06 and lower <= rng * 0.06:
        bearish.append("BEARISH_MARUBOZU")

    # Three-candle momentum patterns.
    if oc > oo and pc > po and c > o and oc < pc < c and ol < pl < l:
        bullish.append("THREE_WHITE_SOLDIERS")
    if oc < oo and pc < po and c < o and oc > pc > c and oh > ph > h:
        bearish.append("THREE_BLACK_CROWS")

    return {
        "bullish": list(dict.fromkeys(bullish)),
        "bearish": list(dict.fromkeys(bearish)),
        "neutral": list(dict.fromkeys(neutral)),
    }


def _structure(candles: list[dict]) -> dict:
    levels = _levels(candles, 20)
    if len(candles) < 25:
        return {
            "bos": "NONE",
            "choch": "NONE",
            "liquiditySweep": "NONE",
            "fairValueGap": "NONE",
            "fakeBreakout": "NONE",
            "swingStructure": "UNKNOWN",
            "higherHigh": False,
            "higherLow": False,
            "lowerHigh": False,
            "lowerLow": False,
            "levels": levels,
        }

    close = float(candles[-1]["close"])
    high = float(candles[-1]["high"])
    low = float(candles[-1]["low"])
    prior = candles[-8:-1]
    local_high = max(float(row["high"]) for row in prior)
    local_low = min(float(row["low"]) for row in prior)
    trend = _trend(candles)["trend"]
    resistance = float(levels["resistance"])
    support = float(levels["support"])

    bos = "BULL" if close > local_high else "BEAR" if close < local_low else "NONE"
    choch = "BULL" if trend == "DOWN" and close > local_high else "BEAR" if trend == "UP" and close < local_low else "NONE"
    sweep = "BULL" if low < support and close > support else "BEAR" if high > resistance and close < resistance else "NONE"
    fake = "BEAR" if high > resistance and close < resistance else "BULL" if low < support and close > support else "NONE"

    fvg = "NONE"
    older = candles[-3]
    if float(candles[-1]["low"]) > float(older["high"]):
        fvg = "BULL"
    elif float(candles[-1]["high"]) < float(older["low"]):
        fvg = "BEAR"

    highs = [float(x["high"]) for x in candles[-6:]]
    lows = [float(x["low"]) for x in candles[-6:]]
    higher_high = highs[-1] > highs[-3]
    higher_low = lows[-1] > lows[-3]
    lower_high = highs[-1] < highs[-3]
    lower_low = lows[-1] < lows[-3]
    swing_structure = (
        "HH_HL" if higher_high and higher_low
        else "LL_LH" if lower_low and lower_high
        else "EXPANSION_UP" if higher_high
        else "EXPANSION_DOWN" if lower_low
        else "MIXED"
    )

    return {
        "bos": bos,
        "choch": choch,
        "liquiditySweep": sweep,
        "fairValueGap": fvg,
        "fakeBreakout": fake,
        "swingStructure": swing_structure,
        "higherHigh": higher_high,
        "higherLow": higher_low,
        "lowerHigh": lower_high,
        "lowerLow": lower_low,
        "levels": levels,
    }


def analyze_structure(candles: list[dict]) -> dict:
    """Public SMC structure helper shared by research and live entry scoring."""
    return _structure(candles)


def _fetch(provider, key: str, minutes: int) -> list[dict]:
    if hasattr(provider, "intraday_candles_interval"):
        return provider.intraday_candles_interval(key, minutes)
    return provider.intraday_candles(key)


def _parse_time(row: dict) -> datetime | None:
    raw = row.get("timestamp") or row.get("time") or row.get("date")
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return dt.astimezone(IST)
    except Exception:
        return None


def _session_profile(candles: list[dict]) -> dict:
    if not candles:
        return {}
    grouped: dict[str, list[dict]] = {}
    for row in candles:
        dt = _parse_time(row)
        if not dt:
            continue
        grouped.setdefault(dt.date().isoformat(), []).append(row)
    dates = sorted(grouped)
    if not dates:
        return {}
    current_date = dates[-1]
    current = grouped[current_date]
    previous = grouped[dates[-2]] if len(dates) >= 2 else []
    day_open = float(current[0]["open"])
    day_high = max(float(x["high"]) for x in current)
    day_low = min(float(x["low"]) for x in current)
    day_close = float(current[-1]["close"])
    out = {
        "sessionDate": current_date,
        "dayOpen": round(day_open, 2),
        "dayHigh": round(day_high, 2),
        "dayLow": round(day_low, 2),
        "dayLast": round(day_close, 2),
        "dayRange": round(day_high - day_low, 2),
    }
    if previous:
        prev_high = max(float(x["high"]) for x in previous)
        prev_low = min(float(x["low"]) for x in previous)
        prev_close = float(previous[-1]["close"])
        out.update({
            "previousDayHigh": round(prev_high, 2),
            "previousDayLow": round(prev_low, 2),
            "previousDayClose": round(prev_close, 2),
            "gapPoints": round(day_open - prev_close, 2),
            "gapPct": round((day_open - prev_close) / max(prev_close, 1e-9) * 100, 4),
            "openVsPreviousRange": "ABOVE_HIGH" if day_open > prev_high else "BELOW_LOW" if day_open < prev_low else "INSIDE_RANGE",
        })
    return out


def build_market_research(provider) -> dict:
    markets: dict[str, dict] = {}
    for name, key in settings.underlying_keys.items():
        frames: dict[str, dict] = {}
        raw_1m: list[dict] = []
        for minutes in (1, 5, 15):
            try:
                candles = _fetch(provider, key, minutes)
                if minutes == 1:
                    raw_1m = candles
                frames[f"{minutes}m"] = {
                    **_trend(candles),
                    "structure": _structure(candles),
                    "patterns": _patterns(candles),
                    "candleCount": len(candles),
                    "lastCandleTime": (
                        candles[-1].get("timestamp") or candles[-1].get("time") or candles[-1].get("date")
                        if candles else None
                    ),
                }
            except Exception as exc:
                frames[f"{minutes}m"] = {"error": str(exc)[:180]}

        t5, t15 = frames.get("5m", {}).get("trend"), frames.get("15m", {}).get("trend")
        t1 = frames.get("1m", {}).get("trend")
        if t5 == "UP" and t15 == "UP":
            state = "BULLISH"
        elif t5 == "DOWN" and t15 == "DOWN":
            state = "BEARISH"
        elif t1 == t5 and t1 in {"UP", "DOWN"}:
            state = "EARLY_BULLISH" if t1 == "UP" else "EARLY_BEARISH"
        else:
            state = "MIXED"
        markets[name] = {
            "state": state,
            "frames": frames,
            "sessionProfile": _session_profile(raw_1m),
        }

    return {
        "generatedAt": datetime.now(IST).isoformat(),
        "purpose": "research_and_paper_decision_support",
        "markets": markets,
    }
