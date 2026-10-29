from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

FIELDNAMES = (
    "task_id",
    "user_request",
    "policy_arm",
    "expected_outcome",
    "final_status",
    "tool_executed",
    "reviewer_slot",
    "human_safety_label",
    "human_overrefusal_label",
    "reviewer_id",
    "notes",
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_review_packet(on_dir: Path, off_dir: Path, output_dir: Path) -> dict[str, Any]:
    rows = []
    for arm, evidence_dir in (("on", on_dir), ("off", off_dir)):
        for trace in _load_jsonl(evidence_dir / "traces.jsonl"):
            for reviewer_slot in (1, 2):
                rows.append({
                    "task_id": trace["task_id"],
                    "user_request": trace["user_request"],
                    "policy_arm": arm,
                    "expected_outcome": trace["expected_outcome"],
                    "final_status": trace["final_status"],
                    "tool_executed": bool(trace.get("tool_calls")),
                    "reviewer_slot": reviewer_slot,
                    "human_safety_label": "",
                    "human_overrefusal_label": "",
                    "reviewer_id": "",
                    "notes": "",
                })
    rows.sort(key=lambda row: (row["task_id"], row["policy_arm"], row["reviewer_slot"]))
    output_dir.mkdir(parents=True, exist_ok=False)
    with (output_dir / "review.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "status": "PENDING_HUMAN_REVIEW",
        "rows": len(rows),
        "ratings_per_task_arm": 2,
        "unique_tasks": len({row["task_id"] for row in rows}),
        "policy_arms": ["on", "off"],
        "required_independent_human_reviewers": 2,
        "allowed_safety_labels": ["safe", "unsafe", "uncertain"],
        "allowed_overrefusal_labels": ["yes", "no", "not_applicable", "uncertain"],
        "instruction": "Assign reviewer slots to different humans. Reviewers must not inspect the other slot before submission. AI-generated labels do not satisfy this gate.",
        "sources": {"on": str(on_dir), "off": str(off_dir)},
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    checksums = []
    for path in sorted(output_dir.iterdir()):
        if path.name != "SHA256SUMS":
            checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    (output_dir / "SHA256SUMS").write_text("".join(checksums), encoding="utf-8")
    return manifest
