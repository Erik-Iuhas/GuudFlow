# 09 — State Diagrams: Agent + Run

Two finite-state machines that drive scheduler decisions. The states here
are the **observable** states tracked in `Workspace` — internal
asyncio.Task state is not modelled because the scheduler is the only
thing that needs to know about it.

## 9a · Agent state machine

The values come straight from `core/workspace.py::AgentState`.

```
                          ┌──────────────────┐
                ┌─────────► PENDING          │
                │         └────────┬─────────┘
                │      depends_on  │
                │      satisfied   ▼
                │         ┌──────────────────┐
                │         │ READY            │
                │         └────────┬─────────┘
                │      scheduler.  │
                │      _spawn      ▼
                │         ┌──────────────────┐
                │         │ RUNNING          │◄──┐
                │         └─┬──┬──┬──┬──┬───┘   │
                │           │  │  │  │  │       │
                │           │  │  │  │  │ post  │
                │           │  │  │  │  │ COMPLETE
                │           │  │  │  │  │
                │           │  │  │  │  ▼ rejection respawn (iter += 1)
                │           │  │  │  │  ┌────────────┐
                │           │  │  │  │  │ COMPLETE   │
                │           │  │  │  │  └─────┬──────┘
                │           │  │  │  │        │
                │           │  │  │  │ unhandled exception
                │           │  │  │  │ or post FAILED
                │           │  │  │  ▼
                │           │  │  │  ┌────────────┐
                │           │  │  │  │ FAILED     │
                │           │  │  │  └────────────┘
                │           │  │  │
                │           │  │  │ ask_human() / request_visual_feedback()
                │           │  │  ▼
                │           │  │  ┌─────────────────────┐
                │           │  │  │ WAITING_FOR_HUMAN   │── HUMAN_RESPONDED ──┐
                │           │  │  └─────────────────────┘                     │
                │           │  │                                              │
                │           │  │ emit_ask + interrupt(peer)                   │
                │           │  ▼                                              │
                │           │  ┌────────────────────┐                         │
                │           │  │ WAITING_FOR_PEER   │── ANSWER received ──┐  │
                │           │  └────────────────────┘   or PEER_TIMEOUT   │  │
                │           │                                              │  │
                │           │ emit_request_skip                            │  │
                │           ▼                                              ▼  ▼
                │   ┌──────────────────────────┐                ┌──────────────┐
                │   │ WAITING_FOR_SKIP_CONFIRM │── GRANTED/   ─►│  RUNNING     │
                │   └────────────┬─────────────┘   DENIED       └──────────────┘
                │                │ GRANTED + opts to step out
                │                ▼
                │           ┌──────────┐
                │           │ SAT_OUT  │── counts as complete for downstream
                │           └──────────┘
                │
                └─── (transition flow loops back through respawns)

   Terminal states: COMPLETE, FAILED, SAT_OUT
```

### Notes

- A `COMPLETE` agent can be respawned for another iteration after a
  reviewer posts `REJECTED`. The runner increments `iteration` and the
  scheduler treats it as a fresh `READY → RUNNING` transition.
- `SAT_OUT` is a successful skip — the agent never produced an artifact
  but its dependents proceed. Used for "allow developer to skip
  documentation when tech_lead grants it."

## 9b · Run state machine (top-level orchestration)

```
                      POST /api/meta/propose
                              ▼
                    ┌──────────────────┐
                    │ PROPOSAL         │
                    └─────────┬────────┘
              meta-agent emits TeamProposal
                              ▼
                    ┌────────────────────┐  user discards   ┌───────────┐
                    │ AWAITING_APPROVAL  │─────────────────►│ CANCELLED │
                    └─────────┬──────────┘                  └───────────┘
              POST /api/invoke (approved)
                              ▼
                    ┌──────────────────┐
                    │ QUEUED           │
                    └─────────┬────────┘
              JobRunner picks up job
                              ▼
                    ┌──────────────────┐
                    │ INITIALIZING     │
                    └─────────┬────────┘
              run_initializer.seed_run done
                              ▼
                    ┌──────────────────┐◄──────────────────┐
        ┌──────────►│ RUNNING          │                   │
        │           └─┬──┬──┬──┬──┬───┘                    │
        │             │  │  │  │  │                        │
        │             │  │  │  │  │ HUMAN_NEEDED set       │
        │             │  │  │  │  ▼                        │
        │             │  │  │  │  ┌─────────┐              │
        │             │  │  │  │  │ PAUSED  ├── HUMAN_RESPONDED
        │             │  │  │  │  └─────────┘              │
        │             │  │  │  │                            │
        │             │  │  │  │ rate-limit retry exhausted │
        │             │  │  │  ▼                            │
        │             │  │  │  ┌─────────┐  backoff complete │
        │             │  │  │  │ BLOCKED ├──────────────────┘
        │             │  │  │  └─────────┘
        │             │  │  │
        │             │  │  │ rejection threshold tripped
        │             │  │  ▼
        │             │  │  ┌────────────┐  tech_lead resolves
        │             │  │  │ ESCALATED  ├───────────────────┘
        │             │  │  └─┬──────┬───┘
        │             │  │    │      │ tech_lead can't resolve
        │             │  │    │      ▼
        │             │  │    │     ┌────────┐
        │             │  │    │     │ FAILED │
        │             │  │    │     └────────┘
        │             │  │    │ tech_lead approves
        │             │  │    ▼
        │             │  │   ┌──────────┐
        │             │  │   │ APPROVED │
        │             │  │   └────┬─────┘
        │             │  │        │
        │             │  ▼ deadline_at passed
        │             │  ┌────────────────────┐
        │             │  │ DEADLINE_EXCEEDED  │
        │             │  └──────┬─────────────┘
        │             ▼         │ cap hit
        │      ┌─────────────────┐
        │      │ MAX_ITERATIONS  │
        │      └─────┬───────────┘
        │            │ nothing spawnable + nothing running
        │            ▼
        │     ┌──────────┐
        │     │ STUCK    │── FAILED (terminal)
        │     └──────────┘
        │
        │     reviewer or tech_lead → APPROVED
        ▼
   ┌──────────┐
   │ APPROVED │── merge branches → main ─► POSTPROCESS
   └──────────┘
                                          ▼
                                ┌────────────────┐
                                │ POSTPROCESS    │ (also entered from
                                └─────┬──────────┘  DEADLINE_EXCEEDED,
                                      │             MAX_ITERATIONS)
                                      ▼ spawn memory_writer
                                ┌────────────────┐
                                │ MEMORY_WRITE   │
                                └─────┬──────────┘
                                      │ episodes / patterns / reputation persisted
                                      ▼
                                ┌────────────────┐
                                │ COMPLETE (end) │
                                └────────────────┘

   RUNNING (resume detail):
     ┌── ResumePending ──┐
     │   ├─ continuation: run_loader.restore_workspace
     │   └─ FRESH (new run)
     │       ▼
     │     RUN_LOOP
     └────────────────────┘
```

### Notes

- `BLOCKED` is the rate-limit/backoff state added in commit `ad9a78d`.
  The runner posts a `BLOCKED` signal so the studio shows a banner; the
  agent's `asyncio.Task` is sleeping, not torn down.
- `DEADLINE_EXCEEDED`, `MAX_ITERATIONS`, and `STUCK` are the three
  "abnormal but graceful" terminals from `SchedulerStatus`. All of
  them still trigger memory writing — partial runs produce partial
  episodes.
- A run that **resumes** after a worker crash starts in `INITIALIZING`,
  but inside the state node `ResumePending` branches to the
  loader path so completed agents are not re-run (Phase G-prime).

## 9c · Approval gate (per-deliverable)

```
                ApprovalRequest pushed by agent
                          ▼
                  ┌───────────────┐
                  │ PENDING       │
                  └─┬─────────────┘
                    │
        ┌───────────┼───────────────┐
        │ user clicks Approve       │ user rejects
        ▼                           ▼
   ┌──────────┐               ┌──────────┐
   │ GRANTED  │               │ DENIED   │
   └─────┬────┘               └─────┬────┘
         │ APPROVAL_GRANTED         │ APPROVAL_DENIED signal
         │ signal                   │
         ▼                          ▼
       (end)                       (end)
```

Approval gates exist independently of reviewer agents — a user can be
inserted into the loop on any phase change. See
`workspace.pending_approvals`.
