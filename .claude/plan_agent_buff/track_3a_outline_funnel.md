# Track 3a — Outline-First Code Context Funnel

**Status:** Ready for implementation. Sonnet-executable.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 250–400k. Stop and reassess if over 500k.
**Depends on:** Track 1 (for measurement). Do not start until Track 1 lands — without metrics you cannot validate the win.
**Pairs with:** Track 4 design spec (the doc-graph half of the original Track 3).

---

## Why

Today's `ContextManager.get_context()` ([core/context_manager.py:138](core/context_manager.py:138)) does the right thing for code structure (repo_map signatures via tree-sitter — already excellent, see [core/repo_map.py](core/repo_map.py)), but its iteration-2+ path falls back to a **wholesale `git diff` between agent branch and main** ([context_manager.py:166-168](core/context_manager.py:166)). That conflates two different artifact types:

- **Code** is structural and relational. A diff in isolation is misleading — you need the surrounding call graph.
- **Documents** are linear and stateful. Diff IS the right primitive there.

Today's flow uses a code-diff as if it were document staleness. Wrong primitive for the wrong artifact. The fix is two-sided:

- **This track (3a)** — give agents a proper outline-first read funnel for code: `repo_map → file-outline → scoped read → git-diff (only for own commits)`. Remove the iteration-2 wholesale-diff path.
- **Track 3b (deferred, depends on Track 4)** — apply diff as the right primitive over `.flow/docs/`. Specced separately.

Expected win, measured against Track 1's `context_log.jsonl`: **40–60% reduction in iteration-2 reviewer context tokens** with no loss in review quality.

---

## Scope

**In scope:**
- New `file-outline` tool: returns top-level signatures only, no bodies, ~200 tokens per file.
- Removal of the wholesale-diff path in `ContextManager.get_context()` (lines 157–177). Replace with relevant-files selection on iteration 2+ as well.
- Persona prompt updates: teach the funnel order explicitly.
- Reviewer prompt update: `git-diff` is for "what did *I* just commit" review only — scope to own branch HEAD, not branch vs main.
- A baseline-ref alternative for the per-run scoping (so Track 4's `.flow/baseline_ref.txt` slots in cleanly later — for now, accept commit SHA as parameter on git-diff).

**Out of scope:**
- `.flow/docs/` doc-graph (Track 4 + Track 5).
- Doc-diff over markdown (Track 3b, deferred).
- Recall / memory layer changes (separate concern).
- Removing repo_map — it's already correctly budgeted (1500 tok cap, signatures only).
- Changing the `_score_entry()` ranking algorithm for relevant files. Out of scope; only change context flow, not scoring.

---

## Files to modify

| File | Change |
|---|---|
| `agents/tools/file_outline.py` | **New tool**. Tree-sitter parse → signatures + line numbers. |
| `agents/tools/__init__.py` (or equivalent registry) | Register `file-outline`. |
| [core/context_manager.py](core/context_manager.py) | Remove iter-2 wholesale-diff path; let relevant-files selection run on every iteration. |
| [agents/abstract.py](agents/abstract.py) | Add `file-outline` to `default_tools` for reviewers and coders. |
| [agents/builtin/code_reviewer.py](agents/builtin/code_reviewer.py) | Prompt update: outline-first funnel. |
| `agents/builtin/coder.py` (or equivalent) | Same. |
| [agents/tools/git.py](agents/tools/git.py) | Update `git-diff` tool to accept `from_ref` and `to_ref` parameters; default to the agent's branch HEAD vs prior commit. |
| `tests/agents/tools/test_file_outline.py` | New file. Outline of a known fixture matches expected signatures. |
| `tests/core/test_context_manager.py` | Remove or update tests asserting iter-2 wholesale-diff behavior. Add test that relevant-files runs on iter-2. |
| `tests/integration/test_outline_funnel.py` | New: end-to-end token reduction check using Track 1's context_log. |

---

## Implementation steps

### Step 1 — `file-outline` tool
Files: `agents/tools/file_outline.py` (new)

Reuse the tree-sitter parsers already loaded by `core/repo_map.py` ([core/repo_map.py](core/repo_map.py) — `tree-sitter-python`, `tree-sitter-javascript`). The repo_map module exposes per-file parsed structures (`PythonFile`, etc.) — verify by reading [core/repo_map.py](core/repo_map.py) before writing the tool.

Tool contract:
```python
class FileOutline(Tool):
    name = "file-outline"
    description = "Return top-level signatures (classes, functions, methods) of a single file with line numbers. Use this BEFORE file-read to decide whether you need bodies. ~200 tokens per file vs 2-5k for a full read."

    parameters = {
        "path": {"type": "string", "description": "Path relative to workspace root"},
    }

    async def run(self, path: str) -> str:
        # 1. Validate path via FileWorkspace._safe_path() (CLAUDE.md §13)
        # 2. Reuse repo_map's parser for the file's language
        # 3. Render: one line per top-level symbol:
        #      class Foo:                                      [12-78]
        #          def __init__(self, x: int) -> None          [13-15]
        #          def bar(self, y: str) -> Foo                [17-25]
        #      def helper(name: str) -> dict[str, int]:        [80-92]
        # 4. Truncate to MAX_OUTLINE_LINES (default 200) with marker
```

Implementation hints:
- Use `repo_map.RepoMap`'s already-cached `entries` if the file has been parsed; otherwise parse on demand.
- Output format: `<indent><signature>  [start-end]`. Match how repo_map.render_compact already presents signatures, but include method bodies (signatures only) under the class.
- Token budget: a 200-line outline is ~600–800 tokens — that's fine. The point is "much smaller than full read."

Tool follows the established naming convention (hyphens, see CLAUDE.md §31 and the project's memory file). RBAC: read-baseline (no scope required), per CLAUDE.md §19.

### Step 2 — Register tool
Files: `agents/tools/__init__.py` (find existing tool registration), [agents/abstract.py:80](agents/abstract.py:80)

Add `"file-outline"` to:
- The tool registry exporting symbols.
- `default_tools` for reviewer and coder roles.

### Step 3 — Remove wholesale-diff iter-2 path
Files: [core/context_manager.py](core/context_manager.py)

Delete lines 157–177 (the `if include_diff and self.workspace.iteration > 1 ...` block). Leave the rest of `get_context()` intact.

The `include_diff` parameter on `get_context()` becomes unused — keep the parameter for backwards compat but mark deprecated:
```python
async def get_context(
    self,
    task: str,
    agent_role: str,
    max_tokens: int | None = None,
    tier: str = "solid",
    include_diff: bool = False,    # DEPRECATED — agents now request diffs explicitly via git-diff tool
    recent_signals_limit: int = 5,
) -> AgentContext:
```

Update docstring to explain: "Code diffs are no longer auto-included. Agents request them explicitly via the `git-diff` tool, scoped to their own commits."

### Step 4 — Update `git-diff` tool for self-scoping
Files: [agents/tools/git.py](agents/tools/git.py)

Currently `git-diff` (find at [agents/tools/git.py](agents/tools/git.py)) likely diffs against main. Extend parameters:
```python
parameters = {
    "from_ref": {"type": "string", "description": "Base ref. Default: this agent's branch HEAD~1 (your last commit's parent)."},
    "to_ref": {"type": "string", "description": "Target ref. Default: this agent's branch HEAD."},
    "path": {"type": "string", "description": "Optional file path to scope the diff. Default: full working tree."},
}
```

Defaults compute `agent_branches[role] HEAD~1...HEAD` — i.e., "what *I* just committed." Not "everything on my branch vs main."

This keeps `git-diff` useful for self-review without flooding context.

### Step 5 — Prompt updates
Files: [agents/builtin/code_reviewer.py](agents/builtin/code_reviewer.py), `agents/builtin/coder.py` (if exists)

Append to reviewer system prompt:
```
Read funnel — follow this order for ANY code question:

1. Repo map (already in your context — always-on, signatures only)
2. file-outline <path>     — when you need signature detail for one file
3. file-read <path>        — when you actually need bodies
4. git-diff                 — when you need to review YOUR OWN recent commits

Do NOT call file-read until you have ruled out the outline being enough.
Do NOT use git-diff to understand existing code; use it only to review
changes you yourself made.

Token budget is real. Each iteration logs context-token usage.
```

Coder prompt: similar funnel, with emphasis that git-diff over their own
commits is the right way to review before signing off.

### Step 6 — Tests
- **`tests/agents/tools/test_file_outline.py`**:
  1. Fixture: a known python file with two classes and one helper function.
  2. Run `file-outline` on it.
  3. Assert output contains the class names, method names, helper name, and line ranges. Assert NO method bodies present. Assert token count < 800 for a ~200-line file.
- **[tests/core/test_context_manager.py](tests/core/test_context_manager.py)**:
  - Remove or rewrite the existing test at lines 108–135 (which asserts iter-2 diff behavior).
  - Add: `test_iter_2_uses_relevant_files` — assert that on iteration 2, `ctx.relevant_files` is populated (not `ctx.git_diff`).
- **`tests/integration/test_outline_funnel.py`**:
  - Run a 2-iteration reviewer flow against a known fixture project.
  - Read `context_log.jsonl` (Track 1).
  - Assert: iter-2 `used_tokens` <= 60% of iter-1 `used_tokens`.
  - Assert: `git_diff_tokens == 0` on both iterations (no auto-diff).
  - Assert: `tools_called` for reviewer includes `file-outline` at least once.

---

## Acceptance criteria

1. `pytest tests/agents/tools/test_file_outline.py` — green.
2. `pytest tests/core/test_context_manager.py` — green, no references to wholesale-diff iter-2 behavior remain.
3. `pytest tests/integration/test_outline_funnel.py` — green, including the token-reduction assertion.
4. `pytest tests/` full suite green.
5. Manual: run a coder + reviewer flow on a small repo. Open Track 1's Run Analytics panel:
   - Iteration 2 of the reviewer shows lower `context_tokens_total` than iteration 1.
   - `tools_called` for the reviewer includes `file-outline` and `git-diff` (not `file-read` for files unchanged by this run).
6. The reviewer successfully catches a real bug using only outline + scoped diff (not full reads). Validate by introducing a deliberate bug into a fixture and confirming the reviewer flags it.

---

## Risks / things to watch

- **Reviewer regression**: removing wholesale diff means a reviewer relying on it for "what did anyone change since I last looked" loses that signal. Track 2a (orphan question) and the new self-scoped `git-diff` together cover this. Verify with a test where reviewer reviews coder's commit on coder's branch.
- **Cross-branch awareness**: if reviewer needs to see what a *different* agent changed, they call `git-diff --from_ref agent/coder~1 --to_ref agent/coder`. Document in the reviewer prompt.
- **Repo-map staleness**: repo_map should be rebuilt incrementally on commit per CLAUDE.md "Context Management Rules." Verify this still happens; no new work needed if so.
- **Persona overrides**: user-defined personas may not have the new funnel guidance. Their first runs after this lands may use file-read aggressively. Acceptable — they self-correct via the persona memory layer.

---

## Commit plan

```
feat(tools): file-outline tool — tree-sitter signatures
feat(tools): register file-outline; grant to reviewer/coder defaults
refactor(context): remove wholesale-diff iter-2 path
feat(tools): git-diff accepts from_ref/to_ref, defaults to self-scope
docs(prompts): outline-first funnel guidance for reviewer and coder
test(tools): file-outline fixture coverage
test(context): iter-2 uses relevant_files, no auto-diff
test(integration): outline funnel reduces iter-2 context tokens
```
