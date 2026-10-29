from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

MAX_LOG_CHARS = 20_000


@dataclass(frozen=True)
class SupportCase:
    question: str
    case_id: str = ""
    versions: dict[str, str] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    logs: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SupportCase":
        question = str(payload.get("question", "")).strip()
        if not question:
            raise ValueError("question is required")
        versions = {str(k): str(v) for k, v in dict(payload.get("versions") or {}).items()}
        environment = {str(k): str(v) for k, v in dict(payload.get("environment") or {}).items()}
        return cls(
            question=question,
            case_id=str(payload.get("case_id", "")).strip(),
            versions=versions,
            environment=environment,
            logs=str(payload.get("logs", ""))[:MAX_LOG_CHARS],
        )


@dataclass(frozen=True)
class Citation:
    source_id: str
    title: str
    quote: str
    url: str


@dataclass
class SupportResult:
    case_id: str
    status: str
    answer: str = ""
    citations: list[Citation] = field(default_factory=list)
    confidence: float = 0.0
    missing_information: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    escalation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
