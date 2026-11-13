from pathlib import Path
from framework_agent.support.corpus import load_corpus


def test_seed_corpus_has_unique_hashed_versioned_sources():
    root = Path(__file__).resolve().parents[2]
    docs = load_corpus(root / "support_data/corpus-v1.jsonl")
    assert 12 <= len(docs) <= 20
    assert len({doc.source_id for doc in docs}) == len(docs)
    assert all(len(doc.content_sha256) == 64 for doc in docs)
    assert all(doc.version_scope for doc in docs)
    assert {doc.topic for doc in docs} >= {"checkpoint", "interrupt", "recursion", "tool_error", "migration"}
