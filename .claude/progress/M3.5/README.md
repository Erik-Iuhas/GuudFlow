# M3.5 — Per-Agent Task Desks + Scope Envelopes

> **Status: planned, not started.** Estimated 2–3 days of focused work
> across 8 chunks. Strictly additive — empty desks reproduce today's
> behaviour exactly.

## Why this exists

The current handoff format (M1 `INCOMING WORK` + commit-message scraping
in `runner.py:1223`) infers what an agent should do from upstream
commits. That works when the upstream commit is descriptive; it drifts
when it isn't, and there's no machine-checked "this is what you were
asked to do." The empty-upstream rule (`UPSTREAM INCOMPLETE → emit
ASK`) catches absence of work, not scope drift inside work that
exists.

Stories (M1 §7b) are scope envelopes for *groups* of agents but they
don't track per-agent assignment. There is no equivalent of a Claude
Code TODO list at the agent level.

M3.5 adds that layer: **`Task` objects on each agent's desk, assigned
by a planning archetype, with explicit acceptance criteria, closed by a
`COMMIT` that names the task.**

## Goal

After M3.5 ships:

- Every non-trivial unit of work is a `Task` on exactly one agent's
  desk, with a stated `goal`, `acceptance_criteria`, and an optional
  `out_of_scope` list.
- Higher-hierarchy agents (`tech_lead`, `architect`,
  `requirements_writer`, `meta`) hold the `task:assign` RBAC scope and
  push tasks downward via a structured `ASSIGN_TASK` protocol message.
- When the user posts on the board, the highest standby planning agent
  in the team picks it up, decomposes it into tasks, and assigns them.
- Every `COMMIT` declares which task(s) it closes; the runner verifies
  the closed tasks were on that agent's desk.
- An empty desk reproduces today's behaviour — no breakage for projects
  that don't use planning archetypes.

## Read order

1. [`decisions.md`](decisions.md) — Erik's answers + locked tradeoffs +
   rejected alternatives. Read first; everything else assumes these.
2. [`design.md`](design.md) — `Task` dataclass, protocol wire format,
   RBAC scope, layered-prompt impact, signal lifecycle.
3. [`chunks.md`](chunks.md) — implementation plan, 8 chunks, ordered
   so each chunk is independently mergeable and reversible.
4. [`followups.md`](followups.md) — explicit "not in M3.5" list. Read
   before extending; the goal is to leave M4+ space free.

## Chunk index

| # | Chunk | Files touched | Depends on |
|---|-------|---------------|------------|
| 1 | `Task` dataclass + workspace plumbing | `core/workspace.py`, `core/signals.py` | — |
| 2 | Protocol wire format (`ASSIGN_TASK`, `TASK_*`, `closes:`) | `core/protocol.py` | 1 |
| 3 | RBAC scope `task:assign` + manifest descriptor | `core/tool_manifest.py`, `agents/abstract.py` | 1 |
| 4 | Runner — render desk into INCOMING WORK | `core/runner.py` | 1, 2 |
| 5 | Runner — validate `closes:` against desk on COMMIT | `core/runner.py` | 1, 2, 4 |
| 6 | Board router — user-comment → planning archetype pickup | `core/runner.py` | 1, 2, 3 |
| 7 | Persistence — desks join board.jsonl resume | `core/run_loader.py`, `core/workspace.py` | 1 |
| 8 | API — read-only desk endpoint + WS event | `api/routes/runs.py`, `api/websocket.py` | 1 |

Chunks 1–5 are the critical path. Chunk 6 is the user-facing payoff.
Chunks 7 and 8 can land independently after the core is green.

## What stays out of M3.5

See [`followups.md`](followups.md) for the full list. Highlights:

- No new agent archetypes (no Scrum Master, no Planner). Planning
  behaviour is added to existing planning archetypes.
- No dynamic dependency-graph mutation. `depends_on` is still set at
  proposal time. Tasks layer on top.
- No task UI in the studio beyond what falls out of the WS event.
  Studio M4+ owns the board view.
- No replacement of Stories. Tasks are children of Stories.

## Cost / token impact

Per-agent token overhead estimate:

- Empty desk → 0 tokens (block omitted entirely)
- 3-task desk → ~150 tokens added to the user-turn message per
  iteration (cached by Anthropic on second iteration)
- Closes-validation → 0 tokens (runner-side check)

Net: small, bounded, and proportional to actual planned work.
