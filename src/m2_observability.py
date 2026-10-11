#!/usr/bin/env python3
"""M2 可观测台：从 gateway_request + span 派生指标，验证 span 可追溯到请求。

核心口径（写进 README 与 results）：
  - 延迟一律用单调时钟差值，不用墙钟差。墙钟会被 NTP/手改时间污染。
  - span 表里的 start_mono/end_mono 是「相对 chat() 起点的偏移」，
    不是绝对单调值。跨请求汇总只能 sum(duration_ms)，不能比 end-start。
  - 样本量 <100 时 p95 只是描述性统计，不是 SLO。本仓显式不写 SLO。

用法：
    python3 src/m2_observability.py --smoke   # 造 40 条假数据，验证指标链路
"""
from __future__ import annotations

import argparse
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aplat import common as C                       # noqa: E402
from aplat.gateway import LLMGateway, ChatRequest  # noqa: E402
from aplat.ollama_client import OllamaClient       # noqa: E402


def synthesize(db: Path, n: int = 40, seed: int = 20261011) -> dict:
    """造一批带可控延迟分布的请求，用来验证指标口径而不是验证真实性能。

    这是合成数据，不是实测。README 与 results 必须标注 mode=synthetic。
    """
    rng = random.Random(seed)

    class ScriptedClient(OllamaClient):
        """按脚本返回结果：前 10% 超时，其余返回固定内容。"""

        def __init__(self):
            super().__init__(offline=True)
            self.calls: list[str] = []

        def chat(self, **kw):
            self.calls.append(kw["model"])
            u = rng.random()
            if u < 0.30:
                from aplat.ollama_client import OllamaError
                raise OllamaError("timeout", "scripted timeout")
            # 8% 概率模拟思考型模型空回答（预算烧在 thinking 通道）
            if rng.random() < 0.08:
                return {"content": "", "thinking": "推理" * 40,
                        "done_reason": "length", "eval_count": 64,
                        "prompt_eval_count": 12}
            ms = int(rng.lognormvariate(5.6, 0.45))  # ~270ms 中位
            import time as _t
            if ms > 0:
                _t.sleep(ms / 1000.0)
            return {"content": "返回内容" * 20,
                    "prompt_eval_count": 12, "eval_count": 60,
                    "_sim_latency_ms": ms}

    gw = LLMGateway(db, ScriptedClient(),
                    routes={"default": {"primary": "qwen3:8b",
                                        "fallback": "llama3.2:3b"}})
    n_ok = n_err = 0
    for i in range(n):
        # 1/4 概率复用旧 key，制造真实的幂等命中（验证缓存记账，不是造数）
        key = f"syn-{i}" if (i % 4) else f"syn-{i - 1 if i else 0}"
        r = gw.chat(ChatRequest(
            model="qwen3:8b",
            messages=[{"role": "user", "content": f"问题 {i}：解释一下检索增强生成"}],
            idempotency_key=key, max_tokens=256))
        if r.status in ("ok", "degraded"):
            n_ok += 1
        else:
            n_err += 1
    return {"rows": n, "ok": n_ok, "error": n_err}


def summarize(db: Path) -> dict:
    """派生指标。返回的 latency 用单调时钟口径。"""
    with C.db(db) as conn:
        rows = conn.execute(
            "SELECT model, status, latency_ms, total_tokens, cost_usd, cache_hit "
            "FROM gateway_request").fetchall()
        spans = conn.execute(
            "SELECT name, status, duration_ms FROM span").fetchall()

    total = len(rows)
    by_status: dict[str, int] = {}
    by_model: dict[str, dict] = {}
    lat_all: list[int] = []
    lat_ok: list[int] = []
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        m = by_model.setdefault(r["model"], {"n": 0, "lat": [], "err": 0, "tokens": 0})
        m["n"] += 1
        m["lat"].append(r["latency_ms"])
        m["tokens"] += r["total_tokens"]
        if r["status"] in ("error", "empty"):
            m["err"] += 1
        if r["total_tokens"]:
            lat_all.append(r["latency_ms"])
            if r["status"] in ("ok", "degraded"):
                lat_ok.append(r["latency_ms"])

    cache_hits = sum(1 for r in rows if r["cache_hit"])
    saved_tokens = sum(r["total_tokens"] for r in rows if r["cache_hit"])

    out = {
        "requests_total": total,
        "status_counts": by_status,
        # 失败率必须把 error 与 empty（上游 200 但内容为空）都算上。
        # 只算 error 会让思考型模型的静默失败从看板上消失。
        "error_rate": round((by_status.get("error", 0)
                             + by_status.get("empty", 0)) / total, 4) if total else None,
        "error_rate_breakdown": {
            "error": by_status.get("error", 0),
            "empty": by_status.get("empty", 0),
        },
        "cache_hit_count": cache_hits,
        "cache_hit_rate": round(cache_hits / total, 4) if total else None,
        "tokens_total": sum(r["total_tokens"] for r in rows),
        "cost_usd_total": round(sum(r["cost_usd"] for r in rows), 8),
        "latency_ms": {
            "n": len(lat_all),
            "p50": round(C.percentile(lat_all, 0.50), 1),
            "p95": round(C.percentile(lat_all, 0.95), 1),
            "max": max(lat_all) if lat_all else 0,
            # 非参数自助置信区间：样本 40 时也要给出不确定性，而不是只报点估计
            "p50_ci95": [round(x, 1) for x in C.bootstrap_ci(lat_all, "median")],
        },
        "per_model": {
            k: {"n": v["n"], "p50_ms": round(C.percentile(v["lat"], 0.50), 1),
                "err": v["err"], "tokens": v["tokens"]}
            for k, v in by_model.items()
        },
        "spans_total": len(spans),
        "span_status": {
            n: {st: sum(1 for x in spans if x["name"] == n and x["status"] == st)
                for st in {x["status"] for x in spans if x["name"] == n}}
            for n in {x["name"] for x in spans}
        },
        "_口径": {
            "clock": "latency_ms 由 monotonic_ms() 差值得到，不受 NTP 影响",
            "span_start": "span.start_mono 是相对单次 chat() 起点的偏移，"
                          "不能跨请求比较绝对值，只能比 duration_ms",
            "n_小": "n<100 时 p95 属描述统计，不构成 SLO 承诺",
        },
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--db", default=str(ROOT / "data" / "platform.db"))
    ap.add_argument("--n", type=int, default=40)
    a = ap.parse_args()
    if not a.smoke:
        # 生产路径：直接汇总真实 db
        s = summarize(Path(a.db))
        C.write_json(C.RESULTS / "m2_observability.json", s)
        print(C.json_dumps(s))
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="ap_m2_"))
    gen = synthesize(tmp / "m2.db", a.n)
    s = summarize(tmp / "m2.db")

    findings = []
    # 1) span 覆盖率：每次 chat 至少有 cache/upstream 两个 span
    with C.db(tmp / "m2.db") as conn:
        tr = conn.execute("SELECT COUNT(DISTINCT trace_id) c FROM span").fetchone()["c"]
        spans = conn.execute(
            "SELECT COUNT(*) c FROM span WHERE name IN ('cache','upstream')"
        ).fetchone()["c"]
    ok_cov = spans >= tr * 2
    findings.append(
        f"span 覆盖：{tr} 条 trace、{spans} 个 cache/upstream span"
        f"（要求每条 trace ≥2）→ {'PASS' if ok_cov else 'FAIL'}")

    # 2) 口径自洽：瓶颈 span 的 duration 不能超过请求总延迟
    with C.db(tmp / "m2.db") as conn:
        worst = conn.execute(
            "SELECT MAX(duration_ms) m FROM span WHERE name='upstream'").fetchone()["m"]
    lat_p95 = s["latency_ms"]["p95"]
    ok_lat = worst is None or worst <= (s["latency_ms"]["max"] + 1)
    findings.append(
        f"口径自洽：upstream span max duration={worst}ms ≤ 请求 max latency="
        f"{s['latency_ms']['max']}ms → {'PASS' if ok_lat else 'FAIL'}")

    # 2b) 缓存记账：命中缓存的请求必须零 token，否则「省钱」是假话
    with C.db(tmp / "m2.db") as conn:
        bad = conn.execute(
            "SELECT COUNT(*) c FROM gateway_request "
            "WHERE cache_hit=1 AND total_tokens!=0").fetchone()["c"]
        hits = conn.execute(
            "SELECT COUNT(*) c FROM gateway_request WHERE cache_hit=1"
        ).fetchone()["c"]
    findings.append(
        f"缓存记账：{hits} 次命中，其中 tokens≠0 的 {bad} 次"
        f"（要求 0，否则「复用不花钱」是假话）→ {'PASS' if bad==0 else 'FAIL'}")

    # 3) 失败率必须包含 empty，不能只算 error
    br = s["error_rate_breakdown"]
    ok_err = (0 <= s["error_rate"] <= 0.35 and br["empty"] > 0)
    findings.append(
        f"失败率含静默失败：{s['error_rate']*100:.1f}% "
        f"= error {br['error']} + empty {br['empty']}（合成注入 30% timeout "
        f"+ 8% 思考型空回答；empty 必须 >0 否则这条断言是空的）"
        f" → {'PASS' if ok_err else 'FAIL'}")

    result = {
        "milestone": "M2",
        "mode": "synthetic（脚本化客户端，非真实上游）",
        "generated_at": C.now_ms_wall(),
        "findings": findings,
        "refutes": [
            "合成延迟来自 random.Random(20261011).lognormvariate(5.6,0.45)，"
            "不代表任何真实模型性能；真实 latency 需 M1 --live 重启后补测。",
            "n=40 时 p95 的 bootstrap 区间很宽，属描述统计，不能写成 SLO。",
            "错误率 30% 是脚本注入的上游超时率，不是 ollama 的真实可用率。真实可用率需在 M1 --live 下用 n≥100 复测。",
        ],
        "generation": gen,
        "metrics": s,
    }
    C.write_json(C.RESULTS / "m2_observability.json", result)
    for f in findings:
        print(f)
    print(f"p50={s['latency_ms']['p50']}ms p95={s['latency_ms']['p95']}ms "
          f"ci95={s['latency_ms']['p50_ci95']} "
          f"cache_hit={s['cache_hit_rate']} error={s['error_rate']}")
    return 0 if all("PASS" in f for f in findings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
