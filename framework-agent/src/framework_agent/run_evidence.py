from __future__ import annotations

import argparse
from pathlib import Path

from .benchmark import run_benchmark, write_evidence
from .docker_tools import DockerToolbox
from .model import OpenAICompatiblePlanner, ScriptedPlanner
from .tasks import build_tasks, write_task_pack


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--planner", choices=("scripted", "openai"), default="scripted")
    parser.add_argument("--model", default="openrouter/google/gemma-4-31b-it:free")
    parser.add_argument("--base-url", default="http://127.0.0.1:3425/v1")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--policy", choices=("on", "off"), default="on")
    parser.add_argument("--docker-tools", action="store_true")
    args = parser.parse_args()
    tasks = build_tasks()
    planner = ScriptedPlanner() if args.planner == "scripted" else OpenAICompatiblePlanner(args.model, args.base_url)
    toolbox_factory = DockerToolbox if args.docker_tools else None
    traces, report = run_benchmark(
        tasks,
        planner,
        workers=args.workers,
        toolbox_factory=toolbox_factory,
        policy_enabled=args.policy == "on",
    )
    output_dir = args.output_dir or Path("results") / f"langgraph-{args.planner}-{args.policy}-100"
    write_evidence(output_dir, tasks, traces, report)
    write_task_pack(Path("tasks"), tasks)
    print(report["quality_gate"])
    print(f"evidence -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
