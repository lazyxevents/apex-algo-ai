from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo

from .core import SessionLocal, settings
from .dataset_model import dataset_model_service
from .llm_research import ollama_research
from .market_research import build_market_research
from .models import ResearchActivity
from .neural_model import neural_model_service
from .research_engine import research_engine
from .strategy import adaptive_learner
from .strategy_lab import strategy_lab

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
    deepCyclesToday: int = 0
    researchHoursToday: float = 0.0
    learningWindowActive: bool = False
    sourcesReviewed: int = 0
    newsReviewed: int = 0
    patternsDetected: int = 0
    hypothesesTested: int = 0
    backtestsRun: int = 0
    strategyLabTests: int = 0
    candidateScore: float | None = None
    lastSummary: str = ""
    lastError: str | None = None
    datasetSize: int = 0
    datasetInsertedLastCycle: int = 0
    candidateVersion: str | None = None
    candidateMetrics: dict | None = None
    researchPhase: str = "IDLE"
    knowledgeCount: int = 0
    latestPlan: dict | None = None
    sectorsTracked: int = 0
    neuralVersion: str | None = None
    neuralRole: str = "WAITING"
    neuralTrainedSamples: int = 0
    neuralOosAuc: float | None = None
    neuralOosBrier: float | None = None
    lastStrategyLab: dict | None = None


class ContinuousLearningWorker:
    """Bounded learning/research worker. It never places orders or changes hard risk limits."""

    def __init__(self) -> None:
        self.state = LearningState()
        self._lock = Lock()

    def _heartbeat(self, stage: str, task: str) -> None:
        self.state.stage = stage
        self.state.currentTask = task
        self.state.lastHeartbeatAt = datetime.now(IST).isoformat()

    @staticmethod
    def _activity(kind: str, stage: str, title: str, detail: dict | None = None) -> None:
        try:
            with SessionLocal() as db:
                db.add(ResearchActivity(
                    kind=kind[:48],
                    stage=stage[:48],
                    title=title[:300],
                    detail_json=json.dumps(detail or {}, default=str)[:20000],
                ))
                db.commit()
        except Exception:
            pass

    @staticmethod
    def _inside_learning_window(now: datetime) -> bool:
        hm = now.strftime("%H:%M")
        start = settings.learning_worker_window_start_time
        end = settings.learning_worker_window_end_time
        if start > end:  # overnight window, e.g. 17:00 -> 08:00
            return hm >= start or hm < end
        return start <= hm < end

    def snapshot(self) -> dict:
        data = asdict(self.state)
        now = datetime.now(IST)
        data["learningWindowActive"] = self._inside_learning_window(now) or now.weekday() >= 5
        intelligence = research_engine.snapshot()
        data["knowledgeCount"] = int(intelligence.get("knowledgeCount") or 0)
        data["latestPlan"] = intelligence.get("latestPlan") or {}
        data["sectorsTracked"] = len(intelligence.get("latestSectors") or [])
        data["newsHealth"] = intelligence.get("newsHealth") or {}
        data["todayActivityCount"] = intelligence.get("todayActivityCount") or 0
        data["todayNewsCount"] = intelligence.get("todayNewsCount") or 0
        neural = neural_model_service.snapshot()
        production = neural.get("production") or {}
        latest_neural = neural.get("latest") or {}
        data["neural"] = neural
        data["neuralVersion"] = production.get("version") or latest_neural.get("version")
        data["neuralRole"] = production.get("role") or latest_neural.get("role") or "WAITING"
        data["neuralTrainedSamples"] = int(production.get("trainedSamples") or latest_neural.get("trainedSamples") or 0)
        metrics = (production.get("metrics") or latest_neural.get("metrics") or {}).get("outOfSample") or {}
        data["neuralOosAuc"] = metrics.get("auc")
        data["neuralOosBrier"] = metrics.get("brier")
        data.update({
            "intervalMinutes": settings.learning_worker_interval_minutes,
            "dailyHourBudget": settings.learning_worker_daily_hours,
            "learningWindow": f"{settings.learning_worker_window_start_time}-{settings.learning_worker_window_end_time}",
            "premarketPlanTime": settings.premarket_plan_time,
            "maxSourcesPerCycle": settings.learning_worker_max_sources,
            "safetyBoundary": "research_and_learning_only_no_direct_orders_no_risk_override",
        })
        return data

    @staticmethod
    def _research_phase(now: datetime) -> str:
        hm = now.strftime("%H:%M")
        if now.weekday() >= 5:
            return "WEEKEND_RESEARCH"
        if ContinuousLearningWorker._inside_learning_window(now):
            return "NIGHT_RESEARCH"
        if settings.premarket_plan_time <= hm < settings.market_open_time:
            return "PREMARKET"
        if settings.market_open_time <= hm < settings.stop_new_trade_time:
            return "LIVE_SESSION"
        if settings.stop_new_trade_time <= hm < settings.learning_worker_window_start_time:
            return "POSTMARKET"
        return "OFF_HOURS"

    @staticmethod
    def _pattern_count(analytics: dict) -> int:
        total = 0
        for market in (analytics.get("markets") or {}).values():
            for frame in (market.get("frames") or {}).values():
                patterns = frame.get("patterns") or {}
                total += len(patterns.get("bullish") or []) + len(patterns.get("bearish") or []) + len(patterns.get("neutral") or [])
                structure = frame.get("structure") or {}
                total += sum(
                    1 for key in (
                        "bos", "choch", "liquiditySweep", "fairValueGap", "fakeBreakout",
                        "higherHigh", "higherLow", "lowerHigh", "lowerLow",
                    )
                    if structure.get(key) not in (None, "NONE", False)
                )
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
                self.state.deepCyclesToday = 0
                self.state.researchHoursToday = 0.0
                self.state.sourcesReviewed = 0
                self.state.newsReviewed = 0
                self.state.patternsDetected = 0
                self.state.hypothesesTested = 0
                self.state.backtestsRun = 0
                self.state.strategyLabTests = 0

            self.state.enabled = True
            self.state.running = True
            self.state.lastError = None
            phase = self._research_phase(started)
            self.state.researchPhase = phase
            self.state.learningWindowActive = self._inside_learning_window(started) or started.weekday() >= 5
            self._heartbeat("market_research", f"{phase}: analyzing 1m / 5m / 15m trend, SMC, levels and candle patterns")

            if not provider.market_ready:
                self._heartbeat("waiting_market", "Market provider is not ready; no fabricated market research generated")
                return self.snapshot()

            analytics = build_market_research(provider)
            patterns = self._pattern_count(analytics)
            self.state.patternsDetected += patterns

            deep_research = force or phase in {"NIGHT_RESEARCH", "POSTMARKET", "PREMARKET", "WEEKEND_RESEARCH"}
            research_bundle = {"session": phase, "sources": [], "news": [], "sectors": [], "plan": {}}
            if deep_research:
                if not force and self.state.researchHoursToday >= settings.learning_worker_daily_hours:
                    self._heartbeat("budget_complete", "15-hour research coverage budget completed; live monitoring remains active")
                    return self.snapshot()

                self._heartbeat("source_research", f"{phase}: public education + current news + sectors + next-session scenarios")
                research_bundle = research_engine.run(analytics, phase)
                reviewed = len(research_bundle.get("sources") or [])
                news_count = len(research_bundle.get("news") or [])
                self.state.sourcesReviewed += min(reviewed, settings.learning_worker_max_sources)
                self.state.newsReviewed += news_count
                self.state.sectorsTracked = len(research_bundle.get("sectors") or [])
                self.state.latestPlan = research_bundle.get("plan") or {}

                self._heartbeat("reasoning", "Connecting news, gap context, patterns and SMC evidence to research hypotheses")
                summary = ollama_research.summarize(analytics, research_bundle)
                observations = summary.get("observations") or []
                self.state.hypothesesTested += max(1, len(observations))

                if phase in {"POSTMARKET", "NIGHT_RESEARCH", "WEEKEND_RESEARCH"}:
                    self._heartbeat("strategy_lab", "Counterfactual backtesting: entry here vs target/stop on recent historical setups")
                    lab = strategy_lab.run(provider)
                    self.state.lastStrategyLab = lab
                    lab_tests = int(lab.get("totalSetupTests") or 0)
                    self.state.strategyLabTests += lab_tests
                    self.state.backtestsRun += 1
                else:
                    lab = self.state.lastStrategyLab or {"status": "deferred_to_postmarket"}

                self.state.deepCyclesToday += 1
                # Coverage represents scheduled learning-window coverage, not CPU wall-clock runtime.
                coverage = settings.learning_worker_interval_minutes / 60.0
                self.state.researchHoursToday = round(
                    min(settings.learning_worker_daily_hours, self.state.researchHoursToday + coverage),
                    2,
                )
            else:
                self._heartbeat(
                    "live_session",
                    "Live session: storing fresh candle moments and outcomes; web crawling/backtest/neural retraining paused",
                )
                summary = {
                    "summary": f"Live market learning: {patterns} pattern/SMC/structure observations in current research snapshot.",
                    "observations": [],
                }
                lab = self.state.lastStrategyLab or {}

            self._heartbeat("dataset", "Building labeled SMC scalp/swing examples from recent 1-minute history")
            dataset = dataset_model_service.ingest(provider)
            self.state.datasetSize = int(dataset.get("datasetSize", 0))
            self.state.datasetInsertedLastCycle = int(dataset.get("inserted", 0))
            if self.state.datasetInsertedLastCycle:
                self._activity(
                    "DATASET",
                    phase,
                    f"Added {self.state.datasetInsertedLastCycle} new labeled market setups",
                    {
                        "datasetSize": self.state.datasetSize,
                        "inserted": self.state.datasetInsertedLastCycle,
                        "patternInsights": (dataset.get("patternInsights") or [])[:8],
                    },
                )

            if phase in {"POSTMARKET", "NIGHT_RESEARCH", "WEEKEND_RESEARCH"}:
                self._heartbeat("walk_forward", "Running adaptive-arm walk-forward research on recent market history")
                backtest = adaptive_learner.daily_research(provider)
                arms = backtest.get("arms") or {}
                self.state.backtestsRun += len(arms)
            else:
                latest = adaptive_learner.snapshot().get("latestResearch") or {}
                backtest = latest
                arms = latest.get("arms") or {}

            scores = [
                float((v or {}).get("score") or (v or {}).get("expectancyR") or 0)
                for v in arms.values() if isinstance(v, dict)
            ]
            self.state.candidateScore = round(max(scores), 4) if scores else None

            neural_phase = phase in {"POSTMARKET", "NIGHT_RESEARCH", "PREMARKET", "WEEKEND_RESEARCH"}
            if neural_phase:
                self._heartbeat("neural_training", "Training/evaluating MLP only on eligible labeled SMC V2 outcomes")
                candidate = neural_model_service.train_candidate()
                self.state.candidateVersion = candidate.get("version")
                self.state.candidateMetrics = candidate
                self.state.neuralVersion = candidate.get("version")
                self.state.neuralRole = candidate.get("role") or candidate.get("status") or "WAITING"
                self.state.neuralTrainedSamples = int(candidate.get("eligibleSamples") or candidate.get("trainedSamples") or 0)
                oos = candidate.get("outOfSample") or {}
                self.state.neuralOosAuc = oos.get("auc")
                self.state.neuralOosBrier = oos.get("brier")
                self._activity(
                    "NEURAL",
                    phase,
                    f"Neural cycle: {self.state.neuralRole}",
                    {
                        "version": self.state.neuralVersion,
                        "trainedSamples": self.state.neuralTrainedSamples,
                        "oosAuc": self.state.neuralOosAuc,
                        "oosBrier": self.state.neuralOosBrier,
                        "status": candidate.get("status"),
                    },
                )
            else:
                self._heartbeat("candidate_evaluation", "Live session: neural weights frozen; promoted model used only for inference")
                neural = neural_model_service.snapshot()
                candidate = neural.get("production") or neural.get("latest") or {"status": "waiting"}
                self.state.candidateVersion = candidate.get("version")
                self.state.candidateMetrics = candidate.get("metrics") or candidate

            self.state.cyclesToday += 1
            plan = research_bundle.get("plan") or {}
            news_health = research_bundle.get("newsHealth") or {}
            plan_note = (
                f" Plan {plan.get('planDate')} bias={plan.get('marketBias')}."
                if plan else ""
            )
            lab_note = (
                f" Strategy lab tested {int((lab or {}).get('totalSetupTests') or 0)} setups."
                if lab else ""
            )
            news_note = (
                f" News={news_health.get('status','N/A')} ({news_health.get('fetchedCount',0)} fresh)."
                if news_health else ""
            )
            self.state.lastSummary = (
                str(summary.get("summary") or f"Cycle completed with {patterns} pattern/structure observations.")[:420]
                + plan_note + lab_note + news_note
            )[:700]
            self.state.lastCompletedAt = datetime.now(IST).isoformat()
            self._activity(
                "LEARNING_CYCLE",
                phase,
                f"Completed {phase} learning cycle #{self.state.cyclesToday}",
                {
                    "patternsDetectedThisSnapshot": patterns,
                    "datasetSize": self.state.datasetSize,
                    "newLabels": self.state.datasetInsertedLastCycle,
                    "researchCoverageHours": self.state.researchHoursToday,
                    "strategyLabTests": int((lab or {}).get("totalSetupTests") or 0),
                    "summary": self.state.lastSummary,
                },
            )
            self._heartbeat("complete", "Cycle complete; waiting for next scheduled learning/research pass")
            return self.snapshot()
        except Exception as exc:
            self.state.lastError = str(exc)[:300]
            self._activity("LEARNING_ERROR", self.state.researchPhase, "Learning cycle failed safely", {"error": self.state.lastError})
            self._heartbeat("error", "Learning cycle failed safely; trading risk controls were not changed")
            return self.snapshot()
        finally:
            self.state.running = False
            self._lock.release()


learning_worker = ContinuousLearningWorker()
