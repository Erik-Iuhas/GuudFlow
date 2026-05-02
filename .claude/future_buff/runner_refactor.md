# Runner Refactor — Extract Subsystems out of `core/runner.py`

**Type:** Architectural debt cleanup. Pure refactor (no behaviour change).
**Token budget when picked up:** ~400–600k. Heavy because of test surface that touches the runner.
**Depends on:** Nothing hard. Best done during a slow week.
**When to elevate:** when runner edits start regularly causing unrelated test failures, or when adding a new feature in the runner becomes painful (you find yourself reading 500 lines to understand context).

## The problem

`core/runner.py` is **~4150 lines** and growing. Original intent was a thin orchestrator. It's accumulated:

- Persister wiring (board, tasks, artifacts, iteration_metrics, context_log)
- RBAC enforcement intersection points
- Escalation logic (handler dispatch, threshold tracking, REJECTED redispatch)
- Stage-3-style scheduler hooks
- Signal subscribers (rejection redispatch, peer-rework redispatch when it lands, others)
- Repo map rebuild logic (Track 1d's dirty flag)
- Tool registry building (`_build_local_registry`, `_instantiate_local_tools`)
- Continuation run loading
- Per-agent injection (LLM, model, broker, manifest, workspace, current_task, etc.)
- Auto-commit + git operations
- Quip dispatch
- Approval flows

This is the canonical god-class shape. Every new feature touches the runner. Every test on the runner takes longer to write because there's so much surrounding context.

## Proposed extractions

| New module | Lifts | Why |
|---|---|---|
| `core/persistence/run_persisters.py` | The 4–5 persister builder methods + their attachment in `__init__` | Pure I/O concern; isolates JSONL/JSON write decisions |
| `core/escalation.py` | `_dispatch_escalation`, `_on_signal_for_rejection_redispatch`, threshold tracking, `_maybe_escalate` | Discrete subsystem with its own state and event semantics |
| `core/refgraph_lifecycle.py` | `_repo_map_dirty` flag, `_rebuild_repo_map_if_dirty`, the GIT_COMMIT subscriber that sets the flag | Track 1d added these; they're a coherent slice |
| `core/runner_subscribers.py` | The signal-subscription registration that the runner does at startup | Currently scattered through `__init__` |
| `core/tool_registry.py` | `_build_local_registry` + `_instantiate_local_tools` + `LOCAL_TOOL_CLASSES` (currently in runner.py) + manifest builder construction | Tool plumbing is a discrete domain; runner shouldn't own the catalog |
| `core/agent_injection.py` | The agent-runtime-injection blocks (lines ~935 + ~4150 — the two spawn paths that set `agent._llm`, `agent._model`, `agent._workspace`, etc.) | Currently duplicated; should be one helper called from both |

After: runner.py shrinks to ~1500 lines that orchestrate these subsystems. Each extracted module is independently testable.

## Migration approach

1. Extract **one module at a time**. Each extraction is its own commit. Tests pass after each.
2. For each extraction:
   - Move the methods to the new module (typically as free functions or a small class).
   - Replace the runner's method bodies with thin delegation calls.
   - Update tests to import from the new location where they previously imported from runner.
3. After all extractions land, runner.py should read top-to-bottom as the orchestration narrative — what fires when, who handles it.
4. **Do not change behavior.** The 1465 tests must all pass after every commit. This is a refactor, not a redesign.

## Why this is debt, not bug

The runner works correctly today. Every new feature has been adding ~50–200 lines and working. The cost of the debt:

- **Cognitive load**: a new developer touching the runner has to understand 4150 lines of intermingled concerns.
- **Test coupling**: `test_runner.py` is 1500+ lines and growing; every escalation-related change touches the same file as every persister change.
- **Merge conflict surface**: any two developers working in different parts of the runner will conflict on the `__init__` block.
- **Future extraction cost**: each line we add is one more line that has to move when we eventually extract.

It's not blocking. It's eroding.

## Risks

- **Big refactor on a working system.** Highest risk: a subtle behaviour change in one extraction breaks something the test suite doesn't cover. Mitigation: keep extractions small and incremental; bisect-friendly commits.
- **Test-import churn.** Many tests import from runner.py today. Each extraction adds an import update.
- **Plan drift.** Specs in `.claude/plan_agent_buff/` reference `core/runner.py` line numbers. They'll go stale after the refactor. Acceptable — re-anchor by symbol name when needed.

## Concrete signal that this should be picked up next

- A future track says "modify the runner's escalation handler" and you find yourself reading 200 lines of unrelated context first.
- A new test in `test_runner.py` takes >30 minutes to write because of fixture coupling.
- A pull request gets merge-conflict-reverted three times because two unrelated changes touched the runner's `__init__`.

Until then, accept the debt.
