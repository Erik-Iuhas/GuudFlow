# 09 — State Diagrams: Agent + Run

Two finite-state machines that drive scheduler decisions. The states here
are the **observable** states tracked in `Workspace` — internal
asyncio.Task state is not modelled because the scheduler is the only
thing that needs to know about it.

## 9a · Agent state machine

The values come straight from `core/workspace.py::AgentState`.

```mermaid
stateDiagram-v2
    [*] --> PENDING: configured

    PENDING --> READY: depends_on satisfied
    READY --> RUNNING: scheduler._spawn

    RUNNING --> COMPLETE: post COMPLETE
    RUNNING --> FAILED: unhandled exception<br/>or post FAILED
    RUNNING --> WAITING_FOR_HUMAN: ask_human()<br/>or request_visual_feedback()
    RUNNING --> WAITING_FOR_PEER: emit_ask + interrupt(peer)
    RUNNING --> WAITING_FOR_SKIP_CONFIRM: emit_request_skip

    WAITING_FOR_HUMAN --> RUNNING: HUMAN_RESPONDED
    WAITING_FOR_PEER --> RUNNING: ANSWER received<br/>or PEER_TIMEOUT
    WAITING_FOR_SKIP_CONFIRM --> RUNNING: SKIP_GRANTED / SKIP_DENIED
    WAITING_FOR_SKIP_CONFIRM --> SAT_OUT: SKIP_GRANTED + opts to step out

    COMPLETE --> RUNNING: rejection respawn (iter += 1)
    SAT_OUT --> [*]: counts as complete for downstream
    COMPLETE --> [*]
    FAILED --> [*]
```

### Notes

- A `COMPLETE` agent can be respawned for another iteration after a
  reviewer posts `REJECTED`. The runner increments `iteration` and the
  scheduler treats it as a fresh `READY → RUNNING` transition.
- `SAT_OUT` is a successful skip — the agent never produced an artifact
  but its dependents proceed. Used for "allow developer to skip
  documentation when tech_lead grants it."

## 9b · Run state machine (top-level orchestration)

```mermaid
stateDiagram-v2
    [*] --> PROPOSAL: POST /api/meta/propose

    PROPOSAL --> AWAITING_APPROVAL: meta-agent emits TeamProposal
    AWAITING_APPROVAL --> QUEUED: POST /api/invoke (approved)
    AWAITING_APPROVAL --> CANCELLED: user discards proposal

    QUEUED --> INITIALIZING: JobRunner picks up job
    INITIALIZING --> RUNNING: run_initializer.seed_run done

    RUNNING --> PAUSED: HUMAN_NEEDED set
    PAUSED --> RUNNING: HUMAN_RESPONDED
    RUNNING --> BLOCKED: rate-limit retry exhausted
    BLOCKED --> RUNNING: backoff complete (BLOCKED signal cleared)

    RUNNING --> APPROVED: reviewer or tech_lead → APPROVED
    RUNNING --> ESCALATED: rejection threshold tripped
    ESCALATED --> RUNNING: tech_lead resolves
    ESCALATED --> APPROVED: tech_lead approves
    ESCALATED --> FAILED: tech_lead can't resolve

    RUNNING --> DEADLINE_EXCEEDED: workspace.deadline_at passed
    RUNNING --> MAX_ITERATIONS: cap hit
    RUNNING --> STUCK: nothing spawnable + nothing running

    APPROVED --> POSTPROCESS: merge branches → main
    POSTPROCESS --> MEMORY_WRITE: spawn memory_writer
    MEMORY_WRITE --> COMPLETE: episodes / patterns / reputation persisted

    DEADLINE_EXCEEDED --> POSTPROCESS
    MAX_ITERATIONS --> POSTPROCESS
    STUCK --> FAILED
    FAILED --> [*]
    COMPLETE --> [*]
    CANCELLED --> [*]

    state RUNNING {
        [*] --> ResumePending
        ResumePending --> RUN_LOOP: run_loader.restore_workspace<br/>(if continuation)
        ResumePending --> FRESH: new run
        FRESH --> RUN_LOOP
        RUN_LOOP --> [*]
    }
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

```mermaid
stateDiagram-v2
    [*] --> PENDING: ApprovalRequest pushed by agent
    PENDING --> GRANTED: user clicks Approve in studio
    PENDING --> DENIED: user rejects with reason
    GRANTED --> [*]: APPROVAL_GRANTED signal
    DENIED --> [*]: APPROVAL_DENIED signal
```

Approval gates exist independently of reviewer agents — a user can be
inserted into the loop on any phase change. See
`workspace.pending_approvals`.
