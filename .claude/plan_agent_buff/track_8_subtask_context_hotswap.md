# Track 8 — Subtask context hot-swap

**Status:** Specced. **Do not start until Track 7 ships** — subtask
boundaries have to exist before there's anything to swap on.
**Parent plan:** `master_subtasks_dispatch_t7_t8_t12.md` (Phase 3)
**Token budget:** 200–350k. Stop and reassess if over 450k.
**Depends on:**
  - **Track 7** (subtask decomposition) — provides the `closes:`
    closure events Track 8 hooks into.
  - **Track 12** (sub-LLM dispatch) — uses `summarise-subtask` (commit
    `1148bf1`) to produce the marker text. Currently dormant; this
    track is the primary consumer.

---

## Why

Two compounding pressures, same diagnosis from earlier in this sprint:

- **codebase_analyst on QuickRefs-web** (run_f7d1422d5b): 19 LLM calls,
  ~24K input tokens by the final round-trip, 142s elapsed, then
  PROTOCOL_PARSE_FAIL retries because the agent drowned in its own
  tool-result history. Sonnet absorbed it; Haiku lost format.
- **Cheap models are unusable on real repos today.** Same root cause
  from the cost angle for the heavies — they pay full freight to
  re-process stale tool outputs every call.

Track 7 introduces subtask boundaries. Track 8 makes those boundaries
*matter* for context size: when a subtask closes, its tool-result span
is stashed out of the live message history and replaced with a one-
line summary marker. A new `recall-subtask` tool re-hydrates the
stashed span if a later subtask needs the detail.

The mechanism already exists in skeleton form
([core/runner.py:2180](core/runner.py:2180) —
`_compact_messages_after_close`). Track 8 generalises it:
- Marker text comes from Track 12's `summarise-subtask` (an LLM call,
  not the static `record.summary`).
- Original messages are *stashed* in a per-run dict keyed by
  `subtask_id` (today they're discarded).
- New `recall-subtask(subtask_id)` tool splices the stashed messages
  back into `_current_agent_messages` at the marker position.

Erik's framing for the bucket system: small models gain working room,
large models save money. Designed once, paid back everywhere.

---

## Outcome

- When an agent closes a subtask via `[COMMIT]\n  closes: task:s1`:
  1. Runner extracts the span of `_current_agent_messages` produced
     since `s1` started (assistant tool-use blocks + matching
     tool_result user blocks).
  2. Runner calls `summarise-subtask` (Track 12 wrapper) with the
     subtask's goal + a compact list of the tool calls + their
     compressed results. Returns `{summary, deliverables}`.
  3. Runner stashes the original span in
     `self._stashed_subtask_messages[s1]`.
  4. Runner splices in a single user message at the same position:
     `[SUBTASK CLOSED — id=s1, summary: <one line>, deliverables:
     [...], recall via recall-subtask("s1")]`.
- Agent's next LLM call sees the marker, not the verbose history.
- A later subtask can call `recall-subtask("s1")` to splice the
  stashed messages back into context. Tool returns
  `{ok: true, restored_messages: N}`.
- Re-closing s1 after a recall re-stashes (idempotent).
- Cost & token usage from the `summarise-subtask` LLM call land in
  `IterationMetrics.sub_llm_breakdown["summarise-subtask"]` (already
  wired by Track 12's metrics sink).

## Files to modify

| File | Change |
|---|---|
| `core/runner.py` `_compact_messages_after_close` | Renamed to `_hot_swap_subtask_context`. Behaviour shifts from discard-and-marker to **stash-and-marker**: stores the extracted span in `self._stashed_subtask_messages: dict[str, list[LLMMessage]]` keyed by `subtask_id`. The marker text format extends the existing `[runner] Task X closed at <sha>. Summary: ...` to also list `deliverables:` and a `recall via recall-subtask("<id>")` hint. |
| `core/runner.py` summary path | Replace `record.summary` static text with a runner-internal call to the `summarise-subtask` tool (uses Track 12's resolver + sink — same plumbing as agent invocation). On dispatch error, falls back to `record.summary` (today's behaviour) so a sub-LLM outage doesn't break runs. |
| `core/runner.py` `__init__` | New attribute `self._stashed_subtask_messages: dict[str, list[LLMMessage]] = {}` — process-memory only, not persisted to disk (acceptable per master plan §"Out of scope"). |
| `agents/tools/recall_subtask.py` | **New.** `RecallSubtaskTool`. Inputs: `{subtask_id: str}`. Effect: splice `runner._stashed_subtask_messages[subtask_id]` back into `_current_agent_messages` at the position of the most-recent `[SUBTASK CLOSED — id=<subtask_id>` marker. Returns `{ok, restored_messages}` or `{error}` if the id isn't stashed. RBAC scope: `recall-subtask` (read; cheap, no token spend beyond a single marker swap). |
| `core/runner.py` LOCAL_TOOL_CLASSES + `_instantiate_local_tools` | Register `RecallSubtaskTool`. Construct it with a closure over `self._stashed_subtask_messages` and `self._current_agent_messages`. |
| `core/tool_manifest.py` | Register `recall-subtask` as a known scope. **Read-class** (cheap), so add to `BASELINE_LOCAL_SCOPES` — every agent gets it without explicit grant. The tool only does anything if the agent had a subtask close earlier in this same run, so there's no abuse vector. |
| `core/workspace.py` `IterationMetrics` | New field `subtask_messages_stashed: int = 0`, `subtask_messages_restored: int = 0`. Counts of message blocks stashed/restored per iteration — useful for proving the win quantitatively. |
| `tests/integration/test_subtask_context_hotswap.py` | **New.** Happy path: open subtask, simulate 3 tool calls, close → message list shrinks; recall → restores. Stash key isolation between two subtasks. Re-stash after recall + re-close. |
| `tests/integration/test_subtask_hotswap_metrics.py` | **New.** Asserts `sub_llm_breakdown["summarise-subtask"]` is populated and `subtask_messages_stashed` reflects the swap. |
| `tests/agents/tools/test_recall_subtask.py` | **New.** Unit-level: missing id → error; happy splice; idempotent re-recall. |

## Marker shape (locked)

```
[SUBTASK CLOSED — id=s1, sha=abc1234]
summary: <one-line outcome from summarise-subtask>
deliverables: [routes/login.py, tests/test_login.py]
recall via recall-subtask("s1") to see the full tool history
```

Single user-role LLMMessage, plain string content. The `recall via`
hint is the affordance the agent needs to know the data is retrievable
— without it, the agent forgets it can recall (one of the master
plan's risks).

## Implementation order (fine-grained)

1. Add `IterationMetrics.subtask_messages_stashed/restored` fields
   (defaults zero — backward compatible).
2. Add `_stashed_subtask_messages: dict[str, list[LLMMessage]]` to
   runner `__init__`.
3. Build `RecallSubtaskTool` in `agents/tools/recall_subtask.py`.
4. Register `RecallSubtaskTool` in `LOCAL_TOOL_CLASSES` and add to
   `_instantiate_local_tools` with the right closures.
5. Add `recall-subtask` to `BASELINE_LOCAL_SCOPES` (it's cheap and
   inert without prior stash).
6. Rename `_compact_messages_after_close` → `_hot_swap_subtask_context`
   in `core/runner.py`. Two behaviour changes:
   - Stash the extracted span before mutation.
   - Compose the summary marker via Track 12's `summarise-subtask`
     wrapper called runner-side (not as an agent tool — the agent
     never sees the dispatch).
7. Add the marker-format extension (`deliverables:` + `recall via …`).
8. Tests:
   - `tests/agents/tools/test_recall_subtask.py` — unit happy path,
     missing-id error, idempotent re-recall.
   - `tests/integration/test_subtask_context_hotswap.py` — close
     subtask → message list shrinks; recall → restores;
     re-stash-after-recall is idempotent; isolation between two
     subtasks.
   - `tests/integration/test_subtask_hotswap_metrics.py` —
     `sub_llm_breakdown["summarise-subtask"]` populated;
     `subtask_messages_stashed` reflects the swap.
9. Full sweep + commit per the commit plan below.

## Acceptance criteria

1. **Stash + marker.** Open subtask `s1`, simulate 3 tool calls (each
   ~500 tokens of result). Close `s1` via COMMIT. The agent's
   `_current_agent_messages` list has shrunk by N blocks where
   N = 3 assistant-tool-use + 3 user-tool-result blocks, replaced by
   a single user-role marker matching the format above.
2. **Track 12 dispatch fired.** `summarise-subtask` was called once
   with the subtask's goal + the trace. Cost lands in
   `IterationMetrics.sub_llm_breakdown["summarise-subtask"]`. Token
   counts > 0.
3. **Recall.** Calling `recall-subtask("s1")` from a later subtask
   splices the stashed messages back into `_current_agent_messages` at
   the position of the marker. Marker is removed; original blocks
   restored in order.
4. **Idempotent.** Re-closing `s1` after a recall stashes again
   (overwrites previous stash entry; new marker takes its place).
5. **Isolation.** Two simultaneously-stashed subtasks don't collide;
   recalling one leaves the other stashed.
6. **Sub-LLM failure isolated.** If `summarise-subtask` errors (e.g.
   no tool-capable model in registry), the runner falls back to the
   today's static `record.summary` text and proceeds — never
   crashes the run.
7. **End-to-end token saving.** A 3-subtask decomposition with
   hot-swap reduces final iteration's `input_tokens` by ≥40% vs the
   same flow without hot-swap (measured against a synthetic test that
   runs both modes).
8. **Metrics.** `subtask_messages_stashed > 0` after a closure;
   `subtask_messages_restored > 0` after a recall.

## Files NOT touched (intentional)

- `core/protocol.py` — no new block kinds. Subtasks reuse COMMIT.
- `core/task_summarizer.py` — Track 12's `summarise-subtask` wrapper
  produces the same record shape; no merge needed.
- The `ContextManager` / `ContextSnapshot` dead-code path stays as is
  (Track 1c made it dead and we're not reviving it).

## Risks

- **Summary loses critical detail.** The marker hides specifics. If a
  later subtask needs them, the agent must explicitly call
  `recall-subtask`. Mitigation: marker text *says* "recall via
  recall-subtask(...)" — reminds the agent the data is retrievable.
- **Stash leaks.** Process-memory only; on a crash mid-iteration, the
  stash is lost. Acceptable — the original tool calls are still in
  `agent_turns.jsonl` for forensics. Document.
- **Marker matching brittleness.** `recall-subtask` finds the marker
  by string match (`[SUBTASK CLOSED — id=s1`). If the LLM's free-form
  reply happens to include that exact prefix, false positive. Use a
  unique sentinel format and search ONLY in user-role messages with
  `role == "user"` to narrow the surface.
- **Sub-LLM cost surfaces here for the first time at scale.** If
  every subtask close fires a `summarise-subtask` call, cost is
  N_subtasks × ~200 input tokens × ~150 output tokens at the
  configured model. Defaults to cheapest tool-capable, so net cost is
  small, but worth showing in the run analytics row before this
  rolls out widely.
- **Recall used too eagerly.** An agent that recalls every subtask
  every turn defeats the purpose. Mitigation: log a warning at
  iteration end if `subtask_messages_restored > 3` for one role per
  iteration (cheap bookkeeping, surfaces a regression).

## Commit plan

```
feat(track-8): IterationMetrics fields for stash/restore counts
feat(track-8): _stashed_subtask_messages on runner + recall plumbing
feat(track-8): recall-subtask tool (read-class, baseline)
refactor(track-8): rename _compact_messages_after_close → _hot_swap_subtask_context
feat(track-8): hot-swap calls summarise-subtask + new marker format
test(track-8): unit + integration coverage for stash/recall + metrics
```

Each phase: ~50–120 LOC, ~30–80k tokens. Total estimated: ~250k of the
master-plan budget for Phase 8.

## Status — what shipped vs. what's deferred

Shipped (`dfa8958`):
- `_compact_messages_after_close` upgraded to stash + new marker.
- Stash dict on runner, `recall-subtask` tool, baseline RBAC.
- New `[SUBTASK CLOSED — id=…]` marker format with deliverables +
  recall hint. Compaction recognises both new and legacy markers.
- 7 integration tests + 4 updated compaction tests.

**Deferred** (acceptance criterion #2 from the plan):
- Marker summary text still comes from M5.5's existing task_summarizer
  (agent's main LLM client), NOT Track 12's `summarise-subtask`
  wrapper. The stash architecture doesn't need it; this is a
  cost-attribution improvement only. Hooking it up means routing
  task_summarizer through Track 12's resolver → cost moves from the
  agent's heavy tier to the project's `sub_llm_models[summarise-subtask]`
  pick (typically a cheap model). Bounded follow-up: ~30-50 LOC,
  one new test asserting `IterationMetrics.sub_llm_breakdown` rolls.
- The plan's #7 acceptance ("≥40% input_tokens reduction") is left
  as an empirical check — needs a real run on a 3-subtask
  decomposition to measure honestly. Tested mechanically in
  test_close_subtask_stashes_message_span_and_inserts_marker (live
  list shrinks to a single marker).

## After this lands

- The codebase_analyst class of failure has a real architectural fix:
  the agent's first 18 LLM calls accumulate cleanly under one root
  task → close that subtask → next iteration sees a 1-line marker
  instead of 1.16M raw token characters of tool output.
- Track 11 (worker cognition view) now has meaningful state to render
  — "context window: subtask T3 (24K tokens)" stops being aspirational.
- Track 9 (protocol-cleaner sidecar) becomes the obvious next move:
  same Track 12 primitive, different consumer (auto-fire on
  PROTOCOL_PARSE_FAIL, with `protocol-cleanup` as the rewriter).
