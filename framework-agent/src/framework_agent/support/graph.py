from __future__ import annotations

import hashlib
from typing import Annotated, Any, TypedDict
from operator import add

from langgraph.graph import END, START, StateGraph

from .contracts import Citation, SupportCase, SupportResult
from .diagnosis import Diagnoser, DiagnosisDraft
from .retrieval import LexicalRetriever, RetrievedSource


class SupportState(TypedDict, total=False):
    case: SupportCase
    case_id: str
    missing: list[str]
    sources: list[RetrievedSource]
    draft: DiagnosisDraft
    status: str
    reason: str
    timeline: Annotated[list[dict[str, Any]], add]


def _case_id(case: SupportCase) -> str:
    if case.case_id:
        return case.case_id
    digest = hashlib.sha256(f"{case.question}|{case.versions}|{case.logs}".encode()).hexdigest()[:12]
    return f"case-{digest}"


def _missing(case: SupportCase) -> list[str]:
    missing = []
    if "langgraph" not in {key.lower() for key in case.versions}:
        missing.append("langgraph version")
    if not case.logs.strip() and len(case.question.split()) < 8:
        missing.append("error message or minimal reproduction")
    return missing


def build_support_graph(retriever: LexicalRetriever, diagnoser: Diagnoser):
    graph = StateGraph(SupportState)

    def intake(state):
        case = state["case"]
        return {"case_id": _case_id(case), "missing": _missing(case), "timeline": [{"node": "intake", "status": "ok"}]}

    def intake_route(state):
        return "clarify" if state["missing"] else "retrieve"

    def clarify(state):
        return {"status": "needs_clarification", "timeline": [{"node": "clarify", "status": "waiting"}]}

    def retrieve(state):
        case = state["case"]
        sources = retriever.search(f"{case.question} {case.logs}", case.versions)
        return {"sources": sources, "timeline": [{"node": "retrieve", "status": "ok", "count": len(sources)}]}

    def assess(state):
        reason = "" if state["sources"] else "insufficient_evidence"
        return {"reason": reason, "timeline": [{"node": "assess", "status": "ok" if not reason else "insufficient"}]}

    def assess_route(state):
        return "escalate" if state["reason"] else "diagnose"

    def diagnose(state):
        draft = diagnoser.diagnose(state["case"], state["sources"])
        return {"draft": draft, "timeline": [{"node": "diagnose", "status": "ok"}]}

    def verify(state):
        known = {item.source.source_id for item in state["sources"]}
        valid = bool(state["draft"].source_ids) and set(state["draft"].source_ids) <= known
        return {"reason": "" if valid else "invalid_citation", "timeline": [{"node": "verify_citations", "status": "ok" if valid else "rejected"}]}

    def verify_route(state):
        return "escalate" if state["reason"] else "answer"

    def answer(state):
        return {"status": "completed", "timeline": [{"node": "answer", "status": "completed"}]}

    def escalate(state):
        return {"status": "escalated", "timeline": [{"node": "escalate", "status": state["reason"]}]}

    for name, function in (("intake", intake), ("clarify", clarify), ("retrieve", retrieve), ("assess", assess), ("diagnose", diagnose), ("verify", verify), ("answer", answer), ("escalate", escalate)):
        graph.add_node(name, function)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", intake_route, {"clarify": "clarify", "retrieve": "retrieve"})
    graph.add_edge("clarify", END)
    graph.add_edge("retrieve", "assess")
    graph.add_conditional_edges("assess", assess_route, {"escalate": "escalate", "diagnose": "diagnose"})
    graph.add_edge("diagnose", "verify")
    graph.add_conditional_edges("verify", verify_route, {"escalate": "escalate", "answer": "answer"})
    graph.add_edge("answer", END)
    graph.add_edge("escalate", END)
    return graph.compile()


def run_support_case(case: SupportCase, retriever: LexicalRetriever, diagnoser: Diagnoser) -> SupportResult:
    state = build_support_graph(retriever, diagnoser).invoke({"case": case, "timeline": []})
    draft = state.get("draft")
    sources = {item.source.source_id: item.source for item in state.get("sources", [])}
    citations = []
    if draft and state["status"] == "completed":
        for source_id in draft.source_ids:
            source = sources[source_id]
            citations.append(Citation(source_id, source.title, source.text, source.url))
    return SupportResult(
        case_id=state["case_id"],
        status=state["status"],
        answer=draft.answer if draft and state["status"] == "completed" else "",
        citations=citations,
        confidence=draft.confidence if draft else 0.0,
        missing_information=state.get("missing", []),
        next_actions=["Provide the missing details and resume this case"] if state["status"] == "needs_clarification" else [],
        timeline=state.get("timeline", []),
        escalation={"reason": state.get("reason", "unknown"), "question": case.question, "versions": case.versions, "logs": case.logs} if state["status"] == "escalated" else None,
    )
