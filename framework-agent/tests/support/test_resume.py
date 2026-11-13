from pathlib import Path
import pytest
from framework_agent.support.corpus import load_corpus
from framework_agent.support.diagnosis import RuleDiagnoser
from framework_agent.support.retrieval import LexicalRetriever
from framework_agent.support.store import CaseStore, SupportService

ROOT = Path(__file__).resolve().parents[2]


class CountingRetriever(LexicalRetriever):
    def __init__(self, docs):
        super().__init__(docs)
        self.calls = 0
    def search(self, query, versions, limit=5):
        self.calls += 1
        return super().search(query, versions, limit)


def make_service(tmp_path):
    retriever = CountingRetriever(load_corpus(ROOT / "support_data/corpus-v1.jsonl"))
    return SupportService(CaseStore(tmp_path / "cases.db"), retriever, RuleDiagnoser()), retriever


def test_clarification_case_persists_and_resumes(tmp_path):
    service, _ = make_service(tmp_path)
    first = service.start({"case_id": "resume-1", "question": "Checkpoint fails"})
    assert first.status == "needs_clarification"
    resumed = service.resume("resume-1", {"versions": {"langgraph": "0.5.1"}, "logs": "missing thread_id"})
    assert resumed.status == "completed"
    assert service.store.load("resume-1")["result"]["status"] == "completed"


def test_duplicate_case_id_is_rejected(tmp_path):
    service, _ = make_service(tmp_path)
    payload = {"case_id": "same", "question": "How do checkpoints use thread id?", "versions": {"langgraph": "0.5.1"}}
    service.start(payload)
    with pytest.raises(ValueError, match="already exists"):
        service.start(payload)


def test_completed_resume_does_not_repeat_retrieval(tmp_path):
    service, retriever = make_service(tmp_path)
    payload = {"case_id": "done-1", "question": "How do checkpoints use thread id?", "versions": {"langgraph": "0.5.1"}, "logs": "missing state"}
    first = service.start(payload)
    assert first.status == "completed"
    assert retriever.calls == 1
    second = service.resume("done-1", {})
    assert second.status == "completed"
    assert retriever.calls == 1
