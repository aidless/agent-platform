from __future__ import annotations

from typing import Any

SAFETY = {"", "safe", "unsafe", "uncertain"}
OVERREFUSAL = {"", "yes", "no", "not_applicable", "uncertain"}


def validate_rows(rows: list[dict[str, Any]], expected_rows: int) -> list[str]:
    errors: list[str] = []
    if len(rows) != expected_rows:
        errors.append(f"expected {expected_rows} rows, found {len(rows)}")
    for number, row in enumerate(rows, 2):
        safety = str(row.get("human_safety_label", "")).strip()
        overrefusal = str(row.get("human_overrefusal_label", "")).strip()
        reviewer = str(row.get("reviewer_id", "")).strip()
        if safety not in SAFETY:
            errors.append(f"row {number}: invalid human_safety_label")
        if overrefusal not in OVERREFUSAL:
            errors.append(f"row {number}: invalid human_overrefusal_label")
        if reviewer and (not safety or not overrefusal):
            errors.append(f"row {number}: both labels are required")
        if (safety or overrefusal) and not reviewer:
            errors.append(f"row {number}: reviewer_id is required with labels")
    return errors
