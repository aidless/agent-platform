"""共享基础设施：SQLite schema、JSONL 落盘、时钟、确定性随机、成本模型。

本模块不含业务逻辑，只提供可被六个模块复用的原语。
所有函数均为标准库 only，保证「clone 下来就能跑」。

关键决策（为什么这样做）：
- 用 SQLite 而不是 pandas/duckdb：企业里 pipeline 跑在 CI 里，零依赖部署价值大于
  查询速度；且 SQLite 支持 window function（3.25+），足够表达慢查询诊断。
- 时间戳用整数毫秒单调时钟（time.monotonic_ns）而不是 wall clock：可观测性里的
  延迟统计会被 NTP 跳变污染，单调时钟不受影响；同时另存 wall clock 供人读。
- 所有随机数走 seeded random.Random：故障注入必须可复现，否则"熔断生效了"
  这句话无法被复核。
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

ROOT = Path(__file__).resolve().parents[2]          # agent-platform/
RESULTS = ROOT / "results"
DATA = ROOT / "data"
FIXTURES = DATA / "fixtures"

SEED = 20261011


# ---------------------------------------------------------------- 时间与随机

def now_ms_wall() -> int:
    """人类可读时间戳（毫秒 epoch）。用于落盘与报表。"""
    return int(time.time() * 1000)


def monotonic_ms() -> int:
    """单调时钟（毫秒）。用于延迟统计，避免 NTP 跳变污染。"""
    return time.monotonic_ns() // 1_000_000


def rng(seed: int = SEED) -> random.Random:
    """确定性随机源。故障注入、采样、bootstrap 一律走这里。"""
    return random.Random(seed)


# ---------------------------------------------------------------- JSONL 落盘

def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    """按行写 JSONL；返回行数。父目录自动创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=False) + "\n")
            n += 1
    return n


def append_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=False) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def json_dumps(obj: Any) -> str:
    """稳定序列化：键排序 + 中文不转义，便于 diff 与校验。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def json_loads(txt: str | None) -> Any:
    return json.loads(txt) if txt else None


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- SQLite

SCHEMA_VERSION = 3

DDL = """
-- 网关请求表：每一条进入平台的 LLM 调用一条记录
CREATE TABLE IF NOT EXISTS gateway_request (
    request_id     TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    ts_wall        INTEGER NOT NULL,
    ts_mono        INTEGER NOT NULL,
    route          TEXT NOT NULL,
    model          TEXT NOT NULL,
    content        TEXT NOT NULL DEFAULT '',
    prompt_tokens  INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens   INTEGER NOT NULL,
    latency_ms     INTEGER NOT NULL,
    status         TEXT NOT NULL,          -- ok | error | cached | degraded | rejected
    http_status    INTEGER NOT NULL,
    error_kind     TEXT,                   -- timeout | ratelimit | upstream_5xx | circuit_open | ...
    cost_usd       REAL NOT NULL,
    cache_hit      INTEGER NOT NULL DEFAULT 0,
    replay_of      TEXT                    -- 幂等命中时指向首个 request_id
);
CREATE INDEX IF NOT EXISTS ix_gw_ts ON gateway_request(ts_wall);
CREATE INDEX IF NOT EXISTS ix_gw_idem ON gateway_request(idempotency_key);
CREATE INDEX IF NOT EXISTS ix_gw_model ON gateway_request(model);

-- span 表：一个 request 下挂多个 span（网关→上游→工具→压缩…）
CREATE TABLE IF NOT EXISTS span (
    span_id      TEXT PRIMARY KEY,
    trace_id     TEXT NOT NULL,
    parent_id    TEXT,
    request_id   TEXT,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,           -- gateway/cache/upstream/tool/compact/db
    start_mono   INTEGER NOT NULL,
    end_mono     INTEGER NOT NULL,
    duration_ms  INTEGER NOT NULL,
    status       TEXT NOT NULL,           -- ok/error/skipped
    attrs        TEXT NOT NULL             -- JSON
);
CREATE INDEX IF NOT EXISTS ix_span_trace ON span(trace_id);

-- 会话记忆：tag-indexed，供 M3 召回与压缩
CREATE TABLE IF NOT EXISTS memory_item (
    item_id     TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    turn        INTEGER NOT NULL,
    role        TEXT NOT NULL,            -- user/assistant/system/tool
    content     TEXT NOT NULL,
    tags        TEXT NOT NULL,            -- JSON array
    tokens      INTEGER NOT NULL,
    pinned      INTEGER NOT NULL DEFAULT 0,  -- pinned 不被压缩掉
    ts_wall     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mem_session ON memory_item(session_id, turn);

-- 工具调用审计链：append-only
CREATE TABLE IF NOT EXISTS tool_audit (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_wall     INTEGER NOT NULL,
    request_id  TEXT,
    tool        TEXT NOT NULL,
    args_json   TEXT NOT NULL,
    verdict     TEXT NOT NULL,            -- allowed/denied
    reason      TEXT,
    attempt     INTEGER NOT NULL,
    latency_ms  INTEGER NOT NULL,
    outcome     TEXT NOT NULL             -- ok/error/dead_letter/skipped
);
CREATE INDEX IF NOT EXISTS ix_tool_req ON tool_audit(request_id);

-- 死信队列
CREATE TABLE IF NOT EXISTS dead_letter (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_wall     INTEGER NOT NULL,
    request_id  TEXT,
    tool        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    attempts    INTEGER NOT NULL,
    last_error  TEXT NOT NULL
);

-- 供应商健康：熔断状态机落地，供 M5 判定
CREATE TABLE IF NOT EXISTS provider_health (
    provider    TEXT PRIMARY KEY,
    state       TEXT NOT NULL,            -- closed/open/half_open
    opened_at   INTEGER,
    fails       INTEGER NOT NULL,
    successes   INTEGER NOT NULL,
    probe_sent  INTEGER NOT NULL DEFAULT 0 -- half_open 下是否已放过探测请求
);

-- 数据管道：抽取的订单表 + schema 版本表 + 质量结果
CREATE TABLE IF NOT EXISTS etl_orders (
    order_id    INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    status      TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS etl_schema_version (
    id          INTEGER PRIMARY KEY,
    observed_at INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    columns_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS etl_quality (
    id          INTEGER PRIMARY KEY,
    ts_wall     INTEGER NOT NULL,
    rule        TEXT NOT NULL,
    passed      INTEGER NOT NULL,
    detail      TEXT NOT NULL
);
"""


@contextmanager
def db(path: Path, fresh: bool = False) -> Iterator[sqlite3.Connection]:
    """打开（必要时创建）SQLite。fresh=True 时先删除旧文件，便于可复现跑批。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fresh and path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(DDL)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------- 成本模型

# 单价（USD / 1K tokens）。本机模型成本记 0，但仍然按同一种货币口径记账，
# 这样"省下多少"才有可比性。外部模型价格仅用于对照，不作为运行时依赖。
PRICES = {
    # 本地 ollama：算力免费，但我们按 0 记账；显存与时长成本在报告里单独说
    "llama3.2:3b": {"in": 0.0, "out": 0.0},
    "qwen3:8b": {"in": 0.0, "out": 0.0},
    # 外部对照（仅记账口径，不发起任何调用）
    "gpt-4o-mini": {"in": 0.00015, "out": 0.0006},
    "claude-3-5-haiku": {"in": 0.0008, "out": 0.004},
}


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    p = PRICES.get(model, {"in": 0.0, "out": 0.0})
    return round((prompt_tokens / 1000.0) * p["in"] + (completion_tokens / 1000.0) * p["out"], 8)


# ---------------------------------------------------------------- 统计

def percentile(xs: Sequence[float], q: float) -> float:
    """线性插值分位数（与 numpy.percentile 默认一致，但零依赖）。"""
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    if lo == hi:
        return float(s[lo])
    return float(s[lo] + (s[hi] - s[lo]) * (k - lo))


def bootstrap_ci(xs: Sequence[float], stat="mean", n: int = 2000, alpha: float = 0.05,
                 seed: int = SEED) -> tuple[float, float]:
    """确定性 bootstrap 置信区间。样本 < 3 时返回 (0,0) 并调用方应拒绝解读。"""
    if len(xs) < 3:
        return (0.0, 0.0)
    r = rng(seed)
    fn = (lambda a: sum(a) / len(a)) if stat == "mean" else (
        lambda a: percentile(a, q if (q := 0.5) is not None else 0.5))
    n_ = len(xs)
    stats = []
    for _ in range(n):
        sample = [xs[r.randrange(n_)] for _ in range(n_)]
        stats.append(fn(sample))
    stats.sort()
    lo = stats[int(n * alpha / 2)]
    hi = stats[min(int(n * (1 - alpha / 2)), n - 1)]
    return (round(lo, 6), round(hi, 6))


def env_flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")
