from __future__ import annotations

from .docker_tools import DockerToolbox
from .graph import run_trace
from .model import Planner


def run_docker_trace(task: dict, planner: Planner) -> dict:
    return run_trace(task, planner, toolbox_factory=DockerToolbox)
