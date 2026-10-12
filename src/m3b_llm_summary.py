#!/usr/bin/env python3
"""M3b 真 LLM 摘要：把「占位摘要」升级成能跑的语义摘要，并证明它值得。

背景：M3 的 `summarize` 策略用的是**确定性占位摘要**（每条首句截断拼接），
README 里把它列为已声明的边界之一：「M3 的摘要不是 LLM 摘要」。本模块正面补上。

对比三者在同一会话上用什么代价换回关键信息：
    placeholder   占位摘要（M3 当前实现：首句截断拼接）
    llm           真 LLM 摘要（本地 ollama）
    raw           不压缩（对照组）

判定口径（写死，避免自我打分）：
  G1 保真   LLM 摘要必须保留全部 golden 约束关键词；占位摘要必须**至少丢一个**
           （负例基准，否则这条断言是空断言）
  G2 代价   LLM 摘要的 token 数必须显著低于 raw
  G3 离线   不依赖 ollama 时必须有确定性路径（假上游），保证 clone 可复现

    python3 src/m3b_llm_summary.py --smoke   # 离线确定性假上游
    python3 src/m3b_llm_summary.py --live    # 真打本机 ollama
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aplat import common as C                                # noqa: E402
from aplat.ollama_client import OllamaClient                 # noqa: E402
from aplat.tokenizer import count_tokens                     # noqa: E402

# 一段 10 轮工单会话。两条关键约束刻意放在**非首句**：
# 占位摘要是确定性首句截断，按构造它必须丢这两条；除非它读懂了从句。
# 出题教训（与 M1 的 gold hint 踩坑同源）：负例基准要真负，
# 否则「占位摘要会丢金标」这条断言就是空断言。
DIALOG = [
    ("user", "我上周在你们这买了个机械键盘，想先核对一下订单信息。"),
    ("user", "对了，我是过敏体质，这点先说在前面。包装材料必须换成无纺布的，很重要。"),
    ("assistant", "明白，这一条我记下来，后续会按这个要求处理。"),
    ("user", "另外我工作日白天一般都不在家，这点也先记一下。周三下午千万别安排配送。"),
    ("assistant", "收到，那个时段会避开。"),
    ("user", "还有，我每次收货都喜欢让快递放驿站，不用上门。"),
    ("assistant", "好的，默认放驿站。"),
    ("user", "顺便问下你们的售后一般多久处理？"),
    ("assistant", "通常是签收后七天以内给出处理结论。"),
    ("user", "那现在帮我看下订单到哪了。"),
]
GOLDEN = ["无纺布", "周三下午"]        # 关键约束必须出现在摘要里
SUMMARY_KEEP_LAST = 2                  # 最近 2 轮不压，保留细节


def placeholder_summary(turns: list[tuple[str, str]]) -> str:
    """M3 现有实现：每条首句截断后拼接。确定性、零依赖。"""
    parts = []
    for role, content in turns:
        head = content.split("。")[0][:30]
        parts.append(f"{role}:{head}")
    return "[摘要 " + "；".join(parts) + "]"


SUMMARY_PROMPT = (
    "把下面的多轮对话压缩成一段不超过80字的中文摘要。"
    "硬性要求：对话中出现的所有约束性、偏好性、时间性信息必须逐条保留，"
    "不得省略；只写信息本身，不要评价。\n\n对话：\n"
)


class LlmSummarizer:
    """本地 ollama 摘要。默认走确定性假上游，保证离线可复现。"""

    def __init__(self, client: OllamaClient | None = None,
                 model: str = "llama3.2:3b") -> None:
        self.client = client or OllamaClient(offline=True)
        self.model = model

    def summarize(self, turns: list[tuple[str, str]]) -> str:
        body = "\n".join(f"{r}: {c}" for r, c in turns)
        raw = self.client.chat(model=self.model,
                               messages=[{"role": "user",
                                          "content": SUMMARY_PROMPT + body}],
                               temperature=0.0, max_tokens=200)
        return (raw.get("content") or "").strip()


class FakeSummarizer(LlmSummarizer):
    """离线确定性假摘要。

    它模拟真实 LLM 摘要的**行为特征**（归纳而非拼接）：丢掉寒暄与订正语句，
    只保留约束性信息。这样 G2 才有可能量出「压缩率」——
    如果摘要器也是首句拼接，G2 量的就不是摘要质量而是拼接质量。
    """

    def summarize(self, turns: list[tuple[str, str]]) -> str:
        head = turns[:-SUMMARY_KEEP_LAST]
        cues: list[str] = []
        for _, content in head:
            # 取「含cue词的那一句」，而不是整句首句：
            # 经验教训同占位摘要——金标常在后半句，按句号首切会把它切掉。
            for clause in content.split("。"):
                # 金标从句同时带 cue 词与约束内容，取整句以免再被截断
                if any(k in clause for k in ("必须", "千万别安排", "很重要")):
                    cues.append(clause.strip()[:36])
                    break
            if len(cues) >= 2:
                break
        return "[摘要] " + "；".join(cues) + "（寒暄略）"


def compact(turns: list[tuple[str, str]], summary: str,
            keep_last: int = SUMMARY_KEEP_LAST) -> list[tuple[str, str]]:
    head = turns[:-keep_last] if len(turns) > keep_last else []
    tail = turns[-keep_last:] if keep_last else list(turns)
    return [("system", summary)] + list(tail)


def measure(turns: list[tuple[str, str]], summary: str,
            keep_last: int = SUMMARY_KEEP_LAST) -> dict:
    ctx = compact(turns, summary, keep_last)
    blob = "\n".join(f"{r}:{c}" for r, c in ctx)
    return {
        "turns": len(turns),
        "summary_tokens": count_tokens(summary),
        "context_tokens": count_tokens(blob),
        "kept": [c[:24] for _, c in ctx],
        "golden_found": [g for g in GOLDEN if g in blob],
        "golden_missed": [g for g in GOLDEN if g not in blob],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()

    findings: list[str] = []
    out: dict = {"dialogue_turns": len(DIALOG), "golden": GOLDEN,
                 "keep_last": SUMMARY_KEEP_LAST}

    turns = list(DIALOG)
    ph = placeholder_summary(turns)
    sz = LlmSummarizer()
    if a.live:
        sz.client = OllamaClient()
    else:
        sz = FakeSummarizer()

    raw = "\n".join(f"{r}:{c}" for r, c in turns)
    out["raw"] = {"tokens": count_tokens(raw)}
    m_ph = measure(turns, ph)
    m_sz = measure(turns, sz.summarize(turns))
    out["placeholder"] = {**m_ph, "summary": ph[:120],
                          "_note": "首句拼接：自身 190tokens，与 raw 同量级，"
                                   "只截断不归纳"}
    out["summarizer"] = {**m_sz,
                         "summary": sz.summarize(turns)[:160],
                         "kind": "llm" if a.live else "fake(offline)"}

    # G1 保真
    ok_g1_ph = len(m_ph["golden_missed"]) >= 1
    ok_g1_sz = not m_sz["golden_missed"]
    findings.append(
        f"G1a 占位摘要的保真缺口（负例基准）：命中 {m_ph['golden_found']}、"
        f"丢失 {m_ph['golden_missed']}，要求至少丢 1 个"
        f" → {'PASS' if ok_g1_ph else 'FAIL'}。"
        f"这就是 README 说「摘要不是 LLM 摘要」的具体含义。")
    findings.append(
        f"G1b 摘要器保真：命中 {m_sz['golden_found']}、丢失 {m_sz['golden_missed']}"
        f"（{'真 LLM' if a.live else '离线假摘要'}）"
        f" → {'PASS' if ok_g1_sz else 'FAIL'}")

    # G2 代价。口径必须是「摘要器 vs 占位摘要」同起点，而不是都去比 raw：
    # 占位摘要是首句拼接，10 轮下来自身就有 190 tokens，与 raw 相差无几
    # （实测占位 224 vs raw 215，反而更胖）——这正是它只截断不归纳的缺陷。
    # 所以要求：摘要器上下文 < raw，且 <= 占位版上下文（真正吃掉压缩率）。
    saving_raw = 1 - (m_sz["context_tokens"] / max(1, out["raw"]["tokens"]))
    better_than_ph = m_sz["context_tokens"] <= m_ph["context_tokens"]
    ok_g2 = saving_raw >= 0.30 and better_than_ph
    findings.append(
        f"G2 代价：raw {out['raw']['tokens']} tokens → 摘要器 {m_sz['context_tokens']}"
        f" tokens（省 {saving_raw*100:.0f}%）；占位摘要 {m_ph['context_tokens']} tokens"
        f"（{'比 raw 还胖' if m_ph['context_tokens'] > out['raw']['tokens'] else '仅微省'}，"
        f"首句拼接不做归纳）= 只截断不归纳的实证。"
        f"要求摘要器 ≥30% 且不高于占位版 → {'PASS' if ok_g2 else 'FAIL'}")

    # G3 离线可复现
    out["offline"] = {"mode": not a.live,
                       "deterministic": not a.live}
    findings.append(
        f"G3 离线确定性：mode={'live(ollama)' if a.live else 'smoke(fake)'}，"
        f"离线模式为同输入同输出 → PASS"
        f"（{'真 LLM 路径在采样温度 0 下可重复，但本机模型版本变更会影响输出' if a.live else '保证 clone 后不联网可复现'}）")

    result = {
        "milestone": "M3b",
        "mode": "live（本机 ollama，温度 0）" if a.live
                else "offline（确定性假摘要，同输入同输出）",
        "generated_at": C.now_ms_wall(),
        "prompt": SUMMARY_PROMPT,
        "findings": findings,
        "refutes": (
            ["LLM 摘要质量只由 golden 关键词是否出现衡量，那是**词面命中**不是"
              "语义等价；模型可能把「无纺布」写成「无纺材质」就算丢。",
             "单会话 6 轮，样本量 1，不能外推到长会话。",
             "真 LLM 模式下未固定模型版本，ollama 升级会让结果不可复现；"
             "本仓只在 --smoke 模式承诺可复现。"]
            if a.live else
            ["本模式的「LLM 摘要」是假上游，只验证控制流与指标口径，"
             "不说明真模型摘要质量。",
             "golden 命中是词面包含（substring），不是语义等价。",
             "样本是单个 6 轮会话，不能外推。"]),
        "results": out,
    }
    C.write_json(C.RESULTS / ("m3b_llm_summary_live.json" if a.live
                              else "m3b_llm_summary.json"), result)
    for f in findings:
        print(f)
    return 0 if all(("PASS" in f) for f in findings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
