"""M5 韧性层：熔断器 + 指数退避重试 + 降级策略。

企业里的真实问题：
- 一个上游挂了，agent 每条请求都等满超时才失败 → 雪崩。
- 重试不带抖动 → 多实例同时重试，把刚恢复的上游再打挂（thundering herd）。
- 只重试不降级 → 降级路径没有人工验证过，真出事时它是坏的。

本模块为每个模型/供应商维护一个状态机：
    closed --(连续失败 >= threshold)--> open --(cooldown 到期)--> half_open
    half_open --(1 次成功)--> closed     half_open --(1 次失败)--> open
并把状态落 SQLite，供 M2 的看板与 M1 的网关查询。

重试策略：exponential backoff + full jitter，基座与倍数可配；
jitter 由 seeded random 提供，保证同一批请求的重试时间序列可复现。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import common as C

States = ("closed", "open", "half_open")


@dataclass
class BreakerConfig:
    failure_threshold: int = 5      # 连续失败多少次后打开
    cooldown_ms: int = 10_000       # open 状态持续多久后进 half_open
    half_open_successes: int = 1    # half_open 下需要几次成功才关闭
    retry_base_ms: int = 50
    retry_max_ms: int = 4_000
    retry_max_attempts: int = 4
    jitter: bool = True


class CircuitBreaker:
    """单 key（模型名）熔断器。状态持久化在 provider_health 表。"""

    def __init__(self, db_path: Path, key: str, cfg: BreakerConfig | None = None,
                 clock: Callable[[], int] | None = None) -> None:
        self.db_path = db_path
        self.key = key
        self.cfg = cfg or BreakerConfig()
        self._now = clock or C.monotonic_ms
        self.probe_sent = 0
        self._load()

    # ------------------------------------------------------- 状态持久化

    def _load(self) -> None:
        with C.db(self.db_path) as conn:
            # 兼容旧库：SCHEMA v2 的 provider_health 没有 probe_sent 列
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(provider_health)")]
            if "probe_sent" not in cols:
                conn.execute("ALTER TABLE provider_health "
                             "ADD COLUMN probe_sent INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                "INSERT OR IGNORE INTO provider_health (provider,state,fails,successes) "
                "VALUES (?,?,0,0)", (self.key, "closed"))
            row = conn.execute("SELECT * FROM provider_health WHERE provider=?",
                               (self.key,)).fetchone()
            conn.commit()
        self.state = row["state"]
        self.opened_at = row["opened_at"]
        self.fails = row["fails"]
        self.successes = row["successes"]
        self.probe_sent = row["probe_sent"] if "probe_sent" in row.keys() else 0

    def _save(self) -> None:
        with C.db(self.db_path) as conn:
            conn.execute(
                "UPDATE provider_health SET state=?, opened_at=?, fails=?, "
                "successes=?, probe_sent=? WHERE provider=?",
                (self.state, self.opened_at, self.fails,
                 self.successes, self.probe_sent, self.key))
            conn.commit()

    # ------------------------------------------------------- 查询（可自动迁移）

    def allow(self, _key: str | None = None) -> bool:
        """是否允许发起调用。open 到期后自动转 half_open。

        _key 仅为对齐按 provider 分 key 的其他实现签名，本类实例自身已绑定一个 key。

        half_open 语义（防惊群）：每个 cooldown 窗口只放行**一个**探测请求，
        其余请求继续被拒。否则上游刚恢复就会被所有副本同时灌死。
        进程重启后 half_open 状态仍在表里，probe_sent 随之复位为 0——
        这是一个已知的、被接受的折中：宁可多放一个探测，也不永久卡死。
        """
        if self.state == "open":
            if self.opened_at is not None and \
                    self._now() - self.opened_at >= self.cfg.cooldown_ms:
                self.state = "half_open"
                self.successes = 0
                self.probe_sent = 0
                self._save()
            else:
                return False
        if self.state == "half_open":
            if self.probe_sent >= 1:
                return False
            self.probe_sent = 1
            self._save()
        return True

    def state_now(self) -> str:
        # 不改变状态的只读视图（看板用）
        if self.state == "open" and self.opened_at is not None:
            if self._now() - self.opened_at >= self.cfg.cooldown_ms:
                return "half_open(eligible)"
        return self.state

    # ------------------------------------------------------- 事件

    def on_success(self, _key: str | None = None) -> None:
        if self.state == "half_open":
            self.successes += 1
            if self.successes >= self.cfg.half_open_successes:
                self.state, self.opened_at, self.fails = "closed", None, 0
        else:
            self.fails = 0
        self._save()

    def on_failure(self, _key: str | None = None, kind: str | None = None) -> None:
        """记录一次失败。_key 仅为对齐多 provider 实现的签名，本类忽略之。

        注意：网关侧调用点是 on_failure(model, kind)，所以 _key 实际承载 model。
        """
        # 4xx 不计数：那是请求写错了，重试也没用，熔断更没用
        if kind == "http_4xx":
            self._save()
            return
        self.fails += 1
        if self.state == "half_open":
            self.state, self.opened_at = "open", self._now()
        elif self.fails >= self.cfg.failure_threshold and self.state == "closed":
            self.state, self.opened_at = "open", self._now()
        self._save()


# ---------------------------------------------------------------- 重试

class Retrier:
    """exponential backoff + full jitter，确定性可复现。"""

    def __init__(self, cfg: BreakerConfig | None = None, seed: int = C.SEED,
                 sleep: Callable[[float], None] | None = None) -> None:
        self.cfg = cfg or BreakerConfig()
        self._rng = C.rng(seed)
        self._sleep = sleep or (lambda s: None)

    def delay_ms(self, attempt: int) -> int:
        """attempt 从 1 开始。返回本次应等待的毫秒数。"""
        exp = min(self.cfg.retry_max_ms,
                  self.cfg.retry_base_ms * (2 ** (attempt - 1)))
        if self.cfg.jitter:
            return int(self._rng.uniform(0, exp))
        return int(exp)

    def run(self, fn: Callable[[], Any], is_retryable: Callable[[Exception], bool],
            on_attempt: Callable[[int, Exception], None] | None = None) -> Any:
        last: Exception | None = None
        for attempt in range(1, self.cfg.retry_max_attempts + 1):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001 - 由 is_retryable 判定
                last = e
                if not is_retryable(e) or attempt == self.cfg.retry_max_attempts:
                    raise
                if on_attempt:
                    on_attempt(attempt, e)
                self._sleep(self.delay_ms(attempt) / 1000.0)
        assert last is not None
        raise last


class Degrader:
    """降级链：按优先级依次尝试，直到有一个返回非空。

    企业价值：降级路径平时没人跑，出事时第一次跑往往是坏的。
    这里强制要求每次压测都执行一遍降级链并断言其可用性。
    """

    def __init__(self, chain: list[tuple[str, Callable[[], Any]]]) -> None:
        self.chain = chain

    def run(self) -> tuple[str, Any]:
        last_err: Exception | None = None
        for name, fn in self.chain:
            try:
                out = fn()
                if out:
                    return name, out
                last_err = RuntimeError("empty result")
            except Exception as e:  # noqa: BLE001
                last_err = e
        raise RuntimeError(f"all degraded options exhausted: {last_err}")
