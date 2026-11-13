from __future__ import annotations

from framework_agent.graph import run_trace
from framework_agent.model import ScriptedPlanner
from framework_agent.tasks import build_tasks


def test_benign_task_completes_through_langgraph() -> None:
    task = next(task for task in build_tasks() if task["expected_outcome"] == "completed" and not task.get("fault_type"))
    trace = run_trace(task, ScriptedPlanner())
    assert trace["final_status"] == "completed"
    assert trace["tool_calls"]
    assert trace["safety_violation"] is False
    assert any(event["node"] == "execute" and event["status"] == "ok" for event in trace["events"])


def test_unsafe_task_is_blocked_before_planner_or_tool() -> None:
    task = next(task for task in build_tasks() if task["expected_outcome"] == "denied")
    trace = run_trace(task, ScriptedPlanner())
    assert trace["final_status"] == "denied"
    assert trace["model_calls"] == 0
    assert trace["tool_calls"] == []
    assert trace["safety_violation"] is False


def test_tool_timeout_is_recovered_in_two_attempts() -> None:
    task = next(task for task in build_tasks() if task.get("fault_type") == "tool_timeout")
    trace = run_trace(task, ScriptedPlanner())
    assert trace["final_status"] == "completed"
    assert trace["recoveries"][0]["kind"] == "tool_timeout"
    assert trace["tool_attempts"] == 2


def test_rate_limit_and_malformed_json_are_recovered() -> None:
    tasks = build_tasks()
    for fault in ("model_rate_limit", "malformed_model_json"):
        task = next(item for item in tasks if item.get("fault_type") == fault)
        trace = run_trace(task, ScriptedPlanner())
        assert trace["final_status"] == "completed"
        assert any(recovery["kind"] == fault for recovery in trace["recoveries"])
        assert trace["model_calls"] >= 2
