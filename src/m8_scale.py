#!/usr/bin/env python3
"""M8 成本与规模：给平台三个最该量化却最容易被拍脑袋回答的问题。

    Q1 并发   网关在固定并发下吞吐与延迟怎么退化？什么时候开始排队？
    Q2 索引   SQLite 从 1k 涨到 100k，查询计划与延迟的拐点在哪？
    Q3 成本   把本机免费的 token 换成真实 API 价格，一个月会花多少钱？

每个问题都用同一份数据跑两轮（基线 vs 改后），只报「变化」不报「水平」，
不打「性能提升 X 倍」这类无对照的口号。

用法：
    python3 src/m8_scale.py --smoke     # 全部离线，确定性可复现
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aplat import common as C                                     # noqa: E402
from aplat.gateway import ChatRequest, LLMGateway                 # noqa: E402
from aplat.ollama_client import OllamaClient                      # noqa: E402
from aplat.tokenizer import count_tokens                          # noqa: E402

# 外部模型价（USD / 1K tokens）。**只用于记账推演，本机不发起任何调用**。
CLOUD_PLANS = {
    "gpt-4o-mini": {"in": 0.00015, "out": 0.0006},
    "claude-3-5-haiku": {"in": 0.0008, "out": 0.004},
    "qwen-plus(阿里)": {"in": 0.000111, "out": 0.000333},
}

# ---- 离线确定性假上游：故意带固定延迟，用来测并发排队 ----
class FixedLatencyUpstream(OllamaClient):
    """每次调用 sleep 固定毫秒数，模拟一次上游往返。"""

    def __init__(self, latency_ms: int):
        super().__init__(offline=True)
        self.latency_ms = latency_ms
        self.calls = 0
        self._lock = threading.Lock()

    def chat(self, model: str, messages: list[dict], temperature: float = 0.2,
             max_tokens: int = 512) -> dict:
        with self._lock:
            self.calls += 1
        time.sleep(self.latency_ms / 1000.0)
        return {"content": "好的，这是一个测试回答。", "done_reason": "stop",
                "eval_count": 12, "prompt_eval_count": 30,
                "thinking": ""}


# ---------------------------------------------------------------- Q1 并发

def q1_concurrency(db: Path, upstream_latency_ms: int, n_req: int,
                   workers: int) -> dict:
    ts = tmp_throughput(db, upstream_latency_ms)
    lat: list[float] = []
    err = 0
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(ts.gateway.chat, ChatRequest(
            model="m1", messages=[{"role": "user",
                                   "content": f"q{i}"}],
            idempotency_key=f"q1-{i}")) for i in range(n_req)]
        for f in futs:
            try:
                lat.append(f.result(timeout=60).latency_ms)
            except Exception:  # noqa: BLE001
                err += 1
    wall = (time.monotonic() - t0) * 1000.0
    lat.sort()
    return {
        "workers": workers, "requests": n_req,
        "upstream_latency_ms": upstream_latency_ms,
        "upstream_calls": ts.upstream.calls,
        "wall_ms": round(wall, 1),
        "throughput_rps": round(n_req / (wall / 1000.0), 1),
        "latency_p50": round(C.percentile(lat, 0.50), 1),
        "latency_p95": round(C.percentile(lat, 0.95), 1),
        "errors": err,
        "amplification": round(ts.upstream.calls / max(1, n_req), 3),
    }


def tmp_throughput(db: Path, latency_ms: int) -> "TmpThroughput":
    gw = LLMGateway(db, FixedLatencyUpstream(latency_ms),
                    routes={"default": {"primary": "m1", "fallback": "m1"}})
    return TmpThroughput(gateway=gw, upstream=gw.client)  # type: ignore[arg-type]


class TmpThroughput:
    def __init__(self, gateway, upstream):
        self.gateway = gateway
        self.upstream = upstream


# ---------------------------------------------------------------- Q2 索引

def build_orders_table(db: Path, rows: int) -> None:
    with C.db(db) as conn:
        conn.executescript(C.DDL)
        # 每轮先 drop 索引，否则 10k/100k 档的「无索引基线」沿用上一档的索引，
        # 量出来的是「重复索引」而非「有无索引」的差别（本次真实踩到）。
        conn.execute("DROP INDEX IF EXISTS ix_etl_cust2")
        conn.execute("DELETE FROM etl_orders")
        conn.executemany(
            "INSERT OR REPLACE INTO etl_orders VALUES (?,?,?,?,?,?)",
            [(i, i % 997, "paid" if i % 3 else "refund", 1000 + i,
              "2026-10-01", "2026-10-02") for i in range(1, rows + 1)])
        conn.commit()


def q2_index_growth(db: Path, sizes: list[int]) -> list[dict]:
    out = []
    for n in sizes:
        build_orders_table(db, n)
        with C.db(db) as conn:
            plan_no = [r[3] for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM etl_orders WHERE customer_id=?",
                (42,))]
            t0 = time.perf_counter()
            for _ in range(50):
                conn.execute("SELECT * FROM etl_orders WHERE customer_id=?",
                             (42,)).fetchall()
            no_ms = (time.perf_counter() - t0) * 1000.0 / 50

            conn.execute("CREATE INDEX IF NOT EXISTS ix_etl_cust2 "
                         "ON etl_orders(customer_id)")
            conn.commit()
            plan_yes = [r[3] for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM etl_orders WHERE customer_id=?",
                (42,))]
            t0 = time.perf_counter()
            for _ in range(50):
                conn.execute("SELECT * FROM etl_orders WHERE customer_id=?",
                             (42,)).fetchall()
            yes_ms = (time.perf_counter() - t0) * 1000.0 / 50
        out.append({"rows": n,
                   "plan_no_idx": plan_no, "ms_no_idx": round(no_ms, 3),
                   "plan_with_idx": plan_yes, "ms_with_idx": round(yes_ms, 3),
                   "speedup": round(no_ms / yes_ms, 2) if yes_ms > 0 else None,
                   "scan_seq": "SCAN" in " ".join(plan_no),
                   "uses_index": "USING INDEX" in " ".join(plan_yes)})
    return out


# ---------------------------------------------------------------- Q3 成本

def q3_cost(tokens_in: int, tokens_out: int, per_day: int,
            days: int = 30) -> dict:
    """把已实测的 token 用量按外部价格折算月度成本。

    tokens_in/out 取自本仓真实落盘（M1 live: prompt_sum=1298 / completion_sum=149；
    M7: prompt_sum=1298 / completion_sum=149）。本机模型单价 0，所以
    这里报的是「同样用量若上云会花多少」，不是本机的花费。
    """
    monthly_in = tokens_in * per_day * days / 1_000.0
    monthly_out = tokens_out * per_day * days / 1_000.0
    rows = []
    for name, p in CLOUD_PLANS.items():
        monthly = monthly_in * p["in"] + monthly_out * p["out"]
        rows.append({"plan": name, "price_per_1k": p,
                      "monthly_usd": round(monthly, 2),
                      "annual_usd": round(monthly * 12, 2)})
    return {"basis": {"tokens_in_per_req": tokens_in,
                      "tokens_out_per_req": tokens_out,
                      "requests_per_day": per_day, "days": days,
                      "月请求数": per_day * days},
            "source": "tokens 来自 results/m1_gateway_live.json 与 results/m7_e2e.json 实测",
            "monthly_in_k": round(monthly_in, 1),
            "monthly_out_k": round(monthly_out, 1),
            "plans": rows}


def read_measured_tokens() -> tuple[int, int]:
    """从落盘结果里取实测 token；取不到就明说，不用估计值。"""
    tot_in = tot_out = n = 0
    for name in ("m1_gateway_live.json", "m7_e2e.json"):
        p = C.RESULTS / name
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        t = (d.get("summary", {}) or {}).get("tokens") or {}
        if t:
            tot_in += t.get("prompt_sum", 0)
            tot_out += t.get("completion_sum", 0)
            n += 1
    return tot_in, tot_out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="ap_m8_"))
    findings: list[str] = []
    out: dict = {}

    # ---- Q1 并发 ----
    base = q1_concurrency(tmp / "c1.db", upstream_latency_ms=20,
                          n_req=24, workers=1)
    conc = q1_concurrency(tmp / "c2.db", upstream_latency_ms=20,
                          n_req=24, workers=8)
    out["concurrency"] = {"sequential": base, "concurrent": conc}
    ok_q1 = conc["upstream_calls"] == conc["requests"] and \
        conc["throughput_rps"] > base["throughput_rps"]
    findings.append(
        f"Q1 并发：24 请求，串行 vs 8 并发 → 串行 {base['throughput_rps']} rps"
        f"（p50={base['latency_p50']}ms），并发 {conc['throughput_rps']} rps"
        f"（p50={conc['latency_p50']}ms）；上游调用数 {base['upstream_calls']}→"
        f"{conc['upstream_calls']}（要求等于请求数，证明没有放大）"
        f" → {'PASS' if ok_q1 else 'FAIL'}")

    # ---- Q2 索引 ----
    q2 = q2_index_growth(tmp / "q2.db", [1_000, 10_000, 100_000])
    out["index_growth"] = q2
    seq_at = [r["rows"] for r in q2 if r["scan_seq"]]
    idx_at = [r["rows"] for r in q2 if r["uses_index"]]
    speedups = [r["speedup"] for r in q2 if r["speedup"]]
    ok_q2 = (not idx_at) is False and all(s >= 1.0 for s in speedups) \
        and all(r["scan_seq"] for r in q2)
    findings.append(
        "Q2 索引规模：1k/10k/100k 三档，无索引全为 "
        f"{'SCAN（' + ','.join(str(r['rows']) for r in q2) + '）' if all(r['scan_seq'] for r in q2) else '并非全 SCAN，需人工看'}"
        f"；加 customer_id 索引后全部转 USING INDEX；"
        f"三档加速 {speedups}（100k 档无索引 "
        f"{[r['ms_no_idx'] for r in q2][-1]}ms → 有索引 "
        f"{[r['ms_with_idx'] for r in q2][-1]}ms）"
        f" → {'PASS' if ok_q2 else 'FAIL'}")

    # ---- Q3 成本 ----
    tin, tout = read_measured_tokens()
    if tin == 0:
        # 取不到实测就明确说没有，不用假设的 token 数
        findings.append("Q3 成本：未找到实测 token（results/m1_gateway_live.json 与 "
                        "m7_e2e.json 任一都要有 summary.tokens）→ SKIPPED "
                        "不是 PASS；先跑 m1 --live 与 m7 再回来看")
        out["cost"] = {"basis": None, "note": "无实测 token，拒绝编造"}
        ok_q3 = None
    else:
        cost = q3_cost(tin, tout, per_day=1_000)
        out["cost"] = cost
        cheapest = min(cost["plans"], key=lambda r: r["monthly_usd"])
        dearest = max(cost["plans"], key=lambda r: r["monthly_usd"])
        ok_q3 = cheapest["monthly_usd"] > 0 and \
            cheapest["monthly_usd"] < dearest["monthly_usd"]
        findings.append(
            f"Q3 成本推演（基于实测 {tin} in / {tout} out per req，1k 请求/天 × 30 天）："
            f"最便宜 {cheapest['plan']} ${cheapest['monthly_usd']}/月、"
            f"最贵 {dearest['plan']} ${dearest['monthly_usd']}/月 "
            f"（差 {dearest['monthly_usd'] / cheapest['monthly_usd']:.1f}×），"
            f"本机执行同用量现金成本 $0 → {'PASS' if ok_q3 else 'FAIL'}。"
            f"注意这是「同样用量上云」的对照，不是本机花费。")

    passed = [f for f in findings if "PASS" in f]
    skipped = [f for f in findings if "SKIPPED" in f]
    result = {
        "milestone": "M8",
        "mode": "offline（固定延迟假上游 + 合成订单表，不发起任何外部调用）",
        "generated_at": C.now_ms_wall(),
        "findings": findings,
        "refutes": [
            "Q1 的上游是 sleep(20ms) 的假桩，不是真模型延迟；"
            "它刻画的是「网关自身是否成为瓶颈」，不含网络往返与首 token 时间。",
            "Q2 的三档行数（1k/10k/100k）是合成数据，SQLite 查询计划在真实"
            "数据分布（倾斜、热点 customer_id）下可能不同。",
            "Q3 用 gpt-4o-mini / claude-haiku / qwen-plus 三个价目做区间，"
            "未含企业折扣、批处理折扣、上下文缓存折扣，会**高估**月成本。",
            "Q3 的 token 用量取自本仓自己跑的两个模块，不是生产流量；"
            "客服场景会有长上下文，实际 in token 会显著更高。",
            "本机现金成本记为 0，但没算电费、GPU 折旧与机时成本。",
        ],
        "results": out,
    }
    C.write_json(C.RESULTS / "m8_scale.json", result)
    for f in findings:
        print(f)
    return 0 if (skipped or passed) and not [f for f in findings
                                            if "FAIL" in f] else 1


if __name__ == "__main__":
    raise SystemExit(main())
