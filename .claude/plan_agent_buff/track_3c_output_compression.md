# Track 3c — Tool Output Compression

**Status:** Ready for implementation. Sonnet-executable.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 200–350k. Stop and reassess if over 450k.
**Depends on:** Track 1 (for measurement). Verification needs `iteration_metrics.jsonl` to show before/after savings.
**Inspired by:** [RTK (Rust Token Killer)](https://github.com/rtk-ai/rtk) — a CLI proxy that compresses shell output by 60–90% before it reaches the LLM. We borrow the *strategies*, not the architecture: their proxy approach is needed because they're tool-agnostic; we already have a single tool-call chokepoint, so compression lives there.

---

## Why

Tool output is a hidden tax on every iteration. A `pytest` run dumps 500-line banners, `git log` floods sign-off lines, `npm install` spams a resolver tree, `git diff` repeats path prefixes hunk after hunk. Today every byte of that goes into the next LLM prompt.

We have a single chokepoint: every tool call goes through `BaseAgent.call_tool()` ([agents/base.py:542](agents/base.py:542)) per CLAUDE.md §31. That's where compression belongs — one wrapper, all tools benefit, no per-tool refactor.

Expected wins, measured against Track 1's `iteration_metrics.jsonl`:
- `code-exec` (test/build runs): **70–90% token reduction** on average.
- `git-log`: **50% reduction** by stripping sign-off / commit-trailer noise.
- `git-diff`: **30–50% reduction** by directory-grouping path prefixes.
- Aggregate per-iteration: **20–35%** for iterations dominated by test runs.

---

## Scope

**In scope:**
- New module `agents/tools/output_compress.py` with a strategy registry.
- Compressors for the four highest-leverage tools: `code-exec`, `git-log`, `git-diff`, `git-changed-files`.
- Wrapping in `BaseAgent.call_tool()` — registry lookup by tool name, identity-pass for unregistered tools.
- Per-tool-call savings captured into Track 1's metrics.
- Studio: a small "tokens saved by compression" stat in the Run Analytics panel.
- Bypass mechanism: agents (or tests) can request raw output via a `compress=False` parameter on the call site.

**Out of scope:**
- Compressing `web-fetch` output — already gets HTML→markdown conversion at fetch time. Revisit only if metrics show it's a problem.
- Compressing `file-read` — files are already user-curated and the funnel (Track 3a) handles oversized reads. Don't touch.
- Compressing `file-outline` — already dense by construction (Track 3a).
- Lossy summarization with an LLM — compressors are pure functions, deterministic, no LLM calls.
- Cross-call deduplication (e.g., remembering that the agent already saw this output last call). Stateless only.
- A general "compression policy" config UI. Hardcoded sensible defaults; revisit if tuning is needed.

---

## Files to modify

| File | Change |
|---|---|
| `agents/tools/output_compress.py` | **New module**. Registry, base `Compressor` protocol, concrete compressors. |
| [agents/base.py](agents/base.py) | Wrap `call_tool()` return path. Capture per-call savings into `_current_metrics`. |
| [core/workspace.py](core/workspace.py) | Extend `IterationMetrics` (added in Track 1) with `tool_output_tokens_raw`, `tool_output_tokens_compressed`, `tokens_saved_by_compression`. |
| `agents/tools/code_exec.py` (or wherever it lives) | No change — compression is external. Confirm tool name string matches registry key. |
| [agents/tools/git.py](agents/tools/git.py) | Same — confirm tool names match. |
| `tests/agents/tools/test_output_compress.py` | **New file**. Per-compressor fixture tests with golden inputs/outputs. |
| `tests/agents/test_base_compression.py` | **New file**. End-to-end through `call_tool()`, asserting metrics capture. |
| `studio/src/views/RunView/RunAnalytics.tsx` | Add a "tokens saved" row to the per-iteration table. |

---

## Architecture

```
agent.call_tool("code-exec", {"command": "pytest"})
    │
    ▼
BaseAgent.call_tool()
    │  ─→ tool.run() returns raw output
    │
    ▼
output_compress.compress(tool_name="code-exec", raw=raw_output)
    │  ─→ registry lookup
    │  ─→ Compressor.compress(raw) returns CompressedOutput
    │
    ▼
metrics.tool_output_tokens_raw += original_tokens
metrics.tool_output_tokens_compressed += compressed_tokens
metrics.tokens_saved_by_compression += (raw - compressed)
metrics.tools_called.append(tool_name)
    │
    ▼
return compressed.text  ─→ feeds back into LLM as tool_result
```

---

## Implementation steps

### Step 1 — Module scaffold + registry
File: `agents/tools/output_compress.py` (new)

```python
"""Tool output compression — token-reduction layer at the call_tool boundary.

Each compressor is a pure function: (raw_output: str) -> CompressedOutput.
Stateless, deterministic, no LLM calls. Inspired by RTK
(https://github.com/rtk-ai/rtk).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable

from core.context_manager import _approx_tokens


@dataclass
class CompressedOutput:
    text: str
    original_tokens: int
    compressed_tokens: int
    strategies_applied: list[str] = field(default_factory=list)

    @property
    def tokens_saved(self) -> int:
        return max(0, self.original_tokens - self.compressed_tokens)


CompressorFn = Callable[[str], CompressedOutput]


_REGISTRY: dict[str, CompressorFn] = {}


def register(tool_name: str, fn: CompressorFn) -> None:
    _REGISTRY[tool_name] = fn


def compress(tool_name: str, raw: str) -> CompressedOutput:
    fn = _REGISTRY.get(tool_name)
    if fn is None:
        n = _approx_tokens(raw)
        return CompressedOutput(text=raw, original_tokens=n, compressed_tokens=n)
    return fn(raw)


# Concrete compressors live below this line.
```

### Step 2 — `code-exec` compressor (highest leverage)
Same file. Strategies, applied in order:

1. **Drop pytest banners**: regex out `=== test session starts ===`, `platform ...`, `rootdir: ...`, `plugins: ...`, `collected N items`. Keep the failure summary at the bottom verbatim.
2. **Dedupe repeated assertion patterns**: collapse N identical traceback heads into `<head>  × N occurrences` keeping only the first instance's full body.
3. **Truncate npm/pip resolver noise**: lines matching `npm WARN ...`, `Resolving dependencies`, `Found N vulnerabilities` collapse to a single summary line.
4. **Cap repeated lines**: any line repeated > 5 times collapses to `<line>  × N`.
5. **Drop trailing whitespace and squash blank runs > 2 lines** to a single blank line.

```python
def compress_code_exec(raw: str) -> CompressedOutput:
    original = _approx_tokens(raw)
    text = raw
    applied = []

    text, hit = _strip_pytest_banners(text);  applied += ["pytest_banner"] if hit else []
    text, hit = _dedupe_tracebacks(text);     applied += ["traceback_dedupe"] if hit else []
    text, hit = _collapse_npm_noise(text);    applied += ["npm_noise"] if hit else []
    text, hit = _dedupe_repeated_lines(text); applied += ["repeated_lines"] if hit else []
    text, hit = _squash_blanks(text);         applied += ["blanks"] if hit else []

    return CompressedOutput(
        text=text,
        original_tokens=original,
        compressed_tokens=_approx_tokens(text),
        strategies_applied=applied,
    )

register("code-exec", compress_code_exec)
```

Each `_strip_*` / `_dedupe_*` is a small pure function with its own unit test.

### Step 3 — `git-log` compressor
Strategies:
1. Drop `Signed-off-by:`, `Co-Authored-By:`, `Reviewed-by:` trailer lines.
2. Strip GPG signature blocks.
3. If there are > 20 commits, keep the first 10 + last 5 with a `... N commits omitted ...` marker.
4. Strip empty commit body paragraphs.

```python
register("git-log", compress_git_log)
```

### Step 4 — `git-diff` compressor
Strategies:
1. **Path prefix grouping**: when consecutive hunks share a path prefix (e.g., `src/api/users/`), emit the prefix once and stripped paths under it.
2. **Drop `index abc..def` lines** unless the diff is for review (cap by raw length — under 2000 tokens, keep them).
3. **Collapse runs of `-` then `+` lines that are identical except whitespace** into a single `whitespace-only N lines` marker.
4. **Truncate diffs > 5000 tokens** with a `... <N hunks omitted, use git-diff with path filter> ...` marker — this teaches the agent to scope rather than re-pulling the whole thing.

```python
register("git-diff", compress_git_diff)
```

### Step 5 — `git-changed-files` compressor
Strategies:
1. **Group by directory**: `src/api/users/{a.py, b.py, c.py}` instead of three separate lines.
2. **Cap at top-N directories**: if > 30 directories, keep top 20 by file count + an "and N more directories" line.

```python
register("git-changed-files", compress_git_changed_files)
```

### Step 6 — Wire into `BaseAgent.call_tool()`
File: [agents/base.py](agents/base.py:542)

Read the existing `call_tool()` carefully. Find the exact return point (after the tool's `run()` returns, before the result is appended to message history).

```python
from agents.tools.output_compress import compress as _compress_tool_output

# inside call_tool, after tool.run() returns `result`:
compressed = _compress_tool_output(name, result)

# Track 1 metrics integration
m = self._current_metrics
if m is not None:
    m.tool_output_tokens_raw += compressed.original_tokens
    m.tool_output_tokens_compressed += compressed.compressed_tokens
    m.tokens_saved_by_compression += compressed.tokens_saved
    # tools_called list already populated by Track 1; nothing to add here

return compressed.text
```

If the tool raises, do not compress — let the exception propagate per CLAUDE.md "Error handling" rule. Compression only runs on successful return.

**Bypass:** add a kwarg to `call_tool(name, arguments, *, compress: bool = True)`. When `False`, skip the compression step entirely. Used by:
- Tests verifying raw output behavior.
- Any future tool whose output the agent specifically asked for un-compressed (none today).

### Step 7 — Extend IterationMetrics
File: [core/workspace.py](core/workspace.py)

Add three fields to `IterationMetrics` (added in Track 1):
```python
tool_output_tokens_raw: int = 0
tool_output_tokens_compressed: int = 0
tokens_saved_by_compression: int = 0
```

If Track 1 hasn't landed yet: this track blocks on it. Do not duplicate the dataclass; merge into Track 1's schema.

### Step 8 — Studio surface
File: `studio/src/views/RunView/RunAnalytics.tsx`

Add one column to the per-iteration table: **"Saved (compression)"** showing `tokens_saved_by_compression`. Add a footer row summing across all iterations: **"Total saved by compression: X tokens (Y%)"** where Y is `total_saved / (total_saved + total_compressed) * 100`.

No new chart. Just two numbers.

### Step 9 — Tests

**`tests/agents/tools/test_output_compress.py`** — fixture-based per-strategy tests:

```
fixtures/
  code_exec_pytest_raw.txt          # ~3000 tokens of real pytest output
  code_exec_pytest_expected.txt     # what the compressor should produce
  git_log_raw.txt
  git_log_expected.txt
  git_diff_raw.txt
  git_diff_expected.txt
  git_changed_files_raw.txt
  git_changed_files_expected.txt
```

Each test:
1. Read raw fixture.
2. Run compressor.
3. Assert `compressed.text == expected_fixture`.
4. Assert `compressed.tokens_saved > raw_tokens * 0.4` (minimum 40% reduction baseline).
5. Assert `strategies_applied` contains the expected strategies.

**Edge cases**:
- Empty input → identity (no crash).
- Already-compressed input (run twice) → idempotent within ±5 tokens.
- Input that triggers no strategy → identity, `strategies_applied=[]`.
- Garbage / non-UTF-8 bytes → graceful; compressor never raises.

**`tests/agents/test_base_compression.py`** — integration:
1. Mock a tool that returns a known long string with detectable redundancy.
2. Register a compressor for it.
3. Call through `BaseAgent.call_tool()`.
4. Assert returned text is compressed AND `IterationMetrics.tokens_saved_by_compression` reflects the delta.
5. Call again with `compress=False` — assert raw output returned, no metrics delta.
6. Tool that raises — assert exception propagates, no metrics mutation.

### Step 10 — Real-world calibration
After tests pass, run the full test suite and one real 2-agent flow. Inspect `iteration_metrics.jsonl`:
- `tokens_saved_by_compression` should be > 0 for any iteration that ran code-exec or git tools.
- For an iteration with a single pytest call: expect savings > 1000 tokens.
- If savings are < 200 tokens on a code-exec call, the fixture-tuned compressor isn't matching real-world output. Capture the real output as a new fixture and tighten the strategy.

---

## Acceptance criteria

1. `pytest tests/agents/tools/test_output_compress.py` — green. Each compressor hits its 40% baseline on its fixture.
2. `pytest tests/agents/test_base_compression.py` — green.
3. `pytest tests/` — full suite green. No regressions in tool-using agent tests (existing tests still see the *compressed* output now; if any test fixture asserts on raw banner text, update it or add `compress=False`).
4. Manual calibration run: a 2-iteration test-runner flow shows `tokens_saved_by_compression >= 1000` per iteration in `iteration_metrics.jsonl`.
5. Studio Run Analytics shows the "Saved (compression)" column populated for the same run.
6. Bypass works: a test using `compress=False` returns raw output and shows zero savings.
7. Idempotency: running a fixture through its compressor twice differs by ≤ 5 tokens.

---

## Risks / things to watch

- **Information loss**: aggressive truncation can hide a real failure. Mitigation: always keep the *last* failure summary verbatim in `code-exec`. Tests assert this. If an agent ever needs the dropped detail, they can re-run with a path filter (which is itself the point — teaches scoping).
- **Brittle regexes**: pytest output formats shift across versions. Mitigation: capture real fixtures during calibration; treat the fixture as the spec, not the regex.
- **Existing test fixtures asserting raw output**: any test that compares tool output to a string literal will break. Audit during step 10; add `compress=False` or update the literal. Do not silently disable compression globally to keep tests passing.
- **Order of strategy application matters**: dedupe-before-truncate vs truncate-before-dedupe yield different results. Lock the order in code, document it in each compressor's docstring, and don't reorder casually.
- **Compressor exceptions**: a buggy compressor must not break tool calls. Wrap the registry lookup result in a try/except — on exception, log a warning and return identity output. Same rule as workspace persisters: a buggy auxiliary can't break the primary path.

---

## Commit plan

```
feat(tools): output compression module with registry and base types
feat(tools): code-exec compressor (banners, traceback dedupe, npm noise)
feat(tools): git-log compressor (trailers, GPG, history truncation)
feat(tools): git-diff compressor (path grouping, whitespace, size cap)
feat(tools): git-changed-files compressor (directory grouping)
feat(metrics): IterationMetrics fields for compression savings
feat(agent): wire compression into BaseAgent.call_tool with bypass kwarg
feat(studio): tokens-saved column in Run Analytics
test(compress): per-compressor fixture coverage
test(agent): call_tool compression integration
test(compress): edge cases (empty, idempotent, raising compressor)
```
