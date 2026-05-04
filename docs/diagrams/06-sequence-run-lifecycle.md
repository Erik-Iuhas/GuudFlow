# 06 — Sequence: Run Lifecycle (Happy Path)

From "user submits a goal" through "deliverable bundle ready", with the
critical control points called out. The example uses a 3-agent team:
`requirements_writer → developer → code_reviewer` with `tech_lead` armed
for escalation.

```
User  Studio  API   Store  Worker  Init   WS    Sched  Meta  Reqs  Dev  Rev  Mem  LLM
 │      │      │      │      │      │      │      │     │     │    │    │    │    │
 │ goal │      │      │      │      │      │      │     │     │    │    │    │    │
 ├─────►│      │      │      │      │      │      │     │     │    │    │    │    │
 │      │ POST /api/meta/propose                                                    │
 │      ├─────►│      │      │      │      │      │     │     │    │    │    │    │
 │      │      │ propose(inputs, persona roster, archetype catalog)                 │
 │      │      ├─────────────────────────────────────────►│     │    │    │    │    │
 │      │      │                                          │ structured proposal call│
 │      │      │                                          ├──────────────────────►│ │
 │      │      │                                          │ TeamProposal           │
 │      │      │                                          ◄──────────────────────┤ │
 │      │      │ ProjectConfig draft                                              │
 │      │      ◄─────────────────────────────────────────┤                          │
 │      │ proposal + cost estimate                                                  │
 │      ◄──────┤                                                                    │
 │ approve / edit team                                                              │
 ├─────►│      │                                                                    │
 │      │ POST /api/invoke (ProjectConfig + bearer)                                 │
 │      ├─────►│                                                                    │
 │      │      │ persist project + create run                                       │
 │      │      ├─────►│                                                             │
 │      │      │ enqueue Job                                                        │
 │      │      ├─────────────►│                                                     │
 │      │ 202 + run_id                                                              │
 │      ◄──────┤                                                                    │
 │      │      │              │ seed_run(run_root, source_repo, inputs)             │
 │      │      │              ├─────►│                                              │
 │      │      │              │      │ build Workspace + FileWorkspace + GitWorkspace
 │      │      │              │      ├─────►│                                       │
 │      │      │              │ AgentRunner(cfg, workspace)                         │
 │      │      │              ├──────────────────────────────►(runner)              │
 │      │      │              │  hire personas (load profiles + memory)             │
 │      │      │              │              │ Scheduler(specs, workspace)          │
 │      │      │              │              │      ├─────►│                        │
 │      │      │                                                                    │
 │── live UI ──┐                                                                    │
 │      │ WS /ws/runs/{id}                                                          │
 │      ├─────►│ subscribe ─►│                                                      │
 │── run loop ─┐                                                                    │
 │             │ spawn (no deps)                                                    │
 │             │       │      ├──────────────►│                                     │
 │             │       │      │ post STARTED  │                                     │
 │             │       │      │      ◄───────┤                                      │
 │             │ effective_system_prompt + tools                                    │
 │             │       │      │      ├──────────────────────►│                      │
 │             │ tool calls (fs-write, board-message, ...)                          │
 │             │       │      │      ◄──────────────────────┤                      │
 │             │ emit_commit · GIT_COMMIT                                           │
 │             │       │      │      │ post COMPLETE                                │
 │             │ spawn (deps satisfied)                                             │
 │             │       │      │      ├─────────────►│                               │
 │             │ reasoning + tool loop (Dev ↔ LLM)                                  │
 │             │ multiple GIT_COMMITs · post COMPLETE                               │
 │             │       │      │      ├──────────────────►│                          │
 │             │ file-by-file review (Rev ↔ LLM)                                    │
 │             │ emit_verify(PASS) + APPROVED                                       │
 │             │                                                                    │
 │ merge agent/* branches → main                                                    │
 │             │ spawn memory_writer (post-run)                                     │
 │             │       │      │      │     │     │     │     │     ├────────►       │
 │             │ synthesize Episode + update Pattern confidence                     │
 │             │ persona files updated on disk                                      │
 │             │ post run COMPLETE                                                  │
 │      │      │ persist final state + tags                                         │
 │      │      ├─────►│                                                             │
 │ live board + deliverable download link                                           │
 ◄─────┤      │                                                                     │
```

## Variants

- **Rejection loop** — `Rev` posts REJECTED → `Runner` re-spawns `Dev`
  with `iteration += 1`. After `N` rejections (default `2`), the runner
  posts ESCALATION; `tech_lead` wakes up event-driven (no `depends_on`)
  and resolves.
- **Hard pause** — any agent calls `ask_human()`; this routes through
  `Scheduler.interrupt(role, "human", ...)`. New spawns are suspended
  but already-running agents finish their step. The runner posts
  `HUMAN_NEEDED`, the studio surfaces it, the user posts
  `HUMAN_RESPONDED`, the scheduler `resume()`s.
- **Resume after crash** — on Worker boot, `run_loader.restore_workspace`
  rehydrates `Workspace` from `data/runs/{run_id}/board.jsonl` and
  artifacts; the scheduler skips agents already in `completed_agents`.
- **Training mode** — `RubricScorer` replaces the human reviewer; runs
  feed `TournamentRegistry` instead of merging to main.

## Concurrency points

The diagram shows agents serially for clarity. In reality:

- **fan-out** — when `developer` and `architect` both depend only on
  `requirements_writer`, they are spawned concurrently by the same
  `_spawn` pass and run in their own `asyncio.Task`.
- **board fan-in** — every signal posted while agents run notifies
  WebSocket subscribers (`API` → `Studio`) live, in addition to the
  in-memory subscribers (`QuipEngine`, `RunRegistry`).
