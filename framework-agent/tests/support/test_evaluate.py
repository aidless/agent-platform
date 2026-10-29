import json
from pathlib import Path
from framework_agent.support.evaluate import evaluate_cases, load_eval_cases, write_evidence
from framework_agent.support.service_factory import build_default_service

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_eval_has_30_cases_and_required_families():
    cases = load_eval_cases(ROOT / "support_data/eval-cases-v1.jsonl")
    assert len(cases) == 30
    assert {case["family"] for case in cases} >= {"clarification", "documented_fix", "version_mismatch", "conflict", "escalation"}
    corpus = (ROOT / "support_data/corpus-v1.jsonl").read_text(encoding="utf-8")
    assert all(case.get("held_out_solution", "") not in corpus for case in cases if case.get("held_out_solution"))


def test_evaluation_metrics_recompute_and_failures_are_preserved(tmp_path):
    cases = load_eval_cases(ROOT / "support_data/eval-cases-v1.jsonl")
    service = build_default_service(tmp_path / "cases.db", ROOT / "support_data/corpus-v1.jsonl")
    report, traces = evaluate_cases(cases, service)
    assert report["case_count"] == 30
    assert report["citation_validity"] == 1.0
    assert report["outcome_accuracy"] == sum(t["passed"] for t in traces) / 30
    assert "failures" in report
    output = tmp_path / "evidence"
    write_evidence(output, cases, traces, report)
    assert (output / "SHA256SUMS").is_file()
    assert json.loads((output / "summary.json").read_text())["case_count"] == 30
