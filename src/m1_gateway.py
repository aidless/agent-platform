#!/usr/bin/env python3
"""M1 网关：跑真实请求，验证幂等、成本记账、降级路径，并把结论落盘。

用法：
    python3 src/m1_gateway.py --smoke      # 只测不联网（离线自检）
    python3 src/m1_gateway.py --live       # 真打本机 ollama
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aplat import common as C                      # noqa: E402
from aplat.gateway import LLMGateway, ChatRequest  # noqa: E402
from aplat.ollama_client import OllamaClient       # noqa: E402
from aplat.resilience import CircuitBreaker, BreakerConfig  # noqa: E402

REQS = [
    ("用一句话解释什么是检索增强生成。", "RAG 一句话"),
    ("把「今天天气不错」翻译成英文。", "翻译"),
    ("列出三个 Python 列表方法。", "列举"),
]


def run_smoke() -> dict:
    """离线自检：不打网络，验证幂等/记账/降级/熔断四条路径都能被观测到。"""
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="ap_m1_"))
    db = tmp / "platform.db"
    client = OllamaClient(offline=True)

    class FakeBreaker:
        """可控熔断，用来触发降级分支。"""
        def __init__(self):
            self.open = False

        def allow(self, _key=None):
            return not self.open

        def on_success(self, _key=None):
            pass

        def on_failure(self, kind=None, _key=None):
            pass

    gw = LLMGateway(db, client, breaker=FakeBreaker())
    gw.client.chat = lambda **kw: {  # type: ignore[assignment]
        "content": "返回内容-stub", "eval_count": 3, "prompt_eval_count": 7}

    findings: list[str] = []
    out: dict = {}

    # --- 1) 首次请求 + 幂等重放 -------------------------------------
    r1 = gw.chat(ChatRequest(model="qwen3:8b",
                             messages=[{"role": "user", "content": "hello"}],
                             idempotency_key="k-001"))
    r2 = gw.chat(ChatRequest(model="qwen3:8b",
                             messages=[{"role": "user", "content": "hello world"}],
                             idempotency_key="k-001"))
    out["idempotent_replay"] = {
        "first_status": r1.status, "second_status": r2.status,
        "second_tokens": r2.total_tokens, "second_cost_usd": r2.cost_usd,
        "replay_of_set": bool(r2.replay_of),
    }
    ok_idem = (r1.status == "ok" and r2.status == "cached"
               and r2.total_tokens == 0 and r2.cost_usd == 0.0)
    findings.append(f"幂等重放：首条 {r1.status} / 重放 {r2.status}，"
                    f"重放 token={r2.total_tokens} 成本=${r2.cost_usd:.6f}"
                    f"（期望 0）→ {'PASS' if ok_idem else 'FAIL'}")

    # --- 1b) 模型解析：显式点名已知模型必须被尊重 ----------------------
    r3 = gw.chat(ChatRequest(model="llama3.2:3b",
                             messages=[{"role": "user", "content": "点名模型"}],
                             idempotency_key="k-model"))
    out["model_resolution"] = {"requested": "llama3.2:3b", "used": r3.model}
    ok_model = r3.model == "llama3.2:3b"
    findings.append(
        f"模型解析：请求 model=llama3.2:3b（route primary=qwen3:8b 的 fallback），"
        f"实际使用={r3.model} → {'PASS' if ok_model else 'FAIL'}"
        f"。修前该字段被无声丢弃（live 实测：curl 请求 3b、服务用了 8b）。")

    # --- 1c) 静默失败检测：上游 200 但 content 为空 --------------------
    class ThinkOnly(OllamaClient):
        """模拟思考型模型：预算被 thinking 通道吃光，content 为空。"""

        def __init__(self):
            super().__init__(offline=True)

        def chat(self, **kw):
            return {"content": "", "thinking": "推理过程" * 30,
                    "done_reason": "length", "eval_count": 64,
                    "prompt_eval_count": 10}

    tmp_t = Path(tempfile.mkdtemp(prefix="ap_m1t_"))
    gw_t = LLMGateway(tmp_t / "t.db", ThinkOnly())
    r_t = gw_t.chat(ChatRequest(model="qwen3:8b",
                                messages=[{"role": "user", "content": "hi"}],
                                idempotency_key="k-empty"))
    out["empty_content"] = {
        "status": r_t.status, "http": r_t.http_status,
        "empty": r_t.empty_content, "done_reason": r_t.done_reason,
        "thinking_tokens": r_t.thinking_tokens, "error_kind": r_t.error_kind,
    }
    ok_empty = (r_t.status == "empty" and r_t.empty_content
                and r_t.http_status == 200 and r_t.thinking_tokens > 0)
    findings.append(
        f"静默失败检测：thinking 型模型的空 content 被标记为 "
        f"status={r_t.status} / error_kind={r_t.error_kind} / "
        f"done_reason={r_t.done_reason} / thinking≈{r_t.thinking_tokens} tokens"
        f" → {'PASS' if ok_empty else 'FAIL'}"
        f"。修前会被记成 status=ok（live 实测：qwen3:8b 空回答 29.5s）")

    # --- 2) 记账完整性 ------------------------------------------------
    with C.db(db) as conn:
        row = conn.execute(
            "SELECT COUNT(*) n, SUM(total_tokens) t, SUM(cost_usd) c "
            "FROM gateway_request").fetchone()
    out["ledger"] = {"rows": row["n"], "tokens": row["t"], "cost_usd": round(row["c"], 8)}
    # 本地模型单价为 0，所以 cost=0 是口径正确而非记账失败
    findings.append(f"记账：{row['n']} 行，合计 {row['t']} tokens、${row['c']:.6f}"
                    f"（本机模型单价 0，外部价仅作对照口径）")

    # --- 3) 降级路径 ------------------------------------------------
    gw.client.chat = lambda **kw: (_ for _ in ()).throw(  # type: ignore[assignment]
        __import__("aplat.ollama_client", fromlist=["OllamaError"]).OllamaError(
            "timeout", "simulated"))
    degraded = gw.chat(ChatRequest(model="qwen3:8b",
                                   messages=[{"role": "user", "content": "hi"}],
                                   idempotency_key="k-timeout"))
    out["degraded"] = {"status": degraded.status, "degraded": degraded.degraded,
                       "error_kind": degraded.error_kind, "http": degraded.http_status}
    # fallback 也 timeout → 应落到 error + http 502
    ok_deg = degraded.status == "error" and degraded.http_status == 502 and degraded.degraded
    findings.append(f"降级：timeout 时降级到 fallback，仍失败 → status={degraded.status} "
                    f"http={degraded.http_status} degraded={degraded.degraded}"
                    f" → {'PASS' if ok_deg else 'FAIL'}")

    # --- 4) 熔断 open 时不打上游 --------------------------------------
    calls: list[str] = []

    class CountingClient(OllamaClient):
        def chat(self, **kw):
            calls.append(kw["model"])
            return {"content": "ok", "eval_count": 1, "prompt_eval_count": 1}

    # 造一个必然失败的客户端
    class FailingClient(OllamaClient):
        def chat(self, **kw):
            calls.append("fail:" + kw["model"])
            from aplat.ollama_client import OllamaError as _E
            raise _E("http_5xx", "boom")

    tmp2 = Path(tempfile.mkdtemp(prefix="ap_m1b_"))
    brk = CircuitBreaker(tmp2 / "b.db", "qwen3:8b",
                         BreakerConfig(failure_threshold=3, cooldown_ms=60_000))
    gw2 = LLMGateway(tmp2 / "b2.db", FailingClient(offline=True), breaker=brk)
    before = len(calls)
    for i in range(5):
        gw2.chat(ChatRequest(model="qwen3:8b",
                             messages=[{"role": "user", "content": f"m{i}"}],
                             idempotency_key=f"bk-{i}"))
    after = len(calls)
    out["breaker"] = {"upstream_attempts": after - before, "state": brk.state_now(),
                      "threshold": 3}
    # 期望：前 3 次真正打到上游，之后被熔断拦住（不新增 upstream 调用）
    ok_brk = (after - before) == 3
    findings.append(f"熔断：阈值 3，5 次请求实际打到上游 {after - before} 次"
                    f"（期望 3，其余被 circuit_open 拦截）→ "
                    f"{'PASS' if ok_brk else 'FAIL'}；当前状态 {brk.state_now()}")

    result = {
        "milestone": "M1",
        "generated_at": C.now_ms_wall(),
        "mode": "smoke(offline, no network by design)",
        "findings": findings,
        "refutes": [
            "本机模型单价记为 0，因此 cost_usd 全 0 是记账口径而非缺陷；"
            "外部模型价（gpt-4o-mini 等）只用于对照，不发起调用。",
            "--smoke 不打网络，验证的是控制流与记账，不验证真实延迟。"
            "真实延迟见 --live 的 p50/p95。",
        ],
        "results": out,
    }
    C.write_json(C.RESULTS / "m1_gateway.json", result)
    return result


def run_live(db_path: Path) -> dict:
    """每轮用干净 db：复用旧库会让幂等键命中历史记录，把首次请求显示成 cached。"""
    """真打 ollama。前提：ollama serve 在 127.0.0.1:11434。"""
    client = OllamaClient()
    models = set(client.list_models())
    out = {"ollama_version": client.version(),
           "models": sorted(models), "requests": [], "findings": []}
    # 路由表要覆盖实际会点名的模型，否则 _resolve_model 会把未知模型当「忽略」处理，
    # 现场探针就测不到 qwen3:8b（本次踩到：探针请求 qwen3 实际用了 3b）。
    route_cfg = {
        "default": {"primary": "llama3.2:3b", "fallback": "llama3.2:3b"},
        "quality": {"primary": "qwen3:8b", "fallback": "llama3.2:3b"},
    }
    brk = CircuitBreaker(db_path, "llama3.2:3b",
                         BreakerConfig(failure_threshold=3, cooldown_ms=30_000))
    gw = LLMGateway(db_path, client, routes=route_cfg, breaker=brk)

    latencies: list[int] = []
    for i, (q, label) in enumerate(REQS):
        t0 = C.monotonic_ms()
        r = gw.chat(ChatRequest(model="llama3.2:3b",
                                messages=[{"role": "user", "content": q}],
                                idempotency_key=f"live-{i}", max_tokens=128))
        wall = C.monotonic_ms() - t0
        latencies.append(r.latency_ms)
        out["requests"].append({
            "label": label, "status": r.status, "http": r.http_status,
            "latency_ms": r.latency_ms, "wall_ms": wall,
            "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
            "content_head": r.content[:80], "degraded": r.degraded,
            "error_kind": r.error_kind,
        })

    # 幂等重放（同 key 应 0 token）
    r_rep = gw.chat(ChatRequest(model="llama3.2:3b",
                                messages=[{"role": "user", "content": REQS[0][0]}],
                                idempotency_key="live-0", max_tokens=128))
    out["requests"].append({
        "label": "幂等重放(live-0)", "status": r_rep.status,
        "latency_ms": r_rep.latency_ms, "total_tokens": r_rep.total_tokens,
        "replay_of": r_rep.replay_of, "degraded": r_rep.degraded,
    })
    out["findings"].append(
        f"幂等重放：重复 live-0 后 status={r_rep.status}、tokens={r_rep.total_tokens}"
        f"、replay_of={r_rep.replay_of}。"
        f"{'PASS：重复请求不再产生 token 成本' if r_rep.status=='cached' and r_rep.total_tokens==0 else 'FAIL'}")

    # 现场复现「思考型模型静默失败」：显式点名 qwen3:8b。
    # 本机实测该模型把 num_predict 烧在 thinking 通道，content 为空、
    # done_reason=length。修前网关会记成 status=ok，修后应记 status=empty。
    if "qwen3:8b" in models:
        r_think = gw.chat(ChatRequest(
            model="qwen3:8b",
            messages=[{"role": "user", "content": "说三个 Python 列表方法"}],
            idempotency_key="live-think", max_tokens=64))
        out["thinking_model_probe"] = {
            "model": r_think.model, "status": r_think.status,
            "empty_content": r_think.empty_content,
            "done_reason": r_think.done_reason,
            "thinking_tokens": r_think.thinking_tokens,
            "error_kind": r_think.error_kind, "latency_ms": r_think.latency_ms,
        }
        out["findings"].append(
            f"思考型模型实况：显式点名 qwen3:8b（num_predict=64）→ "
            f"status={r_think.status}、empty={r_think.empty_content}、"
            f"done_reason={r_think.done_reason}、thinking≈"
            f"{r_think.thinking_tokens} tokens、用时 {r_think.latency_ms}ms。"
            f"{'已被标记为 empty（不再伪装成成功）' if r_think.status == 'empty' else '本次未触发空内容'}")

    lat = [r for r in out["requests"] if r.get("status") != "cached" and r.get("latency_ms")]
    vals = [r["latency_ms"] for r in lat if r.get("latency_ms")]
    out["latency"] = {
        "n": len(vals),
        "p50_ms": round(C.percentile(vals, 0.50), 1),
        "p95_ms": round(C.percentile(vals, 0.95), 1),
        "max_ms": max(vals) if vals else 0,
    }
    out["findings"].append(
        f"延迟（n={len(vals)}，llama3.2:3b 本机）：p50={out['latency']['p50_ms']}ms "
        f"p95={out['latency']['p95_ms']}ms。样本极小，不能当 SLO 基线。")
    out["refutes"] = [
        "n=3 的 p50/p95 不具备统计意义，只证明链路可用。"
        "SLO 基线要求 n>=100 且分布稳定，本仓未达。",
        "token 数为估算值（见 tokenizer.py），与真实 BPE 计数有偏差。",
    ]
    C.write_json(C.RESULTS / "m1_gateway_live.json", out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--live", action="store_true")
    # live 每次用新鲜 db：复用旧库会让幂等键命中历史记录，
    # 把本该首发的请求显示成 cached（本次真实踩到过）。
    ap.add_argument("--db", default="")
    a = ap.parse_args()
    if a.live and not a.db:
        import tempfile
        a.db = str(Path(tempfile.mkdtemp(prefix="ap_m1live_")) / "live.db")
    rc = 0
    if a.smoke or not a.live:
        r = run_smoke()
        for f in r["findings"]:
            print(f)
        if any("FAIL" in f for f in r["findings"]):
            rc = 1
    if a.live:
        out = run_live(Path(a.db))
        print(json.dumps({k: v for k, v in out.items() if k != "requests"},
                         ensure_ascii=False, indent=2)[:1200])
        for q in out["requests"]:
            print("  ", {k: v for k, v in q.items() if k != "content_head"},
                  q.get("content_head", "")[:40])
    return rc


if __name__ == "__main__":
    import json
    raise SystemExit(main())
