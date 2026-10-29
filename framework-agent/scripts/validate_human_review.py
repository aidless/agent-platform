#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from framework_agent.human_review_validation import validate_rows


def validate(packet: Path) -> list[str]:
    manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
    with (packet / "review.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return validate_rows(rows, expected_rows=manifest["rows"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("packet", type=Path)
    args = parser.parse_args()
    errors = validate(args.packet)
    if errors:
        print("\n".join(errors))
        return 1
    print("human review packet: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
