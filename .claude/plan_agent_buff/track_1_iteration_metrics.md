# Track 1 — Iteration Metrics & Context Instrumentation

**Status:** Ready for implementation. Sonnet-executable.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md` (multi-track roadmap)
**Token budget:** 200–300k. Stop and reassess if over 400k.

---

## Why this is Track 1

Every other track in the roadmap is a tuning decision (context windows, doc-graph layering, peer-rework caps). We cannot tune what we cannot measure. Today we have per-LLM-call token counts in `LLMResponse` ([core/llm/client.py:26](core/llm/client.py:26)) and per-role spawn counts in `Scheduler._spawn_count` ([core/scheduler.py:141](core/scheduler.py:141)) — but no per-iteration aggregation, no timing, no record of which model ran, and no record of what `get_context()` actually injected. This track closes that gap.

After this lands, every later track gets an empirical before/after comparison.

---

## Scope

**In scope:**
1. `IterationMetrics` dataclass + capture pipeline + JSONL persistence.
2. Context-assembly debug log (what `get_context()` returned, broken down by source).
3. A read-only Studio "Run Analytics" panel that visualises both.

**Out of scope (do not gold-plate):**
- Aggregating across runs / cross-run dashboards.
- Cost estimation in dollars (model→price table). Numbers stay as raw tokens.
- Editing the metrics from the UI.
- Hooking into the live WebSocket — analytics reads JSONL files via existing run-data endpoints.
- Any change to scheduler or context_manager *logic*. We are observing, not altering.

---

## Architecture

Two new persistence streams parallel to the existing board/tasks/artifacts persisters in [core/workspace.py:306-324](core/workspace.py:306):

```
{run_root}/iteration_metrics.jsonl   ← one line per finished agent iteration
{run_root}/context_log.jsonl         ← one line per get_context() call
```

Both follow the **persister callback pattern** already in use:
- A `Callable` field on `Workspace` (`_iteration_metrics_persister`, `_context_log_persister`).
- The runner attaches them at run start using the same pattern as [core/runner.py:698](core/runner.py:698) (`board.jsonl` setup).
- Persister exceptions are swallowed (same rule as existing persisters — "a buggy persister can't break the workspace").

Capture hooks live in two places:
- **Iteration metrics** — inside `BaseAgent.run()`, since that's where the per-iteration boundary actually lives and where LLM responses are consumed. Scheduler stays untouched.
- **Context log** — inside `ContextManager.get_context()`, immediately before return.

---

## Files to modify

| File | Change |
|---|---|
| `core/workspace.py` | Add `IterationMetrics` and `ContextSnapshot` dataclasses. Add two fields + two persister callbacks to `Workspace`. Add `add_iteration_metrics()` and `record_context_snapshot()` methods. |
| `core/runner.py` | At run start, attach persisters that write to `iteration_metrics.jsonl` and `context_log.jsonl` (mirror existing `board.jsonl` setup at line 698). |
| `core/context_manager.py` | At end of `get_context()`, build a `ContextSnapshot` and call `workspace.record_context_snapshot()`. No logic change to context selection. |
| `agents/base.py` | Wrap LLM-call accumulator and timing in `run()`. Emit one `IterationMetrics` per `run()` invocation in a `finally` block. |
| `tests/core/test_workspace.py` | Tests for the new dataclasses + persister wiring. |
| `tests/agents/test_base_metrics.py` | New file. Test that running a mocked agent produces the expected metrics row. |
| `tests/core/test_context_manager.py` | Add a test that asserts `record_context_snapshot()` is called with the correct breakdown. |
| `studio/src/views/RunView/RunAnalytics.tsx` | New component. |
| `studio/src/views/RunView/index.tsx` | Mount the panel as a new tab/section in the Run view. |
| `api/routes/runs.py` | Add `GET /runs/{run_id}/iteration_metrics` and `GET /runs/{run_id}/context_log` (JSONL → JSON array). |

No deletions. No other files should change.

---

## Dataclass schemas

Both go in [core/workspace.py](core/workspace.py), grouped near the existing `CommitRecord` (line 77). All fields default-valued so partial captures still serialise.

```python
@dataclass
class IterationMetrics:
    """One row per completed agent iteration.

    An "iteration" = one full invocation of BaseAgent.run() — from the
    scheduler spawning the agent to the agent returning (or being cancelled).
    Multiple LLM calls within one run() are summed into the *_tokens fields.
    """

    run_id: str
    role: str
    iteration: int                  # spawn number for this role (1-indexed)
    model: str = ""                 # primary model used; if escalation occurred mid-iteration, this is the LAST model used

    # token sums across all LLM calls in this iteration
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    llm_calls: int = 0              # number of LLM calls in this iteration

    # final assistant message — useful for "did the agent ramble?" analysis
    final_message_tokens: int = 0

    # timing (epoch seconds; ISO is derivable client-side)
    started_at: float = 0.0
    ended_at: float = 0.0
    elapsed_seconds: float = 0.0

    # context-side context that fed this iteration's first reasoning step
    recall_episodes_count: int = 0
    recall_patterns_count: int = 0
    repo_map_tokens: int = 0
    relevant_files_tokens: int = 0
    git_diff_tokens: int = 0
    context_tokens_total: int = 0

    # tools the agent invoked this iteration (names only, not arguments)
    tools_called: list[str] = field(default_factory=list)

    # outcome
    completed: bool = False         # True if iteration ended via normal return
    failed: bool = False            # True if iteration ended via exception
    no_op: bool = False             # True if iteration emitted COMPLETE with no commits


@dataclass
class ContextSnapshot:
    """One row per get_context() call. Records what was injected, not why."""

    run_id: str
    role: str
    iteration: int
    task: str                       # truncated to 500 chars

    tier: str                       # "heavy" | "solid" | "fast"
    max_tokens: int
    used_tokens: int

    repo_map_tokens: int = 0
    git_diff_tokens: int = 0
    relevant_files: list[tuple[str, int]] = field(default_factory=list)  # [(path, tokens), ...]
    recent_signals_count: int = 0

    # captured *if* recall is wired through context_manager (it is in
    # later iterations; gracefully empty if not)
    recalled_episodes: list[str] = field(default_factory=list)   # episode IDs
    recalled_patterns: list[str] = field(default_factory=list)   # pattern IDs

    timestamp: float = 0.0
```

---

## Implementation steps (in order)

### Step 1 — Dataclasses + Workspace plumbing
Files: [core/workspace.py](core/workspace.py)

1. Add `IterationMetrics` and `ContextSnapshot` dataclasses near `CommitRecord`.
2. Add two fields to `Workspace`:
   ```python
   iteration_metrics: list[IterationMetrics] = field(default_factory=list)
   context_log: list[ContextSnapshot] = field(default_factory=list)
   ```
3. Add two persister fields next to the existing ones at line 314:
   ```python
   _iteration_metrics_persister: Callable[[IterationMetrics], None] | None = field(default=None, repr=False)
   _context_log_persister: Callable[[ContextSnapshot], None] | None = field(default=None, repr=False)
   ```
4. Add two methods on `Workspace`:
   ```python
   def add_iteration_metrics(self, m: IterationMetrics) -> None:
       self.iteration_metrics.append(m)
       persister = self._iteration_metrics_persister
       if persister is not None:
           try:
               persister(m)
           except Exception:
               pass

   def record_context_snapshot(self, s: ContextSnapshot) -> None:
       self.context_log.append(s)
       persister = self._context_log_persister
       if persister is not None:
           try:
               persister(s)
           except Exception:
               pass
   ```

### Step 2 — Runner persister wiring
Files: [core/runner.py](core/runner.py)

Find the existing `board.jsonl` persister setup at line 698. Add two more, same pattern:

```python
# iteration_metrics.jsonl
metrics_path = self.run_root / "iteration_metrics.jsonl"
def _metrics_persister(m: IterationMetrics) -> None:
    with metrics_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(m)) + "\n")
self.workspace._iteration_metrics_persister = _metrics_persister

# context_log.jsonl
ctx_log_path = self.run_root / "context_log.jsonl"
def _ctx_persister(s: ContextSnapshot) -> None:
    with ctx_log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(s)) + "\n")
self.workspace._context_log_persister = _ctx_persister
```

Place these where the other persisters are wired (search for `_board_persister` assignment in runner.py).

### Step 3 — Context snapshot capture
Files: [core/context_manager.py](core/context_manager.py)

At the very end of `get_context()` (after line 199 where `recent_signals` is set), build a `ContextSnapshot` from local variables and call `self.workspace.record_context_snapshot(snapshot)`. The function already tracks `used`, `repo_map_text`, `ctx.git_diff`, `ctx.relevant_files` — pull from those.

For per-file token counts in the snapshot's `relevant_files`, capture them inline in the loop at line 185–196 instead of recomputing.

For recall fields: if `ctx.recalled_episodes` / `ctx.recalled_patterns` exist on `AgentContext` use them; otherwise leave empty. Do NOT add recall logic if it's not already there — that's a separate track.

`task` field: truncate to 500 chars to keep the JSONL rows bounded.

### Step 4 — IterationMetrics capture in BaseAgent
Files: [agents/base.py](agents/base.py)

In `BaseAgent.__init__` add:
```python
self._current_metrics: IterationMetrics | None = None
```

In `BaseAgent.run()`:
1. At entry, before any work:
   ```python
   self._current_metrics = IterationMetrics(
       run_id=self.workspace.run_id,
       role=self.cfg.role,
       iteration=self.workspace.iteration,
       model=self.cfg.model,
       started_at=time.time(),
   )
   ```
2. Wrap the existing call site to `self.llm.complete(...)`. Find every call site (there should be a single helper inside `BaseAgent`; if there isn't, *create one* called `_complete()` and route all calls through it). The wrapper:
   ```python
   async def _complete(self, *args, **kwargs) -> LLMResponse:
       resp = await self.llm.complete(*args, **kwargs)
       m = self._current_metrics
       if m is not None:
           m.input_tokens += resp.input_tokens
           m.output_tokens += resp.output_tokens
           m.cache_read_tokens += resp.cache_read_tokens
           m.cache_creation_tokens += resp.cache_creation_tokens
           m.llm_calls += 1
           m.model = resp.model or m.model
           m.final_message_tokens = _approx_tokens(resp.text)  # last call wins == final
       return resp
   ```
3. Wrap `call_tool()` in [agents/base.py:542](agents/base.py:542) to append the tool name to `m.tools_called`.
4. At the end of `run()`, in a `try/finally`:
   ```python
   try:
       result = await self._do_run(...)   # existing body
   except Exception:
       self._current_metrics.failed = True
       raise
   finally:
       m = self._current_metrics
       if m is not None:
           m.ended_at = time.time()
           m.elapsed_seconds = m.ended_at - m.started_at
           m.completed = not m.failed
           # no_op detection: branch was clean at the COMPLETE emission
           m.no_op = self._last_commit_was_noop  # set by the existing emit_commit path
           self.workspace.add_iteration_metrics(m)
           self._current_metrics = None
   ```

If `BaseAgent.run()` is heavily nested or has multiple return paths, prefer extracting the body into `_do_run()` so the finally block is exception-safe.

**`_last_commit_was_noop`** — if this flag doesn't exist yet, set it from the runner's `_auto_commit()` path at [core/runner.py](core/runner.py) where `status.clean` is detected. (It's also referenced in Track 2b but adding the bare flag here is harmless.)

### Step 5 — Recall counts
Files: `agents/persona.py` (or wherever `recall()` lives)

If `recall()` is invoked from `BaseAgent.run()` (likely at the start of each iteration), capture the result counts into `self._current_metrics.recall_episodes_count` / `recall_patterns_count`. One-line addition next to the existing recall call. If recall happens deeper, leave the fields at 0 — this is best-effort.

### Step 6 — API endpoints
Files: [api/routes/runs.py](api/routes/runs.py)

Add two endpoints, both gated by existing auth/run-ownership middleware:

```
GET /runs/{run_id}/iteration_metrics  →  list[IterationMetrics]   (JSONL → JSON array)
GET /runs/{run_id}/context_log         →  list[ContextSnapshot]
```

Implementation: read `{run_root}/iteration_metrics.jsonl` (or `context_log.jsonl`), one `json.loads()` per line, return as a list. 404 if file missing. No streaming; runs are bounded.

### Step 7 — Studio Run Analytics panel
Files: `studio/src/views/RunView/RunAnalytics.tsx`, `studio/src/views/RunView/index.tsx`

`RunAnalytics.tsx`:
- Fetches both endpoints once on mount, refetches on a 5s interval while the run is active.
- Renders three sections, in order:
  1. **Per-iteration table** — columns: `role | iteration | model | input | output | cache_read | elapsed | tools | no_op`. Sort newest-first.
  2. **Token totals** — sum across all rows, grouped by role. Plain table.
  3. **Context composition stacked bar** — one bar per `ContextSnapshot`, segments coloured by source (`repo_map_tokens`, `git_diff_tokens`, `relevant_files_tokens` summed, `other`). Use existing chart primitive in studio if one exists; else a simple flexbox-row of coloured divs is fine.

Mount in `index.tsx` as a new tab next to BoardFeed/QuipFeed (whatever pattern the existing tabs use). Tab label: "Analytics".

Match existing studio styling — no new dependencies. If `recharts` or similar is already installed, use it; otherwise plain JSX.

### Step 8 — Tests
- `tests/core/test_workspace.py`: assert that `add_iteration_metrics` appends to the list AND fires the persister.
- `tests/agents/test_base_metrics.py`: spawn a `BaseAgent` with a mocked LLM client returning known token counts, run one iteration, assert exactly one `IterationMetrics` row written with summed tokens, model name, elapsed > 0, and the right `tools_called` list.
- `tests/core/test_context_manager.py`: extend an existing test (or add a new one) — after `get_context()` returns, assert `workspace.context_log` has exactly one new `ContextSnapshot` with the right `used_tokens` and `relevant_files` length.
- Run `pytest tests/core/ tests/agents/` and confirm green.

---

## Acceptance criteria

A run is complete when ALL of these are true:

1. After a 2-agent run completes, `{run_root}/iteration_metrics.jsonl` exists and contains exactly N rows, where N = total spawn count across all roles.
2. Each row has non-zero `elapsed_seconds`, a populated `model`, and tokens summing to a positive value when LLM was called.
3. `{run_root}/context_log.jsonl` has one row per `get_context()` call. The sum of `repo_map_tokens + git_diff_tokens + Σrelevant_files_tokens` equals `used_tokens` ± rounding.
4. The Studio Run Analytics panel renders without errors against a real completed run, shows the per-iteration table, and the stacked bar reflects context composition.
5. `pytest tests/` passes (full suite, not just new tests).
6. No change in scheduler or context_manager *behavior* — existing scheduler tests still pass with no edits to their assertions.

---

## Verification (end-to-end manual check)

1. `pytest tests/` — green.
2. Start backend, worker, frontend. Create a fresh test project with a 2-agent flow (e.g., coder + reviewer).
3. Run it to completion.
4. `cat data/runs/{run_id}/iteration_metrics.jsonl | jq .` — confirm structure.
5. `cat data/runs/{run_id}/context_log.jsonl | jq .` — confirm structure.
6. Open the Studio Run view, switch to the Analytics tab. Confirm rows match the JSONL files and the stacked bar renders.
7. Crash test: `kill -9` the worker mid-run. Restart. Confirm the JSONL files are still readable line-by-line up to the kill point (persister-on-each-call means partial files are valid).

---

## Risks / things to watch

- **Tool wrapping in `call_tool()`**: must not change return semantics. If the existing `call_tool()` raises, the metrics append must not swallow the exception. Use a `try/finally` that records the tool name and re-raises.
- **`_last_commit_was_noop` detection**: if the existing runner doesn't expose this cleanly, default the field to `False` and leave a `# TODO` rather than blocking. Track 2b will properly set it.
- **JSONL append safety**: file opens are inside the persister callback, one open per row. That's intentional — no buffering means a crash leaves a complete file. Don't optimise to a long-lived handle without a corresponding flush-on-write.
- **Studio fetch loop**: the 5s interval should clear when the run reaches a terminal state. Use the existing run-status hook if present; otherwise check the last `IterationMetrics` row's `completed`/`failed` flags.

---

## Commit plan

One commit per step, in order. Per CLAUDE.md top-priority rule: commit after every code change, never batch.

```
feat(metrics): add IterationMetrics and ContextSnapshot dataclasses
feat(metrics): wire iteration_metrics.jsonl and context_log.jsonl persisters
feat(metrics): capture context composition in get_context()
feat(metrics): capture per-iteration metrics in BaseAgent.run()
feat(metrics): record recall counts when available
feat(api): GET /runs/{run_id}/iteration_metrics and /context_log
feat(studio): Run Analytics panel
test(metrics): coverage for IterationMetrics and ContextSnapshot capture
```

If a commit fails the pre-commit hook or `npm test`, fix and create a NEW commit (do not amend).
