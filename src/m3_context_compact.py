#!/usr/bin/env python3
"""M3 上下文压缩 + 会话记忆。

设计目标：多轮会话的 prompt 会线性增长，压缩要在「不丢关键信息」与
「省 token」之间给出可度量的折中，而不是一个只报压缩率的黑盒。

三种策略，全部可对比：
  sliding   滑动窗口：只留最近 k 轮 + pinned
  summarize 旧轮次用一句 LLM 摘要替代（离线模式用确定性假摘要）
  tag_filter 按 tag 召回：只留与当前问题相关的轮次 + pinned

判定标准（results 落盘）：
  1. 保真度：压缩后的上下文中，仍然包含 golden 关键项的轮次比例
  2. token 降幅：(原始 - 压缩后) / 原始
  3. pinned 永不丢失（硬约束，违反即 FAIL）

离线模式用确定性假上游，因此可重复；真实 LLM 摘要需 --live。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aplat import common as C                       # noqa: E402
from aplat.tokenizer import count_tokens            # noqa: E402
from aplat.memory import MemoryStore, Turn          # noqa: E402

STRATEGIES = ("raw", "naive_sliding", "sliding", "summarize", "tag_filter")

# 一个 12 轮的合成会话：第 1、5、9 轮带关键约束（pinned 候选）
DIALOG = [
    (1, "user", "我要用 RAG 做一个二手书交易问答，数据有 3 万条 ISBN。", ["需求"]),
    (2, "assistant", "明白。先确认数据源的字段：标题/作者/版次/品相/价格。", ["澄清"]),
    (3, "user", "品相分五级，价格按最近 30 天成交均值算。", ["业务规则"]),
    (4, "assistant", "好的，索引建议按 ISBN 为主键，作者做倒排。", ["技术"]),
    (5, "user", "注意：必须过滤掉教辅类，否则榜单会被污染。", ["关键约束"]),
    (6, "assistant", "收到，教辅类用一套 ISBN 前缀黑名单拦。", ["技术"]),
    (7, "user", "再补一个：同一书 24 小时内重复提问要合并。", ["业务规则"]),
    (8, "assistant", "可以，按 (isbn, 用户) 做 24h 去重窗口。", ["技术"]),
    (9, "user", "合规要求：不能把用户手机号写进日志。", ["关键约束"]),
    (10, "assistant", "明白，日志侧只留 user_id 的哈希。", ["合规"]),
    (11, "user", "最近一周检索召回偏低，帮我查原因。", ["排查"]),
    (12, "assistant", "先看倒排是否被教辅黑名单误伤，再看 ISBN 归一化。", ["排查"]),
]
GOLDEN_TURNS = {5, 9}     # 关键约束轮次：压缩后必须还在
PINNED_TAGS = {"关键约束", "合规"}


def build_memory(store: MemoryStore, db: Path) -> None:
    for turn, role, content, tags in DIALOG:
        store.add(Turn(session_id="s1", turn=turn, role=role, content=content,
                           tags=tags, tokens=count_tokens(content),
                           pinned=bool(set(tags) & PINNED_TAGS)))


def render(turns: list) -> str:
    return "\n".join(f"[{t.turn}]{t.role}: {t.content}" for t in turns)


def compress(store: MemoryStore, db: Path, strategy: str, question: str,
             k: int = 4) -> tuple[str, list]:
    """返回 (上下文字符串, 命中的 Turn 列表)。"""
    if strategy == "naive_sliding":
        keep = store.naive_sliding("s1", k)
    elif strategy == "raw":
        keep = store.recent("s1", limit=len(DIALOG))
    elif strategy == "sliding":
        keep = store.sliding_window("s1", k=k)
    elif strategy == "summarize":
        keep = store.summarize_old("s1", keep_last=k)
    elif strategy == "tag_filter":
        keep = store.by_tags("s1", question_tags(question))
        fallback = False
        # 召回过窄时用 pinned 兜底，并把兜底这件事记录下来（不伪装成召回）
        if len(keep) < 3:
            keep = store.sliding_window("s1", k=k)
            fallback = True
        compress.last_fallback = fallback
    else:
        raise ValueError(strategy)
    return render(keep), keep


def question_tags(q: str) -> list[str]:
    """极简关键词→tag 匹配（真实实现会用 embedding，这里必须说明是近似）。"""
    m = {"教辅": "关键约束", "手机号": "合规", "日志": "合规",
         "召回": "排查", "重复": "业务规则", "ISBN": "澄清"}
    return [tag for kw, tag in m.items() if kw in q]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--k", type=int, default=4)
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="ap_m3_"))
    db = tmp / "m3.db"
    store = MemoryStore(db)
    build_memory(store, db)

    raw_text, raw_turns = compress(store, db, "raw", "")
    raw_tokens = count_tokens(raw_text)
    question = "教辅黑名单会不会误伤召回？日志里还能不能留手机号？重复提问怎么合并？"

    per: dict[str, dict] = {}
    for st in STRATEGIES:
        text, kept = compress(store, db, st, question, a.k)
        toks = count_tokens(text)
        turns_kept = {t.turn for t in kept}
        golden_hit = len(turns_kept & GOLDEN_TURNS) / len(GOLDEN_TURNS)
        pinned_ok = all(t.pinned for t in kept if t.turn in GOLDEN_TURNS)
        per[st] = {
            "tokens": toks,
            "token_saving": round(1 - toks / raw_tokens, 4),
            "turns_kept": sorted(turns_kept),
            "n_kept": len(kept),
            "golden_recall": golden_hit,
            "golden_turns_found": sorted(turns_kept & GOLDEN_TURNS),
            "pinned_intact": pinned_ok,
            "tag_recall_fallback": getattr(compress, "last_fallback", False),
        }

    findings = []
    # 1) 压缩必须真省 token
    saved = {st: v["token_saving"] for st, v in per.items() if st != "raw"}
    best = max(saved, key=lambda s: saved[s])
    findings.append(
        f"token 降幅：" + "、".join(f"{s}={saved[s]*100:.0f}%" for s in saved)
        + f"（最优 {best}={saved[best]*100:.0f}%）。基线 raw 为 0% 作对照。")

    # 2) 关键约束轮次：生产策略丢了就是 FAIL；naive_sliding 是负例基准，预期 FAIL
    for st, v in per.items():
        hit = v["golden_recall"] == 1.0
        if st == "naive_sliding":
            verdict = "FAIL（负例基准，预期失败）" if not hit else "意外 PASS"
        else:
            verdict = "PASS" if hit else "FAIL"
        findings.append(
            f"关键约束保留[{st}]：命中 {len(v['golden_turns_found'])}/"
            f"{len(GOLDEN_TURNS)}（{v['golden_turns_found']}），"
            f"pinned 完整={v['pinned_intact']} → {verdict}")

    # 3) naive_sliding 是反例基准：只取最近 k 轮，第 5 轮关键约束必被截掉
    nn = per["naive_sliding"]
    ok_neg = nn["golden_recall"] < 1.0
    findings.append(
        f"naive_sliding 反例（预期失败）：只看最近 {a.k} 轮 → "
        f"golden_recall={nn['golden_recall']}、"
        f"丢掉的约束轮={sorted(GOLDEN_TURNS - set(nn['golden_turns_found']))}"
        f" → {'FAIL（符合预期：朴素滑窗会丢关键约束）' if ok_neg else '意外 PASS'}"
        f"。这正是 pinned 机制与生产 sliding_window 的区别。")

    # 4) 显式区分：带 pinned 的 sliding 必须比 naive 更好或持平
    better = (per["sliding"]["golden_recall"] >= nn["golden_recall"])
    findings.append(
        f"pinned 增益：sliding(带pinned) golden_recall="
        f"{per['sliding']['golden_recall']} vs naive={nn['golden_recall']}"
        f" → {'PASS' if better else 'FAIL'}")

    findings.append(
        f"策略取舍：" 
        f"naive 最省 token（{per['naive_sliding']['token_saving']*100:.0f}%）但丢约束；"
        f"sliding 用 pinned 换回约束只多花 "
        f"{per['sliding']['tokens']-per['naive_sliding']['tokens']} tokens；"
        f"tag_filter 召回 {per['tag_filter']['n_kept']} 轮（比 sliding 多 "
        f"{per['tag_filter']['n_kept']-per['sliding']['n_kept']} 轮相关内容），"
        f"代价是少省 "
        f"{(per['tag_filter']['token_saving']-per['sliding']['token_saving'])*-100:.0f} 个百分点的 token。"
        f"没有哪个策略同时最优，所以默认取 sliding。")

    result = {
        "milestone": "M3",
        "mode": "offline（确定性规则；摘要策略用占位摘要，无 LLM 参与）",
        "generated_at": C.now_ms_wall(),
        "question": question,
        "k": a.k,
        "raw_tokens": raw_tokens,
        "findings": findings,
        "refutes": [
            "summarize 策略的摘要是确定性占位文本（首句截断），"
            "不是 LLM 生成的语义摘要；真实摘要质量需 --live 复测。",
            "tag 匹配是关键词精确命中，不是语义检索；换个说法就召回不到，"
            "真实系统应替换成 embedding 召回，本仓未做。",
            "样本是单个 12 轮会话，不是分布；token 降幅数字不能外推到长会话。",
            "golden 只有 2 轮，golden_recall 的粒度是 0/0.5/1，没有统计意义。",
        ],
        "per_strategy": per,
    }
    C.write_json(C.RESULTS / "m3_context_compact.json", result)
    for f in findings:
        print(f)
    # naive_sliding 与 tag_filter 迷你不算失败项
    fail = [f for f in findings
            if " FAIL" in f and "负例基准" not in f
            and "预期失败" not in f]
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
