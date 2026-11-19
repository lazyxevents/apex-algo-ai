from __future__ import annotations

import json
from threading import Lock, Thread
from datetime import datetime, timedelta, timezone
import time
from typing import Any

from sqlalchemy import func, select, update

from .core import SessionLocal
from .llm_advisor import ollama_advisor
from .models import LiveMarketObservation, Trade


class LiveLearningService:
    """Persist pre-trade observations and post-trade diagnostics without changing risk authority."""

    def __init__(self) -> None:
        self._review_lock = Lock()
        self._reviews_inflight: set[int] = set()
        self._last_llm_review_at = 0.0

    @staticmethod
    def _deterministic_trade_review(trade: Trade, meta: dict[str, Any]) -> dict[str, Any]:
        entry = float(trade.entry or 0.0)
        exit_price = float(trade.current_price or 0.0)
        gross = float(meta.get("grossPnl") or 0.0)
        net = float(trade.pnl or 0.0)
        costs = float(meta.get("estimatedCharges") or 0.0)
        stop_distance = float(meta.get("initialStopDistance") or max(0.0, entry - float(meta.get("initialStop") or trade.stop or 0.0)))
        spread_pct = max(0.0, float(meta.get("spreadPct") or 0.0))
        spread_points = entry * spread_pct / 100.0
        duration = meta.get("holdingSeconds")
        if duration is None:
            try:
                opened = trade.opened_at
                closed = trade.closed_at
                if opened and closed:
                    if opened.tzinfo is None:
                        opened = opened.replace(tzinfo=timezone.utc)
                    if closed.tzinfo is None:
                        closed = closed.replace(tzinfo=timezone.utc)
                    duration = max(0.0, (closed - opened).total_seconds())
            except Exception:
                duration = None
        duration = float(duration) if duration is not None else None
        exit_slippage = float(meta.get("exitSlippagePoints") or 0.0)
        exit_quote = meta.get("exitQuote") or meta.get("lastExitQuote") or {}
        exit_quote_source = str(exit_quote.get("source") or "")
        flags: list[str] = []
        lessons: list[str] = []

        if str(trade.status).upper() == "STOPPED" and duration is not None and duration <= 5:
            flags.append("FAST_STOP_OUT")
            lessons.append("A stop-out within five seconds needs tick/quote and spread review; duration alone does not prove the stop was too tight.")
        if stop_distance > 0 and spread_points > 0 and stop_distance <= spread_points * 2.0 + 0.05:
            flags.append("STOP_DISTANCE_NEAR_SPREAD")
            lessons.append("The planned premium stop was close to the quoted spread; validate a spread buffer before accepting similar contracts.")
        if str(trade.status).upper() == "STOPPED" and exit_slippage > max(spread_points * 2.0, entry * 0.005):
            flags.append("STOP_OVERRUN_OR_GAP")
            lessons.append("The recorded exit was materially beyond the stop; inspect quote timestamps, polling delay, gaps and fill simulation before widening the stop.")
        if exit_quote_source == "LTP_FALLBACK_UNVERIFIED":
            flags.append("UNVERIFIED_EXIT_QUOTE")
            lessons.append("Exit used an unverified last-traded-price fallback rather than executable bid depth; do not treat this fill as fully reliable.")
        if gross > 0 and net < 0:
            flags.append("COST_EROSION")
            lessons.append("Gross profit did not cover estimated round-trip charges; require a larger cost-aware target or skip the trade.")
        if not meta.get("stopPlan"):
            flags.append("LEGACY_TRADE_MISSING_RISK_PLAN")
            lessons.append("This trade predates the full ATR/Greeks stop-plan audit fields; do not infer the exact stop calculation.")
        if not flags:
            flags.append("NO_CLEAR_ANOMALY_FROM_RECORDED_DATA")
            lessons.append("No single root cause is proven by the stored trade fields; retain the sample for aggregate analysis.")

        if "STOP_OVERRUN_OR_GAP" in flags:
            root_cause = "Exit materially exceeded the stored stop; execution/quote gap requires investigation."
        elif "STOP_DISTANCE_NEAR_SPREAD" in flags:
            root_cause = "Planned premium stop may have been too close to spread noise."
        elif "FAST_STOP_OUT" in flags:
            root_cause = "Very fast stop-out; compare planned stop with ATR, spread and tick-by-tick prices."
        elif "COST_EROSION" in flags:
            root_cause = "Transaction costs erased the gross result."
        else:
            root_cause = "No definitive loss cause can be established from the available fields."

        return {
            "rootCause": root_cause,
            "flags": flags,
            "lessons": lessons,
            "holdingSeconds": round(duration, 2) if duration is not None else None,
            "initialStopDistance": round(stop_distance, 2),
            "spreadPoints": round(spread_points, 4),
            "exitSlippagePoints": round(exit_slippage, 2),
            "grossPnl": round(gross, 2),
            "netPnl": round(net, 2),
            "estimatedCosts": round(costs, 2),
            "evidenceLimit": "A final entry/exit snapshot cannot prove the intratrade price path; tick-level data is needed to distinguish a tight stop from a gap or delayed quote.",
        }

    def queue_post_trade_review(self, trade_id: int) -> None:
        with self._review_lock:
            if trade_id in self._reviews_inflight:
                return
            self._reviews_inflight.add(trade_id)
        Thread(target=self._review_closed_trade, args=(trade_id,), daemon=True).start()

    def queue_unreviewed_closed_trades(self, limit: int = 1) -> int:
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            rows = list(db.execute(
                select(Trade).where(
                    Trade.status != "OPEN",
                    Trade.status != "INVALID_CONTRACT",
                ).order_by(Trade.id.asc())
            ).scalars().all())
            pending: list[int] = []
            for trade in rows:
                try:
                    meta = json.loads(trade.reason or "{}")
                except json.JSONDecodeError:
                    meta = {}
                review = meta.get("postTradeReview") or {}
                status = str(review.get("status") or "")
                if status in {"ok", "not_configured", "rate_limited", "auth_error", "billing_error", "error"}:
                    continue
                retry_after = review.get("retryAfter")
                if retry_after:
                    try:
                        if datetime.fromisoformat(str(retry_after).replace("Z", "+00:00")) > now:
                            continue
                    except ValueError:
                        pass
                pending.append(int(trade.id))
                if len(pending) >= max(1, int(limit)):
                    break
        for trade_id in pending:
            self.queue_post_trade_review(trade_id)
        return len(pending)

    def _review_closed_trade(self, trade_id: int) -> None:
        try:
            with SessionLocal() as db:
                trade = db.get(Trade, trade_id)
                if not trade or trade.status == "OPEN":
                    return
                try:
                    meta = json.loads(trade.reason or "{}")
                except json.JSONDecodeError:
                    meta = {}
                existing = meta.get("postTradeReview") or {}
                if existing.get("status") in {"ok", "not_configured"}:
                    return
                deterministic = self._deterministic_trade_review(trade, meta)
                snapshot = {
                    "phase": "CLOSED_TRADE_REVIEW",
                    "trade": {
                        "tradeId": trade.id,
                        "instrument": meta.get("index") or trade.symbol,
                        "contract": meta.get("displayName") or trade.symbol,
                        "direction": trade.direction,
                        "strategy": meta.get("strategy") or meta.get("learningArm"),
                        "status": trade.status,
                        "exitReason": meta.get("exitReason"),
                        "entry": trade.entry,
                        "exit": trade.current_price,
                        "initialStop": meta.get("initialStop"),
                        "finalStop": meta.get("finalStop") or trade.stop,
                        "target": meta.get("initialTarget") or trade.target,
                        "quantity": trade.quantity,
                        "holdingSeconds": deterministic.get("holdingSeconds"),
                        "grossPnl": deterministic.get("grossPnl"),
                        "netPnl": deterministic.get("netPnl"),
                        "estimatedCosts": deterministic.get("estimatedCosts"),
                        "initialStopDistance": deterministic.get("initialStopDistance"),
                        "exitSlippagePoints": deterministic.get("exitSlippagePoints"),
                        "entryPriceSource": meta.get("entryPriceSource"),
                        "entryBid": meta.get("entryBid"),
                        "entryAsk": meta.get("entryAsk"),
                        "lastTradedPremium": meta.get("lastTradedPremium"),
                        "exitQuote": meta.get("exitQuote") or meta.get("lastExitQuote"),
                        "spreadPct": meta.get("spreadPct"),
                        "delta": meta.get("delta"),
                        "gamma": meta.get("gamma"),
                        "theta": meta.get("theta"),
                        "iv": meta.get("iv"),
                        "underlyingStructureDistance": meta.get("underlyingStructureDistance"),
                        "underlyingAtrPoints": meta.get("underlyingAtrPoints"),
                        "stopPlan": meta.get("stopPlan"),
                        "context": meta.get("context"),
                        "patterns": meta.get("patterns"),
                        "smc": meta.get("smc"),
                        "entryReason": meta.get("entryReason"),
                        "signalScore": meta.get("signalScore"),
                    },
                    "deterministicReview": deterministic,
                    "instruction": (
                        "Perform a post-trade root-cause review using only supplied evidence. "
                        "Separate entry quality, stop placement, option Greeks, spread, slippage, "
                        "quote delay and transaction-cost effects. Do not invent tick history. "
                        "If the cause is not proven, say UNKNOWN. Lessons and recommended changes "
                        "are hypotheses for later validation, never automatic risk overrides."
                    ),
                }
                db.commit()

            retry_after = None
            if ollama_advisor.configured:
                minimum_interval = 300.0 if ollama_advisor.provider == "openrouter" else 30.0
                with self._review_lock:
                    now_mono = time.monotonic()
                    can_call_llm = now_mono - self._last_llm_review_at >= minimum_interval
                    if can_call_llm:
                        self._last_llm_review_at = now_mono
                if can_call_llm:
                    llm = ollama_advisor.analyze_trade(snapshot)
                else:
                    retry_after = (datetime.now(timezone.utc) + timedelta(seconds=minimum_interval)).isoformat()
                    llm = {
                        "enabled": True, "status": "queued_for_llm",
                        "provider": ollama_advisor.provider, "model": ollama_advisor.model,
                        "warnings": ["Post-trade LLM review is queued to protect provider quota; deterministic diagnostics are already saved."],
                    }
            else:
                llm = {
                    "enabled": False, "status": "not_configured", "provider": "none",
                    "warnings": ["LLM provider is not configured; deterministic trade diagnostics were still recorded."],
                }
            review = {
                "status": str(llm.get("status") or "unknown"),
                "retryAfter": retry_after,
                "reviewedAt": datetime.now(timezone.utc).isoformat(),
                "provider": llm.get("provider"),
                "model": llm.get("model"),
                "deterministic": deterministic,
                "llm": {
                    "diagnosis": llm.get("diagnosis"),
                    "rootCause": llm.get("rootCause"),
                    "stopAssessment": llm.get("stopAssessment"),
                    "entryAssessment": llm.get("entryAssessment"),
                    "executionAssessment": llm.get("executionAssessment"),
                    "lessons": llm.get("lessons") or [],
                    "recommendedChanges": llm.get("recommendedChanges") or [],
                    "reasons": llm.get("reasons") or [],
                    "warnings": llm.get("warnings") or [],
                },
            }
            with SessionLocal() as db:
                trade = db.get(Trade, trade_id)
                if trade:
                    try:
                        meta = json.loads(trade.reason or "{}")
                    except json.JSONDecodeError:
                        meta = {}
                    meta["postTradeReview"] = review
                    trade.reason = json.dumps(meta, default=str)
                    db.commit()
        except Exception as exc:
            try:
                with SessionLocal() as db:
                    trade = db.get(Trade, trade_id)
                    if trade:
                        try:
                            meta = json.loads(trade.reason or "{}")
                        except json.JSONDecodeError:
                            meta = {}
                        meta["postTradeReview"] = {
                            "status": "error",
                            "reviewedAt": datetime.now(timezone.utc).isoformat(),
                            "error": str(exc)[:220],
                        }
                        trade.reason = json.dumps(meta, default=str)
                        db.commit()
            except Exception:
                pass
        finally:
            with self._review_lock:
                self._reviews_inflight.discard(trade_id)

    def observe(self, signal: dict[str, Any], *, candle_time: datetime | None = None) -> dict[str, Any]:
        context = signal.get("context") or {}
        smc = signal.get("smc") or {}
        stored_context = {
            **context,
            "smc": smc,
            "smcOverride": bool(signal.get("smcOverride")),
            "entryReason": signal.get("entryReason"),
            "entryThreshold": signal.get("entryThreshold"),
            "directionCandidate": signal.get("directionCandidate"),
            "blockedBy": signal.get("blockedBy") or [],
            "confirmations": signal.get("confirmations") or {},
        }
        patterns = signal.get("patterns") or {}
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

            minute = candle_time.minute if candle_time else now.minute
            high_value = str(signal.get("action") or "NO_TRADE") in {"CE", "PE"} or float(signal.get("score") or 0) >= 0.65
            scheduled_review = minute % 15 == 0
            if ollama_advisor.configured and (high_value or scheduled_review):
                llm = ollama_advisor.analyze_trade({
                    "phase": "LIVE_OBSERVATION",
                    "instrument": signal.get("index"),
                    "marketPrice": signal.get("underlyingPrice"),
                    "action": signal.get("action", "NO_TRADE"),
                    "signalScore": signal.get("score", 0),
                    "strategy": signal.get("chosenStrategy") or signal.get("strategy"),
                    "context": stored_context,
                    "smc": smc,
                    "patterns": patterns,
                    "instruction": "Review the current market moment only. Do not override hard risk or place orders.",
                })
            else:
                llm = {
                    "enabled": bool(ollama_advisor.configured),
                    "status": "quota_saver" if ollama_advisor.configured else "not_configured",
                    "provider": ollama_advisor.provider,
                    "model": ollama_advisor.model,
                    "bias": "NEUTRAL",
                    "confidence": 0.0,
                    "risk": "UNKNOWN",
                    "reasons": [],
                    "warnings": [],
                }
            row = LiveMarketObservation(
                observed_at=now,
                instrument=str(signal.get("index") or "UNKNOWN"),
                timeframe="1m",
                candle_time=candle_time,
                market_price=float(signal.get("underlyingPrice") or 0.0),
                action=str(signal.get("action") or "NO_TRADE"),
                signal_score=float(signal.get("score") or 0.0),
                strategy=str(signal.get("chosenStrategy") or signal.get("strategy") or ""),
                context_json=json.dumps(stored_context, default=str),
                patterns_json=json.dumps(patterns, default=str),
                llm_json=json.dumps(llm, default=str),
                decision_json=json.dumps({
                    "reason": signal.get("reason"),
                    "previousHigh": signal.get("previousHigh"),
                    "previousLow": signal.get("previousLow"),
                    "entryReason": signal.get("entryReason"),
                    "entryThreshold": signal.get("entryThreshold"),
                    "smcOverride": bool(signal.get("smcOverride")),
                    "directionCandidate": signal.get("directionCandidate"),
                    "blockedBy": signal.get("blockedBy") or [],
                    "confirmations": signal.get("confirmations") or {},
                }, default=str),
                outcome="OBSERVED" if str(signal.get("action") or "NO_TRADE") == "NO_TRADE" else "PENDING",
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

    def mark_execution_block(
        self,
        observation_id: int,
        *,
        stage: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Mark a directional signal that did not become a paper position."""
        with SessionLocal() as db:
            row = db.get(LiveMarketObservation, observation_id)
            if not row or row.trade_id is not None:
                return
            try:
                decision = json.loads(row.decision_json or "{}")
            except json.JSONDecodeError:
                decision = {}
            decision["executionBlock"] = {
                "stage": str(stage),
                "reason": str(reason),
                "details": details or {},
                "at": datetime.now(timezone.utc).isoformat(),
            }
            row.decision_json = json.dumps(decision, default=str)
            if row.action in {"CE", "PE"}:
                row.outcome = "BLOCKED"
                row.outcome_pnl = 0.0
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
        self.queue_post_trade_review(trade_id)
        return len(rows)

    def snapshot(self, limit: int = 12) -> dict[str, Any]:
        with SessionLocal() as db:
            # One-time/idempotent cleanup for rows created before NO_TRADE was classified as OBSERVED.
            repaired = db.execute(
                update(LiveMarketObservation)
                .where(
                    LiveMarketObservation.action == "NO_TRADE",
                    LiveMarketObservation.outcome == "PENDING",
                )
                .values(outcome="OBSERVED", outcome_pnl=0.0)
            )
            if repaired.rowcount:
                db.commit()

            # Old directional observations without an attached trade were signals only,
            # not open outcomes. Repair them after a short grace period so PENDING means
            # an actually attached/open paper trade.
            stale_pending_before = datetime.now(timezone.utc) - timedelta(minutes=2)
            orphaned = db.execute(
                update(LiveMarketObservation)
                .where(
                    LiveMarketObservation.action.in_(["CE", "PE"]),
                    LiveMarketObservation.outcome == "PENDING",
                    LiveMarketObservation.trade_id.is_(None),
                    LiveMarketObservation.observed_at < stale_pending_before,
                )
                .values(outcome="BLOCKED", outcome_pnl=0.0)
            )
            if orphaned.rowcount:
                db.commit()

            valid_filter = LiveMarketObservation.market_price > 0
            total = db.scalar(select(func.count()).select_from(LiveMarketObservation).where(valid_filter)) or 0
            pending = db.scalar(select(func.count()).select_from(LiveMarketObservation).where(
                valid_filter,
                LiveMarketObservation.outcome == "PENDING",
                LiveMarketObservation.action.in_(["CE", "PE"]),
                LiveMarketObservation.trade_id.is_not(None),
            )) or 0
            rows = list(db.execute(
                select(LiveMarketObservation).where(valid_filter).order_by(LiveMarketObservation.id.desc()).limit(max(1, min(limit, 50)))
            ).scalars().all())
            return {
                "enabled": True,
                "mode": "LIVE_MARKET_OBSERVATION",
                "totalObservations": int(total),
                "pendingOutcomes": int(pending),
                "legacyNoTradeRowsRepaired": int(repaired.rowcount or 0),
                "legacyUnattachedSignalsRepaired": int(orphaned.rowcount or 0),
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
