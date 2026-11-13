from pathlib import Path
from .corpus import load_corpus
from .diagnosis import RuleDiagnoser
from .retrieval import LexicalRetriever
from .store import CaseStore, SupportService


def build_default_service(db_path: Path, corpus_path: Path) -> SupportService:
    return SupportService(CaseStore(db_path), LexicalRetriever(load_corpus(corpus_path)), RuleDiagnoser())
