"""M4 工具沙箱：白名单 + 参数校验 + 审计链 + 死信。

边界必须说清楚：这是进程内策略层，不是 OS 级隔离。它拦得住
「调了不该调的工具」，拦不住「进程本身被攻破」。README 与 docs 里
必须同时写这一句，否则读者会误以为工具调用已经安全。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplat import common as C  # noqa: E402
from aplat.common import DDL  # noqa: E402

# 参数级规则：key 必填 + 值类型限制。尽量简单，复杂 schema 留给 jsonschema。
ARG_RULES: dict[str, dict[str, tuple[type, bool]]] = {
    "search": {"q": (str, True)},
    "calc": {"expr": (str, True)},
    "read_doc": {"doc_id": (str, True), "max_chars": (int, False)},
}


@dataclass
class ToolPolicy:
    allowed: set[str]
    rules: dict | None = None

    def check(self, tool: str, args: dict) -> tuple[bool, str]:
        if tool not in self.allowed:
            return False, f"tool '{tool}' 不在白名单"
        rules = self.rules or ARG_RULES
        spec = rules.get(tool)
        if not spec:
            return True, "whitelisted"
        for k, (ty, required) in spec.items():
            if k not in args or args[k] is None:
                if required:
                    return False, f"缺少必填参数 '{k}'"
                continue
            if not isinstance(args[k], ty) or isinstance(args[k], bool) and ty is int:
                return False, f"参数 '{k}' 应为 {ty.__name__}"
        return True, "args-ok"


@dataclass
class Verdict:
    tool: str
    verdict: str          # allowed / denied
    reason: str


class Sandbox:
    """每次调用都在 tool_audit 留一行；denied 不进入执行路径。"""

    def __init__(self, db: Path, policy: ToolPolicy):
        self.db_path = Path(db)
        self.policy = policy
        with C.db(self.db_path) as conn:
            conn.executescript(DDL)
            conn.commit()

    # ---------------------------------------------------------- 审计写

    def _audit(self, request_id: str, tool: str, args: dict, verdict: str,
               reason: str, attempt: int, latency_ms: int, outcome: str) -> None:
        with C.db(self.db_path) as conn:
            conn.execute(
                """INSERT INTO tool_audit
                   (ts_wall, request_id, tool, args_json, verdict, reason,
                    attempt, latency_ms, outcome)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (C.now_ms_wall(), request_id, tool, C.json_dumps(args),
                 verdict, reason, attempt, latency_ms, outcome),
            )
            conn.commit()

    def _dead_letter(self, request_id: str, tool: str, payload: dict,
                     attempts: int, last_error: str) -> None:
        with C.db(self.db_path) as conn:
            conn.execute(
                """INSERT INTO dead_letter
                   (ts_wall, request_id, tool, payload, attempts, last_error)
                   VALUES (?,?,?,?,?,?)""",
                (C.now_ms_wall(), request_id, tool, C.json_dumps(payload),
                 attempts, last_error),
            )
            conn.commit()

    # ---------------------------------------------------------- 主入口

    def call(self, request_id: str, tool: str, args: dict) -> Verdict:
        """只做策略判定 + 审计，不执行任何工具。"""
        import time as _t
        t0 = _t.monotonic()
        ok, reason = self.policy.check(tool, args)
        verdict = "allowed" if ok else "denied"
        ms = int((_t.monotonic() - t0) * 1000)
        self._audit(request_id, tool, args, verdict, reason, 1, ms,
                    "skipped" if not ok else "ok")
        return Verdict(tool=tool, verdict=verdict, reason=reason)

    def call_with_retry(self, request_id: str, tool: str, args: dict,
                        fn: Callable[[], object], retrier,
                        is_retryable: Callable[[Exception], bool] | None = None
                        ) -> str:
        """先过沙箱，再过重试。返回 ok / denied / dead_letter。

        is_retryable 缺省时按异常类型判定：RuntimeError 视为可重试，
        ValueError 视为不可重试（参数写错，重试无意义）。
        """
        import time as _t
        t0 = _t.monotonic()
        ok, reason = self.policy.check(tool, args)
        if not ok:
            ms = int((_t.monotonic() - t0) * 1000)
            self._audit(request_id, tool, args, "denied", reason, 1, ms, "skipped")
            return "denied"

        pred = is_retryable or (
            lambda e: isinstance(e, (RuntimeError, TimeoutError, OSError)))
        attempts = 0
        last_err: Exception | None = None
        for attempt in range(1, retrier.cfg.retry_max_attempts + 1):
            attempts = attempt
            try:
                fn()
                ms = int((_t.monotonic() - t0) * 1000)
                self._audit(request_id, tool, args, "allowed", reason,
                            attempt, ms, "ok")
                return "ok"
            except Exception as e:  # noqa: BLE001
                last_err = e
                # 失败的「中间」尝试也留一行 outcome=error，否则审计链看不出重试痕迹。
                # 最后一次失败的收尾由循环之后统一写，避免同一 attempt 两行。
                will_retry = pred(e) and attempt < retrier.cfg.retry_max_attempts
                if will_retry:
                    self._audit(request_id, tool, args, "allowed",
                                f"attempt {attempt} failed: {type(e).__name__}",
                                attempt, int((_t.monotonic() - t0) * 1000), "error")
                if not will_retry:
                    break
                retrier._sleep(retrier.delay_ms(attempt) / 1000.0)
        ms = int((_t.monotonic() - t0) * 1000)
        # 不可重试错误：记单次 allowed 但 outcome=error，不进死信
        if last_err is not None and not pred(last_err):
            # 不可重试：审计记为 denied 与 outcome=error 一致（没被执行成功）
            self._audit(request_id, tool, args, "denied",
                        f"不可重试错误 {type(last_err).__name__}: {last_err}",
                        attempts, ms, "error")
            return "denied"
        self._audit(request_id, tool, args, "allowed", reason,
                    attempts, ms, "dead_letter")
        self._dead_letter(request_id, tool, args, attempts,
                          f"{type(last_err).__name__}: {last_err}")
        return "dead_letter"
