"""Transparent historical candle reference library, not an asserted trained neural model.

Only actual broker 1-minute OHLC is stored. Future label comes from next three
completed bars, so the latest three bars are never labeled or counted.
This corpus is observational and does not authorize live orders.
"""
from collections import Counter
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
import json

from sqlalchemy import func, select

from .core import SessionLocal
from .models import CandlePatternReference


def classify_pattern(previous: dict, candle: dict) -> str:
    o, h, l, c = (float(candle[key]) for key in ("open", "high", "low", "close"))
    po, pc = float(previous["open"]), float(previous["close"])
    span = max(1e-8, h - l)
    body = abs(c - o)
    lower = min(o, c) - l
    upper = h - max(o, c)
    if lower >= max(body * 2, span * .52) and upper <= span * .22:
        return "BULLISH_PIN_BAR"
    if upper >= max(body * 2, span * .52) and lower <= span * .22:
        return "BEARISH_PIN_BAR"
    if c > o and pc < po and c >= po and o <= pc:
        return "BULLISH_ENGULFING"
    if c < o and pc > po and c <= po and o >= pc:
        return "BEARISH_ENGULFING"
    if body <= span * .12:
        return "DOJI"
    if body >= span * .7:
        return "BULLISH_MARUBOZU" if c > o else "BEARISH_MARUBOZU"
    return "NO_NAMED_PATTERN"


class PatternReferenceService:
    minimum = 1000

    def __init__(self):
        self._lock = Lock()
        self._last_ingest_at = 0.0
        self._last_error = None
        self._last_added = 0

    def ingest(self, candles: list[dict], instrument: str = "SENSEX") -> int:
        if monotonic() - self._last_ingest_at < 180:
            return 0
        if not self._lock.acquire(blocking=False):
            return 0
        try:
            self._last_ingest_at = monotonic()
            ordered = sorted(candles, key=lambda c: str(c.get("timestamp") or ""))
            if len(ordered) < 7:
                return 0
            ordered = ordered[-2200:]
            parsed = []
            for row in ordered:
                try:
                    stamp = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=timezone.utc)
                    stamp = stamp.astimezone(timezone.utc)
                    if min(float(row[k]) for k in ("open", "high", "low", "close")) <= 0:
                        continue
                    parsed.append((stamp, row))
                except (TypeError, ValueError, KeyError):
                    continue
            if len(parsed) < 7:
                return 0
            # Up to 2,000 real examples. Exclude latest three unresolved candles.
            rows = parsed[-2004:]
            with SessionLocal() as db:
                existing = set(db.scalars(select(CandlePatternReference.candle_time).where(
                    CandlePatternReference.instrument == instrument,
                    CandlePatternReference.timeframe == "1m",
                    CandlePatternReference.candle_time >= rows[0][0],
                )).all())
                existing = {x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x.astimezone(timezone.utc) for x in existing}
                added = 0
                for i in range(1, len(rows)-3):
                    stamp, bar = rows[i]
                    if stamp in existing:
                        continue
                    prior = rows[i-1][1]
                    future = rows[i+3][1]
                    first = float(bar["close"])
                    move = (float(future["close"]) / first - 1.0) * 100
                    pattern = classify_pattern(prior, bar)
                    direction = "UP" if move > 0.03 else "DOWN" if move < -0.03 else "FLAT"
                    db.add(CandlePatternReference(
                        instrument=instrument,
                        timeframe="1m",
                        candle_time=stamp,
                        pattern=pattern,
                        direction=direction,
                        future_move_pct=round(move, 5),
                        features_json=json.dumps({
                            "open": float(bar["open"]),
                            "high": float(bar["high"]),
                            "low": float(bar["low"]),
                            "close": first,
                            "volume": float(bar.get("volume") or 0),
                            "futureBars": 3,
                            "labelDefinition": "3_bar_close_change_0.03pct",
                        }),
                    ))
                    added += 1
                    if added >= 2000:
                        break
                db.commit()
                self._last_added = added
                self._last_error = None
                return added
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {str(exc)[:150]}"
            return 0
        finally:
            self._lock.release()

    def snapshot(self) -> dict:
        try:
            with SessionLocal() as db:
                count = int(db.scalar(select(func.count()).select_from(CandlePatternReference)) or 0)
                sample = db.execute(select(CandlePatternReference.pattern, func.count()).group_by(
                    CandlePatternReference.pattern
                ).order_by(func.count().desc()).limit(9)).all()
                bullish = int(db.scalar(select(func.count()).select_from(CandlePatternReference).where(
                    CandlePatternReference.direction == "UP"
                )) or 0)
                bearish = int(db.scalar(select(func.count()).select_from(CandlePatternReference).where(
                    CandlePatternReference.direction == "DOWN"
                )) or 0)
            return {
                "count": count,
                "goal": self.minimum,
                "ready": count >= self.minimum,
                "progressPct": round(min(count / self.minimum * 100, 100), 1),
                "patterns": [{"name": name, "count": int(n)} for name, n in sample],
                "up": bullish,
                "down": bearish,
                "lastAdded": self._last_added,
                "lastError": self._last_error,
                "note": "Historical index OHLC examples with 3-candle future labels. Not option premium results or validated neural training.",
            }
        except Exception as exc:
            return {"count": 0, "goal": self.minimum, "ready": False, "lastError": str(exc)[:140]}


pattern_reference_service = PatternReferenceService()
