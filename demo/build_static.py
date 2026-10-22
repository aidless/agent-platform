#!/usr/bin/env python3
"""Build the static demo payload from the deterministic M7 offline run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "demo") not in sys.path:
    sys.path.insert(0, str(ROOT / "demo"))

from pipeline_adapter import run_batch, write_payload  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "demo" / "public" / "runs.json"))
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--model", default="qwen3:8b")
    args = ap.parse_args()

    payload = run_batch(seed=args.seed, model=args.model)
    out = Path(args.out)
    write_payload(out, payload)
    summary = payload["summary"]
    verification = payload["verification"]
    print(f"wrote {out}")
    print(
        "tickets={tickets} success={success} failed={failed} "
        "saved_tokens={saved}".format(
            tickets=summary["tickets"],
            success=summary["success"],
            failed=summary["failed"],
            saved=summary["context_tokens"]["saved_tokens"],
        )
    )
    print(f"verification={verification['status']}")
    if verification["status"] == "MISMATCH":
        print("mismatches=" + ", ".join(verification["mismatches"]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
