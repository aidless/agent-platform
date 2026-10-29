from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .contracts import Citation, SupportCase, SupportResult
from .diagnosis import Diagnoser
from .graph import run_support_case
from .retrieval import LexicalRetriever


class CaseStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS support_case (case_id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            connection.execute("CREATE TABLE IF NOT EXISTS support_event (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, event TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def exists(self, case_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM support_case WHERE case_id=?", (case_id,)).fetchone() is not None

    def save(self, case_id: str, payload: dict[str, Any], result: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO support_case(case_id,payload,result) VALUES(?,?,?) ON CONFLICT(case_id) DO UPDATE SET payload=excluded.payload,result=excluded.result,updated_at=CURRENT_TIMESTAMP",
                (case_id, json.dumps(payload, sort_keys=True), json.dumps(result, sort_keys=True)),
            )
            for event in result.get("timeline", []):
                connection.execute("INSERT INTO support_event(case_id,event) VALUES(?,?)", (case_id, json.dumps(event, sort_keys=True)))

    def load(self, case_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT payload,result FROM support_case WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(case_id)
        return {"payload": json.loads(row[0]), "result": json.loads(row[1])}


def _result_from_dict(payload: dict[str, Any]) -> SupportResult:
    return SupportResult(
        case_id=payload["case_id"], status=payload["status"], answer=payload.get("answer", ""),
        citations=[Citation(**item) for item in payload.get("citations", [])], confidence=payload.get("confidence", 0.0),
        missing_information=payload.get("missing_information", []), next_actions=payload.get("next_actions", []),
        timeline=payload.get("timeline", []), escalation=payload.get("escalation"),
    )


class SupportService:
    def __init__(self, store: CaseStore, retriever: LexicalRetriever, diagnoser: Diagnoser) -> None:
        self.store = store
        self.retriever = retriever
        self.diagnoser = diagnoser

    def start(self, payload: dict[str, Any]) -> SupportResult:
        case = SupportCase.from_dict(payload)
        if case.case_id and self.store.exists(case.case_id):
            raise ValueError(f"case_id already exists: {case.case_id}")
        result = run_support_case(case, self.retriever, self.diagnoser)
        stored_payload = dict(payload)
        stored_payload["case_id"] = result.case_id
        self.store.save(result.case_id, stored_payload, result.to_dict())
        return result

    def resume(self, case_id: str, updates: dict[str, Any]) -> SupportResult:
        saved = self.store.load(case_id)
        if saved["result"]["status"] in {"completed", "escalated"} and not updates:
            return _result_from_dict(saved["result"])
        payload = dict(saved["payload"])
        for key, value in updates.items():
            if key in {"versions", "environment"}:
                payload[key] = {**payload.get(key, {}), **dict(value)}
            elif value is not None:
                payload[key] = value
        payload["case_id"] = case_id
        result = run_support_case(SupportCase.from_dict(payload), self.retriever, self.diagnoser)
        self.store.save(case_id, payload, result.to_dict())
        return result
