# Track 3d.4 — Manifest-Driven Tool Guidance (auto-render)

**Status:** Ready for implementation. Sonnet-executable.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 200–350k. Stop and reassess if over 450k.
**Sprint slot:** 1.9 — directly after Track 3e.
**Supersedes:** the 3d.4 sub-deliverable inside `track_3d_agent_io_hygiene.md`. Strike that sub-section once this lands.

---

## Context

`run_543a09eb6a` proved the adoption problem:

| Tool | Calls in that run |
|---|---|
| `code-outline-batch` | **1** (architect, who naturally batches) |
| `fs-edit-batch` | **0** |
| `fs-write-batch` | **0** |

The infrastructure works (manifest grants verified), but agents kept reaching for the singular variants because nothing in their prompt tells them to prefer batches. Persona prose is the only source of "what tool to use when," and persona prose drifts the moment a new tool ships.

The structural fix: make the **manifest** the source of truth for tool guidance, not persona prose. Each tool class declares its own `priority` and `when_to_use`. The manifest renders these into the system prompt's tool catalog automatically. New tool? Set fields on the class. Every existing persona's prompt updates the next time it renders. No hand-sync.

This is the long-promised Track 3d.4 — extracted from the broader `track_3d_agent_io_hygiene` so it can ship standalone.

---

## What an agent will see in their prompt after this lands

Today (Layer 5 — tool catalog from `manifest.render_for_prompt()`):

```
AVAILABLE TOOLS

fs-read — Read a file under working/ as UTF-8 text.
  args: {path}

fs-edit — Surgical edit on an existing file: find/replace exact strings.
  args: {path, find, replace, replace_all?}

fs-edit-batch — Surgical find/replace on many files in one tool call. ...
  args: {edits: [{path, find, replace, replace_all?}, ...]}

...
```

After this track:

```
AVAILABLE TOOLS (use in this order of preference — lower number = reach first)

[20] code-outline — when you need signature detail for one file BEFORE fs-read
     args: {path}

[22] code-outline-batch — prefer over code-outline when navigating multiple files at once
     args: {paths}

[30] fs-list — to discover what files exist in a directory
     args: {path, recursive?}

[35] fs-grep — to find symbols or text across files
     args: {query, path?, regex?}

[60] fs-read — only when you need full file bodies (use code-outline first)
     args: {path}

[100] fs-edit-batch — PREFER this when you have multiple edits planned
      args: {edits: [{path, find, replace, replace_all?}, ...]}

[105] fs-edit — single edit; use fs-edit-batch when you have ≥ 2 edits
      args: {path, find, replace, replace_all?}

...
```

Two changes: tools are sorted by priority (numbers shown), and each line carries a `when_to_use` hint — but only one short line per tool.

---

## Scope

**In scope:**
- Two new class attributes on `BaseTool`: `priority: int` (default 100) and `when_to_use: str` (default `""`).
- Plumb both fields through `LocalToolEntry` → `ToolDescriptor` → `ToolManifest.render_for_prompt()`.
- Modify `render_for_prompt()` to sort by priority and include `when_to_use` lines (when populated).
- Populate `priority` and `when_to_use` on every built-in tool class. This is the bulk of the work — ~30 tools to update.
- Tests covering: defaults, plumbing, rendering, ordering.

**Out of scope:**
- Removing persona prose that already mentions tools. Persona prose stays as-is; the auto-rendered section is additive. Trim later if redundant.
- Per-role priority overrides. All agents see the same priority order. If a role wants to deviate, persona prose can still nudge.
- Dynamic priority based on context (e.g. boost `git-diff` after a commit). Static.
- New tool categories. Just guidance for existing ones.
- Modifying any tool's actual behavior. Pure metadata + render change.

---

## Files to modify

| File | Change |
|---|---|
| [agents/tools/base.py](agents/tools/base.py) | Add `priority: int = 100` and `when_to_use: str = ""` class attributes to `BaseTool`. |
| [core/tool_manifest.py](core/tool_manifest.py) | Extend `LocalToolEntry` and `ToolDescriptor` with the two new fields. Modify `_build_local_registry` (in [core/runner.py](core/runner.py)) to read them off the tool class. Modify `build_for_agent` to propagate. Modify `render_for_prompt` to sort + render. |
| [core/runner.py](core/runner.py) | Update `_build_local_registry` to populate the new fields when constructing `LocalToolEntry` instances. |
| [agents/tools/fs.py](agents/tools/fs.py) | Set `priority` and `when_to_use` on `FsRead`, `FsList`, `FsExists`, `FsReadRange`, `FsGrep`, `FsCreate`, `FsWrite`, `FsEdit`, `FsDelete`, `FsRename`, `FsDiff`. |
| [agents/tools/fs_batch.py](agents/tools/fs_batch.py) | Set on `FsWriteBatch`, `FsEditBatch`. |
| [agents/tools/git.py](agents/tools/git.py) | Set on all git tools. |
| [agents/tools/code.py](agents/tools/code.py) | Set on `CodeExec`, `CodeOutline`. |
| [agents/tools/code_batch.py](agents/tools/code_batch.py) | Set on `CodeOutlineBatch`. |
| [agents/tools/board.py](agents/tools/board.py) | Set on `BoardMessage`, `AssignTask`. |
| [agents/tools/data.py](agents/tools/data.py) | Set on data-* tools. |
| [agents/tools/doc.py](agents/tools/doc.py) | Set on doc-markdown / doc-html. |
| [agents/tools/desk_search.py](agents/tools/desk_search.py), `desk_push.py` | Set on each. |
| [agents/tools/text.py](agents/tools/text.py), `time_tool.py`, `math_tool.py` | Set on each. |
| [agents/tools/web.py](agents/tools/web.py) | Set on `WebFetch`, `WebSearch`. |
| [agents/tools/story.py](agents/tools/story.py) | Set on `StoryOpen`, `StoryComplete`. |
| `tests/agents/test_tool_manifest_priorities.py` | **New file.** Sorting + when_to_use rendering. |
| `tests/agents/test_tools_priority_coverage.py` | **New file.** Sanity: every built-in tool has both fields set (not just default). |

---

## Priority scale (the convention)

Buckets in steps of 10–20. Lower number = reach first. Document this in `agents/tools/base.py` as a comment so future tool authors stay consistent.

```
  5  — implicit / always-on (e.g. repo_map injection)
 10  — discovery (where am I? what's around?)
 20  — structure (signatures, outlines)
 30  — search & filter (grep, list)
 40  — read recent state (git-diff, git-status, git-log)
 50  — read precise (read_range, desk-search)
 60  — read full (fs-read, doc-markdown)
 70  — fetch external (web-fetch, web-search, data-*)
 80  — convenience (math, text-diff, time-now)
100  — write batch (fs-edit-batch, fs-write-batch)
105  — write single (fs-edit, fs-write, fs-create)
110  — destructive (fs-delete, fs-rename)
115  — execute / commit (code-exec)
120  — coordination (assign-task, desk-push, board-message)
130  — story lifecycle (story-open, story-complete)
```

The KEY signal: **batch tools sit at 100 and singular write tools sit at 105**. That's the load-bearing nudge. When the LLM sees the catalog sorted, it sees `fs-edit-batch` *before* `fs-edit`, with a `when_to_use` line saying "prefer this when you have ≥ 2 edits."

Read tools come before write tools. The funnel `repo_map → outline → list/grep → read_range → read → diff → edit/write` is encoded in the priority numbers — no separate prose needed.

---

## Implementation steps

### Step 1 — Add class attributes
File: [agents/tools/base.py](agents/tools/base.py)

```python
class BaseTool(ABC):
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {}

    # Track 3d.4 — manifest-driven guidance.
    # `priority` controls ordering in the rendered tool catalog: lower
    # = reach first. Convention buckets in 10s; see comment block in this file.
    # `when_to_use` is a short (≤ ~80 char) hint shown next to the tool name
    # in the catalog. Empty string suppresses the hint cleanly.
    priority: int = 100
    when_to_use: str = ""

    # ... rest unchanged
```

Add a docstring comment block enumerating the priority buckets.

### Step 2 — Plumb through the manifest
File: [core/tool_manifest.py](core/tool_manifest.py)

Extend `LocalToolEntry` and `ToolDescriptor`:

```python
@dataclass
class LocalToolEntry:
    name: str
    description: str
    input_schema: dict[str, Any]
    priority: int = 100         # NEW
    when_to_use: str = ""        # NEW

@dataclass
class ToolDescriptor:
    name: str
    description: str
    input_schema: dict[str, Any]
    invocation: Invocation
    scope: str | None = None
    server: str | None = None
    priority: int = 100         # NEW
    when_to_use: str = ""        # NEW
```

`build_for_agent`: when constructing each `ToolDescriptor`, copy `priority` and `when_to_use` from the registry entry.

### Step 3 — Update the registry builder
File: [core/runner.py](core/runner.py) `_build_local_registry`

When constructing each `LocalToolEntry`, copy `priority` and `when_to_use` from the tool class:

```python
self._local_registry[cls.name] = LocalToolEntry(
    name=cls.name,
    description=cls.description,
    input_schema=cls.input_schema,
    priority=cls.priority,
    when_to_use=cls.when_to_use,
)
```

### Step 4 — Render with priority + when_to_use
File: [core/tool_manifest.py](core/tool_manifest.py) `render_for_prompt`

Sort descriptors by `priority` (stable, then by name for determinism). Render each with the priority number prefix and the `when_to_use` line when populated:

```
AVAILABLE TOOLS (use in this order of preference — lower number = reach first)

 [20] code-outline — when you need signature detail for one file BEFORE fs-read
      args: {path: string}

 [22] code-outline-batch — prefer when navigating multiple files at once
      args: {paths: [string]}

...
```

If no tool has a `when_to_use` (legacy / opting out), render without the hint line — just description.

### Step 5 — Populate every built-in tool
Files: each tool module under `agents/tools/`.

This is the bulk of the work. For each tool class, set both attributes. Detailed mapping:

**fs.py:**
- `FsRead`: `priority=60, when_to_use="when you need full file bodies; prefer code-outline or fs-read_range when possible"`
- `FsList`: `priority=30, when_to_use="to discover what files exist in a directory"`
- `FsExists`: `priority=10, when_to_use="to check existence before reading"`
- `FsReadRange`: `priority=50, when_to_use="when you need a specific line range, not the full file"`
- `FsGrep`: `priority=35, when_to_use="to find symbols or text across files; prefer code-outline first if you know the file"`
- `FsCreate`: `priority=107, when_to_use="to create a NEW file; errors if it already exists"`
- `FsWrite`: `priority=105, when_to_use="single file write; use fs-write-batch when creating ≥ 2 files"`
- `FsEdit`: `priority=105, when_to_use="single edit; use fs-edit-batch when you have ≥ 2 edits planned"`
- `FsDelete`: `priority=110, when_to_use="destructive — verify with fs-list/fs-exists first"`
- `FsRename`: `priority=110, when_to_use="rename or move a file"`
- `FsDiff`: `priority=42, when_to_use="story-scoped diff of one file vs the story baseline"`

**fs_batch.py:**
- `FsWriteBatch`: `priority=100, when_to_use="PREFER over fs-write when creating multiple files"`
- `FsEditBatch`: `priority=100, when_to_use="PREFER over fs-edit when you have ≥ 2 edits"`

**code.py / code_batch.py:**
- `CodeOutline`: `priority=20, when_to_use="signatures of one file BEFORE fs-read"`
- `CodeOutlineBatch`: `priority=22, when_to_use="prefer over code-outline when navigating ≥ 3 files"`
- `CodeExec`: `priority=115, when_to_use="run tests / scripts; expensive — gather context first"`

**git.py:**
- `GitDiff`: `priority=40, when_to_use="review YOUR OWN recent commits — scope to commit range"`
- `GitChangedFiles`: `priority=42, when_to_use="list which files changed; cheaper than git-diff"`
- `GitLog`: `priority=44, when_to_use="see commit history for one path or branch"`
- `GitStatus`: `priority=46, when_to_use="check what's uncommitted on your branch"`
- `GitRead`: `priority=48, when_to_use="read a file at a specific commit"`

**board.py:**
- `BoardMessage`: `priority=120, when_to_use="post a message to a peer agent"`
- `AssignTask`: `priority=120, when_to_use="hand a structured task to another agent"`

**doc.py / data.py / web.py / desk_*.py / text.py / time_tool.py / math_tool.py / story.py:**
- See implementation; priorities follow the bucket scale. Each gets a one-line `when_to_use`.

### Step 6 — Tests

**`tests/agents/test_tool_manifest_priorities.py`** (new):
- `test_descriptors_sorted_by_priority` — build manifest with 3 tools at priorities 5/100/50, render, assert order in output is 5 → 50 → 100.
- `test_when_to_use_appears_when_set` — tool with `when_to_use="X"` renders the hint; tool with empty string omits it.
- `test_tied_priority_falls_back_to_name` — two tools at priority 100, sorted alphabetically by name.
- `test_render_includes_priority_prefix` — output contains `[5]`, `[50]`, `[100]` markers.

**`tests/agents/test_tools_priority_coverage.py`** (new — coverage check):
- Iterate over `LOCAL_TOOL_CLASSES` from `core/runner.py`.
- For each, assert `priority != 100 OR when_to_use != ""` (i.e. at least one was deliberately set).
- This catches regressions where someone adds a new tool but forgets to populate the metadata.

---

## Acceptance criteria

1. `pytest tests/agents/test_tool_manifest_priorities.py` — green.
2. `pytest tests/agents/test_tools_priority_coverage.py` — green; every built-in tool has its metadata populated.
3. `pytest tests/` full suite green. No regressions.
4. Manual: render an agent's effective system prompt for a role with `fs-edit` granted. Confirm:
   - Tool catalog lists `fs-edit-batch` BEFORE `fs-edit` (lower priority number).
   - `fs-edit-batch`'s line includes "PREFER over fs-edit when you have ≥ 2 edits".
   - `fs-edit`'s line includes "use fs-edit-batch when you have ≥ 2 edits planned".
5. Re-run RougePack equivalent. Compare against `run_543a09eb6a`:
   - At least one of `fs-edit-batch` / `fs-write-batch` is called by an edit-heavy agent.
   - `tools_called` shows fewer sequential singular-edit bursts.
   - `llm_calls` per edit-heavy agent drops by ≥10%.

If criterion 5 fails (no batch adoption observed despite ranked rendering), the issue is more fundamental than catalog ordering — escalate to a follow-up that adds explicit batch examples in the AUTONOMY RULES block.

---

## Risks / things to watch

- **Catalog grows in tokens:** every tool now has a `when_to_use` line (~80 chars). With ~30 tools, that's ~2.4K tokens of catalog. Compare to today's catalog (~1.5K tokens). Net add: ~1K tokens to every system prompt. Acceptable — cache absorbs most of it. If excessive, drop `when_to_use` for tools with priority > 80 (the convenience tier).
- **Priority bickering:** different roles may want different orderings. v1 keeps it global. If real runs show a role consistently working against the order, add per-role override later.
- **Adoption still poor:** the priority order plus the explicit "PREFER ..." hints might still not be enough. The model's training bias toward singular operations is real. If post-3d.4 runs still show 0 batch use, we need stronger intervention (concrete `[ACTION] fs-edit-batch(...)` examples in the AUTONOMY RULES, or a runtime nudge that appends "Note: you have N edits queued — consider fs-edit-batch" to the agent's user message).
- **Tied priorities at 100:** be deliberate when picking numbers. Two tools at exactly 100 will render in arbitrary (alphabetical) order. Fine for the convention, but use distinct numbers when ordering matters.
- **Tool description duplication:** the existing `description` field also appears in the catalog. After this lands, `when_to_use` partially overlaps with `description`. Don't dedupe in v1 — the LLM benefits from redundancy here. Trim later if the catalog feels verbose.

---

## Commit plan

```
feat(tools): priority and when_to_use class attributes on BaseTool
feat(manifest): plumb priority + when_to_use through registry → descriptor → render
feat(manifest): render_for_prompt sorts by priority and includes when_to_use
feat(tools): populate priority/when_to_use on fs tools (single + batch)
feat(tools): populate priority/when_to_use on git tools
feat(tools): populate priority/when_to_use on code tools (single + batch)
feat(tools): populate priority/when_to_use on board / desk / story tools
feat(tools): populate priority/when_to_use on data / doc / web / utility tools
test(manifest): priority sorting and when_to_use rendering
test(tools): coverage — every built-in tool has metadata populated
```

---

## After this lands

- New tools added in the future automatically appear in the agent's prompt with their guidance — no persona prose update needed.
- `fs-edit-batch` and `fs-write-batch` get the visibility they need to actually be picked. Track 3e's investment pays off.
- The "drift" problem (CLAUDE.md §31 manifest as runtime truth, prose as guidance truth) collapses to one source.
- Future Track 3d slimmed to: 3d.1 (signal compression) + 3d.5 (cost column). 3d.2 was superseded by 1b. 3d.3 was superseded by 3e. 3d.4 is this track.
