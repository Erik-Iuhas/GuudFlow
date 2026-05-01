# Track 1b — Logging Truth-Telling (Sprint 1.5)

**Status:** Ready for implementation. Sonnet-executable.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 80–150k. Stop and reassess if over 200k.
**Sprint slot:** 1.5 — between Sprint 1 (Track 1 shipped) and Sprint 2.
**Why standalone:** Lifted out of Track 3d.2. The other sub-deliverables of Track 3d (board signal compression, batch tools, drift fix, cost) can wait. This one cannot — without it every Sprint 2 track ships blind.

---

## Why this is sprint-1.5 critical

Two consecutive runs (`run_75df8cf97a`, `run_26d48d4328`) show identical instrumentation pathology:

```
"repo_map_tokens": 0,
"relevant_files_tokens": 0,
"git_diff_tokens": 0,
"context_tokens_total": 0,
"direct_file_read_tokens": 0
```

…despite agents spending 240k+ uncached input tokens reading files. This is not a bug in Track 1's wiring — it's a bug in scope. Diagnosis shows:

| Layer | Status |
|---|---|
| Persisters attached at [core/runner.py:509-514](core/runner.py:509) | ✅ working |
| `record_context_snapshot()` called at [core/context_manager.py:225](core/context_manager.py:225) | ✅ working |
| Aggregation loop at [core/runner.py:1503-1514](core/runner.py:1503) | ✅ working |
| Built-in agents calling `get_context()` | ❌ **none do** |
| Code that ever assigns `direct_file_read_tokens` | ❌ **does not exist** |

So `context_tokens_total` is correctly 0 (nothing went through `get_context()`), and `direct_file_read_tokens` is 0 because no code path writes to it. The metric reads false. Every "did Track 3a help?" / "did Track 3c help?" question is unanswerable until this is fixed.

---

## Scope

**In scope:**
- Capture token counts from read-class tool results in `BaseAgent.call_tool()` and accumulate into `_current_metrics.direct_file_read_tokens`.
- Define which tools count as "read-class" via a hardcoded list (with a TODO to migrate to a class attribute when Track 3d.4 lands).
- Add a `tool_token_breakdown: dict[str, int]` field on `IterationMetrics` so we can see per-tool contributions, not just the aggregate. (E.g., `{"fs-read": 12000, "fs-grep": 8000, "code-outline": 1500}`.)
- Tests covering the capture path, aggregation, and the "no double-counting with `context_tokens_total`" invariant.
- Re-run `run_26d48d4328`-equivalent and confirm fields populate.

**Out of scope:**
- Forcing agents to call `get_context()`. They have legitimate reasons to go direct. The metric's job is to tell the truth, not to reshape behavior.
- Compressing tool output (Track 3c does that).
- Per-tool-call argument logging. We log the result token count, not what was queried.
- Fixing or removing `get_context()` itself. It still has a role (recall, repo_map, git_diff staging) — we're just acknowledging that direct reads are also a first-class path.

---

## Architecture

Single hook point: [agents/base.py:577](agents/base.py:577) `call_tool()`. After `tool.run()` returns and before the result is handed back to the agent's reasoning loop, count the result's token cost and — if the tool is in the read-class set — accumulate it.

```
agent calls call_tool("fs-read", {"path": "src/main.py"})
    │
    ▼
manifest check, find descriptor
    │
    ▼
tools_called.append(name)        ← already exists
    │
    ▼
result = await tool.run(arguments)
    │
    ▼
[NEW]  result_tokens = _approx_tokens(json.dumps(result, default=str))
       if name in READ_CLASS_TOOLS:
           m.direct_file_read_tokens += result_tokens
       m.tool_token_breakdown[name] = m.tool_token_breakdown.get(name, 0) + result_tokens
    │
    ▼
return result
```

Failure semantics: if the tool raised, do NOT count tokens (the agent saw an error, not file content). Compression (Track 3c, future) runs *before* this counter so we measure post-compression tokens — which is what the LLM actually receives.

---

## Read-class tool set

Tools whose output represents *content the LLM is going to reason over* (vs. confirmations / status):

```python
# agents/base.py — module-level constant
# TODO: migrate to a class attribute on Tool base when Track 3d.4 lands.
READ_CLASS_TOOLS: frozenset[str] = frozenset({
    "fs-read",
    "fs-read_range",
    "fs-list",
    "fs-grep",
    "fs-exists",         # tiny but counts; consistency
    "code-outline",
    "doc-markdown",
    "git-log",
    "git-diff",
    "git-status",
    "git-changed-files",
    "git-read",
    "desk-search",
    "web-fetch",
    "web-search",
})
```

Write-class tools (`fs-write`, `fs-edit`, `fs-create`, `git-commit`, `assign-task`, `desk-push`, `peer-rework`) get token counts in `tool_token_breakdown` for completeness but do not contribute to `direct_file_read_tokens`. Their results are typically `{"ok": true, "bytes_written": 1234}` — small, not context-bloating.

---

## Files to modify

| File | Change |
|---|---|
| [core/workspace.py](core/workspace.py) | Add `tool_token_breakdown: dict[str, int] = field(default_factory=dict)` to `IterationMetrics`. Existing `direct_file_read_tokens` field stays. |
| [agents/base.py](agents/base.py) | At top: add `READ_CLASS_TOOLS` constant + import `_approx_tokens` from `core.repo_map`. In `call_tool()` ([agents/base.py:577](agents/base.py:577)): after `tool.run()` returns successfully, compute result tokens and update `_current_metrics`. Mirror for the broker path at [agents/base.py:608-616](agents/base.py:608). |
| `tests/agents/test_base_metrics.py` | Add three tests (see below). |
| `tests/integration/test_logging_truth.py` | **New file.** End-to-end: run a small flow, assert that `direct_file_read_tokens > 0` and `tool_token_breakdown` has entries for any read-class tool actually called. |

No changes to runner, context_manager, or workspace methods. The persister and aggregation loop already work — they just had nothing to aggregate.

---

## Implementation steps

### Step 1 — IterationMetrics field
File: [core/workspace.py](core/workspace.py)

In the `IterationMetrics` dataclass (around line 89–128), add right after `direct_file_read_tokens`:
```python
tool_token_breakdown: dict[str, int] = field(default_factory=dict)
```

This is JSON-serializable as-is (dataclass `asdict` handles dicts of primitives). The existing `iteration_metrics.jsonl` persister will pick it up.

### Step 2 — Read-class constant + import
File: [agents/base.py](agents/base.py)

At the top of the file (after existing imports — there's already `_approx_tokens` imported per the diagnosis at line 42 / line 717):
```python
from core.repo_map import _approx_tokens  # already imported

# Track 1b: tools whose output is content the LLM reasons over.
# When Track 3d.4 lands, migrate this to a `read_class: bool` class attr
# on the Tool base.
READ_CLASS_TOOLS: frozenset[str] = frozenset({
    "fs-read", "fs-read_range", "fs-list", "fs-grep", "fs-exists",
    "code-outline", "doc-markdown",
    "git-log", "git-diff", "git-status", "git-changed-files", "git-read",
    "desk-search", "web-fetch", "web-search",
})
```

### Step 3 — Hook into call_tool()
File: [agents/base.py](agents/base.py:577)

The current shape (lines 595–616):
```python
m = self._current_metrics
if m is not None:
    m.tools_called.append(name)

if d.invocation == "local":
    tool = self._local_tools.get(name)
    if tool is None:
        raise ToolError(f"local tool {name!r} declared but not instantiated")
    try:
        return await tool.run(arguments)
    except Exception:
        raise

# Broker-routed.
result = await self.request_credential(...)
if not result.granted:
    raise ToolDenied(name, result.reason)
return result.result or {}
```

Replace both return paths to capture tokens:

```python
m = self._current_metrics
if m is not None:
    m.tools_called.append(name)

if d.invocation == "local":
    tool = self._local_tools.get(name)
    if tool is None:
        raise ToolError(f"local tool {name!r} declared but not instantiated")
    try:
        result = await tool.run(arguments)
    except Exception:
        raise
    self._account_tool_tokens(name, result)
    return result

# Broker-routed.
broker_result = await self.request_credential(
    scope=d.scope or name,
    operation=name,
    payload=arguments,
)
if not broker_result.granted:
    raise ToolDenied(name, broker_result.reason)
result = broker_result.result or {}
self._account_tool_tokens(name, result)
return result
```

Add the helper as a method on `BaseAgent`:

```python
def _account_tool_tokens(self, name: str, result: Any) -> None:
    """Track 1b: record the token cost of a tool result.

    Counts tokens of the JSON-serialised result (the form the LLM will
    receive as tool_result). Read-class tools also contribute to
    direct_file_read_tokens. Failures here must never break call_tool —
    on any exception, return silently.
    """
    m = self._current_metrics
    if m is None:
        return
    try:
        as_text = json.dumps(result, default=str) if not isinstance(result, str) else result
        tokens = _approx_tokens(as_text)
        m.tool_token_breakdown[name] = m.tool_token_breakdown.get(name, 0) + tokens
        if name in READ_CLASS_TOOLS:
            m.direct_file_read_tokens += tokens
    except Exception:
        # Never break a tool call on accounting. Mirror the persister-error
        # rule: a buggy auxiliary cannot break the primary path.
        pass
```

`json` is already imported at the top of `base.py` (used at line 533 for signal payload parsing).

### Step 4 — Tests
Files: `tests/agents/test_base_metrics.py`, `tests/integration/test_logging_truth.py`

**Unit tests** (extend `test_base_metrics.py`):

1. `test_call_tool_records_breakdown` — mock a local tool returning `{"content": "x" * 4000}`. Call it twice. Assert `m.tool_token_breakdown[name] == 2 * (4000 // 4 + 1)` (or close — use an inequality with tolerance for the JSON wrapping overhead).

2. `test_call_tool_read_class_increments_direct_reads` — mock an `fs-read` returning a known string. Call. Assert `m.direct_file_read_tokens > 0` and equals `m.tool_token_breakdown["fs-read"]`.

3. `test_call_tool_write_class_does_not_increment_reads` — mock an `fs-write` returning `{"ok": true}`. Call. Assert `m.direct_file_read_tokens == 0` AND `m.tool_token_breakdown["fs-write"] > 0`.

4. `test_call_tool_failure_does_not_record_tokens` — mock a tool that raises. Call inside try/except. Assert `m.tool_token_breakdown == {}` and `m.direct_file_read_tokens == 0`.

5. `test_account_tool_tokens_swallows_exceptions` — pass a deliberately unserialisable result (e.g. an object with a `__repr__` that raises). Call `_account_tool_tokens`. Assert no exception propagates and metrics are unchanged.

**Integration test** (`tests/integration/test_logging_truth.py`):

```python
async def test_direct_file_reads_show_in_metrics(tmp_run_root, mock_llm):
    """Run a small 1-agent flow that does fs-read calls. Assert the
    iteration_metrics.jsonl row has direct_file_read_tokens > 0 and
    tool_token_breakdown contains 'fs-read'."""
    # Build a minimal flow. Inject a few real files via FileWorkspace.
    # Run agent that calls fs-read 3 times.
    # Read iteration_metrics.jsonl from tmp_run_root.
    # Assertions:
    #   - exactly 1 row written
    #   - row["direct_file_read_tokens"] > 0
    #   - "fs-read" in row["tool_token_breakdown"]
    #   - row["tool_token_breakdown"]["fs-read"] == row["direct_file_read_tokens"]
```

### Step 5 — Reconciliation sanity check (manual)

After steps 1–4 land and tests pass, run a fresh project (any existing personas — RougePack works). Inspect `iteration_metrics.jsonl`:

For each row, compute:
```
tracked = (
    direct_file_read_tokens
    + context_tokens_total
    + sum(tool_token_breakdown for write-class tools)
)
```

`tracked` should account for ≥ 60% of `input_tokens` (the rest is system prompt, conversation history, recall payload). If it's much lower than that, there's another untracked input source — flag in a follow-up.

This isn't a hard test (recall payload tokens aren't yet broken out — separate issue), just a sanity floor.

---

## Acceptance criteria

1. `pytest tests/agents/test_base_metrics.py` — green.
2. `pytest tests/integration/test_logging_truth.py` — green.
3. `pytest tests/` full suite green (no regressions).
4. Manual: re-run any 2-agent project. Inspect `iteration_metrics.jsonl`:
   - Every row has `direct_file_read_tokens > 0` (assuming the agent called any read-class tool, which all built-in agents do).
   - Every row has a populated `tool_token_breakdown` matching the entries in `tools_called`.
5. Studio Run Analytics still renders without errors. (No frontend changes in this track — that comes with Track 3d.5 cost column. Just confirm the backend change doesn't break the panel.)
6. The reconciliation sanity check above shows ≥ 60% of input tokens accounted for.

---

## Risks / things to watch

- **Token estimation accuracy**: `_approx_tokens` uses `len(text) // 4`. That's a known cheap proxy. It will undercount tokens for code (denser than English) and overcount for whitespace-heavy markdown. We accept this — the metric is for *trends and ratios*, not billing. The same estimator is already used in context_manager and repo_map, so it's consistent with everything else.
- **Result-shape variance**: tools return `dict[str, Any]`, sometimes deep nested, sometimes a string field, sometimes raw bytes (e.g. `web-fetch`). `json.dumps(result, default=str)` handles all of these; bytes get stringified to their `repr()`. That's a slight overcount for binary content, but binary content shouldn't be flowing through these tools to begin with (file ops are utf-8 per FileWorkspace).
- **Future Track 3c interaction**: when output compression lands, `_account_tool_tokens` should run on the *compressed* output (post-compressor). The hook position in `call_tool()` is correct for this — when 3c wraps the return path, it'll naturally compress before our counter sees it. Note in Track 3c's plan if not already.
- **Manifest-membership tools we don't recognise**: a user-defined tool not in `READ_CLASS_TOOLS` is treated as write-class. Acceptable default (under-count) — better than misclassifying. Migrate to class attribute (Track 3d.4) to make this a tool-author choice.
- **Don't add a `tool_call_count` field**: `tools_called` already gives that via `len()` and per-tool count via `Counter(tools_called)`. No new field needed.

---

## Commit plan

```
feat(metrics): tool_token_breakdown field on IterationMetrics
feat(agent): READ_CLASS_TOOLS constant
feat(agent): _account_tool_tokens helper
feat(agent): wire token accounting into call_tool (local + broker paths)
test(metrics): tool_token_breakdown captures all tool calls
test(metrics): read-class tools increment direct_file_read_tokens
test(metrics): write-class tools do not increment direct_file_read_tokens
test(metrics): tool failure does not record tokens
test(metrics): exceptions in accounting are swallowed
test(integration): direct file reads visible in iteration_metrics.jsonl
```

---

## After this lands

- Track 3d.2 in `track_3d_agent_io_hygiene.md` becomes redundant — strike it from that file's scope and reference this track instead.
- Sprint 2 tracks (3a, 3c, 3d remainder, 6) become measurable. Their acceptance criteria can now reference real before/after numbers from `iteration_metrics.jsonl`.
- One follow-up worth tracking but NOT in this scope: recall payload tokens. The `recall_episodes_count` / `recall_patterns_count` fields exist but the *token cost* of recall isn't broken out. If reconciliation falls short of 60%, that's likely where the gap is.
