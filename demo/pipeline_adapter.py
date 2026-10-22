"""Turn the M7 offline pipeline into a UI-friendly evidence payload.

The demo deliberately does not reimplement the pipeline. It imports the same
``TICKETS``, ``ScriptedUpstream``, ``TicketPipeline``, ``summarize``,
``REFUTES`` and ``ensure_order_db`` used by ``src/m7_e2e.py``, runs the
deterministic offline path, and reads back the SQLite rows the pipeline wrote.
Every number shown in the browser therefore has a database or result-file
source, not a hand-written copy.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aplat import common as C  # noqa: E402
from m7_e2e import (  # noqa: E402
    REFUTES,
    TICKETS,
    ScriptedUpstream,
    TicketPipeline,
    TicketResult,
    ensure_order_db,
    summarize,
)

DEMO_MODEL = "qwen3:8b"

SCENARIOS = {
    "T02": "脚本注入 timeout：primary 失败，网关走 fallback，结果标记 degraded。",
    "T04": "脚本注入空 content + done_reason=length：HTTP 200 但回答为空，端到端记为 silent_empty_answer。",
    "T05": "模型尝试调用 issue_refund；它不在白名单，沙箱拒绝，但模型仍给出不带工具数据的回答。",
    "T09": "订单号 00000000 查不到；工具如实返回空，模型回答查无此单。",
}


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
        # results/ are generated artifacts and runs.json is the artifact this
        # script writes; neither should make the demo's source tree look dirty.
        ignored = ("results/", "demo/public/runs.json")
        dirty_paths = [
            line[3:] for line in raw
            if len(line) > 3 and not line[3:].startswith(ignored)
        ]
        return commit, bool(dirty_paths), dirty_paths[:12]
    except Exception:
        return "unknown", False, []


def _fmt_tokens(n: int) -> str:
    return f"{n} tokens"


def _fmt_ms(n: int | float) -> str:
    return f"{n:g} ms"


def _stage(name: str, key: str, status: str, detail: str,
           metrics: list[dict]) -> dict:
    return {
        "name": name,
        "key": key,
        "status": status,
        "detail": detail,
        "metrics": metrics,
    }


def _item_payload(result: TicketResult, db: Path) -> dict:
    key = f"{result.ticket_id}-{result.tool_verdict}"
    with C.db(db) as conn:
        gateway_rows = [
            dict(row) for row in conn.execute(
                "SELECT * FROM gateway_request WHERE idempotency_key=? "
                "ORDER BY ts_wall, ts_mono", (key,)
            ).fetchall()
        ]
        tool_rows = [
            dict(row) for row in conn.execute(
                "SELECT * FROM tool_audit WHERE request_id=? ORDER BY seq",
                (result.ticket_id,),
            ).fetchall()
        ]
        mem = conn.execute(
            "SELECT COUNT(*) AS total, SUM(pinned) AS pinned "
            "FROM memory_item WHERE session_id=?",
            (result.session_id,),
        ).fetchone()
        pinned_row = conn.execute(
            "SELECT content FROM memory_item WHERE session_id=? AND pinned=1 LIMIT 1",
            (result.session_id,),
        ).fetchone()

    gateway = gateway_rows[-1] if gateway_rows else {}
    audit = tool_rows[-1] if tool_rows else {}
    answer = gateway.get("content", "") or ""
    saved = result.context_tokens_uncompacted - result.context_tokens
    saved_pct = (saved / result.context_tokens_uncompacted
                 if result.context_tokens_uncompacted else 0.0)
    pinned_count = int(mem["pinned"] or 0) if mem else 0
    memory_turns = int(mem["total"] or 0) if mem else 0
    pinned_text = (pinned_row["content"] if pinned_row else "")
    tool_attempts = len(tool_rows)
    tool_reason = audit.get("reason", "") or ""
    tool_latency = int(audit.get("latency_ms", 0) or 0)
    gateway_attempts = len(gateway_rows)

    stages = [
        _stage(
            "工单进入", "intake", "ok", result.text,
            [{"label": "字符数", "value": len(result.text),
              "source": "m7_e2e.TICKETS"}],
        )
    ]

    if result.tool:
        tool_status = "ok" if result.tool_verdict == "ok" else (
            "error" if result.tool_verdict in ("denied", "dead_letter") else "skipped"
        )
        tool_detail = f"{result.tool} → {result.tool_verdict}"
        if tool_reason:
            tool_detail += f"：{tool_reason}"
        stages.append(_stage(
            "工具沙箱", "tool", tool_status, tool_detail,
            [
                {"label": "判定", "value": result.tool_verdict,
                 "source": f"tool_audit.verdict (request_id={result.ticket_id})"},
                {"label": "尝试次数", "value": tool_attempts,
                 "source": "tool_audit rows"},
                {"label": "耗时", "value": _fmt_ms(tool_latency),
                 "source": "tool_audit.latency_ms"},
            ],
        ))
    else:
        stages.append(_stage(
            "工具沙箱", "tool", "skipped",
            "无需工具：策略/知识类问题，未进入工具调用路径。",
            [{"label": "判定", "value": "skipped",
              "source": "m7_e2e.tool_for_ticket"}],
        ))

    stages.append(_stage(
        "记忆压缩", "memory", "ok",
        f"滑窗 k=3；pinned 合规约束仍在上下文：{pinned_text}",
        [
            {"label": "压缩后", "value": _fmt_tokens(result.context_tokens),
             "source": "m7_e2e.TicketResult.context_tokens"},
            {"label": "压缩前", "value": _fmt_tokens(result.context_tokens_uncompacted),
             "source": "m7_e2e.TicketResult.context_tokens_uncompacted"},
            {"label": "节省", "value": f"{saved} tokens ({saved_pct * 100:.0f}%)",
             "source": "压缩前 - 压缩后"},
            {"label": "pinned", "value": pinned_count,
             "source": "memory_item.pinned"},
            {"label": "会话轮次", "value": memory_turns,
             "source": "memory_item.session_id"},
        ],
    ))

    model = gateway.get("model", "") or ""
    error_kind = gateway.get("error_kind") or ""
    cache_hit = bool(gateway.get("cache_hit", 0))
    gateway_detail = (
        f"model={model or '—'}；route={gateway.get('route', 'default')}；"
        f"cache_hit={cache_hit}；degraded={result.degraded}"
    )
    if error_kind:
        gateway_detail += f"；error_kind={error_kind}"
    stages.append(_stage(
        "网关 / LLM", "gateway", result.llm_status, gateway_detail,
        [
            {"label": "prompt", "value": _fmt_tokens(int(gateway.get("prompt_tokens", result.prompt_tokens))),
             "source": "gateway_request.prompt_tokens"},
            {"label": "completion", "value": _fmt_tokens(int(gateway.get("completion_tokens", result.completion_tokens))),
             "source": "gateway_request.completion_tokens"},
            {"label": "耗时", "value": _fmt_ms(int(gateway.get("latency_ms", 0) or 0)),
             "source": "gateway_request.latency_ms"},
            {"label": "成本", "value": f"${float(gateway.get('cost_usd', 0.0)):.6f}",
             "source": "gateway_request.cost_usd"},
            {"label": "网关记录", "value": gateway_attempts,
             "source": "gateway_request.idempotency_key"},
        ],
    ))

    classification_status = "error" if result.failure else "ok"
    stages.append(_stage(
        "失败分类", "classification", classification_status,
        result.failure or "未分类失败 0",
        [{"label": "flags", "value": ", ".join(result.flags) or "—",
          "source": "m7_e2e.TicketResult.flags"}],
    ))

    stages.append(_stage(
        "回答", "answer", "ok" if answer else "error",
        answer or "(空回答：HTTP 200 但 content 为空)",
        [{"label": "回答长度", "value": f"{len(answer)} 字",
          "source": "gateway_request.content"}],
    ))

    return {
        "id": result.ticket_id,
        "text": result.text,
        "scenario": SCENARIOS.get(result.ticket_id, "正常路径"),
        "tool": result.tool,
        "tool_verdict": result.tool_verdict,
        "llm_status": result.llm_status,
        "answer": answer,
        "answer_len": result.answer_len,
        "latency_ms": result.latency_ms,
        "failure": result.failure,
        "flags": result.flags,
        "ctx_tokens": result.context_tokens,
        "ctx_tokens_uncompacted": result.context_tokens_uncompacted,
        "saved_tokens": saved,
        "saved_pct": round(saved_pct, 4),
        "pinned": pinned_count,
        "stages": stages,
        "sources": {
            "answer": {"table": "gateway_request", "field": "content",
                       "key": key},
            "gateway_status": {"table": "gateway_request", "field": "status",
                               "key": key},
            "tool_verdict": {"table": "tool_audit", "field": "verdict",
                             "key": result.ticket_id},
            "pinned": {"table": "memory_item", "field": "pinned",
                       "key": result.session_id},
            "summary": {"artifact": "results/m7_e2e.json#/summary"},
        },
    }


def _verify(summary: dict, results: list[TicketResult]) -> dict:
    """Compare the fresh run with the committed M7 result file.

    ``results/`` is gitignored, so a clean clone may not have the file. In that
    case this is ``NOT_AVAILABLE`` rather than a failure; the demo itself is
    still generated from code.
    """
    path = ROOT / "results" / "m7_e2e.json"
    if not path.exists():
        return {"status": "NOT_AVAILABLE",
                "detail": "results/m7_e2e.json 不存在；Demo 仍由当前代码生成。",
                "source": "results/m7_e2e.json"}
    previous = json.loads(path.read_text(encoding="utf-8"))
    old_summary = previous.get("summary", {})
    checks = {
        "tickets": summary.get("tickets") == old_summary.get("tickets"),
        "success": summary.get("success") == old_summary.get("success"),
        "failed": summary.get("failed") == old_summary.get("failed"),
        "failure_kinds": summary.get("failure_kinds") == old_summary.get("failure_kinds"),
        "context_tokens": summary.get("context_tokens") == old_summary.get("context_tokens"),
        "flags": summary.get("flags") == old_summary.get("flags"),
    }
    old_tickets = {t.get("id"): t for t in previous.get("tickets", [])}
    for result in results:
        old = old_tickets.get(result.ticket_id)
        if old is None:
            checks[f"{result.ticket_id}.present"] = False
            continue
        checks[f"{result.ticket_id}.tool_verdict"] = (
            old.get("tool_verdict") == result.tool_verdict)
        checks[f"{result.ticket_id}.llm_status"] = (
            old.get("llm_status") == result.llm_status)
        checks[f"{result.ticket_id}.failure"] = (
            old.get("failure") == result.failure)
    mismatches = [name for name, ok in checks.items() if not ok]
    return {
        "status": "MATCH" if not mismatches else "MISMATCH",
        "checks": len(checks),
        "mismatches": mismatches,
        "source": "results/m7_e2e.json",
    }


def run_batch(seed: int | None = None, model: str = DEMO_MODEL) -> dict:
    seed = C.SEED if seed is None else int(seed)
    with tempfile.TemporaryDirectory(prefix="aplat_demo_") as tmp:
        tmp_path = Path(tmp)
        db = tmp_path / "e2e.db"
        order_db = tmp_path / "orders.db"
        ensure_order_db(order_db)

        upstream = ScriptedUpstream(seed=seed)
        pipe = TicketPipeline(db, upstream, order_db, model=model)
        rng = C.rng(seed)
        results: list[TicketResult] = []
        for i, (text, want) in enumerate(TICKETS):
            results.append(pipe.run(f"T{i:02d}", text, want, rng))

        summary = summarize(results)
        verdicts = {
            verdict: sum(1 for r in results if r.tool_verdict == verdict)
            for verdict in {r.tool_verdict for r in results if r.tool_verdict}
        }
        with C.db(db) as conn:
            pinned = int(conn.execute(
                "SELECT COUNT(*) AS c FROM memory_item WHERE pinned=1"
            ).fetchone()["c"])

        items = [_item_payload(r, db) for r in results]
        commit, dirty, dirty_paths = _git_snapshot()
        payload = {
            "meta": {
                "repo": "agent-platform",
                "demo": "m7-ticket-pipeline",
                "commit": commit,
                "worktree_dirty": dirty,
                "dirty_paths": dirty_paths,
                "generated_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"),
                "mode": "offline-scripted",
                "seed": seed,
                "model": model,
                "python": sys.version.split()[0],
                "source_result": "results/m7_e2e.json",
                "note": (
                    "静态回放是一次确定性离线运行的产物；"
                    "本地运行 python3 demo/server.py 可实时重算。"
                ),
            },
            "summary": {
                **summary,
                "pinned": pinned,
                "tool_verdicts": verdicts,
            },
            "items": items,
            "boundaries": list(REFUTES),
            "verification": _verify(summary, results),
        }
        return payload


def write_payload(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
