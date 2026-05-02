# Track 12 — Sub-LLM dispatch primitive

**Status:** In progress. Sonnet-executable.
**Parent plan:** `~/.claude/plans/ok-so-i-think-mellow-rainbow.md` (Phase 1)
**Token budget:** 250–400k. Stop and reassess if over 500k.
**Depends on:** Nothing hard. Track 1 metrics make sub_llm_breakdown rollup observable.
**Unblocks:** Track 8 (uses `summarise-subtask`), Track 9 (uses `protocol-cleanup`), and any future "delegate-and-summarise" patterns.

---

## Why

Two real symptoms surfaced before this track:

- **codebase_analyst on QuickRefs-web** (run_f7d1422d5b): 19 LLM calls,
  142s, two PROTOCOL_PARSE_FAIL retries before user stopped it. The
  agent reasoned correctly but never closed with `[COMMIT]`. Heavy
  models drown in their own output by call 19; no escape valve to
  delegate the format-clean-up to a smaller model.
- **No primitive for "agent calls a small LLM."** Today only built-in
  agents (meta, tech_lead, memory_writer) call `_llm.complete()`
  directly. Tools have zero LLM access. So every "summarise this
  closed subtask" or "rewrite this prose into a `[COMMIT]` block"
  ends up either inside the agent's main reasoning loop (expensive,
  loses context) or as a new agent (massive overhead).

Track 12 ships **one foundational tool primitive** plus **two
opinionated wrappers** so future tracks can lean on it without
reimplementing dispatch plumbing.

---

## Outcome

- `llm-dispatch(system, user, max_tokens)` — generic one-shot LLM call,
  returns `{text, input_tokens, output_tokens}`. Per-call max_tokens
  hard-bounded server-side at 1024.
- `summarise-subtask(subtask_goal, tool_calls, max_tokens=200)` —
  pre-fills the system prompt to produce a `TaskCompletionRecord`-shaped
  summary (1-3 sentences + deliverables bullets).
- `protocol-cleanup(raw_response, expected_kind)` — pre-fills the
  system prompt to convert prose into a parseable
  `[COMMIT]`/`[VERIFY]`/`[DELTA]`/`[ACTION]` block.
- All three tools route through the same dispatch path; only the
  `system` prompt and post-processing differ.
- Per-use-case model selection lives in `ProjectConfig.sub_llm_models`
  as a `dict[str, str]` mapping `use_case → model_id`. Empty dict +
  registry-driven fallback to the cheapest tool-capable model. **No
  hardcoded tier defaults.**
- Metrics: per-use-case input/output/calls roll into a new
  `IterationMetrics.sub_llm_breakdown` field. Dashboards can show
  "summarise-subtask cost $X this run."

## Files to modify

| File | Change |
|---|---|
| `core/llm/registry.py` | ✅ **Done.** `cheapest_tool_capable() -> ModelInfo`. Picks lowest-cost tool-capable, non-deprecated, key-having model. Alphabetical tiebreak. |
| `core/runner.py` `ProjectConfig` | New field `sub_llm_models: dict[str, str] = field(default_factory=dict)`. |
| `worker/job_runner.py` `serialise_project` | Validate each value in `sub_llm_models` is a known model id. Construct ModelRegistry once, lookup each value. Unknown id → ValueError that surfaces through the existing project-load error handler. |
| `core/workspace.py` `IterationMetrics` | Add `sub_llm_breakdown: dict[str, dict[str, int]] = field(default_factory=dict)`. Each entry: `{"calls": int, "input_tokens": int, "output_tokens": int}`. |
| `agents/tools/llm_dispatch.py` | **New.** Three classes: `LlmDispatchTool`, `SummariseSubtaskTool`, `ProtocolCleanupTool`. Each `BaseTool` subclass with `name`, `description`, `priority`, `when_to_use`, `input_schema`, async `run()`. |
| `core/runner.py` `_build_local_registry` | Add the three new classes to `LOCAL_TOOL_CLASSES` (mirrors how other built-in tools are registered). |
| `core/runner.py` `_instantiate_local_tools` | When instantiating the dispatch tools for an agent, inject: (a) the agent's `_llm`, (b) a `model_resolver` closure that takes `use_case` and returns a `(LLMClient, str)` tuple via the project's `sub_llm_models` map (with registry fallback), (c) a `metrics_sink` closure that writes per-use-case roll-ups onto `agent._current_metrics.sub_llm_breakdown`. |
| `core/tool_manifest.py` | Register `llm-dispatch`, `summarise-subtask`, `protocol-cleanup` as known scope names. **Not baseline** — explicit grant required (these spend tokens). |
| `studio/src/types.ts` | Add `sub_llm_models?: Record<string, string>` to `ProjectConfig`. |
| `studio/src/views/Studio.tsx` | New section in the Team tab below "Max iterations" / "PR mode": "Sub-LLM models". Renders a small table — one row per known use case (`llm-dispatch`, `summarise-subtask`, `protocol-cleanup`) — with a model dropdown populated from `modelsApi.list()`, grouped by provider. Empty selection shows placeholder "(auto: cheapest tool-capable)". |
| `tests/core/test_llm_registry.py` | ✅ **Done.** 5 new tests cover lowest-cost pick, deprecated skip, no-tools skip, no-key skip, alphabetical tiebreak. |
| `tests/agents/tools/test_llm_dispatch.py` | **New.** Generic dispatch happy path; wrapper system prompts; resolver picks override; resolver falls back to cheapest; metrics sink fires; max_tokens bound enforced. |
| `tests/core/test_runner.py` | New: ProjectConfig accepts `sub_llm_models`; runner builds resolver that respects override and falls back. |
| `tests/api/test_settings.py` *(if applicable)* | If `sub_llm_models` lives on project not settings: extend `tests/api/test_projects.py` with a round-trip assertion. (Project-level field, not server-level.) |
| `tests/worker/test_job_runner.py` | `serialise_project` rejects unknown model ids with a clear error. |

## Tool surfaces (locked)

```python
# Generic primitive — agent crafts full prompt
{
  "name": "llm-dispatch",
  "priority": 90,           # high so it shows above bookkeeping tools
  "when_to_use": (
    "to delegate a small task to a cheap model — protocol cleanup, "
    "subtask summary, second opinion. Returns a single text response."
  ),
  "input_schema": {
    "type": "object",
    "required": ["system", "user"],
    "properties": {
      "system": {"type": "string", "description": "system prompt for the sub-call"},
      "user":   {"type": "string", "description": "user message"},
      "max_tokens": {"type": "integer", "description": "output cap, hard-bounded to 1024 server-side", "default": 512},
    },
  },
  # returns: {"text": str, "input_tokens": int, "output_tokens": int, "model": str}
}

{
  "name": "summarise-subtask",
  "priority": 95,
  "when_to_use": (
    "after closing a subtask — produces a TaskCompletionRecord-shaped "
    "summary the runner stashes in place of the subtask's raw tool history."
  ),
  "input_schema": {
    "type": "object",
    "required": ["subtask_goal", "tool_calls"],
    "properties": {
      "subtask_goal": {"type": "string"},
      "tool_calls":   {"type": "array", "items": {"type": "object"}},
      "max_tokens":   {"type": "integer", "default": 200},
    },
  },
  # returns: {"summary": str, "deliverables": list[str], "input_tokens": int, "output_tokens": int}
}

{
  "name": "protocol-cleanup",
  "priority": 95,
  "when_to_use": (
    "your last response had no valid protocol block — call this with "
    "your raw text + expected_kind to get a clean [COMMIT]/[VERIFY] block back."
  ),
  "input_schema": {
    "type": "object",
    "required": ["raw_response", "expected_kind"],
    "properties": {
      "raw_response": {"type": "string"},
      "expected_kind": {"type": "string", "enum": ["COMMIT", "VERIFY", "DELTA", "ACTION", "STATUS"]},
    },
  },
  # returns: {"protocol_block": str, "input_tokens": int, "output_tokens": int}
}
```

System prompts for the wrappers (locked-in copy):

```
SUMMARISE_SUBTASK_SYSTEM = (
  "You are a TaskCompletionRecord summariser.\n"
  "Given a closed subtask's goal and the agent's tool-call trace, "
  "produce a 1-3 sentence summary plus a bulleted list of deliverables.\n"
  "Output strict JSON: {\"summary\": str, \"deliverables\": [str, ...]}.\n"
  "No prose outside the JSON object."
)

PROTOCOL_CLEANUP_SYSTEM = (
  "You convert prose into AgentFlow protocol blocks.\n"
  "Given raw_response and expected_kind, output exactly one protocol block "
  "of that kind and nothing else. Schema reference:\n"
  "  [COMMIT]\\n  summary: <≤12 words>\\n  closes: task:<id>\\n\n"
  "  [VERIFY]\\n  result: PASS|FAIL\\n  detail: <required if FAIL>\n"
  "Do not invent task ids. If the raw response cited a task id, preserve it; "
  "if not, omit the closes: line."
)
```

## Implementation order (fine-grained)

1. ✅ `cheapest_tool_capable()` + tests (done — 5 tests passing).
2. `IterationMetrics.sub_llm_breakdown` field + minimal test that it
   defaults to `{}`.
3. `ProjectConfig.sub_llm_models` field + `serialise_project`
   validation.
4. `agents/tools/llm_dispatch.py`:
   - `LlmDispatchTool` first (generic).
   - Wire `model_resolver` interface.
   - Wire `metrics_sink` callable.
5. `SummariseSubtaskTool` and `ProtocolCleanupTool` as thin wrappers
   that delegate to the same dispatch path with locked system prompts.
6. Runner wiring: register the three tools, inject deps in
   `_instantiate_local_tools`.
7. Tool manifest scopes registered.
8. Studio types + UI table.
9. Full test sweep + commit per the commit plan below.

## Acceptance criteria (mirror the master plan)

1. ✅ Cheapest-tool-capable picker works (5 tests).
2. Agent with `"llm-dispatch"` scope can call generic tool; result
   matches MockLLMClient script.
3. `summarise-subtask` returns `TaskCompletionRecord`-shaped JSON.
4. `protocol-cleanup` returns a parseable block (round-trips through
   `core/protocol.py:parse_compact_block`).
5. Resolver respects `project.sub_llm_models[use_case]`; falls back to
   `cheapest_tool_capable()` on missing entry.
6. Unknown model id at project-load time → ValueError that surfaces
   in `serialise_project`.
7. `IterationMetrics.sub_llm_breakdown[use_case]` is populated after
   a dispatch.
8. Studio dropdown lists models from `/api/models` grouped by
   provider; saving + reload preserves the choice.
9. `max_tokens` request > 1024 is silently clamped to 1024.

## Commit plan

```
feat(track-12): ModelRegistry.cheapest_tool_capable() + tests
feat(track-12): IterationMetrics.sub_llm_breakdown field
feat(track-12): ProjectConfig.sub_llm_models + project-load validation
feat(track-12): agents/tools/llm_dispatch.py — primitive + 2 wrappers
feat(track-12): runner wires dispatch tools + per-use-case resolver
feat(track-12): tool_manifest registers llm-dispatch / summarise-subtask / protocol-cleanup scopes
feat(studio): per-use-case sub-LLM model picker in Team tab
test(track-12): dispatch happy path, RBAC, metrics roll-up, registry validation
```

## Out of scope (Phase 12 only)

- Subtask decomposition itself — Phase 7.
- Hot-swap / context stashing — Phase 8.
- Auto-invoking `protocol-cleanup` on PROTOCOL_PARSE_FAIL — that's a
  Track 9 follow-up; this phase ships the primitive, not the wiring.
- Streaming response support — `complete()` is request/response.
- Per-use-case rate limits beyond the per-iteration call cap.

## Risks (mirror master plan, restated)

- **Footgun loop.** Hard cap of 1024 max_tokens; per-iteration cap of
  5 dispatch calls per agent (matches M5.5 task-summarizer pattern).
- **Empty registry.** `cheapest_tool_capable()` raises clearly if no
  candidate. Project load surfaces the error.
- **Provider skew.** Cheapest-rule gracefully picks the only Anthropic
  model when that's all the user has; transparently switches to a
  cheaper local when it appears.

---

## Status — what shipped is dormant plumbing

**As of commit `1148bf1`, all three tools exist but none of them get
called automatically.** They're agent-callable, but:

- The agent doesn't know when its own response failed protocol parsing.
  The runner detects parse failures and appends a nudge user message
  ("end your response with EXACTLY this text…") — the nudge does NOT
  tell the agent to call `protocol-cleanup`. So the wrapper is reachable
  only if a system prompt explicitly directs the agent to it. Today no
  system prompt does. **Net cost: zero. Net value: zero.**
- `summarise-subtask` is in the same boat — designed to be called by
  the runner from inside `_compact_messages_after_close` once Track 8
  lands; not used otherwise.
- `llm-dispatch` (the generic) requires explicit RBAC grant + an agent
  prompted to use it for some narrow purpose. Possible but unlikely
  to fire in any current built-in agent.

This is the design — **Phase 12 ships the primitive, the consumers
ship in their own phases.** Protocol-cleanup needs Track 9 to wire it
into the runner's parse-fail handler so it fires automatically on
`PROTOCOL_PARSE_FAIL`, with the agent's raw response as input, BEFORE
the existing nudge retry. That's where the win lives. Same for
`summarise-subtask` once Track 8's hot-swap is live.

**Implication for ordering:** if the immediate pain is the protocol-
fail class of stalls (e.g. codebase_analyst on QuickRefs-web),
**Track 9 is higher ROI than Track 7** — it's bounded scope, doesn't
depend on subtasks, and turns the dormant primitive into actual cost
savings. Track 7 → 8 is the bigger architectural arc; Track 9 is the
direct fix.
