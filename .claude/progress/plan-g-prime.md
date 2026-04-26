# Active plan — Phase G-prime — Run resume backend ✅ SHIPPED 2026-04-26

Status: planned. Pre-Phase-G chunk. Estimated ~1.5 days across 4
sub-chunks. Ships the backend so G's resume UI (G.3) is pure
frontend work.

## Decision context

Erik 2026-04-26: "the architecture supports looping from within
the team to make the product at a certain point. The user should
always be able to pick up from the latest state or review point
the agents were at if it is being managed with .git using tags
and what not locally."

Conceptually correct — git workspace + episodes + reputation
persist across runs. The gap: `board.json` is written ONLY at
end-of-run by `_persist_run` (worker/job_runner.py:400), so a
crashed mid-run loses its signal trail. Without the board,
restoring "which agents were complete, which iteration the
scheduler had reached" requires reverse-engineering from git
history — fragile.

G-prime closes that gap and adds the API surface to drive a
resume from outside.

## Goal

`POST /api/runs/{run_id}/resume` re-runs the run from the state
captured at last persistence — not from scratch. Studio's "Resume"
button (G.3) calls this; CLI / API consumers can call it directly.

---

## Chunk 0 — Incremental board persistence ✅ SHIPPED [44c40b6]

### What lands

`board.json` is rewritten after every signal post (debounced) so
a crashed run leaves a usable trail.

### Implementation

- `core/workspace.py` — `Workspace` already exposes a subscriber
  pattern (`_subscribers` from Phase C.2). Add an internal
  "persistence subscriber" that the runner attaches with a
  `data_root` / `run_id` so writes go to
  `data/runs/{run_id}/board.json`.
- Debounce: write at most once per second OR after each signal,
  whichever fires last. Use `asyncio.create_task` with a small
  scheduler-friendly task that flushes pending writes. Final
  flush on runner exit.
- Trade-off: per-signal write is simpler and disk-cheap (board
  files stay small). Pick per-signal for the first cut; debounce
  is a follow-up if profiling shows it's needed.

### Files

- `core/workspace.py` — add a hook (`set_board_persister(callback)`)
  that the runner attaches at construction time.
- `core/runner.py` — wire the persister using `_persist_run`'s
  existing serialisation logic (lift `_signals_to_payload`
  helper out of `_persist_run` so it's reusable).
- `worker/job_runner.py` — pass `data_root` + `run_id` so the
  runner can build the persister.

### Tests

- `tests/core/test_workspace_persistence.py` (new): post 3 signals
  → board.json reflects all 3 after each. Force a "crash"
  (raise inside the runner mid-loop), verify partial board on
  disk.
- Existing `test_runner_e2e.py` and `test_full_journey.py` should
  stay green — final board still gets written.

Commit: `feat(workspace): incremental board persistence (Phase G-prime.0)`

---

## Chunk 1 — State restoration loader ✅ SHIPPED [fef6f1a]

### What lands

`core/run_loader.py:restore_workspace(run_root, project) -> Workspace`
reads `board.json` + the run's git workspace and reconstructs a
Workspace ready to drive a resumed scheduler.

### Reconstruction logic

From `board.json`:
- `workspace.board` ← persisted signals (parsed via
  `Signal.from_dict` or reverse of `_signals_to_payload`)
- `workspace.iteration` ← max iteration across signals
- `workspace.completed_agents` ← agents with a `COMPLETE` signal
- `workspace.agent_states` ← derive per-role state from the
  latest agent-scoped signal (STARTED / COMPLETE / FAILED /
  BLOCKED)
- `workspace.artifacts` ← each role's last `Artifact` (need to
  decide where artifacts persist; today they live in-memory only.
  **Sub-chunk: persist artifacts to `data/runs/{id}/artifacts.json`
  alongside board.json** — same persister hook.)

From `meta.json`:
- `workspace.goal` (already there for new runs; just re-read)

From `data/runs/{id}/workspace/working/` (git):
- Already on disk; runner re-uses it without changes.

### Files

- `core/run_loader.py` (new) — `restore_workspace`,
  `restore_artifacts`, `derive_agent_states_from_board`
- `core/workspace.py` — add `Workspace.from_persisted` constructor
  if cleaner than free-function approach
- `core/runner.py` — minor: ensure artifact-persistence hook fires
  alongside board-persistence

### Tests

- `tests/core/test_run_loader.py` (new):
  - Empty board → fresh-equivalent workspace
  - Board with STARTED for two agents → both in agent_states
  - Board with COMPLETE for one agent + STARTED for another →
    completed_agents has the first; agent_states reflects both
  - Board at iteration 3 → `workspace.iteration == 3`
  - Round-trip: build a workspace, persist, restore, assert equal

Commit: `feat(core): run state restoration loader (Phase G-prime.1)`

---

## Chunk 2 — Resume endpoint ✅ SHIPPED [4818135]

### What lands

`POST /api/runs/{run_id}/resume` re-enqueues a Job for the existing
run_id with `payload.resume = True`.

### Behaviour

- 200/202 + new `job_id` (run_id is the existing one)
- 404 if run doesn't exist (`data/runs/{run_id}/` missing)
- 409 if a non-terminal job exists for this run in the queue
  (running / queued)
- 400 if the run's status is `complete` (no point resuming a
  successful run)

### Files

- `api/routes/runs.py` — new endpoint, bearer-auth gated
- `tests/api/test_runs.py` — extension

### Tests

- Happy: run exists in `failed` state → resume → 202 + job
  enqueued
- 404: unknown run
- 409: run is currently running (active job in queue)
- 400: completed run — resume rejected
- Auth: missing bearer → 401

Commit: `feat(api): POST /runs/{id}/resume (Phase G-prime.2)`

---

## Chunk 3 — Worker resume branch ✅ SHIPPED 2026-04-26

### What lands

When the worker dequeues a job with `payload.resume == True`, it
takes a different code path: load the project, restore the
workspace from disk via `run_loader.restore_workspace`, and pass
the restored workspace into `AgentRunner` instead of letting the
runner seed fresh state.

### Implementation

- `AgentRunner.__init__` — add `restored_workspace: Workspace | None
  = None`. When set, skip the fresh `Workspace(...)` construction
  and use the restored one (and skip `_initialize_run` since
  workspace files already exist).
- `worker/job_runner.py` — `run_one` checks
  `job.payload.get("resume")`. If true:
  - Load project (existing path)
  - Call `run_loader.restore_workspace(run_root, project)`
  - Build runner with `restored_workspace=ws`
  - Skip `_initialize_run` (the workspace dir already exists)
  - Drive the runner normally — the scheduler will see
    completed_agents + iteration and resume from there

### Tests

- `tests/worker/test_job_runner_resume.py` (new):
  - Run a job to completion of agent A but force agent B to fail.
    Resume → second worker run completes agent B without re-running
    agent A.
  - Crash mid-iteration: kill the worker after a board signal
    persists. Resume → workspace state matches what was persisted;
    scheduler picks up from the right iteration.
- Existing `test_full_journey.py` stays green (production runs
  don't carry `resume: true`).

Commit: `feat(worker): resume branch reads restored workspace (Phase G-prime.3)`

---

## What this does NOT include

- **Auto-tag review points.** Layered on top in **Phase G.5** —
  runner tags git at every APPROVED + iteration boundary; rewind
  UI lets user pick a tag.
- **Rewind UI / "resume from a specific tag" semantics.** Different
  problem from "resume from latest." Phase G.5.
- **Resume button + run-history list in studio.** Phase G.3 — pure
  frontend on top of this backend.
- **Crash recovery for jobs in flight at API restart.** If the API
  + worker both crash, in-flight jobs lose their queue slot. Real
  fix needs Redis (Phase E).
- **Deletion of a "stuck" run.** Today there's no DELETE on a run.
  Out of scope — separate cleanup phase.

## Verification at end of phase

```bash
python -m pytest                                  # full suite stays green
cd studio && npm run typecheck && npm run build  # frontend clean
```

Suite expected: ~860+ passed (852 + ~10-15 new).

The acceptance test for the phase as a whole: a run that fails
mid-way (agent B raises) can be resumed via the API endpoint,
and the second pass completes successfully with agent A NOT
re-running. That single end-to-end is what proves the four
chunks compose.
