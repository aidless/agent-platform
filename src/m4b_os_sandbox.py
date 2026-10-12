#!/usr/bin/env python3
"""M4b 断言：OS 级隔离是不是真的。每条隔离都用攻击性用例打一次。

设计原则：**先证明容器能干活（非空性），再证明它跑不出去**。
如果只测「攻击失败」，那么一个根本没启动成功的容器也能让全部断言通过。
所以 I0（良性计算必须成功且结果正确）是其余七条的对照组。

    python3 src/m4b_os_sandbox.py --smoke
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aplat import common as C                                    # noqa: E402
from aplat.os_sandbox import DockerSandbox                       # noqa: E402

# ---- 攻击性用例 ----
CASE_BENIGN = "print(6*7)"
CASE_NETWORK = """
import socket
try:
    socket.create_connection(('1.1.1.1', 53), timeout=3)
    print('CONNECTED')
except OSError as e:
    print('BLOCKED', type(e).__name__)
"""
CASE_READONLY = """
try:
    open('/etc/evil', 'w').write('x')
    print('WROTE')
except OSError as e:
    print('BLOCKED', type(e).__name__)
"""
CASE_NONROOT = "import os; print('UID', os.getuid())"
CASE_CAPS = """
try:
    for ln in open('/proc/self/status'):
        if ln.startswith('CapEff'):
            print('CAPEFF', ln.split()[1]); break
except OSError as e:
    print('BLOCKED', type(e).__name__)
"""
CASE_MEMBOMB = """
try:
    b = bytearray(300 * 1024 * 1024)
    print('ALLOCATED', len(b))
except MemoryError:
    print('BLOCKED MemoryError')
"""
CASE_FORKBOMB = """
import os
n = 0
try:
    for _ in range(200):
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        n += 1
    print('FORKED', n)
except OSError as e:
    print('BLOCKED', type(e).__name__, n)
"""
CASE_INFLOOP = "while True: pass"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()

    sbx = DockerSandbox()
    ok, msg = sbx.available()
    findings: list[str] = []
    out: dict = {"image": sbx.image, "ordered_cases": {}}

    if not ok:
        print(f"环境不可用：{msg}")
        print("本模块需要本机 docker + python:3.12-slim 镜像；"
              "跳过（不是 PASS） → SKIPPED")
        return 0

    def record(tag: str, res, extra: dict | None = None):
        out["ordered_cases"][tag] = {
            "exit_code": res.exit_code, "stdout": res.stdout.strip()[:200],
            "stderr": res.stderr.strip()[:200], "timed_out": res.timed_out,
            "oom_killed": res.oom_killed, "duration_ms": res.duration_ms,
            **(extra or {}),
        }
        return res

    # I0 非空性对照：容器必须真能干活
    r0 = record("I0_benign", sbx.run(CASE_BENIGN))
    ok0 = r0.exit_code == 0 and r0.stdout.strip() == "42"
    findings.append(
        f"I0 非空性对照：良性计算 6*7 在容器里执行，stdout={r0.stdout.strip()!r}、"
        f"exit={r0.exit_code} → {'PASS' if ok0 else 'FAIL'}"
        f"（这条保证了下面七条不是因为容器没起来而「假通过」）")

    r1 = record("I1_network", sbx.run(CASE_NETWORK))
    ok1 = "BLOCKED" in r1.stdout or r1.exit_code != 0
    findings.append(
        f"I1 网络隔离（--network none）：容器内连 1.1.1.1:53 → "
        f"{r1.stdout.strip()[:40]!r} → {'PASS' if ok1 else 'FAIL'}")

    r2 = record("I2_readonly", sbx.run(CASE_READONLY))
    ok2 = "BLOCKED" in r2.stdout or r2.exit_code != 0
    findings.append(
        f"I2 文件系统只读（--read-only）：写 /etc/evil → "
        f"{r2.stdout.strip()[:40]!r} → {'PASS' if ok2 else 'FAIL'}")

    r3 = record("I3_nonroot", sbx.run(CASE_NONROOT))
    uid = (r3.stdout.strip().split()[-1] if r3.stdout.strip() else "?")
    ok3 = uid == "65534"
    findings.append(
        f"I3 非 root（--user 65534:65534）：容器内 uid={uid}"
        f" → {'PASS' if ok3 else 'FAIL'}")

    r4 = record("I4_caps", sbx.run(CASE_CAPS))
    cabeff = (r4.stdout.strip().split()[-1] if r4.stdout.strip() else "?")
    ok4 = cabeff.replace("0", "") == "" and cabeff != "?"
    findings.append(
        f"I4 权限收敛（--cap-drop ALL）：/proc/self/status CapEff={cabeff}"
        f"（全 0 才通过）→ {'PASS' if ok4 else 'FAIL'}")

    r5 = record("I5_membomb", sbx.run(CASE_MEMBOMB))
    ok5 = ("BLOCKED" in r5.stdout or r5.exit_code != 0 or r5.oom_killed)
    findings.append(
        f"I5 内存上限（--memory 64m）：申请 300MiB → "
        f"exit={r5.exit_code} oom={r5.oom_killed} "
        f"out={r5.stdout.strip()[:40]!r} → {'PASS' if ok5 else 'FAIL'}")

    r6 = record("I6_forkbomb", sbx.run(CASE_FORKBOMB))
    ok6 = ("BLOCKED" in r6.stdout or r6.exit_code != 0)
    findings.append(
        f"I6 进程数上限（--pids-limit 32）：fork 200 次 → "
        f"exit={r6.exit_code} out={r6.stdout.strip()[:50]!r} → "
        f"{'PASS' if ok6 else 'FAIL'}")

    r7 = record("I7_timeout", sbx.run(CASE_INFLOOP, timeout_s=8))
    ok7 = r7.timed_out
    findings.append(
        f"I7 时间上限（外部 timeout=8s + docker rm -f）：死循环 → "
        f"timed_out={r7.timed_out}，用时 {r7.duration_ms}ms"
        f" → {'PASS' if ok7 else 'FAIL'}")

    # 残留容器检查：超时后是否真的没留下容器
    import subprocess
    ps = subprocess.run(["docker", "ps", "-a", "--filter", "name=aplat-sbx-",
                         "--format", "{{.Names}}"],
                        capture_output=True, text=True)
    leftovers = [x for x in ps.stdout.split() if x]
    ok8 = not leftovers
    findings.append(
        f"无残留容器：超时路径 kill 后 docker ps -a 里 aplat-sbx-* 数量="
        f"{len(leftovers)} {leftovers} → {'PASS' if ok8 else 'FAIL'}")

    result = {
        "milestone": "M4b",
        "mode": f"真实 docker 隔离（镜像 {sbx.image}，非 mock）",
        "generated_at": C.now_ms_wall(),
        "isolation_args": {
            "--network": "none", "--memory": "64m", "--memory-swap": "64m",
            "--pids-limit": "32", "--read-only": True,
            "--tmpfs": "/tmp:rw,size=8m,noexec,nosuid",
            "--user": "65534:65534", "--cap-drop": "ALL",
            "--security-opt": "no-new-privileges",
        },
        "findings": findings,
        "refutes": [
            "本层只隔离「代码执行」；容器逃逸、内核漏洞不在覆盖范围内"
            "（那需要 gVisor/Kata/seccomp 白名单与内核补丁）。",
            "I6 的 fork 炸弹判据是「被限流或容器死亡」，"
            "没有精确核对 pids-limit=32 的边界值。",
            "I5 只证明 300MiB 申请失败，没有测 cgroup 边界的精确阈值。",
            "tmpfs 的 size=8m 未单独攻击（只验了根文件系统只读）。",
            "容器启动本身有秒级开销，本层不适合高频小工具调用；"
            "生产上应是「热池 + 池内复用」而非每次 docker run。",
        ],
        "results": out,
    }
    C.write_json(C.RESULTS / "m4b_os_sandbox.json", result)
    for f in findings:
        print(f)
    return 0 if all("PASS" in f for f in findings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
