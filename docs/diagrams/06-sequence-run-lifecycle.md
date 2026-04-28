# 06 — Sequence: Run Lifecycle (Happy Path)

From "user submits a goal" through "deliverable bundle ready", with the
critical control points called out. The example uses a 3-agent team:
`requirements_writer → developer → code_reviewer` with `tech_lead` armed
for escalation.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Studio
    participant API as FastAPI
    participant Store as Storage<br/>(ProjectStore / RunStore)
    participant Worker as JobRunner
    participant Runner as AgentRunner
    participant Init as run_initializer
    participant WS as Workspace
    participant Sched as Scheduler
    participant Meta as MetaAgent
    participant Reqs as requirements_writer
    participant Dev as developer
    participant Rev as code_reviewer
    participant Mem as memory_writer
    participant LLM as LLMClient

    User->>Studio: describe goal + inputs
    Studio->>API: POST /api/meta/propose
    API->>Meta: propose(inputs, persona roster, archetype catalog)
    Meta->>LLM: structured proposal call
    LLM-->>Meta: TeamProposal
    Meta-->>API: ProjectConfig draft
    API-->>Studio: proposal + cost estimate
    User->>Studio: approve / edit team
    Studio->>API: POST /api/invoke (ProjectConfig + bearer)
    API->>Store: persist project + create run
    API->>Worker: enqueue Job
    API-->>Studio: 202 + run_id

    Worker->>Init: seed_run(run_root, source_repo, inputs)
    Init->>WS: build Workspace + FileWorkspace + GitWorkspace
    Worker->>Runner: AgentRunner(cfg, workspace)
    Runner->>WS: hire personas (load profiles + memory)
    Runner->>Sched: Scheduler(specs, workspace)

    par live UI
      Studio->>API: WS /ws/runs/{id}
      API->>WS: subscribe
    and run loop
      Sched->>Reqs: spawn (no deps)
      Reqs->>WS: post STARTED
      Reqs->>LLM: effective_system_prompt + tools
      LLM-->>Reqs: tool calls (fs-write, board-message...)
      Reqs->>WS: emit_commit · GIT_COMMIT
      Reqs->>WS: post COMPLETE
      Sched->>Dev: spawn (deps satisfied)
      Dev->>LLM: reasoning + tool loop
      Dev->>WS: emit_commit · multiple GIT_COMMITs
      Dev->>WS: post COMPLETE
      Sched->>Rev: spawn
      Rev->>LLM: file-by-file review
      Rev->>WS: emit_verify(PASS) + APPROVED
    end

    Runner->>WS: merge agent/* branches → main
    Runner->>Mem: spawn memory_writer (post-run)
    Mem->>WS: synthesize Episode + update Pattern confidence
    Mem-->>Runner: persona files updated on disk
    Runner->>WS: post run COMPLETE
    API->>Store: persist final state + tags
    Studio-->>User: live board + deliverable download link
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
