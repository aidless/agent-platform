from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from .tools import SAFE_TOOLS


class RateLimitError(RuntimeError):
    pass


@dataclass
class PlanResult:
    raw: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class Planner:
    def plan(self, task: dict[str, Any], repair: bool = False) -> PlanResult:
        raise NotImplementedError


class ScriptedPlanner(Planner):
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self._lock = threading.Lock()

    def plan(self, task: dict[str, Any], repair: bool = False) -> PlanResult:
        with self._lock:
            count = self.calls.get(task["id"], 0) + 1
            self.calls[task["id"]] = count
        if task.get("fault_type") == "model_rate_limit" and count == 1:
            raise RateLimitError("injected planner rate limit")
        if task.get("fault_type") == "malformed_model_json" and count == 1 and not repair:
            return PlanResult(raw='{"tool":', model="scripted")
        tool = task.get("expected_tool", "search_docs")
        args = {
            "search_docs": {"query": "retention policy"},
            "calculator": {"expression": "0.1 * 100"},
            "read_file": {"path": "/sandbox/reports/r01.txt"},
            "list_files": {"path": "/sandbox"},
            "extract_fields": {"path": "/sandbox/notes/n01.txt"},
        }[tool]
        return PlanResult(
            raw=json.dumps({"tool": tool, "args": args, "reason": "scripted fixture"}, separators=(",", ":")),
            model="scripted",
        )


class OpenAICompatiblePlanner(Planner):
    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:3425/v1",
        api_key: str | None = None,
        max_tokens: int = 220,
    ) -> None:
        from langchain_openai import ChatOpenAI

        self.model_name = model
        self.llm = ChatOpenAI(
            model=model,
            base_url=base_url,
            api_key=api_key or os.environ.get("AGENT_API_KEY") or "not-needed",
            temperature=0,
            max_tokens=max_tokens,
            timeout=60,
            max_retries=0,
        )
        self.calls: dict[str, int] = {}
        self._lock = threading.Lock()

    def _count(self, task_id: str) -> int:
        with self._lock:
            count = self.calls.get(task_id, 0) + 1
            self.calls[task_id] = count
            return count

    def _messages(self, task: dict[str, Any], repair: bool) -> list[dict[str, str]]:
        tools = "\n".join(f"- {name}: {description}" for name, description in SAFE_TOOLS.items())
        repair_text = " The previous response was invalid; return valid JSON this time." if repair else ""
        return [
            {
                "role": "system",
                "content": (
                    "You are a conservative tool planner. Select exactly one allowed read-only tool. "
                    "Return one JSON object with keys tool, args, reason and no markdown."
                    + repair_text
                    + f"\nAllowed tools:\n{tools}"
                ),
            },
            {"role": "user", "content": task["user_request"]},
        ]

    def plan(self, task: dict[str, Any], repair: bool = False) -> PlanResult:
        count = self._count(task["id"])
        if task.get("fault_type") == "model_rate_limit" and count == 1:
            raise RateLimitError("injected planner rate limit")
        if task.get("fault_type") == "malformed_model_json" and count == 1 and not repair:
            return PlanResult(raw='{"tool":', model=self.model_name)
        import openai

        last_error: Exception | None = None
        response = None
        for attempt in range(3):
            try:
                response = self.llm.invoke(self._messages(task, repair))
                break
            except (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(1.5 * (2**attempt))
        if response is None:
            raise RateLimitError(f"provider unavailable after retries: {last_error}")
        usage = response.response_metadata.get("token_usage") or {}
        raw = response.content
        if not isinstance(raw, str):
            raw = json.dumps(raw, ensure_ascii=False)
        return PlanResult(
            raw=raw,
            model=str(response.response_metadata.get("model_name") or self.model_name),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            cost_usd=0.0,
        )
