#!/usr/bin/env python3
"""Lock README and demo claims to committed results/*.json.

Run this before ``make smoke``. Smoke rewrites wall-clock fields, and those
new numbers are not the committed evidence.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))

from modules_adapter import (  # noqa: E402
    MODULES,
    _classify_finding,
    build_modules_payload,
)

FORBIDDEN = (
    "17–21",
    "17-21",
    "3.7–7.5",
    "3.7-7.5",
    "29.5s",
    "不入库",
    "M6 现在用的是 v2",
    "十一个里的 10",
    "11 个里的 10",
)
PY_FORBIDDEN_FILES = (
    ROOT / "src" / "m1_gateway.py",
    ROOT / "src" / "m3b_llm_summary.py",
    ROOT / "src" / "m8_scale.py",
    ROOT / "results" / "m1_gateway.json",
    ROOT / "results" / "m3b_llm_summary.json",
    ROOT / "results" / "m8_scale.json",
)
ABS_PATH = re.compile(r"(?:/home|/Users|/root)/[A-Za-z0-9._-]+|/data1/")
ABS_SCAN_SKIP = {
    "tools/check_contract.py",
    "tools/check_drift.py",
    "demo/test_demo.py",
}


def _fail(errors: list[str], message: str) -> None:
    errors.append(message)


def _version() -> str:
    tree = ast.parse((ROOT / "src" / "aplat" / "__init__.py").read_text(
        encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    if isinstance(node.value, ast.Constant):
                        return str(node.value.value)
    raise RuntimeError("__version__ missing")


def _counts() -> dict[str, int]:
    payload = build_modules_payload(generated_at="1970-01-01T00:00:00+00:00")
    return payload["summary"]


def _replay_matches(errors: list[str]) -> None:
    fresh = build_modules_payload(generated_at="1970-01-01T00:00:00+00:00")
    artifact = json.loads((ROOT / "demo" / "public" / "modules.json").read_text(
        encoding="utf-8"))
    if artifact["summary"] != fresh["summary"]:
        _fail(errors, f"modules.json summary != results: {artifact['summary']} vs {fresh['summary']}")
    by_fresh = {m["id"]: m for m in fresh["modules"]}
    by_art = {m["id"]: m for m in artifact["modules"]}
    if set(by_fresh) != set(by_art):
        _fail(errors, "modules.json module ids differ from results")
        return
    for module_id, module in by_fresh.items():
        other = by_art[module_id]
        for key in ("findings", "refutes", "status", "passed", "failed",
                    "expected_fail", "info", "sources"):
            if module[key] != other[key]:
                _fail(errors, f"modules.json {module_id}.{key} != results")
    note = artifact.get("meta", {}).get("note", "")
    if "不入库" in note:
        _fail(errors, "modules.json still says results are not committed")


def _readme_locks(errors: list[str], readme: str, summary: dict[str, int]) -> None:
    phrase = (
        f"{summary['passed_assertions']} 条 PASS、"
        f"{summary['expected_fail_assertions']} 条预期失败、"
        f"{summary['info_assertions']} 条说明、"
        f"{summary['failed_assertions']} 条未预期失败"
    )
    if phrase not in readme:
        _fail(errors, f"README missing exact count phrase: {phrase}")
    if "STATUS: WORK-IN-PROGRESS — not A yet" not in readme:
        _fail(errors, "README missing STATUS line")
    if _version() not in readme:
        _fail(errors, "README version does not match src/aplat/__init__.py")
    if "CI 重跑 9 个离线模块" not in readme:
        _fail(errors, "README missing the 9-module offline sentence")
    nonempty = [line for line in readme.splitlines() if line.strip()]
    opening = nonempty[1:4]
    if len(opening) < 3:
        _fail(errors, "README opening is shorter than 3 lines")
    else:
        blob = "\n".join(opening)
        if "解决" not in blob or "不解决" not in blob or "STATUS:" not in blob:
            _fail(errors, "README opening must say what it solves, what it does not, and STATUS")
    m8 = json.loads((ROOT / "results" / "m8_scale.json").read_text(encoding="utf-8"))
    speed = next(item for item in m8["findings"] if item.startswith("Q2 "))
    marker = "三档加速 ["
    start = speed.index(marker)
    end = speed.index("]", start) + 1
    speed_text = speed[start:end]
    if speed_text not in readme:
        _fail(errors, f"README missing committed speedup text: {speed_text}")
    for token in ("3.83→7.51", "38×–47×", "10k 档没有记录过区间"):
        if token not in json.dumps(m8, ensure_ascii=False):
            _fail(errors, f"m8 json lost historical token {token}")
        if token not in readme:
            _fail(errors, f"README missing historical token quoted from m8: {token}")
    cost = next(item for item in m8["findings"] if item.startswith("Q3 "))
    for token in ("$5.81/月", "$49.03/月"):
        if token not in cost or token not in readme:
            _fail(errors, f"cost token {token} not in both m8 finding and README")
    sources = m8["results"]["cost"]["basis"]["sources"]
    if list(sources) != ["m7_e2e.json"]:
        _fail(errors, f"m8 cost sources are not m7-only: {list(sources)}")
    if "m1_gateway_live.json 与" in m8["results"]["cost"]["source"]:
        _fail(errors, "m8 cost source still claims the live file was priced")
    m7 = json.loads((ROOT / "results" / "m7_e2e.json").read_text(encoding="utf-8"))
    for token in ("1181 → 620", "48%", "{'denied': 1, 'ok': 5}"):
        if token not in "\n".join(m7["findings"]) or token not in readme:
            _fail(errors, f"M7 token {token!r} missing from finding or README")
    m3b = json.loads((ROOT / "results" / "m3b_llm_summary.json").read_text(encoding="utf-8"))
    blob = "\n".join(m3b["findings"])
    for token in ("raw 215", "省 68%", "224 tokens"):
        if token not in blob or token not in readme:
            _fail(errors, f"M3b token {token!r} missing from finding or README")
    if m3b["results"]["dialogue_turns"] != 10 or "10 轮" not in "\n".join(m3b["refutes"]):
        _fail(errors, "M3b dialogue length and refute disagree")
    if "6 轮" in "\n".join(m3b["refutes"]):
        _fail(errors, "M3b refute still says 6 turns")
    m3 = json.loads((ROOT / "results" / "m3_context_compact.json").read_text(encoding="utf-8"))
    rates = next(item for item in m3["findings"] if item.startswith("token 降幅"))
    rate_text = rates.split("。", 1)[0]
    if rate_text not in readme:
        _fail(errors, f"README missing M3 rate text: {rate_text}")
    live = json.loads((ROOT / "results" / "m1_gateway_live.json").read_text(encoding="utf-8"))
    ok = sum(1 for row in live["requests"] if row.get("status") == "ok")
    if ok != 3 or "3 次 status=ok" not in readme:
        _fail(errors, "README live success count does not match m1_gateway_live.json")
    if str(live["thinking_model_probe"]["latency_ms"]) not in readme:
        _fail(errors, "README missing the committed thinking-model latency")
    m6 = json.loads((ROOT / "results" / "m6_etl.json").read_text(encoding="utf-8"))
    if "v2 没有接到拒同步判定" not in "\n".join(m6["refutes"]):
        _fail(errors, "m6 refute no longer says v2 is not the sync gate")
    if "v2 没有接到拒同步判定" not in readme:
        _fail(errors, "README still omits that the M6 sync gate is v1")


def _layout(errors: list[str], readme: str) -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    needs = next(line for line in makefile.splitlines() if line.startswith("NEEDS_DOCKER"))
    for name in ("m4b_os_sandbox.py", "m6_etl.py"):
        if name not in needs:
            _fail(errors, f"Makefile NEEDS_DOCKER does not list {name}")
    offline = sorted(p.name for p in (ROOT / "src").glob("m[0-9]*.py"))
    docker = {"m4b_os_sandbox.py", "m6_etl.py"}
    if len(offline) != 11 or len(set(offline) - docker) != 9:
        _fail(errors, f"module split changed: {offline}")
    if len(MODULES) != 11:
        _fail(errors, "modules adapter is not listing 11 modules")
    workflow = (ROOT / ".github" / "workflows" / "gates.yml").read_text(encoding="utf-8")
    if "make gates" not in workflow:
        _fail(errors, "CI does not run make gates")
    if "不覆盖" not in workflow or "aihot-db-1" not in workflow:
        _fail(errors, "CI workflow does not state what it skips")
    modes = ROOT / "FAILURE_MODES.md"
    modes_text = modes.read_text(encoding="utf-8")
    if "STATUS: WORK-IN-PROGRESS — not A yet" not in modes_text:
        _fail(errors, "FAILURE_MODES.md missing STATUS")
    if "不是一个做深的单一产品" not in modes_text or "不是一个做深的单一产品" not in readme:
        _fail(errors, "breadth-versus-depth gap is missing")
    engineering_path = ROOT / "docs" / "engineering.md"
    engineering_text = engineering_path.read_text(encoding="utf-8")
    if "被 `.gitignore` 排除" in engineering_text or "不入库" in engineering_text:
        _fail(errors, "engineering.md still says results are not committed")
    for path in (ROOT / "README.md", ROOT / "demo" / "README.md", modes, engineering_path):
        body = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if token in body:
                _fail(errors, f"{path.relative_to(ROOT)} contains forbidden claim {token!r}")
    for path in PY_FORBIDDEN_FILES:
        body = path.read_text(encoding="utf-8")
        if "29.5" in body or "6 轮" in body or "两个模块" in body:
            _fail(errors, f"{path.relative_to(ROOT)} still has a stale count")


def _absolute_paths(errors: list[str]) -> None:
    roots = [ROOT / "src", ROOT / "tools", ROOT / "demo", ROOT / "docs",
             ROOT / "results", ROOT / "README.md", ROOT / "FAILURE_MODES.md"]
    for root in roots:
        files = [root] if root.is_file() else [
            p for p in root.rglob("*")
            if p.suffix in {".py", ".md", ".json", ".html", ".js", ".yml"}
        ]
        for path in files:
            rel = str(path.relative_to(ROOT))
            if rel in ABS_SCAN_SKIP or "framework-agent/" in rel:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if ABS_PATH.search(line):
                    _fail(errors, f"absolute path {rel}:{n}")


def _selftest() -> int:
    samples = {
        "幂等 → PASS": "pass",
        "负例 → FAIL（预期失败）": "expected_fail",
        "门禁 → SKIPPED（不是 PASS）": "info",
        "口径 → FAIL": "fail",
        "记账：3 行，没有箭头": "info",
    }
    bad = [text for text, kind in samples.items() if _classify_finding(text) != kind]
    if bad:
        print("  FAIL  classifier selftest", bad)
        return 1
    print("  PASS  claim-drift selftest（分类器 5/5）")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return _selftest()
    errors: list[str] = []
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    summary = _counts()
    _replay_matches(errors)
    _readme_locks(errors, readme, summary)
    _layout(errors, readme)
    _absolute_paths(errors)
    if errors:
        print(f"  FAIL  claim drift（{len(errors)}）")
        for item in errors:
            print("   ", item)
        return 1
    print(
        "  PASS  claim drift（"
        f"{summary['modules']} 模块，{summary['passed_assertions']} PASS，"
        f"{summary['expected_fail_assertions']} 预期失败，"
        f"{summary['info_assertions']} 说明，"
        f"{summary['failed_assertions']} 未预期失败）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
