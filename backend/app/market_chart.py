from datetime import datetime
from zoneinfo import ZoneInfo

from .core import settings
from .strategy import analyze_structure

IST = ZoneInfo(settings.timezone)


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [float(values[0])]
    for value in values[1:]:
        out.append(alpha * float(value) + (1.0 - alpha) * out[-1])
    return out


def _freshness(candles: list[dict]) -> dict:
    now = datetime.now(IST)
    if not candles:
        return {
            "state": "MISSING",
            "fresh": False,
            "candleTime": None,
            "ageSeconds": None,
            "maxAgeSeconds": settings.live_trade_candle_max_age_seconds,
        }
    raw = str(candles[-1].get("timestamp") or "")
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        ts = ts.astimezone(IST)
        age = (now - ts).total_seconds()
    except (TypeError, ValueError):
        return {
            "state": "INVALID_TIMESTAMP",
            "fresh": False,
            "candleTime": raw or None,
            "ageSeconds": None,
            "maxAgeSeconds": settings.live_trade_candle_max_age_seconds,
        }
    fresh = -120 <= age <= max(60, settings.live_trade_candle_max_age_seconds)
    return {
        "state": "FRESH" if fresh else "STALE",
        "fresh": fresh,
        "candleTime": ts.isoformat(),
        "ageSeconds": round(age, 1),
        "maxAgeSeconds": settings.live_trade_candle_max_age_seconds,
    }


def build_market_chart(provider, timeframe: int = 1, limit: int = 160) -> dict:
    underlying_key = settings.underlying_keys.get("SENSEX")
    if not underlying_key:
        raise RuntimeError("SENSEX is not enabled")
    if not provider.market_ready:
        raise RuntimeError("Market data provider is not configured")

    if hasattr(provider, "intraday_candles_interval"):
        candles = provider.intraday_candles_interval(underlying_key, timeframe)
    elif timeframe == settings.candle_interval_minutes:
        candles = provider.intraday_candles(underlying_key)
    else:
        raise RuntimeError("Current provider does not support requested chart timeframe")

    candles = sorted(candles, key=lambda row: str(row.get("timestamp") or ""))
    visible = candles[-limit:]
    closes = [float(row["close"]) for row in visible]
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)

    chart_rows = []
    for idx, row in enumerate(visible):
        chart_rows.append({
            "timestamp": row.get("timestamp"),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
            "volume": float(row.get("volume") or 0),
            "oi": float(row.get("oi") or 0),
            "ema9": round(float(ema9[idx]), 2),
            "ema21": round(float(ema21[idx]), 2),
        })

    latest = chart_rows[-1] if chart_rows else None
    first = chart_rows[0] if chart_rows else None
    change = (
        ((float(latest["close"]) - float(first["close"])) / max(float(first["close"]), 1e-9) * 100.0)
        if latest and first
        else 0.0
    )
    structure = analyze_structure(candles[-max(80, limit):]) if candles else {}
    freshness = _freshness(candles)

    return {
        "provider": provider.status(),
        "symbol": "SENSEX",
        "instrumentKey": underlying_key,
        "timeframe": timeframe,
        "count": len(chart_rows),
        "latest": latest,
        "changePct": round(change, 3),
        "freshness": freshness,
        "structure": structure,
        "candles": chart_rows,
        "generatedAt": datetime.now(IST).isoformat(),
    }
