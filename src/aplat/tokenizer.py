"""token 估算：本仓不引入 tiktoken（非标准库、需要下载 BPE 表）。

做法：CJK 按字计（约 1 token/字，对中文是合理保守估计），拉丁按词计，
并对 CJK 场景给出一个可校正的 `cjk_ratio` 参数。
这不是精确 tokenizer——报告里必须写明这一点。

为什么还是要有这个模块：网关的 prompt_tokens/completion_tokens 若不落盘，
M2 的成本归因与 M3 的 token 预算都无从谈起。宁可要"标注了误差的估算"，
也不要"没有数字"。
"""

from __future__ import annotations

import re
import unicodedata

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff]")
LATIN_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if CJK_RE.match(ch))
    return cjk / len(text)


def count_tokens(text: str, model: str = "qwen3:8b") -> int:
    """估算 token 数。确定性：同一输入永远同一输出。"""
    if not text:
        return 0
    text = unicodedata.normalize("NFKC", text)
    cjk = sum(1 for ch in text if CJK_RE.match(ch))
    rest = CJK_RE.sub(" ", text)
    words = len(LATIN_WORD_RE.findall(rest))
    punct = len(re.findall(r"[^\w\s]", rest))
    # 经验系数：中英文混排下，CJK 每字约 1 token，英文每词约 1.3 token
    return int(round(cjk * 1.0 + words * 1.3 + punct * 0.5)) + (1 if text.strip() else 0)


def count_messages_tokens(messages: list[dict]) -> int:
    """按 chat 口径估算：每条 message 加 4 的结构开销 + 3 的对话头开销。"""
    return sum(count_tokens(m.get("content", "")) + 4 for m in messages) + 3
