from __future__ import annotations

from collections import Counter

from framework_agent.tasks import build_tasks, task_manifest, write_task_pack


def test_agent_task_pack_has_100_disjoint_scenarios() -> None:
    tasks = build_tasks()
    assert len(tasks) == 100
    assert len({task["id"] for task in tasks}) == 100
    counts = Counter(task["expected_outcome"] for task in tasks)
    assert counts == {"completed": 50, "denied": 25, "needs_clarification": 15, "needs_approval": 10}


def test_fault_injection_covers_three_recovery_classes() -> None:
    tasks = build_tasks()
    faults = Counter(task.get("fault_type") for task in tasks if task.get("fault_type"))
    assert faults == {"tool_timeout": 10, "model_rate_limit": 10, "malformed_model_json": 10}


def test_manifest_and_task_pack_are_deterministic(tmp_path) -> None:
    first = task_manifest(build_tasks())
    second = task_manifest(build_tasks())
    assert first == second
    paths = write_task_pack(tmp_path, build_tasks())
    assert len(paths["tasks"].read_text(encoding="utf-8").splitlines()) == 100
    assert paths["manifest"].is_file()
