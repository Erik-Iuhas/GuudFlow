# Track 3g — Tools-array slim (compress what we send to the API)

**Status:** Specced. **Not urgent — pick up after Track 9 lands or
when measurement shows the savings would matter.**
**Token budget:** 50–120k. Bounded scope.
**Depends on:** Track 3d.4 (manifest auto-render — established
`when_to_use` / `priority`).

---

## Why

Measured 2026-05-02 against the full 42-tool surface:

| Surface | Tokens |
|---|---|
| `render_for_prompt` (Layer 5 of system prompt) | ~3.0K |
| **JSON tools array** (Anthropic API requirement) | **~6.9K** |
| Combined worst-case overhead | ~10K |

Per-agent typical (~25 baseline reads + a few writes) is ~7K combined.
Not the dominant cost (Track 8 targets the much bigger 24K+ accumulated
tool *outputs*), but cheap to win since the tools array doesn't change
mid-iteration and prompt-cache windows reuse it.

The redundancy: every tool's `description` exists in BOTH the system
prompt catalog AND the tools array. The agent only needs ONE place to
read it for tool selection — picking which is fine, but paying for
both is wasteful.

## Outcome

- Strip / compress fields in the tools-array JSON sent to Anthropic's
  API while keeping everything in `render_for_prompt` for the agent
  to read at decision time.
- Specifically:
  - Drop `description` from the tools-array entries when
    `when_to_use` is set on the local tool. The system-prompt
    catalog already shows `name + when_to_use + description` so the
    LLM has the context it needs there.
  - Recursively drop `description` fields from `input_schema`
    properties — Anthropic accepts schema without per-prop
    descriptions; type info alone is enough for arg structure.
  - Keep `name`, `input_schema.type`, `input_schema.properties`
    (with descriptions stripped), `input_schema.required`.

Estimated savings: **~2–3K tokens** on a typical agent (~30%
reduction on the 7K JSON), more on full-surface agents.

## Files to modify

| File | Change |
|---|---|
| `core/tool_manifest.py` | New method `ToolManifest.to_api_tools(slim: bool = True) -> list[dict]`. When `slim=True`, drops descriptions per the rules above. Default `True` since the system prompt has the same info. |
| `core/runner.py` LLM-call site | Replace today's tools-array construction with `manifest.to_api_tools(slim=True)`. Locate by grepping for `tools=...` in the `_llm.complete()` call. |
| `tests/core/test_tool_manifest.py` | New tests: `to_api_tools` with `slim=True` strips description; with `slim=False` keeps it; nested `input_schema` descriptions also stripped. |

## Acceptance criteria

1. `ToolManifest.to_api_tools(slim=True)` returns dicts whose top-level
   `description` is omitted when the source tool has `when_to_use`
   set, AND whose `input_schema.properties[*].description` keys are
   removed.
2. `to_api_tools(slim=False)` returns the full dicts (today's
   shape) — escape hatch for debugging or models that genuinely
   need the long-form description.
3. Token count of `json.dumps(to_api_tools(slim=True))` is at least
   25% smaller than `slim=False` on the full 42-tool surface.
4. Existing integration tests (where the agent calls a tool by name)
   continue to pass — the LLM still picks tools correctly with
   descriptions only in the system prompt.
5. `RestoreSubtaskTool` and other tools without `when_to_use` keep
   their description in the API array (no info loss for those).

## Risks

- **LLM picks the wrong tool.** Anthropic's docs recommend including
  description in the tools array. Stripping it relies on the system
  prompt being the authoritative spec. Mitigation: integration tests
  exercising tool selection across a representative agent must pass
  byte-identically. If they don't, ship slim=False as default and
  let the user opt in per project.
- **Tools that don't have `when_to_use` lose their description in
  the API array** — wait, no, the rule is keep description IF
  when_to_use is missing. Confirm the helper preserves that
  fallback.
- **Anthropic API rejects empty descriptions.** Test against a real
  call. If it does, fall back to single-character placeholder.

## Why this is small

Pure mechanical compression. No new behaviour, no new abstractions,
no agent-facing changes. The dual residency of descriptions today is
just historical accident — Track 3d.4 added `when_to_use` to the
system-prompt catalog without revisiting the API array.

Skip if Track 9's win on retries is bigger and we don't see real
runs hitting context pressure.
