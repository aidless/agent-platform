#!/usr/bin/env python3
"""结果契约检查（供 make gates 与 CI 调用）。

要求 results/*.json 每个都同时有 findings 与 refutes：
  findings = 这个模块声称测出了什么（带 PASS/FAIL/SKIPPED）
  refutes  = 这个模块刻意不声称什么

两者缺一，本仓「只证明能证明的那部分」的自我约束就是空话。

**为什么单独一个文件而不是 Makefile heredoc**：
Makefile 的 recipe 里每行是独立 shell，heredoc 的缩进/结束标记极容易
被 Make 的 tab 规则吃掉（本次实测：heredoc 直接报 `make: import: 没有
那个文件或目录`，因为 heredoc 行被当成 make 语法解析了）。
python 文件能单独跑、能进 CI、退出码语义干净。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REQUIRED = ("findings", "refutes")
ABS_PATH_PREFIXES = ("/home/", "/root/", "/data1/")
# 工具自身的输出不算模块结果（理由见 main() 注释）
EXEMPT = frozenset({"evidence_scan.json"})


def main() -> int:
    # evidence_scan.json 是工具的审计输出，不是模块的 findings/refutes 报告，
    # 按扩展名排除（不是按内容猜）。如果以后有别的工具输出也该在这里排除，
    # 但每一次排除都要写理由——否则「结果契约」会慢慢变成没有契约。
    files = [p for p in sorted(Path("results").glob("*.json"))
             if p.name not in EXEMPT]
    if not files:
        print("  FAIL  没有 results/*.json——先跑 make smoke")
        return 1

    bad: list[str] = []
    for p in files:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            bad.append(f"{p.name}: 解析失败 {e}")
            continue
        if not isinstance(d, dict):
            bad.append(f"{p.name}: 顶层不是对象")
            continue
        for key in REQUIRED:
            if key not in d:
                bad.append(f"{p.name}: 缺 {key}")
            elif not isinstance(d[key], list) or not d[key]:
                bad.append(f"{p.name}: {key} 是空列表（空等于没写）")
        # refutes 里不允许出现「无条件成立」式的免责空话
        refutes = d.get("refutes") or []
        for r in refutes:
            if isinstance(r, str) and r.strip() in ("", "无", "none", "-"):
                bad.append(f"{p.name}: refutes 含空项")
                break

    # 绝对路径泄漏：把开发机路径带进仓库
    for p in sorted(Path("src").rglob("*.py")):
        text = p.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if "ABS_PATH_TOKENS" in line:
                continue
            for pref in ABS_PATH_PREFIXES:
                if pref in line:
                    bad.append(f"{p}:{n}: 绝对路径 {pref}")

    if bad:
        print("  FAIL", len(bad), "项：")
        for b in bad:
            print("    ", b)
        return 1
    print(f"  PASS  结果契约（{len(files)} 个 JSON 均有非空 findings+refutes，"
          f"src/ 无绝对路径泄漏）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
