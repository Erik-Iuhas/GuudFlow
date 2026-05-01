# Track 3e — Batch Tool Calls

**Status:** Ready for implementation. Sonnet-executable.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 200–350k. Stop and reassess if over 450k.
**Sprint slot:** 1.8 — directly after Track 1d.
**Supersedes:** sub-track 3d.3 in `track_3d_agent_io_hygiene.md` — strike that sub-deliverable from 3d's scope and reference this track instead.

---

## Context

`run_0a886033d7` (post-Track-1d Sonnet-architect + Haiku-devs continuation) makes a single waste pattern impossible to ignore: **agents emit long sequential bursts of the same tool**. Concrete numbers from that run:

| Agent | Tool | Burst size | Round-trips |
|---|---|---|---|
| game_logic_dev | fs-edit | **18 in a row** | 18 LLM turns |
| pygame_renderer_dev | fs-edit | **20 across** with verify-back loops | 20+ LLM turns |
| Earlier runs (`run_75df8cf97a`, `run_26d48d4328`) | fs-write/fs-create | **25-30 in a row** | 25-30 LLM turns |

Each round-trip costs:
- Full system prompt re-send (~30k tokens, mostly cached but not free)
- ~200 tokens of conversation history growth
- ~5-10s wall-clock latency
- One model "what's next?" decision cycle that adds nothing — the agent already knows the next 17 edits when it issues the first

For a 142s `game_logic_dev` iteration, the 18-edit burst alone consumes ~90-150s. With a batch tool, those 18 calls collapse to **1 round-trip** — the LLM commits to all the edits at once and gets one response.

## Scope

**Tools getting batch variants (in priority order):**
1. **`fs-edit-batch`** — highest leverage. Clearest win. Most observed bursts.
2. **`fs-write-batch`** — close second. Earlier runs showed 25-30 sequential `fs-write` for "create the initial src/ skeleton."
3. **`code-outline-batch`** — tertiary. Useful when an agent wants to outline 5-7 files at once during a navigation pass.

**Tools NOT in this track (and why):**
- `fs-read` — each read's content informs the next decision. Batching loses that. Out.
- `fs-grep` — patterns often depend on prior results. Same reason. Out.
- `git-status`, `git-log`, `git-diff` — usually single-call inspection points. Already low-frequency. Out.
- `assign-task`, `desk-push`, `peer-rework`, `time-now`, `fs-exists`, `fs-list` — singular by nature or trivial cost. Out.
- `web-fetch`, `web-search` — unique queries; agent decides next based on prior result. Out.

**Out of scope for this track:**
- Cross-call deduplication / streaming output (Track 3c territory).
- Auto-batching at the runtime level (forcing the model to batch when it didn't ask). The agent decides.
- Per-tool RBAC scope changes — batch tools inherit the same scopes as their non-batch counterparts.

---

## Per-tool design

### `fs-edit-batch` (priority 1)

**Input:**
```python
{
  "edits": [
    {"path": "src/foo.py", "old_string": "...", "new_string": "...", "replace_all": false},
    {"path": "src/bar.py", "old_string": "...", "new_string": "..."},
    ...
  ]
}
```

**Behaviour:**
- Apply each entry sequentially using the existing `fs-edit` per-file safety logic (claim, edit, release).
- A failure on entry N does NOT roll back entries 1..N-1. Continue to N+1. Return a summary listing both successes and failures so the agent can retry the failed ones.
- Hard cap: **30 entries per call**. Beyond that, return an error directing the agent to split. Prevents pathological one-shot results.
- Each entry's claim/release still posts its own `FILE_CLAIM`/`FILE_RELEASE` signals to the board (forensic continuity preserved). Track 3d.1's render-time signal compression will collapse these for prompt display later.

**Output:**
```
Edited 17 of 18 entries successfully. Failures:
  src/auth.py: old_string not found (no edit applied)
Successes: src/{game_loop, state_machine, run_context, ...}.py (17 files).
```

The output stays small — uses a directory-grouped path summary like Track 3c's `git-changed-files` compressor would produce, applied here at the tool level.

### `fs-write-batch` (priority 2)

**Input:**
```python
{
  "files": [
    {"path": "src/main.py", "content": "..."},
    {"path": "src/constants.py", "content": "..."},
    ...
  ]
}
```

**Behaviour:**
- Same per-entry safety as `fs-write`: `_safe_path()` validation, claim, write, release.
- Same partial-failure semantics as `fs-edit-batch` — entry N failure doesn't roll back 1..N-1.
- Hard cap: 30 entries per call.
- Each entry produces its own claim/release pair on the board.

**Output:**
```
Wrote 25 files: src/{a, b, c}, src/world/{tile, map, level}, src/state/{...} (25 files, 47.3 KB total).
```

### `code-outline-batch` (priority 3)

**Input:**
```python
{
  "paths": ["src/foo.py", "src/bar.py", "src/baz.py", ...]
}
```

**Behaviour:**
- Apply existing `code-outline` per path. Reuses cached repo_map entries when available; falls back to fresh tree-sitter parse otherwise.
- Hard cap: 10 paths per call (outlines aren't tiny — keep total response bounded).
- Failures (file not found, parse errors) reported per-entry, not fatal.

**Output:** concatenated outlines with file headers, same format as the singular `code-outline` produces today.

---

## Files to modify

| File | Change |
|---|---|
| `agents/tools/fs_batch.py` | **New module**. `FsEditBatch` and `FsWriteBatch` tool classes. Internally call existing per-entry logic. |
| `agents/tools/code_batch.py` | **New module**. `CodeOutlineBatch` tool class. Reuses `code-outline`'s parser entry. |
| Tool registry (likely `agents/tools/__init__.py` or `core/tool_manifest.py:_register_local`) | Register the three new tools. |
| [agents/abstract.py](agents/abstract.py) `default_tools` block | Grant batch tools alongside their non-batch counterparts: any role with `fs-edit` gets `fs-edit-batch`, etc. |
| Built-in agent prompts (`agents/builtin/*.py` or persona prose) | Add a one-line guidance: *"When you have multiple edits/writes/outlines lined up, prefer the batch variant — fewer round-trips, faster work."* This is Track 3d.4's territory long-term but a temporary line is cheap. |
| `tests/agents/tools/test_fs_edit_batch.py` | **New file.** |
| `tests/agents/tools/test_fs_write_batch.py` | **New file.** |
| `tests/agents/tools/test_code_outline_batch.py` | **New file.** |
| `tests/integration/test_batch_tool_reduces_turns.py` | **New file.** End-to-end: a mocked agent that needs to make 5 edits issues one batch call vs five sequential calls; assert fewer LLM round-trips. |

---

## Implementation steps

### Step 1 — `fs-edit-batch` and `fs-write-batch`
File: `agents/tools/fs_batch.py` (new)

Find the existing `FsEdit` and `FsWrite` tool classes (likely under `agents/tools/fs.py` or similar). Each batch tool:
1. Validates entries are a non-empty list, length ≤ 30.
2. Iterates entries; for each, calls the same per-file path-safety + claim/release machinery.
3. Aggregates results into a structured response summarising successes by directory, failures with reasons.
4. Never raises on per-entry failure — failures appear in the response, not as exceptions.

Key invariant per CLAUDE.md §13: every entry goes through `FileWorkspace._safe_path()`. Don't shortcut.

### Step 2 — `code-outline-batch`
File: `agents/tools/code_batch.py` (new)

Mirror `fs-edit-batch`'s shape. Iterates `paths`, calls the existing `code-outline` parser path per entry. If `workspace.repo_map.entries` has the file cached, render from there to avoid re-parsing.

### Step 3 — Registration + grants
- Add the three classes to whatever module is the tool registry's source of truth (likely `_register_local` in `core/tool_manifest.py` or an equivalent factory).
- Update `agents/abstract.py` `default_tools` so any role declaring `fs-edit` also gets `fs-edit-batch`, etc.
- Naming follows CLAUDE.md §31 hyphens (`fs-edit-batch`, `fs-write-batch`, `code-outline-batch`).

### Step 4 — Prompt guidance (small, temporary)

Until Track 3d.4 (manifest auto-render) lands, add a one-line append to the relevant built-in personas (coder, frontend, backend roles):

```
When you have multiple file edits, writes, or outlines lined up, prefer
the batch variant (`fs-edit-batch`, `fs-write-batch`, `code-outline-batch`).
One batch call replaces N sequential round-trips — much faster, and the
batch tool reports per-entry success/failure so you can retry failures.
```

### Step 5 — Tests

**Per-tool unit tests** (`tests/agents/tools/test_*_batch.py`):
- Empty list rejected.
- > 30 entries rejected.
- All-success path: 5 entries, all valid → response lists all 5 successes.
- Partial failure: 3 successes + 1 missing-file failure + 1 success → response lists 4 successes + 1 explicit failure.
- Path safety: an entry with a `..` path → that entry rejected, others proceed.
- Each entry produces correct claim/release signals on the workspace board.

**Integration** (`tests/integration/test_batch_tool_reduces_turns.py`):
- Mock LLM that, when given a "make 5 edits" prompt, issues one `fs-edit-batch` tool call with 5 entries vs the same prompt without the batch variant available.
- Assert: with batch tool, the agent's `IterationMetrics.llm_calls` is exactly 2 (one for tool call, one for final response). Without batch, it's 6+ (one per edit + final).
- Assert: `tool_token_breakdown["fs-edit-batch"]` reflects the entire batch's combined output token count.

### Step 6 — Acceptance + manual reconciliation

After tests pass and the backend is restarted, run a project that previously triggered an edit burst (RougePack continuation works). Inspect `iteration_metrics.jsonl`:

- Per-agent `tools_called` should show `fs-edit-batch` entries instead of long `fs-edit` runs.
- `llm_calls` per agent should drop materially for edit-heavy agents (target: ≥30% reduction vs `run_0a886033d7`'s 45 calls for game_logic_dev).
- Per-agent elapsed time should drop in proportion (~25-40% for edit-heavy agents).

---

## Acceptance criteria

1. `pytest tests/agents/tools/test_fs_edit_batch.py` — green.
2. `pytest tests/agents/tools/test_fs_write_batch.py` — green.
3. `pytest tests/agents/tools/test_code_outline_batch.py` — green.
4. `pytest tests/integration/test_batch_tool_reduces_turns.py` — green, including the LLM-call-count assertion.
5. `pytest tests/` full suite green.
6. Manual: re-run a continuation against RougePack. Compared to `run_0a886033d7`:
   - `game_logic_dev.llm_calls` drops from 45 to ≤30.
   - `tools_called` shows at least one `fs-edit-batch` entry per agent that previously emitted a sequential edit burst.
   - Total run wall-clock drops by ≥20%.

---

## Risks / things to watch

- **Cap of 30 entries:** if an agent legitimately needs 50, it splits into two batches. That's fine — still a 25× reduction in round-trips. Monitor whether 30 is too tight in practice.
- **Mid-batch crashes leaving partial state:** by design — agent sees the partial-failure summary and decides what to do. Don't add transactional rollback; that's a different design.
- **Board signal flood:** 30 entries × 2 signals (claim + release) = 60 signals on the board for one tool call. Track 3d.1 (render-time signal compression) collapses these for prompt display, but raw board.jsonl grows. Acceptable — board is forensic-grade by design.
- **Agents not reaching for the batch variant:** even with the prompt guidance, some agents will keep doing sequential `fs-edit`. Track 3d.4 (manifest auto-render with `when_to_use`) is the long-term fix; this track ships the *capability*. Adoption can be improved later.
- **Tool-result token cost:** a 30-entry batch result is ~600 tokens (path summary + per-failure reason). Track 3c will eventually compress this further.

---

## Commit plan

```
feat(tools): fs-edit-batch — apply many edits in one tool call
feat(tools): fs-write-batch — create many files in one tool call
feat(tools): code-outline-batch — outline many files in one tool call
feat(tools): register batch tools; grant alongside non-batch counterparts
docs(prompts): brief guidance to prefer batch variants when applicable
test(tools): fs-edit-batch — empty/oversize/partial-failure/path-safety
test(tools): fs-write-batch — same coverage shape
test(tools): code-outline-batch — same coverage shape
test(integration): batch tool reduces LLM round-trips
```

---

## After this lands

Two compounding wins:
- **Visible turn-count drop** in `iteration_metrics.jsonl` for edit-heavy and write-heavy agents. Cost drops proportionally.
- **Wall-clock per agent drops 25-40%** on edit-heavy phases — the 18-edit burst that took 90-150s in `run_0a886033d7` collapses to a single round-trip.

This makes Track 3c (output compression) the next-most-leverage move because once edits are batched, the dominant remaining cost is fs-read content flowing into prompt history. 3c attacks that directly.
