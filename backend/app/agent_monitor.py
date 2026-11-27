from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class AgentMonitor:
    """In-process observability for real APEX workers; it does not create trading agents."""

    DEFINITIONS = (
        ("market_scanner", "Market Scanner", "Trading", "Scheduled market scans and signal evaluation"),
        ("position_monitor", "Position Monitor", "Risk", "Open-position price, stop and target monitoring"),
        ("learning_orchestrator", "Learning Orchestrator", "Learning", "Scheduled research and model-learning cycle"),
        ("llm_research", "LLM Research", "LLM", "Research synthesis and market-context reasoning"),
        ("strategy_lab", "Strategy Lab", "Research", "Historical strategy tests and walk-forward evaluation"),
        ("dataset_builder", "Dataset Builder", "ML", "Market observation and labeled dataset ingestion"),
        ("neural_trainer", "Neural Trainer", "ML", "Offline candidate training and evaluation"),
    )
    LEARNING_CHILDREN = ("llm_research", "strategy_lab", "dataset_builder", "neural_trainer")

    def __init__(self) -> None:
        self._lock = RLock()
        now = _now()
        self._agents: dict[str, dict[str, Any]] = {
            agent_id: {
                "id": agent_id,
                "name": name,
                "group": group,
                "description": description,
                "status": "IDLE",
                "currentTask": "Waiting for scheduled work",
                "startedAt": None,
                "lastHeartbeatAt": None,
                "lastCompletedAt": None,
                "runs": 0,
                "failures": 0,
                "lastDurationMs": None,
                "lastError": None,
            }
            for agent_id, name, group, description in self.DEFINITIONS
        }

    def begin(self, agent_id: str, task: str) -> None:
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return
            now = _now()
            if agent["status"] != "RUNNING":
                agent["startedAt"] = now
                agent["runs"] += 1
            agent["status"] = "RUNNING"
            agent["currentTask"] = str(task or "Working")[:240]
            agent["lastHeartbeatAt"] = now
            agent["lastError"] = None

    def heartbeat(self, agent_id: str, task: str | None = None) -> None:
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return
            now = _now()
            if agent["status"] != "RUNNING":
                agent["startedAt"] = now
                agent["runs"] += 1
            agent["status"] = "RUNNING"
            if task:
                agent["currentTask"] = str(task)[:240]
            agent["lastHeartbeatAt"] = now
            agent["lastError"] = None

    def finish(self, agent_id: str, task: str = "Waiting for scheduled work") -> None:
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return
            now = _now()
            started = agent.get("startedAt")
            if started:
                agent["lastDurationMs"] = max(0, int((now - started).total_seconds() * 1000))
            agent["status"] = "IDLE"
            agent["currentTask"] = task
            agent["lastHeartbeatAt"] = now
            agent["lastCompletedAt"] = now
            agent["startedAt"] = None

    def fail(self, agent_id: str, error: str) -> None:
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return
            now = _now()
            started = agent.get("startedAt")
            if started:
                agent["lastDurationMs"] = max(0, int((now - started).total_seconds() * 1000))
            agent["status"] = "FAILED"
            agent["currentTask"] = "Last execution failed"
            agent["lastHeartbeatAt"] = now
            agent["lastCompletedAt"] = now
            agent["startedAt"] = None
            agent["failures"] += 1
            agent["lastError"] = str(error)[:300]

    def transition_learning(self, stage: str, task: str) -> None:
        self.heartbeat("learning_orchestrator", task)
        stage = str(stage or "").lower()
        mapping = {
            "source_research": "llm_research",
            "reasoning": "llm_research",
            "strategy_lab": "strategy_lab",
            "walk_forward": "strategy_lab",
            "dataset": "dataset_builder",
            "neural_training": "neural_trainer",
        }
        active = mapping.get(stage)
        with self._lock:
            for agent_id in self.LEARNING_CHILDREN:
                agent = self._agents[agent_id]
                if agent_id != active and agent["status"] == "RUNNING":
                    now = _now()
                    started = agent.get("startedAt")
                    if started:
                        agent["lastDurationMs"] = max(0, int((now - started).total_seconds() * 1000))
                    agent["status"] = "IDLE"
                    agent["currentTask"] = "Waiting for next learning stage"
                    agent["lastHeartbeatAt"] = now
                    agent["lastCompletedAt"] = now
                    agent["startedAt"] = None
        if active:
            self.heartbeat(active, task)

    def snapshot(self) -> dict[str, Any]:
        now = _now()
        with self._lock:
            rows = []
            for raw in self._agents.values():
                agent = dict(raw)
                heartbeat = agent.get("lastHeartbeatAt")
                if agent["status"] == "RUNNING" and heartbeat and (now - heartbeat).total_seconds() > 120:
                    agent["status"] = "STALE"
                agent["startedAt"] = _iso(agent["startedAt"])
                agent["lastHeartbeatAt"] = _iso(agent["lastHeartbeatAt"])
                agent["lastCompletedAt"] = _iso(agent["lastCompletedAt"])
                rows.append(agent)
        counts = {key.lower(): sum(1 for row in rows if row["status"] == key) for key in ("RUNNING", "IDLE", "FAILED", "STALE", "DISABLED")}
        return {
            "updatedAt": now.isoformat(),
            "registered": len(rows),
            "running": counts["running"],
            "idle": counts["idle"],
            "failed": counts["failed"],
            "stale": counts["stale"],
            "disabled": counts["disabled"],
            "agents": rows,
            "note": "Counts are real in-process APEX workers and sub-workers instrumented in this backend. Registered is not the number of independently trading bots; agents cannot place orders or bypass risk controls.",
        }


agent_monitor = AgentMonitor()
