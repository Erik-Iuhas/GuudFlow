# 07 — Sequence: Handshake Protocol + Escalation

Two flows that show how the M1 inter-agent protocol and the multi-stage
review architecture cooperate:

1. **ASK / ANSWER handshake with peer-timeout** (asynchronous request to
   another agent without blocking the scheduler).
2. **Rejection escalation** to the on-call `tech_lead` after the
   threshold trips.

## 7a · ASK / ANSWER with timeout

```mermaid
sequenceDiagram
    autonumber
    participant Dev as developer
    participant Sched as Scheduler
    participant WS as Workspace
    participant Reqs as requirements_writer
    participant Watch as deadline watcher

    Dev->>Dev: needs clarification on auth requirement
    Dev->>WS: emit_ask(ask_id, target=requirements_writer, question, deadline)
    Dev->>Sched: interrupt(role=developer, kind="peer", payload=WaitingForPeer)
    Note over Sched: developer parked.<br/>scheduler keeps running<br/>other unblocked agents.

    Sched->>Watch: arm timeout(ask_id, deadline_ts)

    alt requirements_writer answers in time
      Reqs->>WS: read_protocol([ASK])
      Reqs->>WS: emit_answer(ask_id, body)
      WS-->>Sched: ANSWER signal observed
      Sched->>Dev: resume(token, value=answer)
      Dev->>WS: continue work
    else deadline elapses
      Watch->>WS: post PEER_TIMEOUT(ask_id, reason)
      WS-->>Sched: PEER_TIMEOUT observed
      Sched->>Dev: resume(token, value=None)
      Dev->>WS: emit_request_skip(reason="PEER_UNRESPONSIVE")
    end
```

Rules from `core/scheduler.py` and `core/protocol.py`:

- An agent may have **at most one outstanding ASK** at a time
  (one-outstanding-ASK guard).
- Allowed-speaker transitions are enforced by the scheduler — only the
  ASK target may emit the matching ANSWER (AutoGen-style).
- `AGENT_MESSAGE` (the post-autonomy fix from commit `d2cdfb1`) is the
  *non-blocking* sibling: it does not park the sender; the recipient
  picks the message up at the start of their next turn.

## 7b · REQUEST_SKIP escalation to tech_lead

```mermaid
sequenceDiagram
    autonumber
    participant Dev as developer
    participant Sched as Scheduler
    participant WS as Workspace
    participant TL as tech_lead
    participant Watch as deadline watcher
    participant Human as user

    Dev->>WS: emit_request_skip(skip_id, reason, deadline)
    Dev->>Sched: interrupt(role=developer, kind="skip", payload=WaitingForSkipConfirm)
    Sched->>WS: notify tech_lead (event-driven, no depends_on)
    Sched->>Watch: arm timeout(skip_id)
    WS->>TL: spawn

    alt tech_lead grants skip
      TL->>WS: emit_skip_granted(skip_id, justification)
      Sched->>Dev: resume(token, value=GRANTED)
      Dev->>WS: continue with skip applied
    else tech_lead denies
      TL->>WS: emit_skip_denied(skip_id, reason)
      Sched->>Dev: resume(token, value=DENIED)
      Dev->>WS: re-attempt without skip
    else SKIP_TIMEOUT — escalate to human
      Watch->>WS: post SKIP_TIMEOUT(skip_id)
      Sched->>WS: post HUMAN_NEEDED(question, context)
      Human-->>WS: HUMAN_RESPONDED
      Sched->>Dev: resume
    end
```

## 7c · Review rejection escalation

```mermaid
sequenceDiagram
    autonumber
    participant Dev as developer
    participant Rev as code_reviewer
    participant WS as Workspace
    participant Runner as AgentRunner
    participant TL as tech_lead

    loop iteration n
      Dev->>WS: emit_commit(...) + COMPLETE
      Rev->>WS: read git-changed-files (capped diff)
      Rev->>WS: emit_verify(PASS|FAIL per file)
      alt all PASS
        Rev->>WS: APPROVED
        Runner->>WS: merge agent/developer → main
      else any FAIL
        Rev->>WS: REJECTED + detail
        Runner->>WS: increment rejection count
      end
    end

    Note over Runner: rejection threshold reached<br/>(default 2 — project config)

    Runner->>WS: post ESCALATION
    WS->>TL: tech_lead spawns event-driven
    TL->>WS: read prior REJECTED + commits
    TL->>WS: emit_action(target=developer, FIX|REVISIT|APPROVE)
    alt tech_lead approves
      TL->>WS: APPROVED
      Runner->>WS: force-merge to main
    else tech_lead requires fix
      TL->>WS: REJECTED + specific guidance
      Runner->>Dev: respawn iteration n+1
    end
```

## Why event-driven `tech_lead` and `IT` are not in `depends_on`

The pipeline DAG is for ordinary work. `tech_lead`, `it_agent`, and
`memory_writer` wake on **signals** (`ESCALATION`, `PACKAGE_REQUEST`,
end-of-run) — not on dependency completion. They are registered as
agents but are filtered out of the topological closure when the
`Scheduler` decides what is "unblocked" by `completed_agents`.
