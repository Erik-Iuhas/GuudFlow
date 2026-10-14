# Stream Jack's items while he drafts (thin)

**Goal.** While Jack works, the Plan tab shows each change as soon as he has written it, greyed
as "being written", instead of "Jack is drafting…" until the turn ends. Nothing about what
counts changes: a streamed item is a preview only; the draft still fills from the validated turn
(`validate_turn`, repairs, `merge_pm_turn`), so nothing half-written can be ticked.

## Pieces (each reuses what is there)

| # | Piece | Reuses | Where |
|---|---|---|---|
| P1 | **Streaming call, optional.** A client may offer `complete_streaming(..., on_text)`; it calls `on_text` with the tool-call arguments written so far and returns the same `LLMResponse` as `complete`. Clients without it keep working: the PM falls back to `complete` (and the mock client, every test and every other caller are untouched). | `_api_to_response` (Anthropic), `_from_openai_response` (OpenAI-compatible: the stream is folded back into one response body) | `core/llm/anthropic.py`, `core/llm/openai.py` |
| P2 | **Read finished items out of half-written JSON.** `complete_items(text, key)` scans the arguments for `"changes": [`, and returns every array element whose closing brace has arrived (string- and escape-aware depth count, then `json.loads` per element). No partial-JSON library. | none | `core/partial_json.py` |
| P3 | **PM preview hook.** `ProjectManager.on_preview(changes)` is called whenever the number of finished items grows (first attempt and repair calls alike). | `_ask` loop | `core/project_manager.py` |
| P4 | **Preview endpoint.** The plan route keeps the latest preview per project in `app.state.pm_previews` while a turn runs and drops it when the turn ends; `GET /plan/preview` returns `{active, changes}`. | `_run_turn` | `api/routes/plan.py` |
| P5 | **Studio.** While a turn is pending, the Plan tab polls the preview each second and lists the items so far (op and title or text), greyed, with "N so far". When the turn returns, the real draft replaces it. | `usePlan` query keys, `Conversation` pending line | `studio/src/features/plan/` |

## Not in it
- No websocket: a one-second poll during a turn is enough and reuses the HTTP routes.
- No streaming of Jack's prose reply.
- No early validation of streamed items: they are shown, not checked, until the turn ends.

## Tests
- `complete_items`: finished elements only, braces and quotes inside strings, escaped quotes,
  nothing before `"changes": [`.
- PM: a fake streaming client feeds the arguments in chunks; `on_preview` sees 1 then 2 items;
  the final draft is the same as without streaming.
- Route: `/plan/preview` is inactive with no turn running.
- Studio: pending with a preview lists the items greyed.
