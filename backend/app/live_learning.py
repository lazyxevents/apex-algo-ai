from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from .core import SessionLocal
from .llm_advisor import ollama_advisor
from .models import LiveMarketObservation


class LiveLearningService:
    """Persist pre-trade market observations. LLM is advisory; deterministic risk stays authoritative."""

    def observe(self, signal: dict[str, Any], *, candle_time: datetime | None = None) -> dict[str, Any]:
        context = signal.get("context") or {}
        patterns = signal.get("patterns") or {}
        llm = ollama_advisor.analyze_trade({
            "phase": "LIVE_OBSERVATION",
            "instrument": signal.get("index"),
            "marketPrice": signal.get("underlyingPrice"),
            "action": signal.get("action", "NO_TRADE"),
            "signalScore": signal.get("score", 0),
            "strategy": signal.get("chosenStrategy") or signal.get("strategy"),
            "context": context,
            "patterns": patterns,
            "instruction": "Review the current market moment only. Do not override hard risk or place orders.",
        })
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            if candle_time is not None:
                existing = db.scalar(select(LiveMarketObservation).where(
                    LiveMarketObservation.instrument == str(signal.get("index") or "UNKNOWN"),
                    LiveMarketObservation.timeframe == "1m",
                    LiveMarketObservation.candle_time == candle_time,
                    LiveMarketObservation.strategy == str(signal.get("chosenStrategy") or signal.get("strategy") or ""),
                ).order_by(LiveMarketObservation.id.desc()).limit(1))
                if existing:
                    return self._dict(existing)
            row = LiveMarketObservation(
                observed_at=now,
                instrument=str(signal.get("index") or "UNKNOWN"),
                timeframe="1m",
                candle_time=candle_time,
                market_price=float(signal.get("underlyingPrice") or 0.0),
                action=str(signal.get("action") or "NO_TRADE"),
                signal_score=float(signal.get("score") or 0.0),
                strategy=str(signal.get("chosenStrategy") or signal.get("strategy") or ""),
                context_json=json.dumps(context, default=str),
                patterns_json=json.dumps(patterns, default=str),
                llm_json=json.dumps(llm, default=str),
                decision_json=json.dumps({
                    "reason": signal.get("reason"),
                    "previousHigh": signal.get("previousHigh"),
                    "previousLow": signal.get("previousLow"),
                }, default=str),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return self._dict(row)

    def attach_trade(self, observation_id: int, trade_id: int) -> None:
        with SessionLocal() as db:
            row = db.get(LiveMarketObservation, observation_id)
            if row:
                row.trade_id = trade_id
                db.commit()

    def label_trade(self, trade_id: int, outcome: str, pnl: float) -> int:
        with SessionLocal() as db:
            rows = list(db.execute(
                select(LiveMarketObservation).where(LiveMarketObservation.trade_id == trade_id)
            ).scalars().all())
            for row in rows:
                row.outcome = outcome
                row.outcome_pnl = float(pnl)
            db.commit()
            return len(rows)

    def snapshot(self, limit: int = 12) -> dict[str, Any]:
        with SessionLocal() as db:
            total = db.scalar(select(func.count()).select_from(LiveMarketObservation)) or 0
            pending = db.scalar(select(func.count()).select_from(LiveMarketObservation).where(LiveMarketObservation.outcome == "PENDING")) or 0
            rows = list(db.execute(
                select(LiveMarketObservation).order_by(LiveMarketObservation.id.desc()).limit(max(1, min(limit, 50)))
            ).scalars().all())
            return {
                "enabled": True,
                "mode": "LIVE_MARKET_OBSERVATION",
                "totalObservations": int(total),
                "pendingOutcomes": int(pending),
                "latest": [self._dict(row) for row in rows],
                "note": "Raw candle/SMC observations are the primary learning evidence; Ollama review is auxiliary.",
            }

    @staticmethod
    def _dict(row: LiveMarketObservation) -> dict[str, Any]:
        def load(value: str) -> Any:
            try:
                return json.loads(value or "{}")
            except json.JSONDecodeError:
                return {}
        return {
            "id": row.id,
            "observedAt": row.observed_at.isoformat() if row.observed_at else None,
            "candleTime": row.candle_time.isoformat() if row.candle_time else None,
            "instrument": row.instrument,
            "timeframe": row.timeframe,
            "marketPrice": row.market_price,
            "action": row.action,
            "signalScore": row.signal_score,
            "strategy": row.strategy,
            "context": load(row.context_json),
            "patterns": load(row.patterns_json),
            "ollama": load(row.llm_json),
            "decision": load(row.decision_json),
            "tradeId": row.trade_id,
            "outcome": row.outcome,
            "outcomePnl": row.outcome_pnl,
        }


live_learning_service = LiveLearningService()
