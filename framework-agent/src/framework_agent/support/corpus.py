from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceDocument:
    source_id: str
    title: str
    url: str
    topic: str
    version_scope: str
    text: str
    content_sha256: str


def document_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_corpus(path: Path) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        source_id = str(item["source_id"])
        if source_id in seen:
            raise ValueError(f"duplicate source_id at line {number}: {source_id}")
        seen.add(source_id)
        expected = document_hash(str(item["text"]))
        supplied = item.get("content_sha256", expected)
        if supplied != expected:
            raise ValueError(f"content hash mismatch: {source_id}")
        documents.append(SourceDocument(content_sha256=expected, **{k: item[k] for k in ("source_id", "title", "url", "topic", "version_scope", "text")}))
    return documents
