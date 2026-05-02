# Cost Ceiling Enforcement

**Type:** Operational safeguard.
**Token budget when picked up:** ~80–150k.
**Depends on:** Track 3d.5 (`estimated_cost_usd` field) — currently in the slimmed Track 3d. Could ship `estimated_cost_usd` as part of this if 3d.5 hasn't landed yet.
**When to elevate:** first time a real run unexpectedly costs >2× what was budgeted.

## What

Add `max_dollars_per_run: float | None` to `ProjectConfig`. After each agent iteration, sum `estimated_cost_usd` across `workspace.iteration_metrics`. If the sum approaches or exceeds the cap:

- **80% of cap:** post a `BUDGET_WARNING` signal to the board so the user can see it during the run.
- **100% of cap:** halt the run cleanly. Same exit path as `DEADLINE_EXCEEDED` (per `core/scheduler.py`'s SchedulerStatus). Final state: `complete` with a `cost_exceeded` reason captured in the artifact.

## Why this matters

For toy projects, $2/iteration is fine. For real projects, agents touching a 500-file codebase could cost $80–250 per iteration. Without an enforced ceiling:

- A misconfigured loop could burn $50 before anyone notices.
- A user trying out the system on their real codebase has no protection against runaway cost.
- The `iteration_metrics.jsonl` cost field is informational only — it tells you what *did* happen, not what *would* happen if a particular run continues.

The `estimated_cost_usd` field exists (or will). This track turns it from telemetry into a guardrail.

## Files (when picked up)

- `core/runner.py` — read accumulated cost after each iteration boundary; trigger halt.
- `core/workspace.py` — `ProjectConfig` gets `max_dollars_per_run`.
- `core/signals.py` — new `BUDGET_WARNING` signal type.
- `core/scheduler.py` — new `SchedulerStatus.COST_EXCEEDED` (or reuse DEADLINE).
- Studio UI — surface budget consumed / cap on the live run view.
- Tests — runner halts at cap, doesn't halt at 79%, halts at 100%.

## Risks

- **Estimate accuracy.** `estimated_cost_usd` uses the pricing table from Track 3d.5 — if vendor prices change and the table is stale, the cap is wrong by that delta. Acceptable; document the staleness window.
- **Mid-iteration exceed.** Cost is checked at iteration boundaries. A single very expensive iteration could blow past cap before the check fires. Mitigation: check after every LLM call, not just iteration boundary, for the agent that last started. Slight overhead but catches single-call blowouts.

## Out of scope

- Per-agent cost caps. Run-level only.
- Per-tool cost caps.
- Soft pause for user approval at 80%. Just a warning.

---

**Why deferred:** Real runs have stayed within expected budget. When that changes, this becomes urgent quickly.
