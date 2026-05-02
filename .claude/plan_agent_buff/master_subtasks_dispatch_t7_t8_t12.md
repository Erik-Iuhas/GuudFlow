# Plan — Subtasks (T7) + Context Hot-Swap (T8) + Sub-LLM Dispatch (T12)

## Context

Two compounding pressures surfaced during the Track 4 sweep:

- The codebase_analyst run on QuickRefs-web stalled because the agent
  burned through 19 LLM calls accumulating ~24K tokens of tool history,
  then failed to emit a `[COMMIT]` block at the end (PROTOCOL_PARSE_FAIL).
  Symptom of context bloat. Track 3d.2 (just shipped, dd7985e) confirms
  the diagnosis: `repo_map_tokens` and friends now report real numbers
  instead of zeros.
- Cheap models with small context windows are basically unusable on real
  repos today. Sonnet absorbs the bloat fine; Haiku loses protocol
  format. Same problem hits large models from the cost angle — they pay
  full freight to re-process the same stale tool outputs every call.

This plan ships three tracks that compose into one coherent answer:

1. **Track 12 — sub-LLM dispatch primitive.** A tool agents can call
   to fire a one-shot LLM request, with per-use-case model selection
   in the project dashboard. Foundational primitive that Tracks 8, 9,
   and future "delegate-and-summarise" patterns build on.
2. **Track 7 — subtask decomposition.** An agent breaks its own work
   into named subtasks within a single `run()` invocation. Reuses the
   existing `Task` model (parent_task_id, child_tasks already exist).
   Each subtask gets a clean message-history boundary.
3. **Track 8 — subtask context hot-swap.** When an agent closes a
   subtask, its message-history span is replaced with a summary marker
   produced by Track 12's `summarise-subtask` wrapper. A new
   `recall-subtask(id)` tool re-hydrates the original messages on
   demand if a later subtask needs the detail.

Erik's framing: this bucket system should help across the board — small
models gain working room, large models save money. Designed once, paid
back everywhere.

## Why this order

Track 12 ships first because it is bounded scope and immediately useful
on its own — the Track 9 protocol-cleaner sidecar can dispatch from it
the moment it lands. Track 7 ships next because Track 8 is meaningless
without subtask boundaries to swap on. Track 8 ships last and uses
Track 12's `summarise-subtask` wrapper internally, so 12 → 7 → 8 is a
strict dependency chain.

Each phase is independently revertable. If Track 8 turns out to be a
mistake, Track 7's subtask boundaries still help (smaller closure-time
compaction spans), and Track 12 still serves the protocol-cleaner
without us.

---

# Phase 1 — Track 12: Sub-LLM dispatch primitive

## Outcome

- New `llm-dispatch(system, user, max_tokens, use_case)` tool that any
  agent with the `llm-dispatch` RBAC scope can call.
- Two opinionated wrappers shipped alongside: `summarise-subtask` and
  `protocol-cleanup`. Both pre-fill the system prompt and route through
  the same primitive.
- **Project config: new `sub_llm_models` dict maps use-case name →
  registered model id** (a string id from `data/models.json` —
  e.g. `"claude-haiku-4-5-20251001"`, `"gpt-4o-mini"`, or any local
  model the user has configured). **No hardcoded "fast/solid/heavy"
  defaults** — those are provider-specific aliases that lie about
  what's actually available. Defaults are **empty dict + a
  registry-side fallback rule** (see resolution below). Settings page
  populates the dropdown from `GET /api/models`, grouped by provider,
  showing model id + cost-per-1K so the user picks honestly.
- The tool inherits the calling agent's `_llm` client but resolves the
  model at call-time from the use-case map: **explicit override → use
  it; otherwise fall back to the cheapest model in the registry that
  supports tool use** (registry already exposes `cost_per_1k_input` +
  `supports_tools`). Single rule, easy to predict, doesn't lock to
  Anthropic.
- Cost & token usage from sub-dispatches roll up into the calling
  agent's `IterationMetrics` row under a new `sub_llm_breakdown` field
  keyed by use_case (so the user sees "summarise-subtask cost $X this
  run" without parsing per-call rows).

## Files to modify

| File | Change |
|---|---|
| `agents/tools/llm_dispatch.py` | **New.** `LlmDispatchTool` (generic), `SummariseSubtaskTool` (wrapper), `ProtocolCleanupTool` (wrapper). All three subclass `BaseTool`. Each takes `agent_llm: LLMClient`, `model_resolver: Callable[[str], str]`, and `metrics_sink: Callable[[dict], None]` in __init__. |
| `core/runner.py` | `_build_local_registry` adds the three new classes. `_instantiate_local_tools` injects `agent._llm`, the resolver (a small closure over `_resolve_llm_for_tier` + project's `sub_llm_models`), and a metrics sink that bumps `_current_metrics`. |
| `core/runner.py` `ProjectConfig` | New field: `sub_llm_models: dict[str, str] = field(default_factory=dict)`. Values are model ids from the registry. Empty dict means every use-case falls back to the cheapest tool-capable model (resolution rule below). |
| `core/llm/registry.py` `ModelRegistry` | New method `cheapest_tool_capable() -> ModelInfo` — picks the model with the lowest `cost_per_1k_input + cost_per_1k_output` weighted equally where `supports_tools=True` and `deprecated=False`. Stable tiebreaker: alphabetical id. |
| `core/workspace.py` `IterationMetrics` | New field `sub_llm_breakdown: dict[str, dict]` — per-use-case token counts (`{use_case: {input, output, calls}}`). |
| `worker/job_runner.py` `serialise_project` | Read `sub_llm_models` from project dict; **validate each value is a known model id from the registry** (rejects typos / models the user hasn't actually configured). Unknown ids → ValueError surfaced through the existing project-load error path. |
| `core/tool_manifest.py` | Register `llm-dispatch`, `summarise-subtask`, `protocol-cleanup` as known scope names. Read tools by default? **No** — these spend tokens, so they require explicit RBAC grant. |
| `studio/src/types.ts` `ProjectConfig` | Add `sub_llm_models?: Record<string, string>` (model ids as strings — same shape as the existing per-agent `model` override field). |
| `studio/src/views/Studio.tsx` (project Team tab, near the agent list) | Render a small table: use-case → model dropdown. Dropdown options come from `modelsApi.list()` (already wired for the per-agent picker), grouped by provider, with cost-per-1K shown inline. Default-empty rows show "(auto: cheapest tool-capable)" as the placeholder. |
| `tests/agents/tools/test_llm_dispatch.py` | **New.** Generic dispatch happy path with MockLLMClient; wrapper pre-fills correct system prompt; model resolver uses use-case override; metrics sink fires; RBAC denial when scope absent. |
| `tests/api/test_settings.py` | Asserts `sub_llm_models` round-trips through GET/PUT. |

## Tool surface (proposed)

```python
# Generic primitive — agents craft full prompt
{
  "name": "llm-dispatch",
  "input_schema": {
    "system": "str — system prompt for the sub-call",
    "user": "str — user message",
    "max_tokens": "int — output cap, hard-bounded to 1024 server-side",
  },
  "returns": {"text": str, "input_tokens": int, "output_tokens": int}
}

# Opinionated wrappers — system prompt baked in
{
  "name": "summarise-subtask",
  "input_schema": {
    "subtask_goal": "str — the closed subtask's goal field",
    "tool_calls": "list[dict] — tool name + args + result for each call",
    "max_tokens": "int — defaults to 200",
  },
  "returns": {"summary": str}  // 1-3 sentences, deliverables bullet list
}

{
  "name": "protocol-cleanup",
  "input_schema": {
    "raw_response": "str — heavy model's prose output",
    "expected_kind": "Literal[COMMIT,VERIFY,DELTA,ACTION]",
  },
  "returns": {"protocol_block": str}  // emits e.g. [COMMIT]\n  summary: ...
}
```

## Acceptance criteria

1. An agent declaring `"llm-dispatch"` in `default_rbac_scopes` can call
   the generic tool; result text matches MockLLMClient script.
2. `summarise-subtask` produces a record matching the `TaskCompletionRecord`
   shape (Erik's existing M5.5 contract) so downstream code reuses
   `render_task_completion_summary`.
3. `protocol-cleanup` emits a parseable `[COMMIT]` / `[VERIFY]` block —
   round-trips through `core/protocol.py:parse_compact_block`.
4. **Model resolution honors registry, not tier defaults.**
   - Explicit `project.sub_llm_models["protocol-cleanup"] = "gpt-4o-mini"`
     → that exact model is used.
   - Missing entry → `registry.cheapest_tool_capable()` is picked.
   - Unknown model id at project load → ValueError, project refuses to
     load (no silent fallback that hides typos).
5. Token usage from sub-dispatches lands in
   `IterationMetrics.sub_llm_breakdown` keyed by use_case.
6. Settings UI dropdown is populated from `/api/models`, grouped by
   provider, with cost-per-1K visible. Saving + reloading preserves
   choices and round-trips through PUT.
7. `cheapest_tool_capable()` ignores deprecated models and tiebreaks
   alphabetically by id (deterministic across restarts).

---

# Phase 2 — Track 7: Subtask decomposition (intra-run)

## Outcome

Three real flows the design must support, all first-class:

- **Self-decomposition (no formal task).** Agent fired up against the
  project goal sees a synthetic root task on their desk; calls
  `decompose-task(subtasks=[...])` to break it up. (This is the
  codebase_analyst-on-QuickRefs-web case — the agent had no task to
  attach to and the goal was too big to handle in one shot.)
- **Delegator chunking at assignment time.** A delegator (architect,
  tech_lead) calls `assign-task(assignee, goal, subtasks=[...])` and
  the runner creates the parent + children atomically. One tool, one
  mental move.
- **Receiver further-decomposition.** A worker who got assigned a task
  decides it's still too big, calls `decompose-task` against that
  task's id. Same tool path as self-decomposition, just with an
  explicit parent_task_id.

Mechanics:

- New tool `decompose-task(subtasks: list[{goal, acceptance}], parent_task_id: str | None = None)`.
  No parent → attach to the agent's synthetic root for this run. Parent
  given → must be a task assigned to the caller and have no children.
- `assign-task` gains an optional `subtasks: list[{goal, acceptance}]`
  field. When present, runner creates the parent task assigned to the
  given assignee and the children atomically (all assigned to the same
  assignee).
- **Synthetic root task per run.** At run start, runner creates one
  `Task` per agent role with `goal=project.goal`, `task_id="root:{role}"`,
  `assigned_to=role`, `status=IN_PROGRESS`. Lives on the desk like any
  other task; serves as the default parent for self-decomposition.
  Closing it ends the run for that role (same cascade path).
- The runner advances the agent through subtasks: when one is COMPLETE
  (or BLOCKED / SAT_OUT / CANCELLED), the next PENDING child becomes
  active. Agent sees the active subtask's goal injected into context
  via a new effective_system_prompt layer (4.7 — "current subtask").
- When all children terminal, the parent auto-closes via the existing
  cascade logic ([core/runner.py:2072-2088](core/runner.py:2072)).
- Loop guard treats subtask transitions as forward progress so a long
  decomposed work item doesn't trip the wall-clock budget unfairly.

## Files to modify

| File | Change |
|---|---|
| `agents/tools/decompose_task.py` | **New.** `DecomposeTaskTool`. `parent_task_id` optional — defaults to the caller's synthetic root for this run. Refuses if explicit parent is unknown / not assigned to caller / already has children. RBAC scope: `decompose-task` (write). |
| `agents/tools/board.py` `AssignTaskTool` | Extend input_schema with optional `subtasks: list[{goal, acceptance}]`. When present, atomically create parent + children all assigned to `assignee`. RBAC scope unchanged — same `assign-task`. |
| `core/runner.py` synthetic root | At run start (after workspace/desks initialised, before scheduler kicks off), create one root Task per agent role with `task_id="root:{role}"`, `goal=project.goal`, `status=IN_PROGRESS`. Posts the standard TASK_ASSIGNED + TASK_STARTED signals so resumed runs replay the same way. |
| `core/workspace.py` `Workspace` | New `active_subtask: dict[role, str]` map. `mutate_task` updates it on START/COMPLETE/BLOCKED transitions. |
| `core/workspace.py` Task depth | Lift the depth-2 cap to depth-3 (synthetic root is depth-1, decomposed subtasks are depth-2, further decomposition is depth-3). |
| `core/runner.py` task-advance loop | Inside `_generic_tool_use_loop`, after a successful `_handle_task_closures` for a task with children, look up the next PENDING child and post `TASK_STARTED` for it. The agent's next LLM call sees the new active subtask in their prompt. |
| `agents/base.py` `effective_system_prompt` | New Layer 4.7 — "ACTIVE SUBTASK" — renders `workspace.active_subtask[role]` if present. Token-counted into `IterationMetrics.relevant_files_tokens` (closest existing bucket). |
| `core/protocol.py` | No change — `closes: task:<id>` syntax already supports child task IDs. |
| `tests/agents/tools/test_decompose_task.py` | **New.** Self-decompose (no parent) attaches to synthetic root; explicit parent happy path; refuses if parent unknown / not own task / has children; siblings preserve order. |
| `tests/agents/test_assign_task_tool.py` | Extend with: `assign-task` with `subtasks` field creates parent + children atomically; assignee owns all of them. |
| `tests/integration/test_subtask_advance_in_run.py` | **New.** Three flows end-to-end: (a) self-decompose against synthetic root, (b) delegator assigns with subtasks list, (c) receiver further-decomposes assigned task. Each closes via COMMIT, parent auto-closes, signals on board. |
| `tests/core/test_runner_desk.py` | Extend desk-render test — confirm 3-deep indentation renders cleanly; synthetic root visible. |
| `tests/core/test_runner.py` synthetic root | New: every run creates exactly one synthetic root per agent role; resumed runs don't double-create. |

## Acceptance criteria

1. **Synthetic root.** Every agent role has exactly one root task on
   their desk at run start, `task_id=root:{role}`, `status=IN_PROGRESS`,
   `goal=project.goal`. Resumed runs reuse the existing one (no
   double-create).
2. **Self-decompose.** `decompose-task(subtasks=[...])` with no parent
   attaches children to the caller's synthetic root. All children
   assigned to caller, all PENDING.
3. **Delegator chunking.** `assign-task(assignee="dev", goal="X",
   subtasks=[{goal:"a"}, {goal:"b"}])` creates parent X + children a,b
   atomically. All assigned to "dev". Parent IN_PROGRESS, children
   PENDING.
4. **Receiver further-decompose.** Worker-assigned task can be further
   decomposed by the assignee — depth-3 cap honored.
5. **Subtask advance.** After agent closes subtask 1 via
   `[COMMIT]\n  closes: task:s1`, `active_subtask` flips to subtask 2
   and the next prompt build surfaces subtask 2's goal.
6. **Cascade closure.** Closing the last child auto-closes the parent
   via the existing cascade. If that parent is the synthetic root,
   the run for that role is considered complete.
7. **Refusal cases.** `decompose-task` returns TASK_OUT_OF_SCOPE if an
   explicit parent is unknown, not the caller's, or already has
   children. `assign-task` with `subtasks` rejects empty subtask lists.
8. **Loop guard.** A 5-subtask decomposition gets the guard's
   wall-clock budget extended proportionally — each subtask transition
   resets the iteration-since-progress counter.

---

# Phase 3 — Track 8: Subtask context hot-swap

## Outcome

- When a subtask closes, its message-history span (every `assistant`
  and `tool_result` block since the subtask started) is replaced in
  `_current_agent_messages` with a single user message:
  `[SUBTASK CLOSED — id=s1, summary: <one line>, recall via recall-subtask("s1")]`.
- The summary comes from Track 12's `summarise-subtask` wrapper. Cost
  is logged into `IterationMetrics.sub_llm_breakdown["summarise-subtask"]`.
- A new tool `recall-subtask(subtask_id)` re-hydrates the stashed
  messages — runner stores them in a per-run dict keyed by subtask_id
  and splices them back into `_current_agent_messages` at the position
  of the summary marker.
- The context manager already has `_compact_messages_after_close`
  ([core/runner.py:2090-2147](core/runner.py:2090)) that does
  task-closure compaction with a static marker. Track 8 generalises it:
  the marker becomes the LLM-generated summary, and the original
  messages are stashed (today they're discarded).

## Files to modify

| File | Change |
|---|---|
| `core/runner.py` `_compact_messages_after_close` | Renamed to `_hot_swap_subtask_context`. Stash original span in `self._stashed_subtask_messages: dict[str, list[LLMMessage]]` keyed by subtask_id. Compose summary marker via Track 12's `summarise-subtask` (called as a runner-internal helper, not as an agent tool — agents shouldn't see this in their tool list). |
| `agents/tools/recall_subtask.py` | **New.** `RecallSubtaskTool`. Inputs: `subtask_id`. Effect: splices `runner._stashed_subtask_messages[subtask_id]` back into `runner._current_agent_messages` at the position of the summary marker. Returns `{ok: true, restored_messages: int}`. RBAC scope: `recall-subtask` (read). |
| `core/workspace.py` `IterationMetrics` | New field `subtask_messages_stashed: int` (count of stashed message blocks per iteration), `subtask_messages_restored: int`. |
| `tests/integration/test_subtask_context_hotswap.py` | **New.** Happy path: open subtask, do 3 tool calls, close → message list shrinks to summary marker; recall → restores. Stash key isolation between subtasks. Re-stash after recall + re-close. |
| `tests/integration/test_subtask_hotswap_metrics.py` | **New.** Asserts `sub_llm_breakdown["summarise-subtask"]` is populated and `subtask_messages_stashed` reflects the swap. |

## Acceptance criteria

1. Open subtask s1, call 3 tools (each producing ~500 tokens of result).
   Close s1 via COMMIT. `_current_agent_messages` has shrunk by N
   blocks where N = 3 tool-result + 3 assistant-tool-call pairs, replaced
   by a single 1-line summary marker.
2. `summarise-subtask` was called once with the closed subtask's
   tool calls. Cost is in `IterationMetrics.sub_llm_breakdown`.
3. Calling `recall-subtask("s1")` from a later subtask splices the
   original messages back; agent's next LLM call sees the full detail.
4. Re-closing s1 after recall stashes again (idempotent).
5. Pure agent-flow test: 3-subtask decomposition end-to-end with
   hot-swap reduces final iteration's input_tokens by ≥40% vs the
   same flow without hot-swap.

---

## Files NOT touched (intentional)

- `core/protocol.py` — no new block kinds. Subtasks reuse COMMIT.
- `agents/persona.py` — no persona schema changes; sub-LLM dispatch
  doesn't load personas.
- The `ContextManager` / `ContextSnapshot` dead-code path is left as is.
  Already discussed — Track 1c made it dead and we're not reviving it.

## Verification (cross-phase)

After all three phases land:

1. `pytest tests/` — full suite green; expect ~1620 tests.
2. Manual run on QuickRefs-web with codebase_analyst:
   - Without subtask use: same input_tokens as baseline (Phase 12+7
     don't change behaviour for agents that don't decompose).
   - With subtask use (architect agent decomposes the codebase scan
     into 3-4 subtasks): final iteration input_tokens drops from
     ~24K → ~10K. Confirmed by reading `iteration_metrics.jsonl`.
3. Settings page shows the per-use-case model dropdown for sub-LLM
   tools; saving + reloading preserves choices.
4. `recall-subtask` works in a 5-subtask flow where subtask 5 needs
   detail from subtask 2.

## Out of scope

- **Auto-decomposition.** Agents must explicitly call `decompose-task`.
  No automatic "this task looks big, break it up." That's a future
  archetype-level concern.
- **Cross-agent subtask sharing.** Subtasks created by agent A are not
  visible on agent B's desk. The existing `assign-task` tool covers the
  cross-agent case.
- **Persistent subtask history across runs.** Stashed messages live in
  process memory only — not flushed to disk. If the run crashes mid-
  iteration, the stash is gone. Acceptable; the original tool calls
  are still in `agent_turns.jsonl` for forensics.
- **UI surfacing.** Track 11 (worker cognition view) is the right home
  for visualising "current subtask" and "stashed context"; deferred
  per its plan.
- **Streaming sub-LLM responses.** `llm-dispatch` is request/response
  only. Streaming would force a different return shape.

## Risks

- **Sub-LLM dispatch as a footgun.** An agent could call `llm-dispatch`
  in a loop and burn the project's budget. Mitigations: hard cap of
  1024 max_tokens per call, per-iteration call cap of 5 (matches
  M5.5's task-summarizer pattern), surfaced in metrics so anomalies
  are visible.
- **Empty model registry.** If `data/models.json` has no tool-capable
  non-deprecated entry, `cheapest_tool_capable()` raises. Project load
  surfaces this as a clear "no usable model in registry" error rather
  than a silent fallback. Setup docs need a one-liner about populating
  `data/models.json` before first sub-LLM dispatch.
- **Provider availability skew.** If the user only has Anthropic
  configured, every use-case lands on Haiku — same end state as the
  old hardcoded "fast" default, but transparently so. If they later
  add a cheaper local model, dispatch automatically prefers it on
  next process start. No behaviour change required.
- **Summary loses critical detail.** The summary marker hides the
  closed subtask's specifics. If a later subtask needs them, the agent
  must explicitly `recall-subtask`. Risk: agent forgets it can recall.
  Mitigation: the marker text says "recall via recall-subtask(...)" —
  reminds the agent the data is retrievable.
- **Depth-3 cascade.** Lifting the depth cap from 2 to 3 means parent
  closure now cascades through one more level. Existing test in
  `test_protocol_tasks.py` covers depth-2 cascade; will extend.
- **Project config schema migration.** Existing projects don't have
  `sub_llm_models`. Loader must default-fill so existing projects
  keep working unchanged.
- **Synthetic root semantics.** Closing `root:{role}` ends the run for
  that role. If an agent accidentally closes their root before doing
  any work, the run is over for them. Mitigation: `decompose-task`
  warns if the agent tries to attach 0 children to root; closure of
  root via direct COMMIT is allowed (legitimate "I'm done" signal)
  but reviewer checks already gate that path.
- **Resumed-run double-creation.** Synthetic-root creation must be
  idempotent — run_loader replay must not create a second
  `root:{role}` task. Test covers this explicitly.

## Commit plan

```
# Phase 12
feat(track-12): llm-dispatch primitive + summarise/cleanup wrappers
feat(track-12): per-use-case sub_llm_models in ProjectConfig
feat(studio): Settings table for per-use-case sub-LLM models
test(track-12): dispatch happy path, RBAC, metrics roll-up

# Phase 7
feat(track-7): decompose-task tool — intra-run subtask creation
feat(track-7): runner advances active subtask on closure
feat(track-7): Layer 4.7 — active subtask in effective_system_prompt
test(track-7): subtask advance + parent auto-close

# Phase 8
refactor(track-8): rename _compact_messages_after_close → _hot_swap_subtask_context
feat(track-8): subtask context stash + summary marker
feat(track-8): recall-subtask tool re-hydrates stashed messages
test(track-8): hot-swap shrinks live history; recall restores
```

Each phase: ~5-7 commits, ~250-400 LOC, ~150-300k tokens. Total
estimated: 800k-1.2M tokens across all three. Within budget.
