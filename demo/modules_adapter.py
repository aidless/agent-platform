"""Build the M1-M9 module evidence payload.

``results/`` is gitignored in agent-platform; this adapter freezes those local
result files into a committed ``modules.json`` replay. The M7 page is generated
from code; this page is generated from the result artifacts and says so.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

MODULES = [
    ("M1", "LLM 网关", ["results/m1_gateway.json"]),
    ("M2", "可观测台", ["results/m2_observability.json"]),
    ("M3", "上下文压缩", ["results/m3_context_compact.json"]),
    ("M3b", "真 LLM 摘要", ["results/m3b_llm_summary.json"]),
    ("M4", "工具沙箱", ["results/m4_tool_sandbox.json"]),
    ("M4b", "OS 级隔离", ["results/m4b_os_sandbox.json"]),
    ("M5", "韧性层", ["results/m5_resilience.json"]),
    ("M6", "数据管道", ["results/m6_etl.json", "results/m6_etl_drift.json"]),
    ("M7", "端到端工单链路", ["results/m7_e2e.json"]),
    ("M8", "成本与规模", ["results/m8_scale.json"]),
    ("M9", "execution-based 评测", ["results/m9_exec_eval.json"]),
]


def _read_sources(paths: list[str]) -> tuple[list[str], list[str], list[str], bool]:
    findings: list[str] = []
    refutes: list[str] = []
    present: list[str] = []
    for rel in paths:
        path = ROOT / rel
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        present.append(rel)
        findings.extend(str(item) for item in data.get("findings", []))
        refutes.extend(str(item) for item in data.get("refutes", []))
    return findings, refutes, present, bool(present)


def _classify_finding(finding: str) -> str:
    """Classify a finding by its own terminal token, not any substring.

    Some modules carry a deliberate negative control: a line ending in FAIL
    that says it was expected to fail. Counting that as an unexpected failure
    would turn the module red for doing the right thing.
    """
    text = finding.strip()
    match = re.search(r"→\s*(PASS|FAIL)", text)
    if not match:
        return "info"
    if match.group(1) == "PASS":
        return "pass"
    expected_markers = ("预期失败", "负例基准", "反例", "符合预期")
    if any(marker in text for marker in expected_markers):
        return "expected_fail"
    return "fail"


def _status(findings: list[str]) -> tuple[str, dict]:
    counts = {"pass": 0, "fail": 0, "expected_fail": 0, "info": 0}
    for finding in findings:
        counts[_classify_finding(finding)] += 1
    if not findings:
        return "NO_FINDINGS", counts
    if counts["fail"]:
        return "FAIL", counts
    if counts["pass"] or counts["expected_fail"]:
        return "PASS", counts
    return "PARTIAL", counts


def _git_snapshot() -> tuple[str, bool, list[str]]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        raw = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=ROOT, text=True, stderr=subprocess.DEVNULL,
        ).splitlines()
        ignored = ("results/", "demo/public/modules.json")
        dirty_paths = [
            line[3:] for line in raw
            if len(line) > 3 and not line[3:].startswith(ignored)
        ]
        return commit, bool(dirty_paths), dirty_paths[:12]
    except Exception:
        return "unknown", False, []


def build_modules_payload(generated_at: str | None = None) -> dict:
    generated_at = generated_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    modules = []
    checks = []
    for module_id, title, sources in MODULES:
        findings, refutes, present, exists = _read_sources(sources)
        status, counts = _status(findings)
        checks.append({
            "module": module_id,
            "sources": sources,
            "present": present,
            "ok": len(present) == len(sources),
        })
        modules.append({
            "id": module_id,
            "title": title,
            "status": status,
            "passed": counts["pass"],
            "failed": counts["fail"],
            "expected_fail": counts["expected_fail"],
            "info": counts["info"],
            "findings": findings,
            "refutes": refutes,
            "sources": sources,
            "available": exists,
        })
    commit, dirty, dirty_paths = _git_snapshot()
    counts = {
        "modules": len(modules),
        "pass": sum(1 for m in modules if m["status"] == "PASS"),
        "fail": sum(1 for m in modules if m["status"] == "FAIL"),
        "no_findings": sum(1 for m in modules if m["status"] == "NO_FINDINGS"),
        "not_available": sum(1 for m in modules if not m["available"]),
        "passed_assertions": sum(m["passed"] for m in modules),
        "expected_fail_assertions": sum(m["expected_fail"] for m in modules),
        "failed_assertions": sum(m["failed"] for m in modules),
        "info_assertions": sum(m["info"] for m in modules),
    }
    return {
        "meta": {
            "repo": "agent-platform",
            "demo": "module-evidence",
            "commit": commit,
            "worktree_dirty": dirty,
            "dirty_paths": dirty_paths,
            "generated_at": generated_at,
            "mode": "result-replay",
            "python": __import__("sys").version.split()[0],
            "note": (
                "M1–M9 结果回放：results/ 是本地生成物、不入库；"
                "公开页回放已提交的 modules.json。"
            ),
        },
        "summary": counts,
        "modules": modules,
        "boundaries": [
            "这些是本地 results/*.json 的回放，不是公开页实时重跑；M7 有一个独立的代码生成页。",
            "findings/refutes 是各模块自己声明的断言与边界，不是外部审计结论。",
            "M6 需要 Docker；M1/M3b 的 live 路径需要本机 ollama；本页不重跑它们。",
            "11 个模块里有 10 个能离线跑（M6 除外）；这不等于 11 个都在生产环境验证过。",
        ],
        "verification": {
            "status": "MATCH" if all(c["ok"] for c in checks) else "PARTIAL",
            "checks": checks,
        },
    }


def write_modules_payload(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
