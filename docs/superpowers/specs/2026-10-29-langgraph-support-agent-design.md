# LangGraph Support Agent Design

## Intent

Turn the existing `framework-agent` reliability prototype into one flagship job-search project. The project will be built in order: a usable LangGraph technical-support agent, a reusable agent runtime, then an evaluation console. Each phase must remain independently runnable and demonstrable.

## Users and primary workflow

The first user is a developer who has a LangGraph error or behavior question. They provide a question, version/environment details, and optionally logs. The system must either ask for missing facts, retrieve version-aware evidence from a frozen documentation and issue corpus, produce a cited diagnostic answer, or prepare an escalation packet when evidence is insufficient.

The first release targets LangGraph because the existing code already uses it and the same project can teach graph state, checkpoints, tools, recovery, and evaluation through direct use.

## Goals

### Phase 1: Support product

- Accept a support case with question, package versions, environment, and logs.
- Detect missing information and ask a focused clarification before diagnosis.
- Retrieve relevant, version-tagged documentation and resolved issue excerpts from an offline corpus.
- Return a diagnosis with source citations and explicit confidence.
- Escalate low-evidence or unresolved cases into a structured handoff packet.
- Persist state so an interrupted case can resume without repeating completed work.
- Ship a local API/CLI and a focused browser demo.
- Evaluate on a frozen set whose expected sources and outcomes are held separately from retrieval input.

### Phase 2: Reusable platform

- Extract graph execution, checkpointing, model/provider adapters, tool registry, approval policy, telemetry, retries, and budget controls behind stable interfaces.
- Keep the support agent as the reference application proving those interfaces.
- Add concurrency and restart acceptance checks using local infrastructure only.

### Phase 3: Evaluation console

- Import datasets and immutable run manifests.
- Launch offline regression runs and compare traces, quality, latency, and cost.
- Support human-review queues and fail-closed release gates.
- Display claims together with raw evidence and known limitations.

## Non-goals

- General customer support for arbitrary products in Phase 1.
- Automatic GitHub comments, issue creation, email, or other external writes.
- Claims of production SLO, user adoption, model alignment, or benchmark leadership.
- Local training, inference sweeps, or multi-hour benchmark runs.
- Replacing LangGraph documentation or maintainers.

## Product behavior

The support graph is:

```text
intake -> normalize -> clarify? -> retrieve -> assess evidence
                                  -> diagnose -> verify citations
                                  -> answer | escalate
```

Every answer includes `case_id`, `status`, `answer`, `citations`, `confidence`, `missing_information`, and `next_actions`. Citation verification must fail closed: unsupported source identifiers or quotations cannot produce a completed answer. Retrieval returns no result rather than inventing a source. A resumed case reuses persisted completed-node outputs.

## Data and evaluation

The repository stores a small, licensed or link-only frozen corpus with source URL, title, version scope, retrieval text, content hash, and retrieval date. Resolved issue solutions are not placed in the retrievable input for the matching evaluation case. Evaluation reports exact case counts and preserves failures. Scripted providers support deterministic tests; real model runs are optional evidence and never replace offline acceptance.

Phase 1 acceptance requires:

- At least 30 frozen cases spanning clarification, documented fixes, version mismatch, conflicting evidence, and escalation.
- Citation validity of 100% on completed scripted runs.
- No invented source IDs.
- Resume tests prove completed retrieval is not repeated.
- At least one intentionally unresolved case escalates rather than guessing.
- Unit and end-to-end tests pass from one documented command.

## User interface

The demo has three columns: case input/history, graph timeline, and evidence-backed result. It should foreground sources, uncertainty, and escalation state rather than generic chat styling. Mobile uses the same sequence as stacked sections.

## Constraints

- Python 3.10+ and the current LangGraph package structure.
- Build inside `framework-agent/`; do not modify unrelated generated result files.
- Local runs are limited to tests, short smoke checks, and evidence parsing.
- No credentials in source, fixtures, traces, or reports.
- No commit, push, release, deployment, paid API, or GPU use without explicit authorization.
- Existing safety policy and evidence packages remain preserved; synthetic safety results are presented as regression evidence, not product adoption.

## Approaches considered

1. Three independent repositories: simple boundaries but duplicates runtime and fragments the job-search story.
2. One generic platform first: technically broad but repeats the current problem of modules without a compelling user workflow.
3. One flagship product with progressively extracted platform and evaluation layers: selected because every abstraction is justified by a working support workflow and all three requested directions remain visible.

## Builder decisions

- Start with LangGraph support and a frozen offline corpus.
- Use deterministic retrieval first; add embeddings only if measured retrieval failures justify them.
- Extend the existing `framework-agent` package rather than create another repository.
- Phase 1 ships before Phase 2 begins; Phase 2 ships before Phase 3 begins.
