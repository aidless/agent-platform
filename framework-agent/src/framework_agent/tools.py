from __future__ import annotations

import ast
import time
from typing import Any


class ToolTimeout(RuntimeError):
    pass


class ToolError(RuntimeError):
    pass


SAFE_TOOLS = {
    "search_docs": "Search an in-memory documentation fixture.",
    "calculator": "Evaluate a harmless arithmetic expression.",
    "read_file": "Read from the in-memory /sandbox fixture.",
    "list_files": "List the in-memory /sandbox fixture.",
    "extract_fields": "Extract owner and due date from an in-memory sandbox note.",
}


def _calc(expression: str) -> float:
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.USub)
    tree = ast.parse(expression, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise ToolError("calculator accepts arithmetic only")
    value = eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, {})
    return float(value)


class SafeToolbox:
    """All effects are process-local in-memory fixtures; no disk, network, or email effect exists."""

    def __init__(self, task_id: str, fault_type: str | None = None) -> None:
        self.task_id = task_id
        self.fault_type = fault_type
        self.calls = 0
        self.files = {
            "/sandbox/reports/r01.txt": "Latency improved after the cache fix.",
            "/sandbox/notes/n01.txt": "owner=alice; due=2026-11-01",
        }

    def execute(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self.fault_type == "tool_timeout" and self.calls == 1:
            raise ToolTimeout("injected first-attempt timeout")
        if tool == "search_docs":
            query = str(args.get("query", ""))
            return {"status": "ok", "result": f"Retention policy for {query}: retain for 30 days."}
        if tool == "calculator":
            expression = str(args.get("expression", "0"))
            return {"status": "ok", "result": _calc(expression)}
        if tool == "read_file":
            path = str(args.get("path", ""))
            if not path.startswith("/sandbox/"):
                raise ToolError("read_file is limited to /sandbox")
            return {"status": "ok", "result": self.files.get(path, f"fixture content for {path}")}
        if tool == "list_files":
            path = str(args.get("path", "/sandbox"))
            if not path.startswith("/sandbox"):
                raise ToolError("list_files is limited to /sandbox")
            return {"status": "ok", "result": ["r01.txt", "r02.txt", "incident.json"]}
        if tool == "extract_fields":
            path = str(args.get("path", ""))
            if not path.startswith("/sandbox/"):
                raise ToolError("extract_fields is limited to /sandbox")
            return {"status": "ok", "result": {"owner": "alice", "due": "2026-11-01"}}
        raise ToolError(f"unknown tool: {tool}")


def timed_execute(toolbox: SafeToolbox, tool: str, args: dict[str, Any], attempt: int) -> tuple[dict[str, Any] | None, str | None, float]:
    started = time.perf_counter()
    try:
        result = toolbox.execute(tool, args)
        return result, None, (time.perf_counter() - started) * 1000
    except ToolTimeout as error:
        return None, f"tool_timeout:{error}", (time.perf_counter() - started) * 1000
    except Exception as error:  # noqa: BLE001 - tool boundary must classify provider/tool failures
        return None, f"{type(error).__name__}:{error}", (time.perf_counter() - started) * 1000
