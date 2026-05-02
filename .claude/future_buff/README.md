# future_buff — Strategic items not in the active sprint queue

These are tracks that came up during strategic discussion but do **not** belong on the immediate execution queue. They're captured here so they're not lost when the active focus shifts back to cost-reduction work.

**Active execution queue:** `.claude/plan_agent_buff/`
**This folder:** medium-/long-term architectural items + lessons. Pick from here when:
- The active queue is clear, OR
- Something here becomes blocking (e.g. cost-ceiling becomes urgent if a runaway run happens), OR
- We're explicitly choosing to invest in foundation over feature work.

## Index

| File | What it captures | When to consider |
|---|---|---|
| `cost_ceiling.md` | Hard $-cap per run. `estimated_cost_usd` already tracked; this enforces. | Becomes urgent the first time a real run blows past expected budget. |
| `runner_refactor.md` | Extract subsystems out of `core/runner.py` (~4150 lines, becoming a god-class). | Becomes urgent when runner edits start regularly causing unrelated test failures. |
| `production_sandbox.md` | Stage 9 from the original CLAUDE.md plan. Container with `network_mode: none`, resource limits, FileWorkspace as only host bridge. | Becomes urgent before running this against any untrusted repo or in any non-toy production context. |
| `functional_verification.md` | Automated "does the produced game run / do the tests pass" gate. Deprioritised because manual quality verification has been excellent — but worth capturing for regression safety later. | Becomes urgent when a refactor change inadvertently hurts output quality (we won't catch it without this). |
| `recalibration_notes.md` | Lesson from May 2 — token efficiency metrics don't measure output quality. Don't repeat the mistake. | Re-read before recommending any "we need to measure quality" track. |

## Framing — why these are "future"

The current focus is **cost reduction at high quality**. The system is already producing professional-grade software (verified manually on `run_70dc78f9bb`); the open work is making it cheaper to do so. Architectural debt cleanup, sandbox hardening, and automated quality gates all add value but don't directly serve the cost-reduction goal.

When cost-reduction work plateaus or these items become blocking, pull from this folder.
