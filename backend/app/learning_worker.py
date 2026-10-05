from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo

from .core import settings
from .llm_research import ollama_research
from .market_research import build_market_research
from .strategy import adaptive_learner

IST = ZoneInfo(settings.timezone)


@dataclass
class LearningState:
    enabled: bool = settings.learning_worker_enabled
    running: bool = False
    stage: str = "idle"
    currentTask: str = "Waiting for next research cycle"
    lastHeartbeatAt: str | None = None
    lastCompletedAt: str | None = None
    runDate: str | None = None
    cyclesToday: int = 0
    researchHoursToday: float = 0.0
    sourcesReviewed: int = 0
    patternsDetected: int = 0
    hypothesesTested: int = 0
    backtestsRun: int = 0
    candidateScore: float | None = None
    lastSummary: str = ""
    lastError: str | None = None


class ContinuousLearningWorker:
    """Bounded research worker. It never places orders or changes hard risk limits."""

    def __init__(self) -> None:
        self.state = LearningState()
        self._lock = Lock()

    def _heartbeat(self, stage: str, task: str) -> None:
        self.state.stage = stage
        self.state.currentTask = task
        self.state.lastHeartbeatAt = datetime.now(IST).isoformat()

    def snapshot(self) -> dict:
        data = asdict(self.state)
        data.update({
            "intervalMinutes": settings.learning_worker_interval_minutes,
            "dailyHourBudget": settings.learning_worker_daily_hours,
            "maxSourcesPerCycle": settings.learning_worker_max_sources,
            "safetyBoundary": "research_only_no_direct_orders_no_risk_override",
        })
        return data

    @staticmethod
    def _pattern_count(analytics: dict) -> int:
        total = 0
        for market in (analytics.get("markets") or {}).values():
            for frame in (market.get("frames") or {}).values():
                patterns = frame.get("patterns") or {}
                total += len(patterns.get("bullish") or []) + len(patterns.get("bearish") or [])
                structure = frame.get("structure") or {}
                total += sum(1 for key in ("bos", "choch", "liquiditySweep", "fairValueGap", "fakeBreakout") if structure.get(key) not in (None, "NONE"))
        return total

    def run_cycle(self, provider, force: bool = False) -> dict:
        if not settings.learning_worker_enabled and not force:
            self.state.enabled = False
            self._heartbeat("disabled", "Learning worker disabled by configuration")
            return self.snapshot()
        if not self._lock.acquire(blocking=False):
            return self.snapshot()
        started = datetime.now(IST)
        try:
            today = started.date().isoformat()
            if self.state.runDate != today:
                self.state.runDate = today
                self.state.cyclesToday = 0
                self.state.researchHoursToday = 0.0
                self.state.sourcesReviewed = 0
                self.state.patternsDetected = 0
                self.state.hypothesesTested = 0
                self.state.backtestsRun = 0
            if not force and self.state.researchHoursToday >= settings.learning_worker_daily_hours:
                self._heartbeat("budget_complete", "Daily research-hour budget completed")
                return self.snapshot()

            self.state.enabled = True
            self.state.running = True
            self.state.lastError = None
            self._heartbeat("market_research", "Reading 1m / 5m / 15m market structure and candlestick evidence")
            if not provider.market_ready:
                self._heartbeat("waiting_market", "Market provider is not ready; no fabricated research generated")
                return self.snapshot()

            analytics = build_market_research(provider)
            patterns = self._pattern_count(analytics)
            self.state.patternsDetected += patterns

            self._heartbeat("source_research", "Collecting bounded news/macro sources for context")
            news = ollama_research.news_research()
            sources = min(len(news.get("results") or []), settings.learning_worker_max_sources)
            self.state.sourcesReviewed += sources

            self._heartbeat("hypothesis", "Generating evidence summary and market-regime hypotheses")
            summary = ollama_research.summarize(analytics, news)
            observations = summary.get("observations") or []
            self.state.hypothesesTested += max(1, len(observations))

            self._heartbeat("backtest", "Running approved strategy walk-forward research")
            backtest = adaptive_learner.daily_research(provider)
            arms = backtest.get("arms") or {}
            self.state.backtestsRun += len(arms)
            scores = [float(v.get("score", 0) or 0) for v in arms.values() if isinstance(v, dict)]
            self.state.candidateScore = round(max(scores), 4) if scores else None

            elapsed_hours = max((datetime.now(IST) - started).total_seconds() / 3600, 0.01)
            self.state.researchHoursToday = round(min(settings.learning_worker_daily_hours, self.state.researchHoursToday + elapsed_hours), 3)
            self.state.cyclesToday += 1
            self.state.lastSummary = str(summary.get("summary") or f"Cycle completed: {patterns} pattern/structure observations; {len(arms)} approved arms backtested.")[:600]
            self.state.lastCompletedAt = datetime.now(IST).isoformat()
            self._heartbeat("complete", "Research cycle complete; waiting for next scheduled cycle")
            return self.snapshot()
        except Exception as exc:
            self.state.lastError = str(exc)[:300]
            self._heartbeat("error", "Learning cycle failed safely; trading risk controls were not changed")
            return self.snapshot()
        finally:
            self.state.running = False
            self._lock.release()


learning_worker = ContinuousLearningWorker()
