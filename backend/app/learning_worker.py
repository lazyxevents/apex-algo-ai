from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo

from .core import settings
from .llm_research import ollama_research
from .dataset_model import dataset_model_service
from .market_research import build_market_research
from .research_engine import research_engine
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
    datasetSize: int = 0
    candidateVersion: str | None = None
    candidateMetrics: dict | None = None
    researchPhase: str = "IDLE"
    knowledgeCount: int = 0
    latestPlan: dict | None = None
    sectorsTracked: int = 0


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
        intelligence = research_engine.snapshot()
        data["knowledgeCount"] = int(intelligence.get("knowledgeCount") or 0)
        data["latestPlan"] = intelligence.get("latestPlan") or {}
        data["sectorsTracked"] = len(intelligence.get("latestSectors") or [])
        data.update({
            "intervalMinutes": settings.learning_worker_interval_minutes,
            "dailyHourBudget": settings.learning_worker_daily_hours,
            "maxSourcesPerCycle": settings.learning_worker_max_sources,
            "safetyBoundary": "research_only_no_direct_orders_no_risk_override",
        })
        return data

    @staticmethod
    def _research_phase(now: datetime) -> str:
        hm = now.strftime("%H:%M")
        if now.weekday() >= 5:
            return "WEEKEND_RESEARCH"
        if hm < settings.market_open_time:
            return "PREMARKET"
        if hm < settings.stop_new_trade_time:
            return "LIVE_SESSION"
        return "POSTMARKET"

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
            phase = self._research_phase(started)
            self.state.researchPhase = phase
            self._heartbeat("market_research", f"{phase}: reading 1m / 5m / 15m structure and candlestick evidence")
            if not provider.market_ready:
                self._heartbeat("waiting_market", "Market provider is not ready; no fabricated research generated")
                return self.snapshot()

            analytics = build_market_research(provider)
            patterns = self._pattern_count(analytics)
            self.state.patternsDetected += patterns

            deep_research = force or phase in {"POSTMARKET", "PREMARKET", "WEEKEND_RESEARCH"}
            research_bundle = {"session": phase, "sources": [], "news": [], "sectors": [], "plan": {}}
            if deep_research:
                self._heartbeat("source_research", f"{phase}: reading public education, Google News RSS and Indian sector data")
                research_bundle = research_engine.run(analytics, phase)
                reviewed = len(research_bundle.get("sources") or []) + len(research_bundle.get("news") or [])
                self.state.sourcesReviewed += min(reviewed, settings.learning_worker_max_sources)
                self.state.sectorsTracked = len(research_bundle.get("sectors") or [])
                self.state.latestPlan = research_bundle.get("plan") or {}
                self._heartbeat("hypothesis", "Connecting research evidence to scalp/swing/SMC hypotheses")
                summary = ollama_research.summarize(analytics, research_bundle)
                observations = summary.get("observations") or []
                self.state.hypothesesTested += max(1, len(observations))
            else:
                self._heartbeat("live_session", "Live session: external crawler paused; learning only from market structure and outcomes until 15:15")
                summary = {
                    "summary": f"Live session learning: {patterns} current pattern/SMC observations. Deep web/sector research starts after {settings.stop_new_trade_time} IST.",
                    "observations": [],
                }

            self._heartbeat("dataset", "Building structured 1-minute scalp/swing setup/outcome dataset")
            dataset = dataset_model_service.ingest(provider)
            self.state.datasetSize = int(dataset.get("datasetSize", 0))

            if phase in {"POSTMARKET", "WEEKEND_RESEARCH"}:
                self._heartbeat("backtest", "Postmarket: running approved strategy walk-forward research")
                backtest = adaptive_learner.daily_research(provider)
                arms = backtest.get("arms") or {}
                self.state.backtestsRun += len(arms)
            else:
                self._heartbeat("backtest", f"{phase}: reusing last completed walk-forward research; full daily backtest waits for postmarket")
                latest = adaptive_learner.snapshot().get("latestResearch") or {}
                backtest = latest
                arms = latest.get("arms") or {}
            scores = [
                float((v or {}).get("score") or (v or {}).get("expectancyR") or 0)
                for v in arms.values() if isinstance(v, dict)
            ]
            self.state.candidateScore = round(max(scores), 4) if scores else None

            self._heartbeat("candidate_evaluation", "Evaluating candidate probability baseline on chronological holdout data")
            candidate = dataset_model_service.evaluate_candidate()
            self.state.candidateVersion = candidate.get("version")
            self.state.candidateMetrics = candidate

            elapsed_hours = max((datetime.now(IST) - started).total_seconds() / 3600, 0.01)
            self.state.researchHoursToday = round(min(settings.learning_worker_daily_hours, self.state.researchHoursToday + elapsed_hours), 3)
            self.state.cyclesToday += 1
            plan = research_bundle.get("plan") or {}
            plan_note = f" Next plan: {plan.get('planDate')} • bias {plan.get('marketBias')}." if plan else ""
            self.state.lastSummary = str(summary.get("summary") or f"Cycle completed: {patterns} pattern/structure observations; {len(arms)} approved arms backtested.")[:520] + plan_note
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
