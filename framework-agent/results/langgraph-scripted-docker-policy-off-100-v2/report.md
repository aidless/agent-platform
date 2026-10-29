# LangGraph agent reliability run

- Planner: `ScriptedPlanner`
- Served models: `scripted` × 100
- Traces: 100
- Task success: 50.0%

| Metric | Result |
|---|---:|
| Unsafe tasks blocked | 0/25 |
| Safety violations | 25 |
| Over-refusal rate | 0.0% |
| Fault recovery | 30/30 |
| p50 latency | 311.1 ms |
| p95 latency | 2367.0 ms |
| Model calls | 120 |
| Cost | $0.000000 |

**Quality gate: HOLD**

## Fault recovery

- `tool_timeout`: 10/10 recovered
- `model_rate_limit`: 10/10 recovered
- `malformed_model_json`: 10/10 recovered

## What this proves

- A real LangGraph state machine routes policy, planning, validation, tool execution, recovery, and finalization.
- All 100 traces record policy decisions, model/tool events, recovery attempts, latency, and final outcome.
- Three independent failure classes are injected and measured: planner rate limit, malformed planner JSON, and first-attempt tool timeout.

## What this does not prove

- The 100 tasks are synthetic keyword-driven scenarios, not a public agent benchmark.
- Tool calls execute in short-lived Docker containers with network disabled, a read-only root, dropped capabilities, and bounded memory/PIDs; they are adapters, not external production systems.
- Safety is defensive workflow behavior under these fixtures, not proof of model alignment.
- Over-refusal is measured only on the 50 explicit benign tasks.
- Task success checks the expected outcome and selected benign tool, but does not assert exact tool arguments.
- Latency is a single local/API run and not a production SLO.
