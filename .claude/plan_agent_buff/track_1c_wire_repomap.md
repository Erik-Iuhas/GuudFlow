# Track 1c — Wire repo_map Into the Effective System Prompt (Sprint 1.6)

**Status:** Ready for implementation. Sonnet-executable.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 80–150k. Stop and reassess if over 200k.
**Sprint slot:** 1.6 — directly after Track 1b.
**Depends on:** Track 1b (so we can measure the impact).

---

## Context

Diagnosis confirms `workspace.repo_map` is built at run start ([core/runner.py:539](core/runner.py:539)) and rebuilt after every `GIT_COMMIT` ([core/runner.py:871](core/runner.py:871)) — but never reaches an agent prompt. The only reader of `workspace.repo_map` is `ContextManager.get_context()`, which no built-in agent invokes. CLAUDE.md §28 explicitly mandates *"Repo map is ALWAYS included in every context — it is cheap and always useful, never skip it."* That rule is currently violated.

Result: agents have no idea what files exist before they start. They discover the layout through `fs-list` and `fs-grep` instead. Two consecutive runs (`run_75df8cf97a`, `run_26d48d4328`) show this pattern — one agent did 30 `fs-write` + 15 `fs-grep` searching for what it could have read off the map upfront. **The aider-style cost-reduction feature has been compute-active and prompt-dead since shipped.**

Track 1c wires it in.

---

## Scope

**In scope:**
- New layer in `effective_system_prompt()` ([agents/base.py:833](agents/base.py:833)) that injects `render_compact(workspace.repo_map, max_tokens=1500)` as a structured section.
- Always-on per CLAUDE.md §28. No conditional gating.
- Make `code-outline` tool consult `workspace.repo_map` first for already-parsed files; fall back to disk parse only on cache miss. Saves redundant tree-sitter work and gives consistent results between the prompt's repo map and the on-demand outline.
- Tests covering: layer ordering, render budget enforcement, integration test that an agent's prompt actually contains repo-map content.
- Acceptance check: re-run a project, confirm `tools_called` shows fewer `fs-list` and `fs-grep` calls per agent.

**Out of scope:**
- Wiring `get_context()` into agents broadly (relevant_files, recent_signals, recall payload). That's a bigger conversation. This track does the smallest thing that closes the most expensive gap.
- Adding `repo_map_tokens` to the existing aggregation. The injected map is a *prompt-side* cost, not a `ContextSnapshot` row. We'll capture it via a new `IterationMetrics.system_prompt_tokens` field if needed later.
- Cache invalidation tuning. The runner already rebuilds repo_map after `GIT_COMMIT` — leave that alone.
- Any change to repo_map's parsing or PageRank logic.

---

## Files to modify

| File | Change |
|---|---|
| [agents/base.py](agents/base.py) | Add `_render_repo_map_layer()` helper. Insert it as Layer 4.5 in `effective_system_prompt()` between memory (4) and base prompt (5). Add a `_workspace` reference so the layer can read `workspace.repo_map` (or accept workspace as an arg — see implementation note). |
| [agents/tools/code.py](agents/tools/code.py) | In `CodeOutline.run()` ([line 383](agents/tools/code.py:383)): before parsing from disk, check `workspace.repo_map.entries` for the path. If present, render its symbols directly. On miss, fall through to existing parse logic. |
| [tests/agents/test_base.py](tests/agents/test_base.py) (or new `test_repo_map_layer.py`) | Tests asserting the layer is present, respects budget, and is omitted when repo_map is empty. |
| `tests/integration/test_repo_map_in_prompt.py` | **New file.** End-to-end: build a workspace with a populated repo_map, call `effective_system_prompt()`, assert the rendered string contains a "Codebase structure" section with at least one expected file path. |

No changes to runner, workspace, context_manager, or the metrics module. The build path already works.

---

## Implementation steps

### Step 1 — Pass workspace into `effective_system_prompt()`

`effective_system_prompt()` currently takes only `teammate_brief`. Repo map needs `workspace`. Two options:

- **A. Add `workspace: Workspace | None = None` parameter.** Backward-compatible; callers that pass it get the layer, callers that don't get an unchanged prompt. Existing tests stay green.
- **B. Use `self._workspace` set during `run()`.** Requires capturing workspace once on entry to `run()`. More invasive.

**Choose A.** Simpler, opt-in for tests, no capture-on-entry refactor. Update callers (the runner's prompt-build site and any agent that calls `effective_system_prompt()` directly) to pass `workspace`.

### Step 2 — Add the renderer helper

In `agents/base.py`, near the other layer helpers:

```python
def _render_repo_map_layer(self, workspace: Workspace) -> str:
    """Render the codebase structure for prompt injection (CLAUDE.md §28).

    Uses repo_map's existing render_compact (signatures-only, PageRank-ranked,
    hard-capped at max_tokens). Returns "" on empty maps so the layer is
    suppressed cleanly.
    """
    rmap = workspace.repo_map
    if not rmap.entries:
        return ""
    from core.repo_map import render_compact
    body = render_compact(rmap, max_tokens=1500)
    if not body.strip():
        return ""
    return (
        "CODEBASE STRUCTURE (always-on map of this run's working tree —\n"
        "consult before fs-list / fs-grep / fs-read, paths are relative to repo root):\n\n"
        + body
    )
```

### Step 3 — Insert layer 4.5 in `effective_system_prompt()`

After the memory block (line 894) and before `if self.system_prompt:` (line 900), insert:

```python
if workspace is not None:
    repo_map_text = self._render_repo_map_layer(workspace)
    if repo_map_text:
        parts.append(repo_map_text)
```

Update the docstring layer-order comment to include 4.5.

### Step 4 — Update callers to pass workspace

Find every call site of `effective_system_prompt()`:

```bash
# expected sites (verify with: grep -rn 'effective_system_prompt' agents/ core/ tests/)
```

Most likely:
- The agent's own `run()` (or `llm()` wrapper) — pass `self.workspace` if captured, else accept workspace as `run()` parameter (already does).
- Tests calling it directly (test fixtures may not have a workspace — pass `None` and skip the layer).

For each non-test caller, pass the workspace. For tests, the default `None` keeps existing assertions valid.

### Step 5 — `code-outline` cache reuse

In [agents/tools/code.py](agents/tools/code.py) `CodeOutline.run()` (around line 383):

```python
# Before parsing from disk, check workspace.repo_map for cached symbols.
rmap_entry = next(
    (e for e in workspace.repo_map.entries if e.path == path),
    None,
)
if rmap_entry is not None and rmap_entry.symbols:
    return _render_outline_from_repo_map_entry(rmap_entry)

# Existing on-demand parse path follows.
```

Implement `_render_outline_from_repo_map_entry()` as a small helper that formats `RepoMapEntry.symbols` (already line-indexed) into the same output shape `CodeOutline` produces today. The existing on-demand parser stays as fallback for files not in repo_map (newly-created files between commits).

This piece is OPTIONAL — ship it if it's cheap. If the existing CodeOutline output structure differs significantly from RepoMapEntry's symbol list, defer to a follow-up.

### Step 6 — Tests

**Unit (`tests/agents/test_base.py` or new file):**

1. `test_repo_map_layer_present_when_map_populated` — build a workspace, populate `repo_map.entries` with two `RepoMapEntry` rows, call `effective_system_prompt(workspace=ws)`, assert the result contains "CODEBASE STRUCTURE" and at least one of the entry paths.
2. `test_repo_map_layer_absent_when_map_empty` — empty repo_map, assert "CODEBASE STRUCTURE" does NOT appear.
3. `test_repo_map_layer_absent_when_workspace_none` — call without workspace, assert layer not injected (existing test behavior unchanged).
4. `test_repo_map_layer_respects_budget` — populate with 200 large entries, assert rendered map ≤ ~1500 tokens (use `_approx_tokens`).
5. `test_repo_map_layer_position` — assert "CODEBASE STRUCTURE" appears AFTER memory block and BEFORE the system prompt / tool catalog markers in the joined string.

**Integration (`tests/integration/test_repo_map_in_prompt.py`):**

Drive a real BaseAgent with a real workspace whose `repo_map` is built from a tmp_path with three known python files. Call `effective_system_prompt(workspace=ws)`. Assert all three filenames appear in the result.

### Step 7 — Manual reconciliation

After tests pass, re-run any existing project (RougePack equivalent). Compare new `iteration_metrics.jsonl` against `run_26d48d4328`:

| Metric | Expected change |
|---|---|
| `tool_token_breakdown["fs-list"]` | down ≥ 30% (agents already see structure) |
| `tool_token_breakdown["fs-grep"]` | down ≥ 30% |
| `tool_token_breakdown["fs-read"]` | possibly UP slightly (agents read more *targeted* files), but with smaller files-per-call |
| `direct_file_read_tokens` total | down 10–25% |
| Total `input_tokens` | down 5–15% (offset by larger system prompt; net positive) |

If `direct_file_read_tokens` doesn't drop, something's off. Diagnose before celebrating.

---

## Acceptance criteria

1. `pytest tests/agents/test_base.py` (or wherever the new tests live) — green.
2. `pytest tests/integration/test_repo_map_in_prompt.py` — green.
3. `pytest tests/` full suite green.
4. Manual: render an agent's effective system prompt for any built-in role with a workspace whose repo_map has entries. Confirm "CODEBASE STRUCTURE" section is present and contains real file paths.
5. Reconciliation against a re-run of an existing project shows the expected drop in `fs-list` / `fs-grep` token contribution per Track 1b's `tool_token_breakdown`.
6. CLAUDE.md §28 rule compliance: every agent's effective prompt contains a repo_map section (when one exists).

---

## Risks / things to watch

- **Empty repo_map on first iteration**: very early in a fresh project, `workspace.repo_map` may have no entries (no source files yet). Layer suppresses cleanly via the `if not rmap.entries: return ""` guard. Verify test #2 covers this.
- **Cache stale between commits**: repo_map is rebuilt after `GIT_COMMIT` ([core/runner.py:871](core/runner.py:871)). Between commits within one iteration, an agent may write files that aren't yet in the map. That's acceptable — the map is for navigation, not authoritative state. Don't add per-write rebuild; that's expensive and the agent knows what it just wrote.
- **System-prompt size growth**: 1500 tokens of repo_map adds to every LLM call. Cache reads will absorb most of it (cached system prompts cost ~10% of fresh input), but on the first call per iteration it's 1500 fresh tokens. Net win still positive — agents save thousands by not having to grep their way to the layout. If reconciliation shows otherwise, lower the budget to 1000 tokens.
- **Persona prompts that already mention "the codebase"**: some persona prose may redundantly tell the agent to "explore the directory structure first." Minor — the new layer is additive, no conflict. Track 3d.4 (drift fix) will eventually trim the redundancy.
- **`code-outline` cache reuse (step 5) shape mismatch**: if `RepoMapEntry.symbols` doesn't carry enough info for CodeOutline's existing output, skip step 5 and ship without the cache reuse. The prompt-side win is independent.

---

## Commit plan

```
feat(agent): _render_repo_map_layer helper for system prompt
feat(agent): inject repo_map as Layer 4.5 in effective_system_prompt
refactor(agent): pass workspace through to effective_system_prompt callers
feat(tools): code-outline reuses workspace.repo_map cache before disk parse  [optional]
test(agent): repo_map layer presence, position, budget, empty-map suppression
test(integration): repo_map content visible in effective_system_prompt
docs(plans): mark Track 1c shipped in execution-queue README
```

---

## After this lands

- CLAUDE.md §28 ("Repo map is ALWAYS included in every context") is no longer violated.
- Track 3a (outline funnel) becomes more effective: agents start with the structure already in hand and use `code-outline` for signature detail rather than for initial navigation.
- Track 3d.4 (manifest auto-render for tools) compounds: the prompt now teaches *both* what tools exist and what files exist.
- The `get_context()` decision (wire it in vs. retire it) is no longer urgent — this track delivers the most-cited benefit (repo_map) without it. The remaining `get_context()` outputs (relevant_files content, recent_signals) can be reconsidered after Sprint 2.
