from pathlib import Path
from framework_agent.support.contracts import SupportCase
from framework_agent.support.corpus import load_corpus
from framework_agent.support.diagnosis import DiagnosisDraft, RuleDiagnoser
from framework_agent.support.graph import run_support_case
from framework_agent.support.retrieval import LexicalRetriever

ROOT = Path(__file__).resolve().parents[2]


def service_parts():
    return LexicalRetriever(load_corpus(ROOT / "support_data/corpus-v1.jsonl")), RuleDiagnoser()


def test_incomplete_case_requests_focused_clarification():
    retriever, diagnoser = service_parts()
    result = run_support_case(SupportCase.from_dict({"question": "It fails"}), retriever, diagnoser)
    assert result.status == "needs_clarification"
    assert "langgraph version" in result.missing_information
    assert not result.citations


def test_documented_case_completes_with_verified_citation():
    retriever, diagnoser = service_parts()
    case = SupportCase.from_dict({"question": "How do I resume an interrupt with the same thread?", "versions": {"langgraph": "0.5.1"}, "logs": "interrupt paused"})
    result = run_support_case(case, retriever, diagnoser)
    assert result.status == "completed"
    assert result.citations
    assert all(c.source_id.startswith("lg-") for c in result.citations)
    assert result.confidence >= 0.5


def test_unknown_problem_escalates_instead_of_guessing():
    retriever, diagnoser = service_parts()
    case = SupportCase.from_dict({"question": "Why does banana satellite fail?", "versions": {"langgraph": "0.5.1"}, "logs": "banana error"})
    result = run_support_case(case, retriever, diagnoser)
    assert result.status == "escalated"
    assert result.escalation["reason"] == "insufficient_evidence"


def test_unknown_citation_fails_closed():
    class BadDiagnoser:
        def diagnose(self, case, sources):
            return DiagnosisDraft("invented", ["missing-source"], 0.99)
    retriever, _ = service_parts()
    case = SupportCase.from_dict({"question": "How do checkpoints resume using thread id?", "versions": {"langgraph": "0.5.1"}, "logs": "checkpoint thread state missing"})
    result = run_support_case(case, retriever, BadDiagnoser())
    assert result.status == "escalated"
    assert result.escalation["reason"] == "invalid_citation"


def test_conflicting_version_evidence_is_explicitly_uncertain():
    retriever, diagnoser = service_parts()
    case = SupportCase.from_dict({"question": "Which checkpoint import should I use after migration?", "versions": {"langgraph": "0.1.9"}, "logs": "import error after upgrade"})
    result = run_support_case(case, retriever, diagnoser)
    assert result.status in {"completed", "escalated"}
    assert result.confidence < 0.9
    assert len(result.timeline) <= 9
