# Track 2a — Orphan Question: Respawn Completed Agents on AGENT_MESSAGE

**Status:** Ready for implementation. Sonnet-executable.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 100–200k. Stop and reassess if over 250k.
**Pairs with:** Track 2b (no-op rationale). Both bugs are observed together; they fix independently.

---

## The bug

Agent B sends a question to agent A *after* A has reached COMPLETE. The message lands on the board. A never wakes up. B (or the user) is left waiting for a reply that will never come.

**Cause:** [core/scheduler.py:409](core/scheduler.py:409) — `_ready_to_spawn()` skips any role already in `completed_agents`. There is no path that removes a role from `completed_agents` when a new signal targets them.

```python
# core/scheduler.py around line 403-423 (_ready_to_spawn)
if self._effective_complete(role):
    continue          # ← orphan question dies here
```

The signal type that carries inter-agent questions is `AGENT_MESSAGE`, posted via [agents/tools/board.py:75-80](agents/tools/board.py:75) (`BoardMessage` tool). Scheduler uses these signals only as transition-rule input ([scheduler.py:456,464](core/scheduler.py:456)) — never as a wake trigger.

## The fix

When an `AGENT_MESSAGE` (or new `QUESTION`) signal is posted with `target_role = R` and `R` is in `completed_agents`:
1. Remove `R` from `completed_agents`.
2. Set `workspace.agent_states[R] = AgentState.READY`.
3. Mark this respawn as **question-mode** so the scheduler/runner know it's an answer-only iteration.
4. Wake the scheduler so `_ready_to_spawn()` picks up R for one iteration.
5. Cap: max 3 question-respawns per role per run. Beyond cap → drop the question with a warning quip; do not infinitely respawn.

The respawned agent enters a short-loop: read the unresolved question off the board, answer it (one LLM call), emit a response signal, re-COMPLETE.

---

## Scope

**In scope:**
- Detect `AGENT_MESSAGE` (and a new `QUESTION` typed alias if cleaner) targeting a completed role.
- Respawn mechanics in scheduler.
- Question-mode iteration in `BaseAgent.run()`.
- Per-role respawn cap.
- One scheduler test, one agent test, one integration test.

**Out of scope:**
- Restructuring the BoardMessage tool's payload schema beyond adding `target_role` if it's not already there.
- Cross-run question persistence.
- UI surfacing of pending-question state (Studio side).
- The "no rationale before signing off" half — that's Track 2b. Even after 2b lands, agents may still want to ask follow-ups, so 2a is independently valuable.

---

## Files to modify

| File | Change |
|---|---|
| [core/signals.py](core/signals.py) | Optionally add `SignalType.QUESTION` if `AGENT_MESSAGE` is too generic. Otherwise no change. |
| [core/scheduler.py](core/scheduler.py) | Add `_respawn_for_question()` path; track per-role respawn count; hook signal subscriber. |
| [core/runner.py](core/runner.py) | Detect question-mode in spawn context, set a flag on the agent. |
| [agents/base.py](agents/base.py) | In `run()`, branch into question-mode short-loop when flag is set. |
| [agents/tools/board.py](agents/tools/board.py) | Confirm `BoardMessage.run()` accepts `target_role` and posts it on the signal. Add it if missing. |
| [tests/core/test_scheduler.py](tests/core/test_scheduler.py) | New test: respawn-on-question. |
| `tests/agents/test_base_question_mode.py` | New file: agent answers and re-completes without redoing prior work. |
| [tests/integration/test_agent_to_agent_git_flow.py](tests/integration/test_agent_to_agent_git_flow.py) | Extend (currently untracked) with the question-after-complete scenario. |

---

## Implementation steps

### Step 1 — Signal field
Files: [core/signals.py](core/signals.py), [agents/tools/board.py](agents/tools/board.py)

Confirm `AGENT_MESSAGE` signals carry `target_role` in `detail` (JSON or `key=value;` form — see `Scheduler._extract_signal_field` at [core/scheduler.py:500](core/scheduler.py:500)). If not, extend `BoardMessage.run()` to include it.

Decision point: **prefer reusing `AGENT_MESSAGE` over a new `QUESTION` type.** Less surface area. Add a `requires_response: bool` field if differentiation is needed.

### Step 2 — Respawn cap field
Files: [core/scheduler.py](core/scheduler.py)

Next to `_spawn_count` at line 141, add:
```python
self._question_respawn_count: dict[str, int] = defaultdict(int)
self._question_respawn_cap: int = 3
```

### Step 3 — Wake-on-question subscriber
Files: [core/scheduler.py](core/scheduler.py)

Locate the existing signal subscription mechanism (the scheduler subscribes to workspace signals to maintain transition rules). Add a callback that:
1. Filters for `signal.type == SignalType.AGENT_MESSAGE` AND `requires_response=True` (or all of them, conservatively).
2. Extracts `target_role`.
3. Checks: is `target_role` in `completed_agents`? Is `_question_respawn_count[target_role] < _question_respawn_cap`?
4. If yes:
   - `self._question_respawn_count[target_role] += 1`
   - `self._completed_agents.discard(target_role)` (or mutate `workspace.completed_agents` per existing rejection-handler pattern at [runner.py:3094](core/runner.py:3094))
   - `self.workspace.agent_states[target_role] = AgentState.READY`
   - Mark a per-role flag: `self._pending_question[target_role] = signal.id` (the question to answer).
   - `self._wake.set()` — wake the main loop.
5. If cap exceeded: post a quip "agent {role} is at question-respawn cap, dropping question {signal.id}".

The pattern to mirror is the existing rejection-redispatch handler at [core/runner.py:3056](core/runner.py:3056) (`_on_signal_for_rejection_redispatch`). Read it before writing this — same shape.

### Step 4 — Question-mode signal flag in spawn
Files: [core/scheduler.py](core/scheduler.py), [core/runner.py](core/runner.py)

When `_spawn()` runs a role that has an entry in `_pending_question`, pass that question signal ID into the runner. The cleanest hook: extend `AgentSpec.runner` calling convention OR set `workspace.agent_pending_question[role] = signal_id` and have BaseAgent read it.

Choose the workspace-flag approach — keeps `AgentSpec` stable. Add to [core/workspace.py](core/workspace.py):
```python
agent_pending_question: dict[str, str] = field(default_factory=dict)  # role → signal_id
```

### Step 5 — Question-mode short-loop in BaseAgent
Files: [agents/base.py](agents/base.py)

At the top of `BaseAgent.run()`:
```python
pending_q_id = self.workspace.agent_pending_question.pop(self.cfg.role, None)
if pending_q_id is not None:
    return await self._answer_question_short_loop(pending_q_id)
```

`_answer_question_short_loop()`:
1. Read the question signal off the board by ID.
2. Build a minimal context: prior agent's reputation snippet + the question text + the agent's own last completion record.
3. One LLM call with a tightly scoped system prompt: "You completed your work and were asked a follow-up question. Answer concisely. Do not redo the work — refer to your existing commits and reasoning."
4. Post a `AGENT_RESPONSE` signal (or AGENT_MESSAGE with a `responding_to=<id>` field) with the answer.
5. Emit COMPLETE again — same role, same iteration semantics. The runner's existing COMPLETE path handles re-marking the agent done.

### Step 6 — Mark question-as-answered
Files: [core/signals.py](core/signals.py), [agents/tools/board.py](agents/tools/board.py)

The original question signal needs a `seen_by` / `responded_at` mutation when the answer is posted. Mirror the existing `mark_seen()` pattern that USER_COMMENT uses (per CLAUDE.md "User Interaction Rules" — `seen_by` updates on absorption, never reprocessed).

### Step 7 — Tests
- **`tests/core/test_scheduler.py`** — new test `test_orphan_question_respawn`:
  1. Build a 2-agent scheduler with mock runners.
  2. Drive A to COMPLETE.
  3. Post an AGENT_MESSAGE from B targeting A.
  4. Assert A appears in `_ready_to_spawn()` and is removed from `completed_agents`.
  5. Assert respawn cap: post a 4th question, assert no 4th respawn happens.
- **`tests/agents/test_base_question_mode.py`** — new file:
  1. Mock LLM with one scripted response.
  2. Set `workspace.agent_pending_question[role] = "Q1"`.
  3. Pre-post a question signal with id="Q1".
  4. Run the agent.
  5. Assert: short-loop ran (one LLM call, not full reasoning loop), an AGENT_MESSAGE response was posted with `responding_to="Q1"`, and the agent re-completed.
- **`tests/integration/test_agent_to_agent_git_flow.py`** — extend with the post-complete-question scenario.

---

## Acceptance criteria

1. `pytest tests/core/test_scheduler.py::test_orphan_question_respawn` passes.
2. `pytest tests/agents/test_base_question_mode.py` passes.
3. `pytest tests/` full suite green (no regressions).
4. Manual: run a 2-agent flow, drive agent A to complete, then via the studio user-comment path inject a follow-up question that another agent forwards. Confirm A respawns once and posts an answer visible in the BoardFeed. Confirm the run still terminates cleanly.
5. Cap behavior: scripted test with 5 questions in sequence. Assert exactly 3 respawns; 4th and 5th drop with a quip log entry.
6. No infinite loops under any test. CI run completes in < 2× baseline.

---

## Risks / things to watch

- **Respawn vs. concurrent in-flight runs**: if A is *currently* running (not yet completed) and a question arrives, do nothing — they'll see it on the board naturally. The respawn path is *only* for `target ∈ completed_agents`.
- **Question targeting a failed agent**: skip respawn; failed agents stay failed. Drop with a quip.
- **Pipeline scheduler vs. group scheduler**: question respawns must work in both scopes. Verify by running the scheduler test with `scope="group"`.
- **Race condition**: the workspace-flag (`agent_pending_question`) approach means if multiple questions arrive between spawns, only the last one survives the dict. Mitigate by making the value a list `dict[str, list[str]]` and draining one per respawn.
- **Order of question signal posting and scheduler wake**: post the signal first, then call `notify()`. Existing `BoardMessage.run()` may already do this — confirm.

---

## Commit plan

```
feat(scheduler): track question-respawn count and cap per role
feat(scheduler): respawn completed agent on AGENT_MESSAGE with target_role
feat(workspace): agent_pending_question dict for cross-spawn question handoff
feat(agent): question-mode short-loop in BaseAgent.run()
feat(board): mark question signal seen_by on response
test(scheduler): orphan-question respawn and cap
test(agent): question-mode short-loop
test(integration): question-after-complete end-to-end
```
