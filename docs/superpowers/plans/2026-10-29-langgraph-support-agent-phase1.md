# LangGraph Support Agent Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline-first LangGraph technical-support agent that clarifies incomplete cases, retrieves version-aware evidence, returns cited diagnoses, resumes interrupted cases, and escalates unresolved cases.

**Architecture:** Add a focused support package beside the existing reliability benchmark. A deterministic corpus and lexical retriever feed a LangGraph workflow; SQLite checkpoints persist case state; a stdlib HTTP service exposes the workflow to a browser demo. Existing benchmark and safety modules remain regression assets.

**Tech Stack:** Python 3.10+, LangGraph, SQLite, standard-library HTTP/JSON, pytest

**Spec:** `docs/superpowers/specs/2026-10-29-langgraph-support-agent-design.md`

## Global Constraints

- Work only under `framework-agent/` plus this repository's design/plan documents.
- Do not modify archived evidence under `framework-agent/results/`.
- No network, paid API, GPU, external write, or long local benchmark is required for acceptance.
- All completed answers must contain verified corpus source IDs; unsupported citations fail closed.
- Preserve failures and escalation outcomes in evaluation reports.

## Review Focus

- Empty or oversized logs must be bounded and must not crash intake; Task 1 tests both.
- A version mismatch must rank compatible evidence above newer incompatible evidence; Task 2 tests it.
- Conflicting high-ranked sources must cause escalation or explicit uncertainty; Task 3 tests it.
- Resume after retrieval must not execute retrieval twice; Task 4 tests call counts.
- A citation referencing an unknown source ID must prevent a completed answer; Task 3 tests fail-closed behavior.

---

### Task 1: Case contracts and frozen corpus

**Files:**
- Create: `framework-agent/src/framework_agent/support/contracts.py`
- Create: `framework-agent/src/framework_agent/support/corpus.py`
- Create: `framework-agent/support_data/corpus-v1.jsonl`
- Test: `framework-agent/tests/support/test_contracts.py`
- Test: `framework-agent/tests/support/test_corpus.py`

**Interfaces:**
- Produces: `SupportCase.from_dict(payload) -> SupportCase`, `SupportResult.to_dict() -> dict`, `load_corpus(path) -> list[SourceDocument]`.

- [ ] Write tests for required question text, normalized optional fields, bounded 20,000-character logs, empty logs, unique source IDs, content hashes, and version metadata.
- [ ] Run the focused tests and confirm they fail because the package does not exist.
- [ ] Implement immutable dataclasses and a 12-20 document seed corpus covering checkpoints, interrupts, recursion limits, tool errors, state updates, and version migration.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Version-aware deterministic retrieval

**Files:**
- Create: `framework-agent/src/framework_agent/support/retrieval.py`
- Test: `framework-agent/tests/support/test_retrieval.py`

**Interfaces:**
- Consumes: `SourceDocument` from Task 1.
- Produces: `LexicalRetriever.search(query: str, versions: dict[str, str], limit: int = 5) -> list[RetrievedSource]`.

- [ ] Write tests for stable ranking, no-match behavior, version preference, source deduplication, and bounded results.
- [ ] Run tests and verify failure.
- [ ] Implement token-normalized BM25-style scoring with deterministic version compatibility boosts and no external dependencies.
- [ ] Run tests and verify pass.

### Task 3: Support graph and citation gate

**Files:**
- Create: `framework-agent/src/framework_agent/support/diagnosis.py`
- Create: `framework-agent/src/framework_agent/support/graph.py`
- Test: `framework-agent/tests/support/test_support_graph.py`

**Interfaces:**
- Consumes: `SupportCase`, `LexicalRetriever`.
- Produces: `build_support_graph(retriever, diagnoser)`, `run_support_case(case, retriever, diagnoser) -> SupportResult`, and deterministic `RuleDiagnoser`.

- [ ] Write tests for clarification, cited completion, unresolved escalation, conflicting evidence, unknown citation rejection, and bounded graph termination.
- [ ] Run tests and verify failure.
- [ ] Implement graph nodes for intake, clarification, retrieval, evidence assessment, diagnosis, citation verification, answer, and escalation.
- [ ] Run tests and verify pass.

### Task 4: Durable cases and resume semantics

**Files:**
- Create: `framework-agent/src/framework_agent/support/store.py`
- Modify: `framework-agent/src/framework_agent/support/graph.py`
- Test: `framework-agent/tests/support/test_resume.py`

**Interfaces:**
- Produces: `CaseStore(db_path)`, `SupportService.start(payload)`, `SupportService.resume(case_id, updates)`.

- [ ] Write tests for persisted state, duplicate case IDs, resume after clarification, and retrieval call count remaining one after resume from a post-retrieval interruption.
- [ ] Run tests and verify failure.
- [ ] Implement SQLite case/event persistence and graph checkpoint/resume integration.
- [ ] Run tests and verify pass.

### Task 5: Frozen evaluation and evidence report

**Files:**
- Create: `framework-agent/support_data/eval-cases-v1.jsonl`
- Create: `framework-agent/src/framework_agent/support/evaluate.py`
- Test: `framework-agent/tests/support/test_evaluate.py`

**Interfaces:**
- Produces: `evaluate_cases(cases, service) -> EvaluationReport` and CLI module output with JSON, Markdown, traces, manifest, and SHA256SUMS.

- [ ] Write tests for exactly 30 cases, family coverage, source leakage guard, metric recomputation, preserved failures, and deterministic checksums except timing fields.
- [ ] Run tests and verify failure.
- [ ] Implement the frozen set and evaluator with citation validity, outcome accuracy, clarification accuracy, escalation accuracy, and retrieval recall metrics.
- [ ] Run tests and verify pass.

### Task 6: Local API and evidence-first demo

**Files:**
- Create: `framework-agent/src/framework_agent/support/api.py`
- Create: `framework-agent/support_demo/index.html`
- Create: `framework-agent/support_demo/app.js`
- Create: `framework-agent/support_demo/style.css`
- Test: `framework-agent/tests/support/test_api.py`

**Interfaces:**
- Produces: `GET /api/health`, `POST /api/cases`, `POST /api/cases/{id}/resume`, and static demo serving.

- [ ] Write API tests for valid case, malformed JSON, oversized payload, clarification resume, escalation response, and no network-dependent assets.
- [ ] Run tests and verify failure.
- [ ] Implement the stdlib HTTP API and responsive three-pane interface with case history, graph timeline, citations, confidence, and escalation packet.
- [ ] Run API tests and verify pass.

### Task 7: Integration, documentation, and regression gates

**Files:**
- Modify: `framework-agent/README.md`
- Modify: `framework-agent/pyproject.toml`
- Create: `framework-agent/src/framework_agent/support/__init__.py`
- Create: `framework-agent/Makefile`
- Test: existing `framework-agent/tests/` and all new support tests.

**Interfaces:**
- Produces: `make test`, `make support-smoke`, `make support-eval`, and documented three-minute demo flow.

- [ ] Add packaging, commands, boundary statements, architecture diagram, evaluation interpretation, and Phase 2/3 roadmap.
- [ ] Run all unit tests, the short offline support smoke, and checksum verification.
- [ ] Inspect the demo at desktop and mobile widths and fix visible layout issues.
- [ ] Record exact test counts and evaluation results without upgrading synthetic evidence into production claims.
