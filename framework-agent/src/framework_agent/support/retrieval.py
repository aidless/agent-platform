from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .corpus import SourceDocument

TOKEN_RE = re.compile(r"[a-z0-9_./-]+", re.IGNORECASE)
STOP_WORDS = {
    "a", "an", "and", "does", "error", "fail", "fails", "for", "how",
    "i", "in", "is", "it", "of", "on", "or", "the", "to", "use", "why",
    "with", "unknown",
}


@dataclass(frozen=True)
class RetrievedSource:
    source: SourceDocument
    score: float
    matched_terms: tuple[str, ...]


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text) if token.lower() not in STOP_WORDS]


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)*", value)
    return tuple(int(part) for part in match.group().split(".")) if match else ()


def _compatible(scope: str, actual: str) -> bool:
    wanted = _version_tuple(scope)
    current = _version_tuple(actual)
    if not wanted or not current:
        return True
    if scope.startswith(">="):
        return current >= wanted
    if scope.startswith("<"):
        return current < wanted
    return current == wanted


class LexicalRetriever:
    def __init__(self, documents: list[SourceDocument]) -> None:
        unique: dict[str, SourceDocument] = {}
        for document in documents:
            unique.setdefault(document.source_id, document)
        self.documents = list(unique.values())
        self._terms = [Counter(_tokens(f"{doc.title} {doc.topic} {doc.text}")) for doc in self.documents]
        self._df = Counter(term for terms in self._terms for term in terms)

    def search(self, query: str, versions: dict[str, str], limit: int = 5) -> list[RetrievedSource]:
        query_terms = set(_tokens(query))
        query_text = query.lower()
        if not query_terms or limit <= 0:
            return []
        count = len(self.documents)
        results: list[RetrievedSource] = []
        for document, frequencies in zip(self.documents, self._terms):
            matched = sorted(query_terms & frequencies.keys())
            if not matched or len(matched) < 2:
                continue
            length = max(1, sum(frequencies.values()))
            score = sum((1 + math.log(frequencies[term])) * math.log(1 + (count + 1) / (self._df[term] + 1)) for term in matched)
            score /= math.sqrt(length)
            if document.topic in query_text or document.topic.replace("_", " ") in query_text:
                score *= 1.45
            if "thread" in query_terms and document.topic == "checkpoint":
                score *= 1.25
            if ("migration" in query_terms or "migrat" in query_text) and document.topic == "migration":
                score *= 1.6
            actual = versions.get("langgraph")
            if actual:
                score *= 1.35 if _compatible(document.version_scope, actual) else 0.35
            results.append(RetrievedSource(document, round(score, 8), tuple(matched)))
        return sorted(results, key=lambda item: (-item.score, item.source.source_id))[:limit]
