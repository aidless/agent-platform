from __future__ import annotations

from framework_agent.graph import run_trace
from framework_agent.model import ScriptedPlanner
from framework_agent.tasks import build_tasks


def _dummy_planner():
    return ScriptedPlanner()


def test_policy_off_cannot_be_run_through_public_trace_without_explicit_override() -> None:
    tasks = build_tasks()
    unsafe = next(task for task in tasks if task["expected_outcome"] == "denied")
    trace = run_trace(unsafe, _dummy_planner(), toolbox_factory=lambda *args: object())
    assert trace["safety_violation"] is False
