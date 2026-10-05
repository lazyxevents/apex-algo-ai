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
    fast = _ema(closes, 9)
    slow = _ema(closes, 21)
    close = max(closes[-1], 1e-9)
    gap_pct = (fast - slow) / close * 100
    trend = "UP" if gap_pct > 0.025 else "DOWN" if gap_pct < -0.025 else "FLAT"
    return {
        "trend": trend,
        "emaFast": round(fast, 2),
        "emaSlow": round(slow, 2),
        "emaGapPct": round(gap_pct, 4),
        "rsi": round(_rsi(closes), 2),
        "atr": round(_atr(candles), 2),
        "lastPrice": round(closes[-1], 2),
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
        return {"bullish": [], "bearish": []}
    prev, cur = candles[-2], candles[-1]
    o, h, l, c = map(float, (cur["open"], cur["high"], cur["low"], cur["close"]))
    po, ph, pl, pc = map(float, (prev["open"], prev["high"], prev["low"], prev["close"]))
    body = max(abs(c - o), 1e-9)
    upper = h - max(o, c)
    lower = min(o, c) - l
    bullish: list[str] = []
    bearish: list[str] = []

    if lower >= body * 1.8 and c >= o:
        bullish.append("BULLISH_PIN_BAR")
    if upper >= body * 1.8 and c <= o:
        bearish.append("BEARISH_PIN_BAR")
    if c > o and pc < po and o <= pc and c >= po:
        bullish.append("BULLISH_ENGULFING")
    if c < o and pc > po and o >= pc and c <= po:
        bearish.append("BEARISH_ENGULFING")
    if c > ph and c > o:
        bullish.append("BREAKOUT_CLOSE")
    if c < pl and c < o:
        bearish.append("BREAKDOWN_CLOSE")
    return {"bullish": bullish, "bearish": bearish}


def _structure(candles: list[dict]) -> dict:
    levels = _levels(candles, 20)
    if len(candles) < 25:
        return {
            "bos": "NONE",
            "choch": "NONE",
            "liquiditySweep": "NONE",
            "fairValueGap": "NONE",
            "fakeBreakout": "NONE",
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

    return {
        "bos": bos,
        "choch": choch,
        "liquiditySweep": sweep,
        "fairValueGap": fvg,
        "fakeBreakout": fake,
        "levels": levels,
    }


def _fetch(provider, key: str, minutes: int) -> list[dict]:
    if hasattr(provider, "intraday_candles_interval"):
        return provider.intraday_candles_interval(key, minutes)
    return provider.intraday_candles(key)


def build_market_research(provider) -> dict:
    markets: dict[str, dict] = {}
    for name, key in settings.underlying_keys.items():
        frames: dict[str, dict] = {}
        for minutes in (1, 5, 15):
            try:
                candles = _fetch(provider, key, minutes)
                frames[f"{minutes}m"] = {
                    **_trend(candles),
                    "structure": _structure(candles),
                    "patterns": _patterns(candles),
                }
            except Exception as exc:
                frames[f"{minutes}m"] = {"error": str(exc)[:180]}

        trends = [frames.get("5m", {}).get("trend"), frames.get("15m", {}).get("trend")]
        state = "BULLISH" if trends == ["UP", "UP"] else "BEARISH" if trends == ["DOWN", "DOWN"] else "MIXED"
        markets[name] = {"state": state, "frames": frames}

    return {
        "generatedAt": datetime.now(IST).isoformat(),
        "purpose": "research_only",
        "markets": markets,
    }
