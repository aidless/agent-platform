from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .tools import ToolTimeout

IMAGE = "python:3.12-slim"
BASE_ARGS = [
    "--rm",
    "--network",
    "none",
    "--memory",
    "64m",
    "--memory-swap",
    "64m",
    "--pids-limit",
    "32",
    "--read-only",
    "--tmpfs",
    "/tmp:rw,size=8m,noexec,nosuid",
    "--user",
    "65534:65534",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    "--workdir",
    "/tmp",
]


@dataclass(frozen=True)
class DockerExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    container_name: str


def run_isolated_python(code: str, timeout_s: float = 20.0) -> DockerExecResult:
    name = f"fwagent-sbx-{uuid.uuid4().hex[:12]}"
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            ["docker", "run", "--name", name, *BASE_ARGS, IMAGE, "python3", "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as error:
        timed_out = True
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        stdout = (error.stdout or b"").decode(errors="replace") if isinstance(error.stdout, bytes) else error.stdout or ""
        stderr = "timeout"
        returncode = 124
    duration_ms = round((time.monotonic() - started) * 1000, 3)
    return DockerExecResult(returncode, stdout, stderr, duration_ms, timed_out, name)


def build_tool_program(tool: str, args: dict[str, Any]) -> str:
    if tool == "calculator":
        return f"""
import ast
expr = {args.get('expression')!r}
allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.USub)
tree = ast.parse(expr, mode='eval')
for node in ast.walk(tree):
    if not isinstance(node, allowed):
        raise SystemExit('calculator accepts arithmetic only')
print(eval(compile(tree, '<calc>', 'eval'), {{'__builtins__': {{}}}}, {{}}))
"""
    if tool == "read_file":
        return f"""
import json, pathlib
path = {args.get('path')!r}
if not path.startswith('/sandbox/'):
    raise SystemExit('read_file is limited to /sandbox')
fixture = {{
    '/sandbox/reports/r01.txt': 'Latency improved after the cache fix.',
}}
print(fixture.get(path, f'fixture content for {{path}}'))
"""
    if tool == "list_files":
        return """
import json
print(json.dumps(['r01.txt', 'r02.txt', 'incident.json']))
"""
    if tool == "search_docs":
        return f"""
import json
query = {args.get('query')!r}
print(json.dumps(f'Retention policy for {{query}}: retain for 30 days.'))
"""
    if tool == "extract_fields":
        return """
import json
print(json.dumps({'owner': 'alice', 'due': '2026-11-01'}))
"""
    raise ValueError(f"unsupported isolated tool: {tool}")


class DockerToolbox:
    """Real process/container isolation with process-local fixture payloads."""

    name = "docker-isolated"

    def __init__(self, task_id: str, fault_type: str | None = None) -> None:
        self.task_id = task_id
        self.fault_type = fault_type
        self.calls = 0

    def execute(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self.fault_type == "tool_timeout" and self.calls == 1:
            result = run_isolated_python("import time; time.sleep(30)", timeout_s=2.0)
            if not result.timed_out:
                raise RuntimeError("timeout injection did not time out")
            raise ToolTimeout("injected first-attempt container timeout")
        result = run_isolated_python(build_tool_program(tool, args), timeout_s=20.0)
        if result.exit_code != 0:
            raise RuntimeError(result.stderr.strip()[:300] or f"container exited {result.exit_code}")
        try:
            payload = result.stdout.strip()
            try:
                parsed: Any = json.loads(payload)
            except json.JSONDecodeError:
                parsed = payload
            return {"status": "ok", "result": parsed}
        except Exception as error:  # pragma: no cover - defensive
            raise RuntimeError("invalid tool payload") from error

    @staticmethod
    def audit(result: DockerExecResult) -> dict[str, Any]:
        return {
            "container_name": result.container_name,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_ms": result.duration_ms,
            "network": "none",
            "read_only_root": True,
            "non_root": True,
            "cap_drop_all": True,
        }
