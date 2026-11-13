from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from .service_factory import build_default_service
from .store import SupportService


def load_eval_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate_cases(cases: list[dict[str, Any]], service: SupportService):
    traces = []
    for item in cases:
        result = service.start(item["input"])
        source_ids = {citation.source_id for citation in result.citations}
        expected_sources = set(item.get("expected_sources", []))
        outcome_ok = result.status == item["expected_status"]
        source_ok = not expected_sources or bool(source_ids & expected_sources)
        citation_valid = all(citation.quote and citation.url.startswith("https://") for citation in result.citations)
        passed = outcome_ok and source_ok and citation_valid
        traces.append({
            "case_id": item["id"], "family": item["family"], "expected_status": item["expected_status"],
            "actual_status": result.status, "source_ids": sorted(source_ids), "expected_sources": sorted(expected_sources),
            "citation_valid": citation_valid, "outcome_ok": outcome_ok, "source_ok": source_ok, "passed": passed,
            "result": result.to_dict(),
        })
    count = len(traces)
    completed = [trace for trace in traces if trace["actual_status"] == "completed"]
    report = {
        "schema_version": 1,
        "case_count": count,
        "outcome_accuracy": sum(trace["passed"] for trace in traces) / count if count else None,
        "citation_validity": sum(trace["citation_valid"] for trace in completed) / len(completed) if completed else 1.0,
        "retrieval_source_recall": sum(trace["source_ok"] for trace in traces) / count if count else None,
        "status_counts": {status: sum(trace["actual_status"] == status for trace in traces) for status in ("completed", "needs_clarification", "escalated")},
        "failures": [trace["case_id"] for trace in traces if not trace["passed"]],
        "limitations": ["Frozen cases and corpus are curated fixtures, not user traffic.", "RuleDiagnoser is deterministic and does not measure model quality."],
    }
    return report, traces


def write_evidence(output: Path, cases, traces, report) -> None:
    output.mkdir(parents=True, exist_ok=True)
    files = {
        "cases.json": json.dumps(cases, sort_keys=True, indent=2) + "\n",
        "traces.jsonl": "".join(json.dumps(trace, sort_keys=True) + "\n" for trace in traces),
        "summary.json": json.dumps(report, sort_keys=True, indent=2) + "\n",
        "report.md": f"# LangGraph support evaluation\n\n- Cases: {report['case_count']}\n- Outcome accuracy: {report['outcome_accuracy']:.1%}\n- Citation validity: {report['citation_validity']:.1%}\n- Failures: {', '.join(report['failures']) or 'none'}\n",
    }
    for name, content in files.items():
        (output / name).write_text(content, encoding="utf-8")
    sums = [f"{hashlib.sha256((output / name).read_bytes()).hexdigest()}  {name}\n" for name in sorted(files)]
    (output / "SHA256SUMS").write_text("".join(sums), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path("support_data/eval-cases-v1.jsonl"))
    parser.add_argument("--corpus", type=Path, default=Path("support_data/corpus-v1.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path("support_evidence/offline-v1"))
    args = parser.parse_args()
    cases = load_eval_cases(args.cases)
    with tempfile.TemporaryDirectory() as directory:
        service = build_default_service(Path(directory) / "cases.db", args.corpus)
        report, traces = evaluate_cases(cases, service)
    write_evidence(args.output_dir, cases, traces, report)
    print(json.dumps(report, sort_keys=True))
    return 0 if not report["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
