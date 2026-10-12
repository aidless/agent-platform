#!/usr/bin/env python3
"""依赖与可用性自检（供 make deps 调用）。

只做只读探测：检查命令在不在 PATH、模块能不能 import。
不安装任何东西，不联网。

为什么单独一个文件而不是 Makefile 里的 heredoc：
Makefile 的 heredoc 会被 $(...) 与制表符规则折腾，出问题很难查；
python 文件能直接 `python3 tools/deps_check.py` 单跑、能进 CI、
能有 --help。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# 模块级依赖：缺了只是某些模块跑不了，不影响其余
#
# 注意（这里栽过一次）：M4b 调的是 `docker` **命令行**（subprocess），
# 不是 Python 的 docker SDK。所以 docker 只在 OPTIONAL_CLI 里出现一次；
# 下面不要再放 ("docker", ...)，否则 `import docker` 失败会被误报成
# "docker 不可用"，看着像环境坏了，其实只是没装那个用不到的 SDK。
OPTIONAL_PY = [
    ("numpy", "m2 分位数、m8 统计"),
]
OPTIONAL_CLI = [
    ("docker", "M4b OS 级隔离 / M6 数据管道", True),
    ("ollama", "m1 --live / m3b --live（可选）", False),
]


def main() -> int:
    print(f"python: {sys.version.split()[0]}  ({sys.executable})")
    hard_missing = []
    soft_missing = []

    print("\n命令行工具：")
    for name, why, required in OPTIONAL_CLI:
        path = shutil.which(name)
        tag = "required" if required else "optional"
        if path:
            print(f"  OK    {name:<8} [{tag}] — {why}")
        else:
            print(f"  MISS  {name:<8} [{tag}] — {why}")
            (hard_missing if required else soft_missing).append(name)

    print("\nPython 模块（全部可选）：")
    for item in OPTIONAL_PY:
        name, why = item[0], item[1]
        try:
            __import__(name)
            print(f"  OK    {name:<8} — {why}")
        except ImportError:
            print(f"  MISS  {name:<8} — {why}")
            soft_missing.append(name)

    print("\nstdlib 模块（必须全部可用，否则本仓不成立）：")
    for name in ("sqlite3", "json", "subprocess", "tempfile", "hashlib",
                 "concurrent.futures", "argparse", "shutil", "re"):
        try:
            __import__(name)
            print(f"  OK    {name}")
        except ImportError as e:  # pragma: no cover - 标准解释器不会到这里
            print(f"  FAIL  {name}: {e}")
            hard_missing.append(name)

    print()
    if soft_missing:
        print(f"缺 {len(soft_missing)} 个可选项：{', '.join(soft_missing)}")
        print("  → 对应能力跑不了，其余模块照常；这不是代码缺陷。")

    # required 项缺失返回非 0（CI 会拦）；optional 缺失不拦。
    if hard_missing:
        print(f"缺必需项：{', '.join(hard_missing)} → 不能声称可复现")
        return 2
    print("结论：当前环境可复现除上述 optional 能力外的全部模块。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
