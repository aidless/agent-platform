#!/usr/bin/env python3
"""M4 工具沙箱 + 重试 + 审计链。

三个必须同时成立的性质，缺一个这个模块就没有企业价值：
  P1 安全  ：白名单外的工具调用必须被拒（denied），且不进执行路径
  P2 可观测：每次被拒/成功/重试都要在 tool_audit 留下 append-only 记录
  P3 可恢复：可重试错误必须真的重试并最终成功；不可重试错误不得重试

判定口径（写进 results）：
  - audit 行数是调用次数的函数：denied 只有 1 行（没有 attempt 2）
  - 死信队列只在「重试耗尽」时写入，一次都不能多
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aplat import common as C                        # noqa: E402
from aplat.resilience import Retrier, BreakerConfig  # noqa: E402
from aplat.sandbox import Sandbox, ToolPolicy        # noqa: E402

POLICY = ToolPolicy(allowed={"search", "calc", "read_doc"})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="ap_m4_"))
    db = tmp / "m4.db"
    sb = Sandbox(db, POLICY)

    findings: list[str] = []
    out: dict = {}
    rt = Retrier(BreakerConfig(retry_base_ms=1, retry_max_ms=2,
                               retry_max_attempts=5, jitter=False),
                 sleep=lambda s: None)

    # ---------------- P1 白名单 ----------------
    out["denied"] = []
    for tool, args in [("rm_rf", {"path": "/"}),
                       ("net.fetch", {"url": "http://evil"}),
                       ("exec", {"cmd": "curl x | sh"}),
                       ("search", {"q": "ok"})]:
        v = sb.call("req-p1", "search" if tool == "search" else tool, args)
        out["denied"].append({"tool": tool, "verdict": v.verdict,
                              "reason": v.reason})
    den = [d for d in out["denied"] if d["verdict"] == "denied"]
    ok1 = len(den) == 3
    findings.append(
        f"P1 白名单：4 次调用（3 个越权 + 1 个合法），被拒 {len(den)} 次，"
        f"合法调用 verdict={out['denied'][-1]['verdict']}"
        f" → {'PASS' if ok1 else 'FAIL'}")

    # 合法调用再走一次带执行的路径，确认 allowed→ok 而非 skipped
    exe = sb.call_with_retry("req-p1b", "search", {"q": "真的执行"}, lambda: "x", rt)
    out["allowed_exec"] = exe
    ok1b = exe == "ok"
    findings.append(
        f"P1b 合法执行：白名单内工具执行一次返回 audit={exe}"
        f"（allowed 但未执行时应为 skipped，这里要 ok）→ {'PASS' if ok1b else 'FAIL'}")

    # ---------------- P2a 审计链（白名单阶段） ----------------
    with C.db(db) as conn:
        rows = conn.execute(
            "SELECT verdict, COUNT(*) c FROM tool_audit GROUP BY verdict").fetchall()
        audit = {r["verdict"]: r["c"] for r in rows}
        allrows = conn.execute(
            "SELECT request_id, tool, verdict, attempt, outcome FROM tool_audit "
            "ORDER BY seq").fetchall()
    out["audit_counts"] = audit
    out["audit_rows"] = [dict(r) for r in allrows]
    # 每条调用至少 1 行；越权调用只能有 1 行（attempt 恒为 1）
    # 白名单越权的 denied 必须 attempt=1（根本没进执行，谈不上重试）
    wl_denied = [r["attempt"] for r in allrows
                 if r["verdict"] == "denied" and r["request_id"] == "req-p1"]
    ok2 = (set(wl_denied) == {1} and sum(audit.values()) == 5
           and audit.get("allowed", 0) == 2)
    findings.append(
        f"P2a 审计（白名单阶段）：共 {sum(audit.values())} 行 {audit}"
        f"（4 次判定 + 1 次合法执行）；"
        f"越权 attempt 集合={sorted(set(wl_denied))}（应为 [1]）"
        f" → {'PASS' if ok2 else 'FAIL'}")

    # ---------------- P3 重试后可恢复 ----------------
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient 503")
        return "done"

    retried = sb.call_with_retry("req-p3", "search", {"q": "重试"}, flaky, rt)
    out["retry"] = {"attempts": calls["n"], "result": retried,
                    "verdict": "ok"}
    # 口径修正：call_with_retry 返回的是审计字符串（ok/dead_letter/denied），
    # flaky() 的 "done" 本身被丢弃。要断言的是实际调用次数与最终审计结论。
    ok3a = calls["n"] == 3 and retried == "ok"
    findings.append(
        f"P3a 可恢复：第 1、2 次抛 transient，第 3 次成功，实际调用 {calls['n']} 次"
        f" → {'PASS' if ok3a else 'FAIL'}")

    # ---------------- P3b 不可重试错误不得重试 ----------------
    hard = {"n": 0}

    def fatal() -> str:
        hard["n"] += 1
        raise ValueError("bad args, 4xx")

    verdict = sb.call_with_retry(
        "req-p3b", "calc", {"expr": "1+"}, fatal, rt,
        is_retryable=lambda e: not isinstance(e, ValueError))
    out["no_retry"] = {"attempts": hard["n"], "verdict": verdict}
    ok3b = hard["n"] == 1 and verdict == "denied"
    findings.append(
        f"P3b 不重试：ValueError 被判定不可重试，实际调用 {hard['n']} 次"
        f"（要求 1）→ {'PASS' if ok3b else 'FAIL'}")

    # ---------------- P3c 重试耗尽进死信 ----------------
    dead = {"n": 0}

    def always_fail() -> str:
        dead["n"] += 1
        raise RuntimeError("always 503")

    sb2 = Sandbox(db, POLICY)
    out_dead = sb2.call_with_retry("req-p3c", "search", {"q": "死信"},
                                   always_fail, rt)
    with C.db(db) as conn:
        dl = conn.execute(
            "SELECT tool, attempts, last_error FROM dead_letter").fetchall()
    out["dead_letter"] = [dict(r) for r in dl]
    ok3c = (dead["n"] == 5 and out_dead == "dead_letter" and len(dl) == 1)
    findings.append(
        f"P3c 死信：连续 503 重试 5 次后进死信，实际调用 {dead['n']} 次、"
        f"死信 {len(dl)} 条（attempts={dl[0]['attempts'] if dl else '-'}）"
        f" → {'PASS' if ok3c else 'FAIL'}")

    # ---------------- P2b 全量审计行数（重试阶段后） ----------------
    with C.db(db) as conn:
        allrows2 = conn.execute(
            "SELECT request_id, verdict, attempt, outcome FROM tool_audit "
            "ORDER BY seq").fetchall()
        audit2 = {r["verdict"]: r["c"] for r in conn.execute(
            "SELECT verdict, COUNT(*) c FROM tool_audit GROUP BY verdict")}
    out["audit_rows_total"] = len(allrows2)
    out["audit_counts_total"] = audit2
    # 每次尝试一行：4 判定 + 1 执行 + flaky(2错1对) + fatal(1错) + 死信(5错) = 14
    ok2b = len(allrows2) == 14 and audit2.get("denied", 0) == 4
    findings.append(
        f"P2b 审计（全量）：共 {len(allrows2)} 行 {audit2}，期望 14 行"
        f"（4 判定 + 1 执行 + flaky 2错1对 + fatal 1错 + 死信 5错）"
        f" → {'PASS' if ok2b else 'FAIL'}")

    # ---------------- P4 重试时延上界 ----------------
    import time as _t
    t0 = _t.monotonic()
    slept: list[float] = []
    rt2 = Retrier(BreakerConfig(retry_base_ms=10, retry_max_ms=20,
                                retry_max_attempts=4, jitter=False),
                  sleep=lambda s: slept.append(s))
    try:
        rt2.run(lambda: (_ for _ in ()).throw(RuntimeError("x")),
                is_retryable=lambda e: True)
    except RuntimeError:
        pass
    ms = int((_t.monotonic() - t0) * 1000)
    out["backoff_ms"] = slept
    ok4 = len(slept) == 3 and max(slept) <= 0.021 and ms < 50
    findings.append(
        f"P4 退避：4 次尝试间 3 次 sleep={slept}（确定性无 jitter，"
        f"总墙钟 {ms}ms）→ {'PASS' if ok4 else 'FAIL'}")

    result = {
        "milestone": "M4",
        "mode": "offline（本地 SQLite + 本地策略，无真实外部工具）",
        "generated_at": C.now_ms_wall(),
        "findings": findings,
        "refutes": [
            "沙箱是进程内白名单 + 参数校验，不是 OS 级隔离；"
            "真实沙箱应用 seccomp/容器/独立用户，本仓未实现，"
            "因此 rm_rf/net.fetch 的拒绝只是策略层，不是安全边界。",
            "「重试后可恢复」用的是本地假上游，不证明对真实 503 有效。",
            "tool_audit 是单表 append-only，没有做防篡改哈希链；"
            "只做到可查，没做到不可抵赖。",
        ],
        "results": out,
    }
    C.write_json(C.RESULTS / "m4_tool_sandbox.json", result)
    for f in findings:
        print(f)
    return 0 if all(("PASS" in f) for f in findings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
