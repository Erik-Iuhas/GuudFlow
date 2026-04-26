# Stage 3 — Scheduler ⚠️ GATE

**Budget:** 200-350k tokens
**Status:** done — GATE CLEARED (2026-04-25)
**Exit criteria (CLAUDE.md):** ALL 6 scheduler tests pass.
**Stop condition:** over 450k tokens OR any test still red — do not proceed under any circumstances.

DESIGN sections: §3.3, §4, plus CLAUDE.md "The Scheduler is the Critical Path".

## Hard rule
**No Stage 4+ work begins until all 6 tests below are green and `grep` confirms no `asyncio.gather` outside `core/scheduler.py`.**

## Files
- [x] `core/scheduler.py` ✅ — single reusable class
  - [x] `AgentSpec` (role/runner/depends_on); `SchedulerStatus` (COMPLETE/MAX_ITERATIONS/STUCK); `SchedulerResult`
  - [x] dependency resolver `_ready_to_spawn()` — `depends_on ⊆ completed_agents` and not failed/already-running/over-cap
  - [x] honours `pending_human` — halts new spawns; running tasks unaffected
  - [x] `notify()` external API for waking on HUMAN_RESPONDED
  - [x] `_wrap_runner` always wakes the loop on exit (success/failure)
  - [x] `_iterations_exceeded()` — any role spawned at the cap without completing → MAX_ITERATIONS
  - [x] `_cancel_remaining()` on shutdown — no orphan tasks
  - [x] same class used at pipeline scope and group scope; `scope` is informational
  - [x] no `asyncio.gather` anywhere; no `asyncio.create_task` for agents outside this file

- [x] `tests/core/test_scheduler.py` ✅ — 6 patterns + contract test (7/7 green)
  - [x] sequential chain A → B → C — start order matches dep order
  - [x] parallel fan-out A → (B + C) — B and C start within tight window after A completes
  - [x] join (A + B) → C — C's start ≥ max(A.complete, B.complete)
  - [x] human pause: B pauses; C (deps on A) becomes eligible during pause but does NOT spawn until HUMAN_RESPONDED + notify()
  - [x] group completion: a group role's COMPLETE unblocks pipeline-level dependent (same code path as sequential by design — DESIGN §4b "no special cases")
  - [x] max iterations cutoff halts cleanly with status=MAX_ITERATIONS, no orphan tasks
  - [x] no-`asyncio.gather` contract — repo scan finds zero offenders outside this file

## Verification commands
```bash
python -m pytest tests/core/test_scheduler.py -v   # all 6 green
grep -r "asyncio.gather" --include="*.py" .         # only matches inside core/scheduler.py
```
