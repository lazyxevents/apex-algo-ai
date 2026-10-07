from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .core import SessionLocal, settings
from .market_research import _patterns, _session_profile, _structure, _trend
from .models import DailyMarketPlan

IST = ZoneInfo(settings.timezone)


def _resample(candles: list[dict], minutes: int) -> list[dict]:
    if minutes <= 1:
        return list(candles)
    buckets: dict[str, dict] = {}
    for row in candles:
        raw = row.get("timestamp") or row.get("time") or row.get("date")
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
            ts = ts.astimezone(IST)
        except Exception:
            continue
        bucket_minute = (ts.minute // minutes) * minutes
        key_dt = ts.replace(minute=bucket_minute, second=0, microsecond=0)
        key = key_dt.isoformat()
        o, h, l, c = map(float, (row["open"], row["high"], row["low"], row["close"]))
        v = float(row.get("volume", 0) or 0)
        if key not in buckets:
            buckets[key] = {"timestamp": key, "open": o, "high": h, "low": l, "close": c, "volume": v}
        else:
            bar = buckets[key]
            bar["high"] = max(float(bar["high"]), h)
            bar["low"] = min(float(bar["low"]), l)
            bar["close"] = c
            bar["volume"] = float(bar.get("volume", 0) or 0) + v
    return list(buckets.values())


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    out: list[float] = []
    seed = values[0]
    for i, value in enumerate(values):
        if i == 0:
            seed = value
        else:
            seed = alpha * value + (1 - alpha) * seed
        out.append(seed)
    return out


def _freshness(candles: list[dict]) -> dict:
    if not candles:
        return {"state": "MISSING", "fresh": False, "ageSeconds": None, "maxAgeSeconds": settings.live_trade_candle_max_age_seconds}
    raw = candles[-1].get("timestamp") or candles[-1].get("time") or candles[-1].get("date")
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        ts = ts.astimezone(IST)
    except Exception:
        return {"state": "INVALID_TIMESTAMP", "fresh": False, "ageSeconds": None, "maxAgeSeconds": settings.live_trade_candle_max_age_seconds}
    age = (datetime.now(IST) - ts).total_seconds()
    max_age = max(60, int(settings.live_trade_candle_max_age_seconds))
    fresh = -120 <= age <= max_age
    return {
        "state": "LIVE" if fresh and age <= 120 else "DELAYED" if fresh else "STALE",
        "fresh": fresh,
        "ageSeconds": round(age, 1),
        "maxAgeSeconds": max_age,
        "candleTime": ts.isoformat(),
    }


class ChartIntelligenceService:
    def __init__(self) -> None:
        self._lock = Lock()
        self._cached: dict = {}
        self._cached_at = 0.0
        self.cache_seconds = 15.0

    @staticmethod
    def _latest_plan() -> dict:
        with SessionLocal() as db:
            row = db.scalar(select(DailyMarketPlan).order_by(DailyMarketPlan.id.desc()).limit(1))
        if not row:
            return {}
        try:
            return json.loads(row.plan_json or "{}")
        except Exception:
            return {}

    @staticmethod
    def _events(candles: list[dict], offset: int) -> list[dict]:
        out: list[dict] = []
        last_key = ""
        start = max(25, len(candles) - 80)
        for i in range(start, len(candles)):
            prefix = candles[: i + 1]
            smc = _structure(prefix)
            pats = _patterns(prefix)
            row = candles[i]
            time = row.get("timestamp") or row.get("time")
            price = float(row["close"])
            events: list[tuple[str, str, str]] = []
            for key, label in (
                ("choch", "CHOCH"),
                ("liquiditySweep", "SWEEP"),
                ("bos", "BOS"),
                ("fakeBreakout", "FAKE"),
                ("fairValueGap", "FVG"),
            ):
                value = smc.get(key)
                if value and value != "NONE":
                    events.append((label, str(value), f"{label} {value}"))
            for name in (pats.get("bullish") or [])[:1]:
                events.append(("PATTERN", "BULL", name))
            for name in (pats.get("bearish") or [])[:1]:
                events.append(("PATTERN", "BEAR", name))
            for kind, direction, label in events:
                dedupe = f"{kind}:{direction}:{label}"
                if dedupe == last_key and kind not in {"CHOCH", "SWEEP"}:
                    continue
                last_key = dedupe
                out.append({
                    "index": i - offset,
                    "time": time,
                    "price": round(price, 2),
                    "kind": kind,
                    "direction": direction,
                    "label": label,
                })
        return [x for x in out if x["index"] >= 0][-28:]

    def snapshot(self, provider, force: bool = False) -> dict:
        now_mono = monotonic()
        if not force and self._cached and now_mono - self._cached_at < self.cache_seconds:
            return self._cached
        if not self._lock.acquire(blocking=False):
            return self._cached or {"status": "busy"}
        try:
            key = settings.underlying_keys.get("SENSEX")
            if not key:
                return {"status": "disabled", "reason": "SENSEX not enabled"}
            candles = (
                provider.intraday_candles_interval(key, 1)
                if hasattr(provider, "intraday_candles_interval")
                else provider.intraday_candles(key)
            )
            if not candles:
                return {"status": "no_data", "freshness": _freshness([])}

            raw = candles[-140:]
            closes = [float(x["close"]) for x in raw]
            ema9 = _ema_series(closes, 9)
            ema21 = _ema_series(closes, 21)
            chart_rows = []
            for i, row in enumerate(raw):
                chart_rows.append({
                    "time": row.get("timestamp") or row.get("time") or row.get("date"),
                    "open": round(float(row["open"]), 2),
                    "high": round(float(row["high"]), 2),
                    "low": round(float(row["low"]), 2),
                    "close": round(float(row["close"]), 2),
                    "volume": round(float(row.get("volume", 0) or 0), 2),
                    "ema9": round(ema9[i], 2),
                    "ema21": round(ema21[i], 2),
                })

            smc = _structure(candles)
            pats = _patterns(candles)
            profile = _session_profile(candles)
            five = _resample(candles, 5)
            fifteen = _resample(candles, 15)
            trends = {
                "1m": _trend(candles),
                "5m": _trend(five),
                "15m": _trend(fifteen),
            }
            swing = candles[-60:] if len(candles) >= 60 else candles
            swing_high = max(float(x["high"]) for x in swing)
            swing_low = min(float(x["low"]) for x in swing)
            span = max(1e-9, swing_high - swing_low)
            fib = {
                "swingHigh": round(swing_high, 2),
                "swingLow": round(swing_low, 2),
                "fib382": round(swing_high - span * 0.382, 2),
                "fib500": round(swing_high - span * 0.500, 2),
                "fib618": round(swing_high - span * 0.618, 2),
            }
            current = chart_rows[-1]
            body = abs(current["close"] - current["open"])
            candle_range = max(0.01, current["high"] - current["low"])
            candle_analysis = {
                "bodyPct": round(body / candle_range * 100, 2),
                "direction": "BULL" if current["close"] > current["open"] else "BEAR" if current["close"] < current["open"] else "DOJI",
                "patterns": pats,
                "smc": smc,
                "trend": trends,
            }
            plan = self._latest_plan()
            offset = max(0, len(candles) - len(raw))
            levels = {
                "support": (smc.get("levels") or {}).get("support"),
                "resistance": (smc.get("levels") or {}).get("resistance"),
                "dayHigh": profile.get("dayHigh"),
                "dayLow": profile.get("dayLow"),
                "previousDayHigh": profile.get("previousDayHigh"),
                "previousDayLow": profile.get("previousDayLow"),
                "previousDayClose": profile.get("previousDayClose"),
                "planSupport": plan.get("support"),
                "planResistance": plan.get("resistance"),
            }
            result = {
                "status": "ok",
                "generatedAt": datetime.now(IST).isoformat(),
                "provider": provider.status().get("provider") if hasattr(provider, "status") else "unknown",
                "freshness": _freshness(candles),
                "candles": chart_rows,
                "events": self._events(candles, offset),
                "levels": levels,
                "fibonacci": fib,
                "sessionProfile": profile,
                "trends": trends,
                "currentCandle": candle_analysis,
                "plan": {
                    "planDate": plan.get("planDate"),
                    "marketBias": plan.get("marketBias"),
                    "patternsSeen": plan.get("patternsSeen") or [],
                    "structureSeen": plan.get("structureSeen") or [],
                },
                "cacheSeconds": self.cache_seconds,
                "note": "Chart overlays use the same underlying candle/SMC/pattern features as APEX paper-decision support. Provider delay still applies.",
            }
            self._cached = result
            self._cached_at = now_mono
            return result
        finally:
            self._lock.release()


chart_intelligence_service = ChartIntelligenceService()
