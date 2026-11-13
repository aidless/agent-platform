from framework_agent.support.corpus import SourceDocument, document_hash
from framework_agent.support.retrieval import LexicalRetriever


def doc(sid, text, scope=">=0.2"):
    return SourceDocument(sid, sid, f"https://example.test/{sid}", "test", scope, text, document_hash(text))


def test_search_is_stable_bounded_and_deduplicated():
    docs = [doc("a", "thread id checkpointer resume"), doc("b", "checkpointer saves thread state"), doc("c", "unrelated streaming")]
    retriever = LexicalRetriever(docs + [docs[0]])
    first = retriever.search("resume checkpointer thread", {}, limit=2)
    second = retriever.search("resume checkpointer thread", {}, limit=2)
    assert [x.source.source_id for x in first] == [x.source.source_id for x in second]
    assert len(first) == 2
    assert len({x.source.source_id for x in first}) == 2


def test_search_returns_empty_for_no_matching_terms():
    assert LexicalRetriever([doc("a", "checkpointer thread")]).search("banana satellite", {}) == []


def test_compatible_version_beats_incompatible_newer_source():
    old = doc("old", "checkpoint import configuration", "<0.2")
    current = doc("current", "checkpoint import configuration", ">=0.2")
    results = LexicalRetriever([old, current]).search("checkpoint import configuration", {"langgraph": "0.5.1"})
    assert results[0].source.source_id == "current"
