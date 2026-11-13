from __future__ import annotations

from framework_agent.benchmark import aggregate, run_benchmark
from framework_agent.model import ScriptedPlanner
from framework_agent.tasks import build_tasks


def test_aggregate_reports_safety_overrefusal_and_recovery() -> None:
    tasks = build_tasks()
    traces = [{"task_id": task["id"], "final_status": task["expected_outcome"], "safety_violation": False,
               "recoveries": [{"kind": task["fault_type"], "recovered": True}] if task.get("fault_type") else [],
               "latency_ms": 10.0, "cost_usd": 0.0, "model_calls": 1, "tool_attempts": 1}
              for task in tasks]
    report = aggregate(tasks, traces, metadata={"planner": "fixture"})
    assert report["traces"] == 100
    assert report["safety"]["unsafe_block_rate"] == 1.0
    assert report["over_refusal_rate"] == 0.0
    assert report["recovery"]["success_rate"] == 1.0


def test_benchmark_runs_exactly_100_traces_with_scripted_planner() -> None:
    traces, report = run_benchmark(build_tasks(), ScriptedPlanner(), workers=1)
    assert len(traces) == 100
    assert report["traces"] == 100
    assert report["safety"]["safety_violations"] == 0
    assert report["quality_gate"]["status"] == "PASS"
