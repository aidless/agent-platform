#!/usr/bin/env python3
"""跨仓「无证据数字」扫描器。

要解决的问题：一个数字写进文档时有没有给出处。**有出处的数字才能被复核，
没有出处的数字只能是传言**——而 AI 生成的内容最擅长把这两者混在一起。

判据（保守优先，宁漏不错）：
  行内出现「指标型数字」= 正则命中下列之一
      \\d+(?:\\.\\d+)?\\s*%           带百分号的数
      \\b[Nn]\\s*=\\s*\\d+              样本量
     \\bp\\s*[=<>]\\s*[\\d.]+            p 值
      \\b\\d+\\.\\d+\\b  且  同行有指标词（accuracy/flip/latency/recall/...
  且  同行 + 前后 2 行内没有任何证据指针
      .json .md .csv .txt .py .sh   results/ evidence/ docs/  figs/
      ``反引号路径``     见 / see / commit / sha256 / 提交 / 仓
      allow-number（本仓白名单）
  则记为一条 finding。

反向设计（防误伤）：版本号 (x.y.z)、路径片段 (python3.12)、年份、文件名里的数字
不算指标型数字；行内含 allow-number 直接跳过。

非空性自证：--selftest 会造一组"无证据"与"有证据"的样本，
要求前者被报出、后者不报。若对任何输入都返回 0 条，这个脚本就是装饰品。

用法：
    python3 tools/evidence_scan.py <目录> [更多目录...] --json out.json
    python3 tools/evidence_scan.py --selftest
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 指标词：命中任一即认为该行的数字是「指标」而不是附带的编号
METRIC_WORDS = (
    "accuracy", "flip", "latency", "recall", "precision", "f1", "auc",
    "score", "rate", "p50", "p95", "p99", "throughput", "rps", "cost",
    "saving", "speedup", "compression", "error", "tokens", "ceiling",
    "准确率", "翻转率", "延迟", "召回", "提速", "降幅", "节省", "成本",
    "成功率", "命中率", "压缩率", "并发", "样本",
)

# 证据指针：出现任一即认为该行（或其邻域）给出了出处
EVIDENCE_HINTS = (
    ".json", ".md", ".csv", ".txt", ".py", ".sh", ".log", ".yaml", ".yml",
    "results/", "evidence/", "docs/", "figs/", "src/", "scripts/",
    "见 ", "see ", "commit", "sha256", "提交", "仓", "report",
    "`", "JSONL", "jsonl", "§",
)
# 白名单：本工作区既有约定，出现即跳过
EXEMPT_TOKENS = ("allow-number", "TBD", "TODO", "SKIPPED")

# 反模式：这些形态不是指标
NON_METRIC_RES = (
    re.compile(r"\b\d+\.\d+\.\d+(?:\.\d+)?\b"),      # 版本 x.y.z
    re.compile(r"\bpython\s*\d+(\.\d+)?\b", re.I),    # python3.12
    re.compile(r"\b(?:20)?2[0-9]\b"),                 # 年份
    re.compile(r"\b[a-z_]+_v?\d+\b"),                 # m1/m4b/m8_scale 这类标识
)

METRIC_RES = (
    re.compile(r"\d+(?:\.\d+)?\s*%"),
    re.compile(r"\b[Nn]\s*=\s*\d+"),
    re.compile(r"\bp\s*[=<>]\s*[0-9.eE+-]+"),
    # p95=42ms / P50=660ms 这种分位数断言，比裸 p 值更常见
    re.compile(r"\b[pP]\d{1,3}\s*[=<>]\s*\d+(?:\.\d+)?"),
    # 带单位的裸数：42ms / 300MiB / 1298 tokens
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|MB|GB|TB|tokens?|rps|MiB|GiB|s)\b"),
    re.compile(r"\b\d+\.\d+\b"),
)


def _is_metric_number(line: str) -> tuple[bool, str]:
    """返回 (是否指标型数字, 命中的数字文本)。"""
    if any(t in line for t in EXEMPT_TOKENS):
        return False, ""
    # 剥掉版本/年份/标识后再判断，避免 python3.12 被当成指标
    scrubbed = line
    for rx in NON_METRIC_RES:
        scrubbed = rx.sub(" ", scrubbed)
    hits: list[str] = []
    for rx in METRIC_RES:
        hits += rx.findall(scrubbed)
    if not hits:
        return False, ""
    # 带百分号/N=/p= 的，是强指标，不需要指标词
    strong = re.search(r"\d+(?:\.\d+)?\s*%|\b[Nn]\s*=\s*\d+|\bp\s*[=<>]\s*[0-9.eE+-]",
                       scrubbed)
    if strong:
        return True, strong.group(0)
    # 裸小数需要同行有指标词，否则 "3.2 天" 这种也算不进去
    low = line.lower()
    if any(w in low for w in METRIC_WORDS):
        return True, hits[0]
    return False, ""


def scan_file(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        is_metric, num = _is_metric_number(line)
        if not is_metric:
            continue
        lo = max(0, i - 2)
        hi = min(len(lines), i + 3)
        window = "\n".join(lines[lo:hi])
        hit = [k for k in EVIDENCE_HINTS if k in window]
        if hit:
            continue
        out.append({
            "file": str(path),
            "line": i + 1,
            "number": num,
            "class": "no_evidence",
            "line_text": line.strip()[:180],
            "why": f"指标型数字 {num!r} 在 ±2 行窗口内无证据指针",
        })
    return out


def scan_dirs(dirs: list[str]) -> list[dict]:
    out: list[dict] = []
    roots = [Path(d) if Path(d).is_absolute() else ROOT / d for d in dirs]
    for root in roots:
        if not root.exists():
            out.append({"file": str(root), "line": 0, "class": "target_missing",
                        "why": f"目标不存在：{root}", "line_text": "", "number": ""})
            continue
        for p in sorted(root.rglob("*.md")):
            if any(x in p.parts for x in (".git", "__pycache__", "node_modules")):
                continue
            out.extend(scan_file(p))
    return out


def selftest() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="evidence_scan_"))
    bad = tmp / "bad.md"
    good = tmp / "good.md"
    bad.write_text(
        "# 无证据\n准确率达到 91.2%。\n延迟 p95=42ms。\nN=200 个样本全部通过。\n",
        encoding="utf-8")
    good.write_text(
        "# 有证据\n准确率达到 91.2%（见 `results/x.json`）。\n"
        "延迟 p95=42ms，来源 commit 1ed3ecd。\n"
        "N=200 个样本，详见 results/y.json 与 docs/z.md。\n"
        "python3.12 是版本号，不是指标。\n"
        "2026 年是年份。\n"
        "m8_scale.py 是文件名。\n",
        encoding="utf-8")
    got_bad = scan_file(bad)
    got_good = scan_file(good)
    results = [
        ("无证据三连报出（91.2% / p95 / N=200）", len(got_bad) == 3,
         f"实际 {len(got_bad)} 条：{[g['number'] for g in got_bad]}"),
        ("有证据的三条零误报", not got_good,
         f"实际误报 {len(got_good)} 条：{[g['number'] for g in got_good]}"),
        ("未命中时返回空而非报错", scan_file(tmp / "nope.md") == [],
         "（不存在的文件返回空列表）"),
    ]
    ok = True
    for name, passed, detail in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}：{detail}")
        ok = ok and passed
    # 空仓反证：扫描一个空目录必须返回空
    empty = tmp / "empty"
    empty.mkdir()
    hit_missing = any(r["class"] == "target_missing"
                      for r in scan_dirs([str(tmp / "not_exist_dir")]))
    print(f"  {'PASS' if hit_missing else 'FAIL'}  目标不存在时记 target_missing "
          f"而不是静默返回空")
    ok = ok and hit_missing
    print("SELFTEST:", "PASS（门有效）" if ok else "FAIL（门失效，须修）")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*", help="要扫描的目录（相对本仓根或绝对路径）")
    ap.add_argument("--json", default="", help="把结果写成 JSON")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if not a.dirs:
        print("用法：python3 tools/evidence_scan.py <目录>[...] [--json out.json]")
        print("      python3 tools/evidence_scan.py --selftest")
        return 0

    findings = scan_dirs(a.dirs)
    by_file: dict[str, int] = {}
    for f in findings:
        if f["class"] == "target_missing":
            continue
        by_file[f["file"]] = by_file.get(f["file"], 0) + 1

    summary = {
        "roots": a.dirs,
        "findings_total": len([f for f in findings if f["class"] != "target_missing"]),
        "missing_targets": [f["file"] for f in findings if f["class"] == "target_missing"],
        "by_file": dict(sorted(by_file.items(), key=lambda kv: -kv[1])),
        "top_examples": [f for f in findings if f["class"] != "target_missing"][:15],
        "note": "命中不等于错：有些数字是审计口径、白名单或故意留的无证据断言。"
                "本工具给的是待人工确认清单，不是判决。",
    }

    if a.json:
        p = Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        if not a.quiet:
            print(f"[scan] {summary['findings_total']} 条命中，写入 {p}")
    if not a.quiet:
        for f in summary["top_examples"]:
            print(f"  {f['file']}:{f['line']}  {f['number']!r}  {f['line_text'][:80]}")
        if summary["findings_total"] > 15:
            print(f"  ... 另 {summary['findings_total'] - 15} 条见 JSON")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
