from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent_monitor import AgentMonitor


def test_agent_monitor_tracks_real_worker_lifecycle():
    monitor = AgentMonitor()

    initial = monitor.snapshot()
    assert initial["registered"] == 7
    assert initial["running"] == 0

    monitor.begin("market_scanner", "Scanning market")
    running = monitor.snapshot()
    scanner = next(row for row in running["agents"] if row["id"] == "market_scanner")
    assert scanner["status"] == "RUNNING"
    assert scanner["runs"] == 1

    monitor.finish("market_scanner", "Waiting for next scan")
    finished = monitor.snapshot()
    scanner = next(row for row in finished["agents"] if row["id"] == "market_scanner")
    assert scanner["status"] == "IDLE"
    assert scanner["lastCompletedAt"] is not None


def test_learning_stage_transitions_only_mark_current_subworker_running():
    monitor = AgentMonitor()
    monitor.begin("learning_orchestrator", "Learning cycle")
    monitor.transition_learning("reasoning", "LLM reasoning")
    first = monitor.snapshot()
    assert next(row for row in first["agents"] if row["id"] == "llm_research")["status"] == "RUNNING"

    monitor.transition_learning("dataset", "Ingesting labeled samples")
    second = monitor.snapshot()
    statuses = {row["id"]: row["status"] for row in second["agents"]}
    assert statuses["llm_research"] == "IDLE"
    assert statuses["dataset_builder"] == "RUNNING"
    assert statuses["learning_orchestrator"] == "RUNNING"


def test_agent_monitor_records_failures_without_claiming_running():
    monitor = AgentMonitor()
    monitor.begin("position_monitor", "Monitoring open positions")
    monitor.fail("position_monitor", "provider timeout")
    row = next(row for row in monitor.snapshot()["agents"] if row["id"] == "position_monitor")
    assert row["status"] == "FAILED"
    assert row["failures"] == 1
    assert row["lastError"] == "provider timeout"
