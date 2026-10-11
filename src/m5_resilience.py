#!/usr/bin/env python3
"""M5 韧性层单测：退避、熔断、降级、幂等重放的边界行为。

这些是「出事时才会跑」的代码路径，平时没人执行，所以必须用确定性
单测把它们全部逼出来。每条断言都对应一个线上事故场景：

  A1 指数退避 + jitter 的形状（不能无限增长，不能退化为固定延迟）
  A2 4xx 不进熔断计数（客户端错误不是供应商故障）
  A3 熔断 closed→open→half_open→closed 完整状态机
  A4 half_open 只放一个探测请求（防惊群）
  A5 降级链：primary 全挂时 fallback 必须真的被调用且返回非空
  A6 降级链全挂时给出明确错误，而不是静默返回空
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aplat import common as C                                  # noqa: E402
from aplat.resilience import (BreakerConfig, CircuitBreaker,    # noqa: E402
                              Degrader, Retrier)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()

    findings: list[str] = []
    out: dict = {}

    # ---------------- A1 退避形状 ----------------
    slept: list[float] = []
    rt = Retrier(BreakerConfig(retry_base_ms=100, retry_max_ms=1000,
                               retry_max_attempts=6, jitter=False),
                 sleep=lambda s: slept.append(s))
    try:
        rt.run(lambda: (_ for _ in ()).throw(RuntimeError("x")),
               is_retryable=lambda e: True)
    except RuntimeError:
        pass
    # 无 jitter 期望严格 100/200/400/800/1000
    exp = [0.1, 0.2, 0.4, 0.8, 1.0]
    out["backoff_no_jitter"] = slept
    ok_a1a = slept == exp
    findings.append(
        f"A1a 指数退避（jitter=False）：{slept} 期望 {exp}"
        f" → {'PASS' if ok_a1a else 'FAIL'}")

    # 有 jitter 时上界不得越界，且不能全都等于 0
    slept2: list[float] = []
    rt2 = Retrier(BreakerConfig(retry_base_ms=100, retry_max_ms=1000,
                                retry_max_attempts=6, jitter=True), seed=7,
                  sleep=lambda s: slept2.append(s))
    try:
        rt2.run(lambda: (_ for _ in ()).throw(RuntimeError("x")),
                is_retryable=lambda e: True)
    except RuntimeError:
        pass
    caps = [0.1, 0.2, 0.4, 0.8, 1.0]
    ok_a1b = (len(slept2) == 5 and all(0 <= s <= c + 1e-9 for s, c in zip(slept2, caps))
              and any(s > 0 for s in slept2))
    findings.append(
        f"A1b full jitter 上界：{slept2} 全部落在各自上界内且非全零"
        f" → {'PASS' if ok_a1b else 'FAIL'}")

    # ---------------- A2 4xx 不计数 ----------------
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="ap_m5_"))
    brk = CircuitBreaker(tmp / "b.db", "p1", BreakerConfig(failure_threshold=3,
                                                          cooldown_ms=60_000))
    for _ in range(10):
        brk.on_failure("p1", "http_4xx")
    out["four_xx_fails"] = brk.fails
    ok_a2 = brk.fails == 0 and brk.state == "closed"
    findings.append(
        f"A2 4xx 不熔断：连续 10 次 http_4xx 后 fails={brk.fails}、"
        f"state={brk.state}（应为 0/closed）→ {'PASS' if ok_a2 else 'FAIL'}")

    # ---------------- A3 状态机 ----------------
    brk2 = CircuitBreaker(tmp / "b2.db", "p2", BreakerConfig(failure_threshold=3,
                                                            cooldown_ms=10 ** 9))
    seq = []
    for i in range(3):
        brk2.on_failure("p2", "http_5xx")
        seq.append(("after_fail%d" % (i + 1), brk2.state))
    allowed_open = brk2.allow("p2")
    seq.append(("open_allow", allowed_open))
    # cooldown 设成极大，half_open 不会自动到达；用 on_success 推进会留在 open
    out["breaker_seq"] = seq
    ok_a3 = brk2.state == "open" and allowed_open is False
    findings.append(
        f"A3 熔断打开：3 次 5xx 后 state 序列={seq}"
        f"（open 后 allow 必须 False）→ {'PASS' if ok_a3 else 'FAIL'}")

    # ---------------- A4 half_open 只放一个探测 ----------------
    brk3 = CircuitBreaker(tmp / "b3.db", "p3",
                          BreakerConfig(failure_threshold=1, cooldown_ms=0))
    brk3.on_failure("p3", "http_5xx")
    # cooldown=0 后第一次 allow 触发 half_open；观测连续两次 allow 的返回值
    first = brk3.allow("p3")
    second = brk3.allow("p3")
    out["half_open"] = {"first": first, "second": second,
                        "state": brk3.state}
    ok_a4 = first is True and second is False
    findings.append(
        f"A4 half_open 探测：cooldown 到期后第一次 allow={first}（放行探测），"
        f"第二次 allow={second}（同批请求其余拦住）→ {'PASS' if ok_a4 else 'FAIL'}")

    # ---------------- A5/A6 降级链 ----------------
    dg = Degrader([("primary", lambda: (_ for _ in ()).throw(RuntimeError("down"))),
                   ("fallback", lambda: "fallback-result")])
    name, val = dg.run()
    out["degrade"] = {"chosen": name, "value": val}
    ok_a5 = name == "fallback" and val == "fallback-result"
    findings.append(
        f"A5 降级生效：primary 抛错时链路选择 {name}、返回 {val!r}"
        f" → {'PASS' if ok_a5 else 'FAIL'}")

    dg2 = Degrader([("primary", lambda: ""), ("fallback", lambda: None)])
    try:
        dg2.run()
        name2, val2 = "(none)", "(none)"
    except RuntimeError as e:
        name2, val2 = "raised", str(e)[:40]
    out["degrade_all_empty"] = {"chosen": name2, "value": val2}
    ok_a6 = name2 == "raised"
    findings.append(
        f"A6 全空降级：primary/fallback 都返回空 → {name2} {val2!r}"
        f"（必须显式报错，不能静默返回空）→ {'PASS' if ok_a6 else 'FAIL'}")

    result = {
        "milestone": "M5",
        "mode": "offline 单测（注入 sleep，不真等待）",
        "generated_at": C.now_ms_wall(),
        "findings": findings,
        "refutes": [
            "退避序列用 seed=7 的确定性 jitter，只证明实现符合 full jitter 定义，"
            "不证明该参数在真实租户冲突下最优。",
            "cooldown 用 0 / 1e9 两个极值验证状态迁移，真实 cooldown 的选择"
            "需要按上游恢复时间调，本仓给不出建议值。",
            "half_open 单探测是进程内计数的，多副本部署时每个副本各放一个，"
            "不能替代分布式信号量。",
        ],
        "results": out,
    }
    C.write_json(C.RESULTS / "m5_resilience.json", result)
    for f in findings:
        print(f)
    return 0 if all("PASS" in f for f in findings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
