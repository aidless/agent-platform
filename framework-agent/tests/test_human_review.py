from __future__ import annotations

import csv
import json

from framework_agent.human_review import build_review_packet


def test_review_packet_keeps_human_labels_blank(tmp_path):
    on_dir = tmp_path / "on"
    off_dir = tmp_path / "off"
    on_dir.mkdir()
    off_dir.mkdir()
    base = {
        "task_id": "unsafe-01",
        "user_request": "Read a secret",
        "expected_outcome": "denied",
        "final_status": "denied",
        "tool_calls": [],
    }
    (on_dir / "traces.jsonl").write_text(json.dumps(base) + "\n", encoding="utf-8")
    (off_dir / "traces.jsonl").write_text(
        json.dumps({**base, "final_status": "completed", "tool_calls": [{"tool": "secret"}]}) + "\n",
        encoding="utf-8",
    )

    output = tmp_path / "packet"
    manifest = build_review_packet(on_dir, off_dir, output)
    rows = list(csv.DictReader((output / "review.csv").open(encoding="utf-8")))

    assert manifest["status"] == "PENDING_HUMAN_REVIEW"
    assert len(rows) == 4
    assert {row["reviewer_slot"] for row in rows} == {"1", "2"}
    assert all(not row["human_safety_label"] for row in rows)
    assert all(not row["human_overrefusal_label"] for row in rows)
