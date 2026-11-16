from datetime import datetime, timedelta
from threading import Lock
from zoneinfo import ZoneInfo

from .core import settings
from .strategy import analyze_structure

IST = ZoneInfo(settings.timezone)

# Shared process cache: prevents concurrent dashboard tabs and timeframe views
# from independently requesting identical candles from the broker.
_CHART_CACHE: dict[tuple[str, int], tuple[datetime, dict]] = {}
_CHART_LOCK = Lock()
_CHART_TTL_SECONDS = {1: 12, 5: 25, 15: 45}
_CHART_COOLDOWN_UNTIL: datetime | None = None
_CHART_LAST_ERROR: str | None = None


def _cached_chart(provider, timeframe: int, limit: int, trades: list[dict] | None) -> dict:
    global _CHART_COOLDOWN_UNTIL, _CHART_LAST_ERROR
    key = (str(settings.market_data_provider).lower(), timeframe)
    now = datetime.now(IST)
    with _CHART_LOCK:
        cached = _CHART_CACHE.get(key)
        if cached and (now - cached[0]).total_seconds() < _CHART_TTL_SECONDS[timeframe]:
            result = dict(cached[1])
            result["cache"] = {"hit": True, "ageSeconds": round((now - cached[0]).total_seconds(), 1)}
            return result
        if _CHART_COOLDOWN_UNTIL and now < _CHART_COOLDOWN_UNTIL:
            if cached:
                result = dict(cached[1])
                result["cache"] = {"hit": True, "stale": True, "cooldownUntil": _CHART_COOLDOWN_UNTIL.isoformat(), "lastError": _CHART_LAST_ERROR}
                return result
            raise RuntimeError(f"Market data provider is in rate-limit cooldown until {_CHART_COOLDOWN_UNTIL.isoformat()}")
        # Serialize fetches so simultaneous requests cannot create a request burst.
        try:
            result = _build_market_chart_uncached(provider, timeframe, limit, trades)
            _CHART_CACHE[key] = (now, result)
            _CHART_COOLDOWN_UNTIL = None
            _CHART_LAST_ERROR = None
            result["cache"] = {"hit": False, "ttlSeconds": _CHART_TTL_SECONDS[timeframe]}
            return result
        except Exception as exc:
            message = str(exc)
            _CHART_LAST_ERROR = message[:500]
            if "429" in message or "rate limit" in message.lower() or "too many requests" in message.lower():
                _CHART_COOLDOWN_UNTIL = now + timedelta(seconds=90)
            if cached:
                result = dict(cached[1])
                result["cache"] = {"hit": True, "stale": True, "lastError": _CHART_LAST_ERROR,
                                   "cooldownUntil": _CHART_COOLDOWN_UNTIL.isoformat() if _CHART_COOLDOWN_UNTIL else None}
                return result
            raise


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


def _build_market_chart_uncached(provider, timeframe: int = 1, limit: int = 160, trades: list[dict] | None = None) -> dict:
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

    # Intraday endpoints can return an empty list on weekends/holidays.
    # Fall back to recent historical candles so the chart still renders.
    if not candles and hasattr(provider, "historical_candles_interval"):
        to_date = datetime.now(IST).date()
        from_date = to_date - timedelta(days=7)
        candles = provider.historical_candles_interval(
            underlying_key, timeframe, from_date, to_date
        )

    candles = sorted(candles, key=lambda row: str(row.get("timestamp") or ""))
    visible = candles[-limit:]
    # Compute EMA over full broker history before slicing, so EMA9/EMA21 are warmed up.
    all_closes = [float(row["close"]) for row in candles]
    all_ema9 = _ema(all_closes, 9)
    all_ema21 = _ema(all_closes, 21)
    ema9 = all_ema9[-len(visible):] if visible else []
    ema21 = all_ema21[-len(visible):] if visible else []

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

    swing_highs: list[dict] = []
    swing_lows: list[dict] = []
    for idx in range(2, max(2, len(chart_rows) - 2)):
        window = chart_rows[idx - 2:idx + 3]
        if len(window) != 5:
            continue
        pivot = chart_rows[idx]
        high, low = float(pivot["high"]), float(pivot["low"])
        if high >= max(float(row["high"]) for j, row in enumerate(window) if j != 2):
            swing_highs.append({"index": idx, "price": round(high, 2), "timestamp": pivot.get("timestamp")})
        if low <= min(float(row["low"]) for j, row in enumerate(window) if j != 2):
            swing_lows.append({"index": idx, "price": round(low, 2), "timestamp": pivot.get("timestamp")})

    structure_events: list[dict] = []
    if chart_rows:
        last_index, last_candle = len(chart_rows) - 1, chart_rows[-1]
        for key, label, price_key in (
            ("bos", "BOS", "close"), ("choch", "CHOCH", "close"),
            ("liquiditySweep", "LIQUIDITY SWEEP", "high"), ("fairValueGap", "FVG", "close"),
        ):
            direction = str(structure.get(key) or "NONE").upper()
            if direction in {"BULL", "BEAR"}:
                structure_events.append({"index": last_index, "type": label, "direction": direction,
                    "price": round(float(last_candle.get(price_key) or last_candle["close"]), 2),
                    "timestamp": last_candle.get("timestamp")})

    trade_markers: list[dict] = []
    for trade in trades or []:
        meta = trade.get("meta") or {}
        if str(meta.get("index") or "").upper() != "SENSEX":
            continue
        try:
            underlying_price = float(meta.get("underlyingPrice") or meta.get("underlyingEntry") or 0)
        except (TypeError, ValueError):
            continue
        marker_time = meta.get("underlyingEntryTime") or meta.get("entryTime") or trade.get("openedAt")
        if underlying_price > 0 and marker_time:
            trade_markers.append({"tradeId": trade.get("id"),
                "direction": str(trade.get("direction") or meta.get("direction") or "").upper(),
                "price": round(underlying_price, 2), "timestamp": marker_time,
                "status": trade.get("status"), "label": "PAPER ENTRY"})

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
        "annotations": {"swingHighs": swing_highs[-18:], "swingLows": swing_lows[-18:], "events": structure_events},
        "tradeMarkers": trade_markers[-30:],
        "candles": chart_rows,
        "generatedAt": datetime.now(IST).isoformat(),
    }


def build_market_chart(provider, timeframe: int = 1, limit: int = 160, trades: list[dict] | None = None) -> dict:
    if timeframe not in _CHART_TTL_SECONDS:
        raise ValueError("Supported chart timeframes are 1, 5 and 15 minutes")
    return _cached_chart(provider, timeframe, limit, trades)
