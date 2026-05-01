# Track 3d — Agent I/O Hygiene

**Status:** Ready for implementation. Sonnet-executable.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 350–550k. Stop and reassess if over 700k.
**Depends on:** Track 1 (for measurement, plus 3d.2 fixes Track 1's instrumentation gap).
**Related:** Track 3a (outline funnel — drift on its adoption is exactly what 3d.4 prevents). Track 3c (tool output compression — same chokepoint, complementary).

---

## Why this is one track, not four

A real run (`run_75df8cf97a` — 3 agents, pygame project) surfaced four issues that look separate but share one root cause: **agents are being fed noisy, repetitive context AND lack consistent guidance on what tools to reach for first.**

Evidence from that run:

| Symptom | Data |
|---|---|
| Board signal noise | 130 of 146 signals are paired `FILE_CLAIM` + `FILE_RELEASE`. Each pair carries the path twice. 89% of signal traffic is bookkeeping noise. |
| Tool-call burst inefficiency | `game_logic_dev` did 25× `fs-write` then 13× `fs-edit` in sequence. 38 conversation turns where each tool result is "Wrote N bytes to <path>" — carried forward in every subsequent call's history. |
| Track 1 instrumentation lying | All `repo_map_tokens` / `relevant_files_tokens` / `context_tokens_total` rows are `0`, despite agents spending 14k–112k uncached input tokens. We can't tune what we can't measure. |
| Persona drift on Track 3a | Both `game_logic_dev` and `pygame_renderer_dev` had `code-outline` granted. Renderer used it 7 times. Logic dev never did — went straight to `fs-read` 18 times. The funnel guidance from Track 3a wasn't in logic dev's effective prompt. |

The unifying theme: **what the agent sees and how it chooses tools.** Fix them together, ship one coherent change.

---

## Scope

Five work items, in dependency order:

- **3d.1** — Board signal compression at context injection (highest leverage on this data).
- **3d.2** — Track 1 context-metrics instrumentation fix (must land before measuring 3d.1's win).
- **3d.3** — Batch `fs-write-batch` and `fs-edit-batch` tools (removes the root cause that 3d.1 is collapsing).
- **3d.4** — Tool manifest auto-rendered into system prompt (drift prevention).
- **3d.5** — Estimated-cost column in iteration metrics + Run Analytics panel.

**Out of scope:**
- Cross-call conversation-history compression (was "Track 3f" in earlier draft) — risky, defer.
- Compressing the full board on disk — `board.jsonl` stays forensic-grade, append-only, untouched. Compression is purely at the *render-into-prompt* boundary.
- Re-architecting `FILE_CLAIM` / `FILE_RELEASE` to not exist — they're advisory safety per CLAUDE.md §32; keep them.
- Auto-generating *persona-specific style* from the manifest. Only the tool-guidance section is auto-rendered; everything else stays hand-authored.

---

## On drift specifically (why 3d.4 exists)

The danger is not bad prompts. The danger is that "what the agent can call" and "what the agent knows it should call" live in two places:

1. **`ToolManifest`** — runtime, code-driven (CLAUDE.md §31). Source of truth for *capability*.
2. **Persona system prompt** — text, hand-maintained. Source of truth for *guidance*.

When (2) lags (1), the agent has superpowers it doesn't know about. That's silent: nothing fails, nothing alerts. The Track 3a funnel showed this exactly — `code-outline` was granted to both devs, only one used it. We caught it only because we instrumented Track 1 and went looking; in production this would have rotted indefinitely.

The structural fix is to make the tool-guidance section of every prompt **render from the manifest at run start**. Each tool class declares:
- A short description (what it does).
- A `when_to_use: str` field (when an agent should reach for it).
- A `priority: int` ordering (e.g., outline before read).

`effective_system_prompt()` injects an auto-generated section listing the agent's actual granted tools, in priority order, with their when-to-use lines. Hand-written prose in personas can still mention tools, but the canonical guidance lives in the manifest. Adding a tool = filling in fields on the tool class. No hand-sync.

This makes the Track 3a funnel self-enforcing: the funnel order *is* the priority numbers on the tool classes, and every persona that has those tools sees the funnel automatically.

---

# 3d.1 — Board signal compression at render-time

## What

When `ContextManager.get_context()` ([core/context_manager.py:198](core/context_manager.py:198)) populates `ctx.recent_signals`, route the signals through a compressor before they hit the agent's prompt. Storage layer untouched.

## Strategies

Apply in order:

1. **Pair collapse**: a `FILE_CLAIM` followed by a same-author `FILE_RELEASE` for the same path with `outcome=written` collapses to one line: `<author> wrote <path>`. Only when the pair is matched and uneventful (no other signals between with the same path).
2. **Default-field stripping** in `detail` JSON: drop `reason: ""`, `estimated_duration_s: <default>`, `commit_hash: null`, any field whose value equals the dataclass default.
3. **Same-author same-type run grouping**: 5+ consecutive collapsed-pair lines from the same author → `<author> wrote 5 files in docs/design/: README.md, game-spec.md, ...`. Path-prefix grouping mirrors Track 3c's `git-changed-files` strategy.
4. **Empty `seen_by` arrays** stripped (only render when populated).
5. **Truncate verbose `summary` repetition**: if `summary` starts with `<author>: ` and `from_agent` already equals that author, drop the prefix.

## Files

| File | Change |
|---|---|
| `core/signal_render.py` | **New module**. `render_signals_for_prompt(signals: list[Signal]) -> str`. Pure function. |
| [core/context_manager.py](core/context_manager.py) | Replace inline `recent_signals` rendering at line 198+ with a call to `render_signals_for_prompt()`. |
| `tests/core/test_signal_render.py` | **New file**. Fixture-based: take real `board.jsonl` from `data/runs/run_75df8cf97a/`, assert reduction ≥ 70%, assert no information loss for non-paired signals. |

## Acceptance

- On `run_75df8cf97a`'s board, `len(rendered) <= 0.30 * len(naive_render(signals))`.
- Single-signal types (GIT_COMMIT, COMPLETE, STARTED) render unchanged byte-for-byte.
- Round-trip test: any signal whose information is needed by an agent (TASK_ASSIGNED, GIT_MERGE, USER_COMMENT) is preserved verbatim.

---

# 3d.2 — Track 1 context & logging instrumentation fix

## What

Two related logging gaps from `run_75df8cf97a`:

**Gap 1: missing file.** Track 1 specced `data/runs/{run_id}/context_log.jsonl` (one row per `get_context()` call, broken down by source). The file does not exist in the run directory at all. The other persisters (`board.jsonl`, `tasks.jsonl`, `iteration_metrics.jsonl`, `artifacts.json`) all wrote successfully — so the persister wiring pattern works; this one specifically isn't attached or isn't firing.

**Gap 2: zero values.** Where `iteration_metrics.jsonl` *does* exist, every row shows `repo_map_tokens: 0`, `relevant_files_tokens: 0`, `git_diff_tokens: 0`, `context_tokens_total: 0` — despite agents spending 14k–112k uncached input tokens.

Three possible root causes, distinct, must be diagnosed in order:

(a) **Persister never attached** — [core/runner.py](core/runner.py) doesn't wire `_context_log_persister`. Easy to verify: search the runner for `_context_log_persister` assignment. Pattern should mirror the other persister setups around [core/runner.py:698](core/runner.py:698).

(b) **`get_context()` never called by these agents** — `game_logic_dev` did 18 raw `fs-read` calls; if it skipped context_manager entirely, both the snapshot AND the file are empty for legitimate reasons. In that case, the metric is correctly 0 *for context_manager flow*, but we need a separate `direct_file_read_tokens` field to capture the bypass path so the row tells the truth.

(c) **`record_context_snapshot()` fires but doesn't aggregate** into `IterationMetrics`. Track 1's design has both: persist *and* aggregate. The second half may be the missing piece.

(c) is the one most likely to apply to all three agents simultaneously. (a) is also possible. (b) is a per-agent variation.

## Steps

1. **Look at the runner**: confirm `_context_log_persister` is attached at run start. If absent, fix path (a) — add it next to the `iteration_metrics.jsonl` setup. Re-run a flow, confirm `context_log.jsonl` now exists.
2. **Look at `get_context()`**: add a debug log at function entry. Re-run. If it never fires for an agent that did fs-reads, you have path (b). Add `direct_file_read_tokens: int = 0` to `IterationMetrics` and capture `fs-read` / `fs-read_range` / `fs-list` output token counts there (using Track 3c's compressed token count, post-compression).
3. **Look at the aggregation**: in `BaseAgent.run()`'s finally block, sum `workspace.context_log` rows filtered by `(role, iteration)` into `_current_metrics.{repo_map_tokens, relevant_files_tokens, git_diff_tokens, context_tokens_total}` before persisting the metrics row.

## Files

| File | Change |
|---|---|
| [core/runner.py](core/runner.py) | Verify/add `_context_log_persister` attachment writing to `data/runs/{run_id}/context_log.jsonl`. Pattern mirrors other persisters at line ~698. |
| [core/context_manager.py](core/context_manager.py) | Verify `record_context_snapshot()` is called at every `get_context()` exit; add it if missing. |
| [agents/base.py](agents/base.py) | At iteration finalize: aggregate `ContextSnapshot`s into `IterationMetrics`. Capture direct `fs-read` token counts into a new `direct_file_read_tokens` field. |
| [core/workspace.py](core/workspace.py) | Add `direct_file_read_tokens: int = 0` to `IterationMetrics`. |
| `tests/agents/test_base_metrics.py` | Add three tests: (1) `context_log.jsonl` is written on a real `get_context()` call, (2) `IterationMetrics` aggregation reflects context tokens, (3) direct fs-read path captures into `direct_file_read_tokens`. |
| `tests/integration/test_context_logging.py` | **New file**. End-to-end: run a small flow with both context_manager and direct-read agents. Assert both files exist (`context_log.jsonl` and `iteration_metrics.jsonl`) and totals reconcile against actual LLM input tokens. |

## Acceptance

- After re-running a 2-agent flow against any project: `data/runs/{run_id}/context_log.jsonl` **exists** and has at least one row per `get_context()` call.
- Every `iteration_metrics.jsonl` row has at least one of `context_tokens_total` or `direct_file_read_tokens` non-zero (no more all-zero context rows).
- Sum of `(context_tokens_total + direct_file_read_tokens + recall tokens)` across an agent's iteration accounts for ≥ 80% of its `input_tokens` (the rest is system-prompt overhead and conversation history). If the gap is bigger than 20%, there's another untracked input source — flag it.
- Integration test passes.

## Files

| File | Change |
|---|---|
| [core/context_manager.py](core/context_manager.py) | Verify `record_context_snapshot()` is called at every `get_context()` exit. Verify the `ContextSnapshot` carries the right tokens. |
| [agents/base.py](agents/base.py) | At `_current_metrics` finalize-time (per Track 1's `finally` block), aggregate this iteration's `ContextSnapshot`(s) into the iteration's `repo_map_tokens` / `relevant_files_tokens` / `git_diff_tokens` / `context_tokens_total`. |
| `tests/agents/test_base_metrics.py` | Add: `test_context_tokens_propagate_to_iteration_metrics` — mock a `get_context()` call returning known sizes, run one agent iteration, assert the iteration row has non-zero context fields. |

## Steps

1. **Diagnose**: run `pytest tests/agents/test_base_metrics.py` against the existing Track 1 tests. If they pass, the bug is path (a). If the existing tests don't actually verify aggregation, that's the gap.
2. **If (a)**: agents going direct to `fs-read` is fine — that's their right. The metric `context_tokens_total` should reflect "tokens delivered via get_context()", which is genuinely 0 for those iterations. Add a separate `direct_file_read_tokens` field that sums tokens returned by `fs-read` calls (post-Track 3c compression). That tells the real story.
3. **If (b)**: aggregate the snapshots in BaseAgent's finalize block. One pass over `workspace.context_log` filtered by `(role, iteration)`, sum the four fields, write into `_current_metrics`.
4. **In both cases**: re-run a small project, confirm new `iteration_metrics.jsonl` rows have meaningful values.

## Acceptance

- After re-running a 2-agent flow, every row in `iteration_metrics.jsonl` has at least one of `context_tokens_total` or `direct_file_read_tokens` non-zero.
- A test asserting `record_context_snapshot()` → `IterationMetrics` aggregation passes.

---

# 3d.3 — Batch fs-write and fs-edit tools

## What

Add `fs-write-batch` and `fs-edit-batch` tools. Each takes a list of operations and applies them atomically per-file (still individual file claims; just one tool-call round trip).

```python
fs-write-batch:
  parameters:
    files: [{"path": str, "content": str}, ...]
  returns:
    "Wrote 25 files: src/{a, b, c}, src/world/{...}, src/state/{...}"

fs-edit-batch:
  parameters:
    edits: [{"path": str, "old_string": str, "new_string": str, "replace_all": bool=false}, ...]
  returns:
    similar grouped summary
```

Why: in `run_75df8cf97a`, `game_logic_dev` did 25 `fs-write` + 13 `fs-edit` — 38 turns. Each turn's tool result lives in conversation history, which the next turn's LLM call must process. Batching collapses 38 turns to 2.

## Files

| File | Change |
|---|---|
| `agents/tools/fs_batch.py` | **New module** with `FsWriteBatch` and `FsEditBatch` tool classes. Internally call existing `fs-write` / `fs-edit` per-file (with their existing claim/safety logic). |
| `agents/tools/__init__.py` (or registry) | Register the new tools. |
| [agents/abstract.py](agents/abstract.py) | Add to `default_tools` for any role that has `fs-write` / `fs-edit`. |
| `tests/agents/tools/test_fs_batch.py` | **New file**. Test atomic-per-file claim/release, partial failure (one file fails, others proceed, summary reports both), correct return string format. |

## Behavioural rules

- Batch tools use the same `_safe_path()` validation per-entry (CLAUDE.md §13).
- Each entry produces its own `FILE_CLAIM` + `FILE_RELEASE` signal pair. (3d.1 collapses these on render — the storage stays granular for forensics.)
- A failure in entry N does not roll back entries 1..N-1. Tool returns a summary listing both successes and failures. The agent sees structured output and can retry just the failed entries.
- Limit: max 50 entries per batch call. Beyond that, split. Prevents a single tool result from going pathological.

## Acceptance

- `pytest tests/agents/tools/test_fs_batch.py` green.
- Manual: re-run a project that previously did 20+ sequential `fs-write` calls. Confirm the agent uses the batch tool (after 3d.4's prompt changes land) and turn count drops accordingly.

---

# 3d.4 — Tool manifest auto-rendered into system prompt

## What

Today, persona prompts hand-list "the tools you have." Drift is silent. After this:

1. Each tool class gets two new metadata fields:
   ```python
   class FileOutline(Tool):
       name = "file-outline"
       description = "Return top-level signatures of a file."
       when_to_use = "BEFORE file-read, when you need to see structure but not bodies."
       priority = 20   # lower = reach first
   ```
2. `effective_system_prompt()` ([agents/persona.py](agents/persona.py) or wherever it lives — per CLAUDE.md §20, the 7-layer composition) gets a new layer between *base prompt* and *CoT primer*: **"Your tools (auto-generated)"**.
3. The auto-rendered section lists only the tools actually granted to the agent (per their `ToolManifest`), sorted by priority, formatted:
   ```
   Your tools (use in this order of preference):
     1. repo-map  (already in context — always-on)
     2. file-outline  — BEFORE file-read, when you need structure not bodies
     3. file-read  — when you actually need bodies
     4. git-diff  — to review YOUR OWN recent commits, scoped to commit range
     5. ...
   ```
4. Persona-specific guidance still goes in the hand-written persona prompt, but the canonical "what tools and when" is now manifest-driven.

## Files

| File | Change |
|---|---|
| `agents/tools/base.py` (the `Tool` base class) | Add `when_to_use: str = ""` and `priority: int = 100` fields. Backward-compatible defaults. |
| All existing tool classes | Populate `when_to_use` and `priority` for each. Outline=20, file-read=40, git-diff=60, etc. Funnel order encoded in priority numbers. |
| `agents/persona.py` (or `effective_system_prompt()` location) | Add Layer 4.5 (between base prompt and CoT primer): render auto-tool-guidance from the agent's `ToolManifest`. |
| `core/llm_psychology_rules.md` (CLAUDE.md §20) — **doc only update** | Update the 7-layer documentation to 8 layers, noting Layer 4.5. |
| `tests/agents/test_effective_system_prompt.py` | New test: mount an agent with two granted tools (`file-outline`, `file-read`), assert the effective prompt contains an auto-rendered section listing exactly those two in priority order with their `when_to_use` strings. |
| `tests/agents/test_drift_safety.py` | New test: grant a tool to a persona but DO NOT mention it in the persona's prose. Render the prompt. Assert the tool still appears in the auto-section. |

## Migration

- Tool classes without `when_to_use` get a generic placeholder ("see tool description") and a warning is logged at startup. Forces gradual adoption without breaking existing tools.
- Personas whose prose redundantly explains tools can stay as-is. The auto-section is additive, not replacing. Over time, hand-prose can be trimmed.

## Acceptance

- `pytest tests/agents/test_effective_system_prompt.py` green.
- `pytest tests/agents/test_drift_safety.py` green — explicitly proves that a tool not mentioned in persona prose still surfaces in the effective prompt.
- Manual: re-run `run_75df8cf97a`-equivalent flow. Inspect `game_logic_dev`'s effective prompt at first invocation. Confirm `file-outline` is listed with priority < `file-read`. Confirm tool usage in `iteration_metrics.jsonl` shows `code-outline` is now called by both dev agents.

---

# 3d.5 — Estimated cost as an analytics column

## What

Track 1 deliberately deferred dollar-cost estimation. Now that we have the token columns populated (after 3d.2), it's cheap to add: cost is a derived value from `model + token sums`, computed once at iteration finalize. Surfaces in the Studio Run Analytics panel as a per-iteration column and a run-total footer.

## Files

| File | Change |
|---|---|
| `core/pricing.py` | **New module**. Hand-maintained `MODEL_PRICING: dict[str, ModelPricing]` table mapping model name → per-1M-token rates (input, output, cache_read, cache_creation). Pure function `estimate_cost(model, m: IterationMetrics) -> float`. Unknown model → 0.0 + one-time warning log. |
| [core/workspace.py](core/workspace.py) | Add `estimated_cost_usd: float = 0.0` to `IterationMetrics`. Backward-compatible default for old rows. |
| [agents/base.py](agents/base.py) | In `_current_metrics` finalize block (the same `finally` Track 1 added): call `estimate_cost(m.model, m)` and write into `m.estimated_cost_usd` before persisting. |
| `studio/src/views/RunView/RunAnalytics.tsx` | Add a **"Est. cost"** column to the per-iteration table (USD, 4 decimal places, e.g. `$0.0312`). Add a footer cell with the run total. Place it next to the existing tokens columns; do NOT replace them. |
| `tests/core/test_pricing.py` | **New file**. Cover: known model returns expected cost (compute by hand for a fixture row), unknown model returns 0.0 + warning, cache_read priced separately from input. |
| `tests/agents/test_base_metrics.py` | Add: a finalized iteration row has `estimated_cost_usd > 0` when model is in the pricing table. |

## Pricing table shape

```python
@dataclass(frozen=True)
class ModelPricing:
    """Per-1M-token rates in USD. Source: vendor pricing pages, dated."""
    input_per_million: float
    output_per_million: float
    cache_read_per_million: float
    cache_creation_per_million: float
    source_url: str = ""
    last_updated: str = ""   # ISO date, for the table maintainer's reference


MODEL_PRICING: dict[str, ModelPricing] = {
    # Populate at implementation time with current Anthropic pricing.
    # Use exact model strings as they appear in LLMResponse.model
    # (e.g., "claude-sonnet-4-6", "claude-haiku-4-5-20251001").
    # Do NOT hardcode in this plan — vendor prices change.
    # Sonnet ~3x cheaper for cache reads vs uncached input.
}


def estimate_cost(model: str, m: IterationMetrics) -> float:
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        _warn_once_per_model(model)
        return 0.0
    return (
        m.input_tokens          * pricing.input_per_million          / 1_000_000
        + m.output_tokens         * pricing.output_per_million         / 1_000_000
        + m.cache_read_tokens     * pricing.cache_read_per_million     / 1_000_000
        + m.cache_creation_tokens * pricing.cache_creation_per_million / 1_000_000
    )
```

`_warn_once_per_model` uses a module-level set so a missing model name doesn't spam logs every iteration.

## Acceptance

- `pytest tests/core/test_pricing.py` green, including hand-computed fixture and unknown-model case.
- After re-running a flow with a known model, every `iteration_metrics.jsonl` row has `estimated_cost_usd > 0`.
- Studio Run Analytics shows "Est. cost" column populated and a run-total footer that matches the sum of the column.
- Unknown model: row writes `0.0`, log shows exactly one warning per unknown model name per process.

## Risks / things to watch

- **Pricing table staleness**: vendor prices change. The `last_updated` field is for the human maintainer; we don't auto-refresh. Acceptable — the table is a one-screen file, easy to update. Document the convention in the module docstring.
- **Cache pricing nuance**: Anthropic charges a one-time write premium and a steep read discount. Both must be tracked separately — already are in `IterationMetrics` (Track 1). Don't conflate.
- **Cost displayed as authoritative**: it's an estimate. The Studio column header should be "Est. cost" (not just "Cost"). A tooltip explains "Estimated from public pricing tables; actual billing may differ slightly."

---

## Combined verification (all five)

After all four ship:

1. `pytest tests/` full suite green.
2. Re-run a project equivalent to `run_75df8cf97a` (3-agent pygame build).
3. Compare new `iteration_metrics.jsonl` to the original:
   - Total tool-call count drops by ≥ 30% (3d.3 batching).
   - `context_tokens_total` is non-zero on every row (3d.2 fix).
   - Agents that previously didn't use `code-outline` now do (3d.4 drift fix).
4. Compare a rendered `recent_signals` block before/after on the same board:
   - Char count drops by ≥ 70% (3d.1 compression).
   - No `TASK_ASSIGNED` / `GIT_MERGE` / `USER_COMMENT` content lost.
5. Run a project where one persona has no hand-written tool guidance at all. Confirm it still behaves correctly (drift safety net working).

---

## Risks / things to watch

- **3d.1 over-collapsing**: a CLAIM with no matching RELEASE (because the agent crashed mid-write) must NOT collapse — render it as `<author> claimed <path> (no release)`. The renderer is informational, not a forensic shortcut.
- **3d.2 disambiguation**: if the bug is path (a), don't "fix" it by forcing `get_context()` calls. Agents that go direct to `fs-read` are exercising legitimate freedom; the metric just needs to reflect both paths.
- **3d.3 batch claim contention**: 50 simultaneous file claims could race against another agent's claims. Batch tool acquires claims sequentially per-entry; on a contested entry it waits per existing claim semantics (CLAUDE.md §32). Test with a fixture where another agent holds a lock on entry 5.
- **3d.4 prompt bloat**: an agent with 20 tools auto-renders 20 lines of guidance. Acceptable — that's still cheap relative to one file read. But cap `when_to_use` to ~80 chars per tool to keep total under ~2 KB.
- **3d.4 priority numbers as a public API**: changing a tool's priority later changes every prompt that uses it. Document the priority scale (10/20/40/60/80/100 buckets) and treat changes as semver-breaking.

---

## Combined commit plan

Order matches sub-track order. One commit per logical change.

```
# 3d.2 — fix instrumentation first so we can measure
fix(runner): attach _context_log_persister to write context_log.jsonl
fix(context): ensure record_context_snapshot fires at every get_context exit
fix(metrics): aggregate ContextSnapshot tokens into IterationMetrics
feat(metrics): direct_file_read_tokens field for non-context-managed reads
test(metrics): context_log.jsonl is written
test(metrics): context tokens propagate to iteration row
test(integration): input_tokens reconcile against tracked sources within 20%

# 3d.1 — compress what's measured
feat(signals): signal_render module with pair-collapse and grouping
refactor(context): route recent_signals through render helper
test(signals): real-board fixture with ≥70% reduction

# 3d.3 — remove the root cause behind the noise
feat(tools): fs-write-batch and fs-edit-batch
feat(tools): register batch tools; grant alongside fs-write/fs-edit
test(tools): batch atomicity, partial failure, claim contention

# 3d.4 — close the drift gap
feat(tools): when_to_use and priority fields on Tool base
feat(tools): populate when_to_use/priority on all built-in tools
feat(persona): auto-render granted tools in effective_system_prompt
docs(claude): document Layer 4.5 in §20 (LLM psychology rules)
test(persona): drift-safety — granted-but-unmentioned tool surfaces in prompt
test(persona): auto-section ordering and content shape

# 3d.5 — estimated cost surfaced in analytics
feat(pricing): MODEL_PRICING table and estimate_cost helper
feat(metrics): estimated_cost_usd field on IterationMetrics
feat(agent): compute cost at iteration finalize
feat(studio): Est. cost column and run-total footer in Run Analytics
test(pricing): known model, unknown model, cache pricing
test(metrics): finalized row has cost when model is priced
```
