from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .contracts import SupportCase
from .retrieval import RetrievedSource


@dataclass(frozen=True)
class DiagnosisDraft:
    answer: str
    source_ids: list[str]
    confidence: float


class Diagnoser(Protocol):
    def diagnose(self, case: SupportCase, sources: list[RetrievedSource]) -> DiagnosisDraft: ...


class RuleDiagnoser:
    """Deterministic baseline used for offline acceptance and regression tests."""

    def diagnose(self, case: SupportCase, sources: list[RetrievedSource]) -> DiagnosisDraft:
        if not sources:
            return DiagnosisDraft("", [], 0.0)
        best = sources[0]
        question = case.question.lower()
        if "migration" in question or "migrat" in question:
            migration_sources = [item for item in sources if item.source.topic == "migration"]
            preferred = "memory" if "memory" in question or "store" in question else "config"
            preferred_sources = [item for item in migration_sources if preferred in item.source.source_id]
            if preferred_sources or migration_sources:
                best = (preferred_sources or migration_sources)[0]
        answer = f"Likely diagnosis: {best.source.text}"
        confidence = min(0.88, 0.55 + best.score / 3)
        scopes = {item.source.version_scope for item in sources[:3]}
        if len(scopes) > 1:
            confidence = min(confidence, 0.75)
            answer += " Version-specific sources differ, so verify the installed package version before applying the fix."
        return DiagnosisDraft(answer, [best.source.source_id], confidence)
