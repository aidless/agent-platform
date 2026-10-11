"""ollama 客户端：本仓唯一的上游 LLM 依赖。

不使用 openai SDK，只用 urllib 直接打 ollama 的 /api/chat。
理由：本仓硬约束是"clone 下来能跑"，openai SDK 需要 pip install；
urllib 在标准库里，且 ollama 的 API 已经足够简单。

错误分类（kind）很关键，它是 M5 熔断/降级策略的输入：
  timeout        —— 读超时，可重试
  connection     —— 连不上/拒绝，可重试但可能是服务挂了
  http_4xx       —— 请求本身错，不可重试（重试只会烧 CPU）
  http_5xx       —— 上游内部错误，可重试
  model_missing  —— 模型名不存在，不可重试（除非换模型）
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE = "http://127.0.0.1:11434"


class OllamaError(Exception):
    def __init__(self, kind: str, msg: str):
        super().__init__(f"{kind}: {msg}")
        self.kind = kind
        self.msg = msg


class OllamaClient:
    def __init__(self, base: str = DEFAULT_BASE, timeout_s: float = 60.0,
                 offline: bool = False) -> None:
        self.base = base.rstrip("/")
        self.timeout_s = timeout_s
        self.offline = offline     # offline=True 时不发起网络调用，用于测试
        self.calls: list[dict] = []  # 记录每次真实调用，供测试/审计断言

    def _post(self, path: str, payload: dict) -> dict:
        if self.offline:
            raise OllamaError("offline", "client in offline mode")
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base}{path}", data=body,
            headers={"content-type": "application/json"}, method="POST")
        self.calls.append({"path": path, "payload": payload})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                return json.loads(r.read().decode())
        except socket.timeout:
            raise OllamaError("timeout", f"no response within {self.timeout_s}s")
        except urllib.error.HTTPError as e:
            kind = "http_4xx" if 400 <= e.code < 500 else "http_5xx"
            try:
                detail = e.read().decode()[:200]
            except Exception:
                detail = ""
            if "not found" in detail.lower() and "model" in detail.lower():
                kind = "model_missing"
            raise OllamaError(kind, f"HTTP {e.code} {detail}")
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, ConnectionRefusedError) or "refused" in str(reason):
                raise OllamaError("connection", f"refused: {reason}")
            raise OllamaError("connection", str(reason))

    def chat(self, model: str, messages: list[dict], temperature: float = 0.2,
             max_tokens: int = 512) -> dict:
        raw = self._post("/api/chat", {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
            "keep_alive": "5m",
        })
        msg = raw.get("message") or {}
        return {
            "content": msg.get("content", ""),
            # 思考型模型（qwen3 / deepseek-r1 系）把推理放在 thinking 通道；
            # 若 num_predict 被推理吃光，content 会是空串而 done_reason=length。
            # 这个字段必须透传，否则网关会把「空回答」记成成功。
            "thinking": msg.get("thinking", "") or "",
            "done_reason": raw.get("done_reason", ""),
            "model": raw.get("model", model),
            "eval_count": raw.get("eval_count"),
            "prompt_eval_count": raw.get("prompt_eval_count"),
            "total_duration_ns": raw.get("total_duration_ns"),
        }

    def version(self) -> str:
        req = urllib.request.Request(f"{self.base}/api/version", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return json.loads(r.read().decode()).get("version", "?")
        except Exception as e:
            return f"unreachable: {e}"

    def list_models(self) -> list[str]:
        req = urllib.request.Request(f"{self.base}/api/tags", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode())
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []
