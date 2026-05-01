# Track 2b — No-Op Completion Rationale

**Status:** Ready for implementation. Sonnet-executable.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 100–200k. Stop and reassess if over 250k.
**Pairs with:** Track 2a. Implement either order; verify together.

---

## The bug

When an agent finishes a run without committing anything (because it judged nothing needed to change), it currently emits `COMPLETE` with `detail=None`. To downstream agents that's indistinguishable from "skipped" or "failed silently." Downstream gets nervous and asks "did you actually do anything?" — which then hits Track 2a's orphan-question bug.

**Cause:** [core/runner.py](core/runner.py) `_auto_commit()` returns `None` when `git status` is clean. The runner then emits:
```python
await agent.signal(workspace, SignalType.COMPLETE,
    summary=f"{cfg.role} complete",
    detail=commit_hash,   # None when no changes
)
```
The reviewer prompt at [agents/builtin/code_reviewer.py:25](agents/builtin/code_reviewer.py:25) doesn't require stating *why* no change was needed before `[VERIFY] PASS`. The agent's reasoning may be in its prose, but it's not exposed to other agents as structured context.

## The fix

Require an explicit no-op rationale on completion when no commits were made. Three changes work together:

1. **Protocol-level**: extend the COMMIT renderer in [core/protocol.py](core/protocol.py) (around line 101 — `render_commit()`) to support an optional `no_op_reason: <text>` trailer. Also add a parser entry that surfaces it as a typed field on `ProtocolMessage`.

2. **Runner-level**: when `_auto_commit()` returns `None`, the runner *must* find a rationale on the agent's last output (or in a structured signal the agent emitted before COMPLETE). If absent, treat as a protocol violation — synthetic FAIL using the existing path at [core/runner.py:2560](core/runner.py:2560).

3. **Prompt-level**: update reviewer/coder prompts (`agents/builtin/*.py` and `data/personas/*`) to mandate: when finishing without commits, the agent must emit a `[NO_OP_REASON: ...]` block before signing off. Include format examples.

---

## Scope

**In scope:**
- New `[NO_OP_REASON: text]` protocol block (renderable, parseable).
- Runner enforcement: refuse silent no-op completions.
- Prompt updates for reviewer + coder built-ins.
- `IterationMetrics.no_op` flag (already added in Track 1; this track sets it).
- Surfacing `no_op_reason` in the BoardFeed UI as structured context.
- Tests.

**Out of scope:**
- Auto-generating the rationale from prior reasoning (that's gold-plating; the agent must articulate it).
- Reworking the COMMIT renderer beyond adding the trailer.
- Cross-run analytics on no-op rates (Track 1's analytics panel can show it later).

---

## Files to modify

| File | Change |
|---|---|
| [core/protocol.py](core/protocol.py) | Extend `render_commit()` and parser; add `no_op_reason: str = ""` to `ProtocolMessage`. |
| [core/runner.py](core/runner.py) | In COMPLETE-emission path: enforce non-empty `no_op_reason` when `_auto_commit()` returned None. Synthetic FAIL otherwise. |
| [agents/base.py](agents/base.py) | Helper `emit_no_op(reason)` that wraps the protocol block. Set `self._last_commit_was_noop = True` so Track 1's metrics pick it up. |
| [agents/builtin/code_reviewer.py](agents/builtin/code_reviewer.py) | Prompt update — mandate `[NO_OP_REASON]` before `[VERIFY] PASS` when no commits made. |
| `agents/builtin/coder.py` (if exists, else equivalent) | Same prompt update. |
| `data/personas/*/profile.json` (built-in personas only) | If their base prompt overrides include no-op guidance, sync it. |
| `studio/src/components/BoardFeed/CommitMessage.tsx` (or equivalent) | Render `no_op_reason` distinctly when present. |
| [tests/core/test_protocol.py](tests/core/test_protocol.py) | Roundtrip test for `[NO_OP_REASON]` block. |
| `tests/agents/test_no_op_completion.py` | New file: agent with no commits + missing rationale → synthetic FAIL; with rationale → clean COMPLETE. |

---

## Implementation steps

### Step 1 — Protocol block
Files: [core/protocol.py](core/protocol.py)

Extend `ProtocolMessage` dataclass with `no_op_reason: str = ""`.

Update `render_commit()` to accept `no_op_reason: str | None = None`. When provided, output:
```
[COMMIT agent=<role> hash=<short>]
files: ...
verify: ...
[NO_OP_REASON: <text>]
```

When the commit hash itself is missing (no commits made), render a degenerate COMMIT block:
```
[COMMIT agent=<role> hash=NONE]
[NO_OP_REASON: <text>]
```

Update the parser (in the same file) to extract `no_op_reason` from a `[NO_OP_REASON: ...]` line. The block is single-line; multi-line rationale gets joined with spaces during parse.

Roundtrip invariant: parse(render(msg)) == msg.

### Step 2 — Helper on BaseAgent
Files: [agents/base.py](agents/base.py)

Add:
```python
async def emit_no_op(self, reason: str) -> None:
    if not reason or not reason.strip():
        raise ValueError("no_op_reason must be non-empty")
    self._last_commit_was_noop = True   # Track 1 metrics consumer
    await self._post_protocol(render_commit(
        agent=self.cfg.role,
        hash=None,
        files=[],
        checkpoints=[],
        no_op_reason=reason.strip(),
    ))
```

### Step 3 — Runner enforcement
Files: [core/runner.py](core/runner.py)

Find the COMPLETE-emission path that handles the `_auto_commit()` returning None. Before emitting COMPLETE:
1. Read the agent's last protocol message off the board.
2. If it's a COMMIT block with `hash=NONE` and `no_op_reason` populated → proceed; emit COMPLETE with `detail=f"no_op: {reason}"`.
3. If it's a COMMIT block with `hash=NONE` and `no_op_reason` empty → treat as protocol violation. Use the existing synthetic-FAIL path at [core/runner.py:2560](core/runner.py:2560) with a clear message: `"agent completed without commits and without a [NO_OP_REASON] — protocol violation"`.
4. If the last message wasn't a COMMIT block at all → existing behavior (whatever it was — synthetic FAIL or COMPLETE-without-detail; do not change).

### Step 4 — Prompt updates
Files: [agents/builtin/code_reviewer.py](agents/builtin/code_reviewer.py), other built-in agents that may emit no-commit completions.

Append to system prompt:
```
If you finish your work without committing any changes, you MUST emit a
[NO_OP_REASON: <text>] block immediately before [VERIFY] / [COMPLETE].
The reason must be specific:

  ✅  [NO_OP_REASON: existing tests already cover the new edge case;
      no schema drift between branches]
  ❌  [NO_OP_REASON: looks fine]
  ❌  [NO_OP_REASON: nothing to do]

Without this block, your completion will be rejected as a protocol violation.
```

### Step 5 — Studio surface
Files: studio BoardFeed components.

When a COMMIT message has `no_op_reason`, render it as a distinct chip (e.g., grey background, italic) above the verify checkpoints. Label: "No-op rationale". Don't bury it inside the file-changes section.

### Step 6 — Tests
- **[tests/core/test_protocol.py](tests/core/test_protocol.py)** — roundtrip test: render with `no_op_reason="X"`, parse, assert `msg.no_op_reason == "X"`. Edge cases: empty rationale (should fail render), multi-line rationale (joined on parse).
- **`tests/agents/test_no_op_completion.py`** — new file:
  1. Mock agent that returns no commits and no `[NO_OP_REASON]` → assert synthetic FAIL signal posted.
  2. Mock agent that returns no commits + valid `[NO_OP_REASON: tests cover this]` → assert clean COMPLETE with `detail` containing the rationale.
  3. Mock agent that makes commits *and* emits `[NO_OP_REASON]` → ignore the rationale (commits take precedence), normal COMPLETE.

---

## Acceptance criteria

1. `pytest tests/core/test_protocol.py` — green, including new roundtrip cases.
2. `pytest tests/agents/test_no_op_completion.py` — green.
3. `pytest tests/` full suite green (no regressions in existing reviewer tests).
4. Manual: run a flow that triggers a clean reviewer pass. Confirm:
   - The reviewer's last protocol message contains `[NO_OP_REASON: ...]`.
   - The COMPLETE signal's `detail` includes the rationale.
   - The studio BoardFeed shows the rationale as a distinct chip.
5. Manual regression: run a flow with actual changes. Confirm reviewer still emits COMMIT + VERIFY normally; no `[NO_OP_REASON]` appears.
6. Track 1 integration: `IterationMetrics.no_op` is `True` for the no-op iteration and `False` for the normal one.

---

## Risks / things to watch

- **Existing prompts that already emit no-commit completions**: any agent that previously emitted `[VERIFY] PASS` with no commits will start failing the new check. Audit the prompt of every built-in reviewer and add the `[NO_OP_REASON]` instruction. If untrained user-defined personas hit this, the synthetic FAIL is recoverable — they retry with the rationale.
- **Format flexibility**: keep the parser tolerant — allow `[NO_OP_REASON: ...]`, `[NO_OP_REASON ...]`, and multi-line. Reject only when the field is empty.
- **Track 2a interaction**: even with rationales, downstream agents may still want to ask follow-ups. Don't remove Track 2a's respawn path "because rationales make it unnecessary" — both fixes coexist.

---

## Commit plan

```
feat(protocol): NO_OP_REASON block render and parse
feat(agent): emit_no_op helper on BaseAgent
feat(runner): enforce non-empty no_op_reason on no-commit COMPLETE
feat(prompts): mandate NO_OP_REASON in built-in reviewer/coder prompts
feat(studio): render no_op_reason as a distinct chip in BoardFeed
test(protocol): NO_OP_REASON roundtrip
test(agents): no_op completion paths (missing/present/with-commits)
```
