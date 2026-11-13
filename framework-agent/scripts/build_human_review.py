from __future__ import annotations

import argparse
from pathlib import Path

from framework_agent.human_review import build_review_packet


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy-on-dir", type=Path, required=True)
    parser.add_argument("--policy-off-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(build_review_packet(args.policy_on_dir, args.policy_off_dir, args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
