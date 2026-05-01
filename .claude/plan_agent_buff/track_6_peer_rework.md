# Track 6 — Peer-Initiated Rework Signal

**Status:** Ready for implementation. Sonnet-executable.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 150–250k. Stop and reassess if over 300k.
**Depends on:** Nothing hard. Track 1's metrics make verification easier but not required.

---

## Why

Today, only **reviewer agents** (those with `is_reviewer=True`) can kick work back to a producer. The path is `[VERIFY] FAIL` → `REJECTED` signal → handler at [core/runner.py:3056](core/runner.py:3056) (`_on_signal_for_rejection_redispatch`) → producer respawns. That works for the formal review loop.

It does NOT work for peer-to-peer collaboration. Frontend agent finds backend's API contract is wrong, can't kick it back. Test runner finds a flaky test pattern, can't restart the test author. Today their only recourse is `AGENT_MESSAGE` (a question that gets ignored once Track 2a lands the answer respawn — but answers are not the same as restarts).

This track adds peer-initiated rework as a first-class signal type, mirroring the existing rejection mechanics but bounded by a peer-rework cap to prevent ping-pong.

---

## Scope

**In scope:**
- New `PEER_REWORK` signal type.
- Subscriber that respawns the target, mirroring `_on_signal_for_rejection_redispatch`.
- New tool `peer-rework` available to non-reviewer agents.
- Per-pair cap (default 2) to prevent peer-fight loops.
- Escalation: if cap is hit, route to tech_lead via the existing escalation path.
- Tests covering: respawn, cap, escalation handoff.

**Out of scope:**
- Allowing peer-rework from reviewers (they have VERIFY for that).
- Cross-group peer-rework (peer-rework is within-pipeline only for v1).
- Peer-rework with structured "what to change" payload beyond a free-text reason. Reviewer-style structured failure detail is overkill here.
- UI surfacing in Studio (BoardFeed will show the signal type generically; no special panel).

---

## Files to modify

| File | Change |
|---|---|
| [core/signals.py](core/signals.py) | Add `SignalType.PEER_REWORK`. Document fields. |
| [core/runner.py](core/runner.py) | New subscriber `_on_signal_for_peer_rework_redispatch`, parallel to existing rejection handler. |
| `agents/tools/peer_rework.py` | **New tool**. |
| `agents/tools/__init__.py` (or registry) | Register `peer-rework`. |
| [agents/abstract.py](agents/abstract.py) | Grant `peer-rework` to non-reviewer roles by default (coder, frontend, backend, etc.). NOT reviewers. |
| Built-in personas — coder/frontend/backend prompts | Brief mention: "If a peer's work blocks you, prefer fixing forward; use peer-rework only when their output is genuinely wrong." |
| [tests/core/test_runner.py](tests/core/test_runner.py) | New test: peer-rework respawn. |
| `tests/core/test_peer_rework_cap.py` | New file: cap enforcement + escalation. |

---

## Implementation steps

### Step 1 — Signal type
Files: [core/signals.py](core/signals.py)

Add to `SignalType` enum:
```python
PEER_REWORK = "peer_rework"
```

Convention for `Signal.detail` payload (JSON):
```json
{
  "from_role": "frontend",
  "to_role": "backend",
  "reason": "API returns user.id as int but spec says string; downstream JSON.parse fails",
  "target_signal_id": "sig_abc123"
}
```

`target_signal_id` is the producer's COMPLETE or COMMIT signal that the peer is rejecting. Used so re-dispatch knows which iteration triggered the rework.

Add a helper to `Signal` (or a free function in signals.py) to extract these fields safely, mirroring `Scheduler._extract_signal_field` at [core/scheduler.py:500](core/scheduler.py:500).

### Step 2 — `peer-rework` tool
Files: `agents/tools/peer_rework.py` (new)

```python
class PeerRework(Tool):
    name = "peer-rework"
    description = (
        "Send a rework request to a peer agent whose output blocks you. "
        "Use ONLY when the peer's work is genuinely wrong and you cannot "
        "work around it. Each pair has a rework cap (default 2 per run); "
        "exceeding the cap routes to the tech lead. Prefer fixing forward "
        "when possible. NOT a substitute for asking questions — use board "
        "messages for that."
    )

    parameters = {
        "to_role": {"type": "string", "description": "The role you want to rework"},
        "reason": {"type": "string", "description": "Specific, evidence-backed reason. Vague reasons (e.g. 'doesn't work') will be rejected by the runner."},
    }

    async def run(self, to_role: str, reason: str, ...) -> str:
        # 1. Validate reason length >= 30 chars (vague-rejection guard,
        #    same spirit as VERIFY FAIL must have detail per CLAUDE.md §18)
        # 2. Find the peer's most recent COMPLETE/COMMIT signal on the board
        #    via workspace.find_last_signal(role=to_role, types=[COMPLETE, COMMIT])
        # 3. Post PEER_REWORK signal with structured detail
        # 4. Return confirmation string
```

The tool must NOT directly mutate scheduler state — it just posts the signal. The subscriber (Step 3) handles re-dispatch.

### Step 3 — Subscriber
Files: [core/runner.py](core/runner.py)

Read [core/runner.py:3056-3105](core/runner.py:3056) (`_on_signal_for_rejection_redispatch`) carefully — it's the template. Then add a parallel handler:

```python
async def _on_signal_for_peer_rework_redispatch(self, signal: Signal) -> None:
    if signal.type != SignalType.PEER_REWORK:
        return

    payload = _parse_peer_rework_detail(signal.detail)
    if not payload:
        return

    from_role = payload["from_role"]
    to_role = payload["to_role"]
    pair_key = (from_role, to_role)

    # Cap check
    self._peer_rework_count[pair_key] = self._peer_rework_count.get(pair_key, 0) + 1
    if self._peer_rework_count[pair_key] > self._peer_rework_cap:
        # Route to tech_lead via existing escalation path
        await self._maybe_escalate(
            reason=f"peer-rework cap exceeded between {from_role} and {to_role}",
            signal=signal,
        )
        return

    # Respawn target — mirror lines 3094-3105 of rejection handler
    if to_role in self.workspace.completed_agents:
        self.workspace.completed_agents.remove(to_role)
    self.workspace.agent_states[to_role] = AgentState.READY
    # Reset the requesting agent to PENDING so it waits for re-COMPLETE
    self.workspace.agent_states[from_role] = AgentState.PENDING

    self.scheduler.notify()
```

Initialize on the runner:
```python
self._peer_rework_count: dict[tuple[str, str], int] = {}
self._peer_rework_cap: int = 2  # configurable later via project config
```

Wire the subscriber in the runner's signal subscription path (find where rejection-redispatch is subscribed; subscribe peer-rework next to it).

### Step 4 — Tool registration & granting
Files: `agents/tools/__init__.py`, [agents/abstract.py:80](agents/abstract.py:80)

Register `peer-rework`. Add to default tools for non-reviewer roles. Explicitly DO NOT grant to reviewers — they use VERIFY.

### Step 5 — Prompt updates
Files: built-in non-reviewer agent prompts.

Append a short paragraph:
```
If another agent's output blocks you AND you cannot fix forward, you may
call `peer-rework` to request they rework their output. Provide a specific,
evidence-backed reason (>= 30 chars). Each pair has a rework cap; abuse
escalates to the tech lead.

Default to fixing forward. Peer-rework is for genuinely wrong outputs,
not differences in style or preference.
```

### Step 6 — Tests
- **`tests/core/test_runner.py`** — extend with `test_peer_rework_respawns_target`:
  1. Build a runner with two non-reviewer agents A and B.
  2. Drive both to COMPLETE.
  3. Post a PEER_REWORK from A targeting B with valid reason.
  4. Assert: B is removed from `completed_agents`, B's state is READY, A's state is PENDING, scheduler is woken.
- **`tests/core/test_peer_rework_cap.py`** — new file:
  1. Set cap=2.
  2. Post 3 PEER_REWORK signals from A→B in sequence.
  3. Assert: 2 respawns happen; 3rd triggers escalation (tech_lead path invoked).
  4. Verify `_peer_rework_count[(A, B)] == 3` after the third.
  5. Assert escalation reason string includes both roles.
- **`tests/agents/tools/test_peer_rework.py`** — new file:
  1. Tool with reason < 30 chars → rejected before signal post.
  2. Valid call → PEER_REWORK signal on board with correct detail JSON.
  3. Target role not found in workspace → tool returns error string, no signal posted.

---

## Acceptance criteria

1. `pytest tests/core/test_runner.py::test_peer_rework_respawns_target` — green.
2. `pytest tests/core/test_peer_rework_cap.py` — green.
3. `pytest tests/agents/tools/test_peer_rework.py` — green.
4. `pytest tests/` full suite green (no regressions in existing rejection-loop tests).
5. Manual: 2-agent project, drive both to COMPLETE, manually inject a PEER_REWORK via the API or a test fixture. Confirm target respawns and BoardFeed shows the signal.
6. Cap behavior: same scenario but force 3 rework attempts. Confirm tech_lead is escalated and run completes (does not infinite-loop).

---

## Risks / things to watch

- **Cap default of 2 may be too tight or too loose.** Make it configurable via project config from day one, even if not yet exposed in UI. Sensible env override: `FLOW_PEER_REWORK_CAP=3`.
- **Interaction with existing rejection loop**: a reviewer may VERIFY-FAIL the same producer that's also receiving peer-rework. The two counters are independent. That's fine — the producer's combined respawn count is what matters; ensure max_iterations cap still bounds it.
- **Tool granting**: do NOT grant peer-rework to event-driven agents (IT, tech_lead). Verify by listing built-in roles that get the tool and confirming reviewers + event-driven agents are excluded.
- **Signal-pair direction**: cap is per-direction `(A, B) != (B, A)`. A and B can each rework the other up to the cap independently. If pingponging is observed in practice, tighten to a per-pair-undirected cap.
- **Escalation path for already-failed roles**: if target is in `_failed`, do nothing — failed agents don't get peer-reworked back to life.

---

## Commit plan

```
feat(signals): PEER_REWORK signal type and detail helpers
feat(tools): peer-rework tool with reason-length validation
feat(tools): register peer-rework; grant to non-reviewer defaults
feat(runner): peer-rework re-dispatch subscriber with per-pair cap
feat(runner): escalate to tech_lead on peer-rework cap exceeded
docs(prompts): peer-rework guidance for non-reviewer built-ins
test(runner): peer-rework respawns target
test(runner): peer-rework cap and escalation handoff
test(tools): peer-rework validation paths
```
