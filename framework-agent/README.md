# Framework Agent: 100-trace LangGraph workflow

This is a real LangGraph state machine with LangChain model invocation, deterministic safety policy, validated tool execution, and three injected failure classes.

```text
intake → policy ─┬→ deny / approval / clarification
                 └→ plan → validate → execute → synthesize
                         ↖ recover_plan      ↖ recover_tool
```

最新安全对照使用短生命周期 Docker 适配器：`--network none`、只读 root、`cap-drop ALL`、`no-new-privileges`、64 MiB 内存和 32 PIDs。它会执行真实隔离进程，但不连接生产外部系统。

## Isolated policy ON/OFF evidence

| Arm | Toolbox | Unsafe blocked | Safety violations | Safety exposure | Over-refusal | Recovery | Gate |
|---|---|---:|---:|---:|---:|---:|---|
| Policy ON | Docker isolated | 25/25 | 0 | 0 | 0/50 | 30/30 | PASS |
| Policy OFF | Docker isolated | 0/25 | 25 | 50 | 0/50 | 30/30 | HOLD |

- Policy ON p50/p95: 140.0/2405.1 ms; 50 traces reached isolated container tools.
- Policy OFF intentionally removes the policy layer; unsafe and approval/ambiguous requests can reach tools, producing 50 exposure traces.
- The matched rerun uses the same Docker toolbox and two workers in both arms. ON p50/p95 is 140.0/2405.1 ms; OFF is 311.1/2367.0 ms. This single local run is not a production latency SLO.
- `results/human-safety-review-policy-on-off-v2/` contains separate reviewer slots for two independent humans. It is **PENDING_HUMAN_REVIEW** and its label columns are intentionally blank.
- Evidence: `results/langgraph-scripted-docker-policy-on-100/` and `results/langgraph-scripted-docker-policy-off-100-v2/`.

The older recorded run below is retained as historical model-route evidence.

## Historical recorded run

- Planner route: `openrouter/google/gemma-4-31b-it:free`
- Served model reported by the gateway: `deepseek-v4.1-flash-1017`
- Trace count: **100**
- Explicit benign: 50
- Unsafe: 25
- Ambiguous: 15
- Approval-required: 10
- Task success: **100/100**
- Unsafe blocked: **25/25**
- Safety violations: **0**
- Over-refusal: **0/50**
- Fault recovery: **30/30**
- p50 / p95 latency: **725.9 ms / 12,331.3 ms**
- Model calls: 70
- Cost: **$0 marginal cost** on the selected free route

Faults recovered:

| Fault | Recovered |
|---|---:|
| First-attempt tool timeout | 10/10 |
| Planner rate limit | 10/10 |
| Malformed planner JSON | 10/10 |

Evidence: [`results/langgraph-openai-100/`](results/langgraph-openai-100/).

## Workflow contract

- **Policy before LLM:** destructive, secret, credential, financial, and host-root requests are denied before planner/model invocation.
- **Approval gate:** email, writes, ticket changes, alert suppression, installs, webhook posts, and production config changes stop for human approval.
- **Clarification gate:** pronoun-only requests without a concrete target do not execute a tool.
- **Planner:** LangChain `ChatOpenAI` returns one JSON plan over an explicit read-only tool allowlist.
- **Validation:** unknown tools or non-object args fail safely.
- **Recovery:** rate limits and malformed JSON receive one bounded repair attempt; tool timeouts receive one bounded retry.
- **Trace:** every node event, policy decision, plan, tool attempt, recovery, token count, latency, and outcome is persisted.

## Reproduce

Install the isolated dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Deterministic harness check:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m framework_agent.run_evidence \
  --planner scripted --workers 4 \
  --output-dir results/langgraph-scripted-rerun
```

Real model route:

```bash
PYTHONPATH=src .venv/bin/python -m framework_agent.run_evidence \
  --planner openai \
  --model openrouter/google/gemma-4-31b-it:free \
  --base-url http://127.0.0.1:3425/v1 \
  --workers 2 \
  --output-dir results/langgraph-openai-rerun
```

Verify the archived package:

```bash
cd results/langgraph-openai-100
sha256sum -c SHA256SUMS
```

## What this proves

- The workflow is a real LangGraph graph, not a linear script or a mocked graph contract.
- All 100 runs produced the expected high-level outcome and benign tool selection.
- The policy prevented every unsafe scenario from reaching model planning or tool execution.
- All three injected failure classes converged to the expected terminal state within a bounded retry.
- Per-trace evidence is sufficient to inspect how each result was reached.

## What this does not prove

- The task pack is synthetic and keyword-driven, not a public agent benchmark.
- Safety behavior here belongs to the workflow policy; it is not evidence that the underlying model is aligned.
- Task success checks outcome plus benign tool name, not exact argument correctness.
- Mock tools establish orchestration behavior, not resilience of real external systems.
- No distributed, multi-tenant, concurrency, or production SLO validation is included.

## LangGraph Support Agent (Phase 1)

The first product layer turns the reliability harness into an evidence-backed technical support workflow:

```text
intake -> clarify? -> retrieve version-aware evidence -> diagnose
       -> verify citations -> answer | escalate
```

It is offline-first: the frozen corpus, deterministic retriever, rule diagnoser, SQLite case store, 30-case evaluation set, and browser demo require no API key or model call. Unknown questions escalate instead of inventing a source. Completed answers only ship after every source ID is verified against retrieved evidence.

```bash
make test            # existing reliability suite + support suite
make support-smoke   # one cited end-to-end case
make support-eval    # 30 frozen cases + checksummed evidence
make support-demo    # http://127.0.0.1:8765
```

The demo shows case input, graph transitions, citations, confidence, and the escalation packet. It is a local learning and portfolio artifact, not a production support service. The corpus is a curated fixture with canonical LangGraph documentation URLs; the deterministic baseline measures workflow behavior, not LLM quality or real-user adoption.

### Roadmap

1. **Support product (current):** clarification, version-aware retrieval, cited diagnosis, persistence, escalation, and frozen evaluation.
2. **Reusable runtime:** extract provider adapters, tool registry, checkpointing, budgets, telemetry, and concurrency contracts from the working support flow.
3. **Evaluation console:** import immutable runs, compare traces and metrics, manage human review, and enforce fail-closed release gates.

## Help with the human review

The policy ON/OFF comparison still requires two independent human reviewers.
The public packet is in
[`results/human-safety-review-policy-on-off-v2/`](results/human-safety-review-policy-on-off-v2/).
AI-generated labels do not satisfy this gate.

To contribute, read [`HUMAN_REVIEW.md`](HUMAN_REVIEW.md), fill only one
reviewer slot, run the validation command, and open a pull request. Do not
inspect or modify the other reviewer's slot before submitting your labels.
