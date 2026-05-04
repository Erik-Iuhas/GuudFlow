# 07 — Sequence: Handshake Protocol + Escalation

Two flows that show how the M1 inter-agent protocol and the multi-stage
review architecture cooperate:

1. **ASK / ANSWER handshake with peer-timeout** (asynchronous request to
   another agent without blocking the scheduler).
2. **Rejection escalation** to the on-call `tech_lead` after the
   threshold trips.

## 7a · ASK / ANSWER with timeout

```
Dev      Sched        WS        Reqs        Watch
 │         │           │          │           │
 │ needs clarification on auth requirement
 │
 │ emit_ask(ask_id, target=requirements_writer, question, deadline)
 ├──────────────────► │           │           │
 │ interrupt(role=developer, kind="peer", payload=WaitingForPeer)
 ├────────► │         │           │           │
 │          │  developer parked.  │           │
 │          │  scheduler keeps running other unblocked agents.
 │          │
 │          │ arm timeout(ask_id, deadline_ts)
 │          ├────────────────────────────────►│
 │
 ├─ requirements_writer answers in time ─────────────────────────┐
 │          │           │          │           │                  │
 │          │           │ read_protocol([ASK])                    │
 │          │           ◄──────────┤                              │
 │          │           │ emit_answer(ask_id, body)               │
 │          │           ◄──────────┤                              │
 │          │ ANSWER signal observed                              │
 │          ◄───────────┤                                         │
 │ resume(token, value=answer)                                    │
 ◄──────────┤                                                     │
 │ continue work                                                  │
 │          │           │          │           │                  │
 ├─ deadline elapses ────────────────────────────────────────────┐│
 │          │ post PEER_TIMEOUT(ask_id, reason)                  ││
 │          │           ◄──────────────────────────────────────┤ ││
 │          │ PEER_TIMEOUT observed                              ││
 │          ◄───────────┤                                         │
 │ resume(token, value=None)                                      │
 ◄──────────┤                                                     │
 │ emit_request_skip(reason="PEER_UNRESPONSIVE")                  │
 │          │           │          │           │
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

```
Dev       Sched        WS        TL        Watch       Human
 │          │           │          │          │           │
 │ emit_request_skip(skip_id, reason, deadline)
 ├──────────────────────►          │          │           │
 │ interrupt(role=developer, kind="skip", payload=WaitingForSkipConfirm)
 ├────────► │           │          │          │           │
 │          │ notify tech_lead (event-driven, no depends_on)
 │          ├──────────►│          │          │           │
 │          │ arm timeout(skip_id) │          │           │
 │          ├──────────────────────────────────►          │
 │          │           │ spawn TL │          │           │
 │          │           ├─────────►│          │           │
 │
 ├─ tech_lead grants skip ──────────────────────────────────────┐
 │          │           │ emit_skip_granted(skip_id, …)         │
 │          │           ◄──────────┤                            │
 │ resume(token, value=GRANTED)                                 │
 ◄──────────┤                                                   │
 │ continue with skip applied                                   │
 │
 ├─ tech_lead denies ────────────────────────────────────────────┐
 │          │           │ emit_skip_denied(skip_id, reason)      │
 │          │           ◄──────────┤                             │
 │ resume(token, value=DENIED)                                   │
 ◄──────────┤                                                    │
 │ re-attempt without skip                                       │
 │
 ├─ SKIP_TIMEOUT — escalate to human ────────────────────────────┐
 │          │ post SKIP_TIMEOUT(skip_id)                         │
 │          │           ◄────────────────────────────────────────┤
 │          │ post HUMAN_NEEDED(question, context)               │
 │          │           ◄──────────┤                             │
 │          │           │ HUMAN_RESPONDED                        │
 │          │           ◄────────────────────────────────────────┤ (user)
 │ resume                                                         │
 ◄──────────┤                                                    │
```

## 7c · Review rejection escalation

```
Dev       Rev       WS        Runner       TL
 │         │         │           │           │
 │── iteration n ───────────────────────────────────────┐
 │         │         │           │           │          │
 │ emit_commit(...) + COMPLETE                         │
 ├────────────────► │           │           │          │
 │         │ read git-changed-files (capped diff)      │
 │         ├────────►          │           │          │
 │         │ emit_verify(PASS|FAIL per file)           │
 │         ├────────►          │           │          │
 │         │                                            │
 │         ├─ all PASS ─────────────────────────────┐  │
 │         │ │ APPROVED                              │  │
 │         │ ├────────►                              │  │
 │         │ │            merge agent/developer → main  │
 │         │ │            ├──────────────►            │  │
 │         │ └─────────────────────────────────────┘  │
 │         │                                            │
 │         ├─ any FAIL ─────────────────────────────┐  │
 │         │ │ REJECTED + detail                     │  │
 │         │ ├────────►                              │  │
 │         │ │            increment rejection count │  │
 │         │ │            ├────────►                 │  │
 │         │ └─────────────────────────────────────┘  │
 │         │                                            │
 └─────────┴───────────────────────────────────────────┘ (loop until threshold)

  ── rejection threshold reached (default 2 — project config) ──

           │         │ post ESCALATION                │
           │         ◄──────────────┤                 │
           │         │ tech_lead spawns event-driven  │
           │         ├──────────────────────────────► │
           │         │ read prior REJECTED + commits  │
           │         ◄──────────────────────────────┤
           │         │ emit_action(target=developer, FIX|REVISIT|APPROVE)
           │         ◄──────────────────────────────┤
           │
           ├─ tech_lead approves ──────────────────────┐
           │ │ APPROVED                                │
           │ ◄──────────────────────────────────────┤  │
           │ │           force-merge to main          │
           │ │           ├────────►                   │  │
           │ └────────────────────────────────────────┘
           │
           ├─ tech_lead requires fix ──────────────────┐
           │ │ REJECTED + specific guidance            │
           │ ◄──────────────────────────────────────┤  │
           │ │           respawn iteration n+1        │  │
           │ │           ├──────► (Dev)               │  │
           │ └────────────────────────────────────────┘
```

## Why event-driven `tech_lead` and `IT` are not in `depends_on`

The pipeline DAG is for ordinary work. `tech_lead`, `it_agent`, and
`memory_writer` wake on **signals** (`ESCALATION`, `PACKAGE_REQUEST`,
end-of-run) — not on dependency completion. They are registered as
agents but are filtered out of the topological closure when the
`Scheduler` decides what is "unblocked" by `completed_agents`.
