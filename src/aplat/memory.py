"""M3 会话记忆存储：SQLite + tag 索引 + pinned 保护。

为什么不用向量库：本机无 faiss、无外网，且「pinned 永不丢失」是硬约束，
用 SQL 的确定性条件表达比近似检索更可验证。语义召回升级路径写在
docs/ 的 TODO 里，不假装已经做了。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplat import common as C  # noqa: E402
from aplat.common import DDL  # noqa: E402


@dataclass
class Turn:
    session_id: str
    turn: int
    role: str
    content: str
    tags: list[str] = field(default_factory=list)
    tokens: int = 0
    pinned: bool = False


class MemoryStore:
    def __init__(self, db: Path):
        self.db_path = Path(db)

    def _ensure(self) -> None:
        with C.db(self.db_path) as conn:
            conn.executescript(DDL)
            conn.commit()

    # ------------------------------------------------------------ 写

    def add(self, turn: Turn) -> None:
        self._ensure()
        with C.db(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO memory_item
                   (item_id, session_id, turn, role, content, tags, tokens,
                    pinned, ts_wall)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (f"{turn.session_id}:{turn.turn}", turn.session_id, turn.turn,
                 turn.role, turn.content, C.json_dumps(turn.tags), turn.tokens,
                 1 if turn.pinned else 0, C.now_ms_wall()),
            )
            conn.commit()

    # ------------------------------------------------------------ 读

    def recent(self, session_id: str, limit: int = 20) -> list[Turn]:
        with C.db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM memory_item WHERE session_id=? ORDER BY turn DESC "
                "LIMIT ?", (session_id, limit)).fetchall()
        return [self._row2turn(r) for r in reversed(rows)]

    def sliding_window(self, session_id: str, k: int) -> list[Turn]:
        """最近 k 轮 + 全部 pinned（pinned 不受窗口限制）。

        这正是朴素滑动窗口与可用滑动窗口的分界。
        """
        with C.db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM memory_item WHERE session_id=? ORDER BY turn",
                (session_id,)).fetchall()
        turns = [self._row2turn(r) for r in rows]
        tail = turns[-k:] if k < len(turns) else list(turns)
        pins = [t for t in turns if t.pinned]
        # pinned 可能在 tail 里，去重后按 turn 排序
        seen, out = set(), []
        for t in tail + pins:
            if t.turn not in seen:
                seen.add(t.turn)
                out.append(t)
        return sorted(out, key=lambda t: t.turn)

    def naive_sliding(self, session_id: str, k: int) -> list[Turn]:
        """天真滑动窗口：只取最近 k 轮，pinned 也在被截之列。

        这是被有意保留的反例基准。生产代码一律用 sliding_window()。
        """
        with C.db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM memory_item WHERE session_id=? ORDER BY turn DESC "
                "LIMIT ?", (session_id, k)).fetchall()
        return [self._row2turn(r) for r in reversed(rows)]

    def summarize_old(self, session_id: str, keep_last: int) -> list[Turn]:
        """把 keep_last 之前的旧轮次折成一条占位摘要 + 保留 pinned 原文。

        离线模式下「摘要」= 每条首句截断拼接。真实 LLM 摘要在 --live。
        """
        with C.db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM memory_item WHERE session_id=? ORDER BY turn",
                (session_id,)).fetchall()
        turns = [self._row2turn(r) for r in rows]
        head, tail = turns[:-keep_last], turns[-keep_last:]
        pins = [t for t in head if t.pinned]
        olds = [t for t in head if not t.pinned]
        if not olds:
            return tail + pins
        digest = "；".join(t.content.split("。")[0][:40] for t in olds[:6])
        summary = Turn(session_id=session_id, turn=0, role="system",
                       content=f"[摘要 {len(olds)} 轮] {digest}",
                       tags=["derived"], tokens=len(digest) // 2, pinned=True)
        out = {t.turn: t for t in pins + tail}
        out[0] = summary
        return [out[t] for t in sorted(out)]

    def by_tags(self, session_id: str, tags: list[str]) -> list[Turn]:
        if not tags:
            return []
        want = set(tags)
        with C.db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM memory_item WHERE session_id=? ORDER BY turn",
                (session_id,)).fetchall()
        out = []
        for r in rows:
            t = self._row2turn(r)
            if t.pinned or (want & set(t.tags)):
                out.append(t)
        return out

    # ------------------------------------------------------------ 工具

    @staticmethod
    def _row2turn(r) -> Turn:
        return Turn(session_id=r["session_id"], turn=r["turn"], role=r["role"],
                    content=r["content"], tags=C.json_loads(r["tags"]) or [],
                    tokens=r["tokens"], pinned=bool(r["pinned"]))


def add_and_read(db: Path, turn: Turn, limit: int = 20) -> list[Turn]:
    """便捷入口（供测试与主流程用）。"""
    s = MemoryStore(db)
    s.add(turn)
    return s.recent(turn.session_id, limit)
