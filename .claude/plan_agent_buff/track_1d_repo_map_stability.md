# Track 1d — Repo Map: Cache Stability + Task-Aware Relevance (Sprint 1.7)

**Status:** Ready for implementation. Sonnet-executable.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 120–200k. Stop and reassess if over 250k.
**Sprint slot:** 1.7 — directly after Track 1c.
**Why now:** `run_ff1e92d43c` failed (deadline + protocol-cap exhaustion) because Track 1c rebuilds repo_map after every commit and the always-on injection is structurally ranked but **task-blind**. This track does both: stabilise the cache AND make the map task-aware so agents see *what matters to them specifically*, not just "what's structurally important to the codebase."

---

## Context

Track 1c gave every agent the codebase structure in their system prompt. Two problems:

**Problem 1 — cache instability.** [core/runner.py:869-873](core/runner.py:869) does a full PageRank rebuild after every `GIT_COMMIT`. Six commits per run × ~30k token system prompt = six cache busts. Cache hit ratio dropped 25× → 9×, per-call wall time doubled, game_logic_dev hit the 600s deadline mid-work, and pygame_renderer_dev exhausted its 3 protocol-parse retries.

**Problem 2 — task-blindness.** What we inject is `render_compact()` output: top-N files **by pure PageRank** ([core/repo_map.py:350](core/repo_map.py:350)). PageRank measures structural importance (heavily-imported files rank high) — not task relevance. The same map is shown to the architect designing a save system, the dev implementing collision logic, and the reviewer chasing a render bug. Aider's repo-map gets this right by mixing PageRank with task-keyword relevance. We have that machinery already at [core/context_manager.py:87](core/context_manager.py:87) (`_score_entry()`), but it's dead code — no built-in agent calls `get_context()` where it lives.

Track 1d closes both gaps.

---

## What the new layer looks like in the prompt

```
CODEBASE STRUCTURE (top by structural importance — PageRank over imports):

src/game/state_machine.py  [py]  symbols: StateMachine, push, pop, peek
src/world/map_generator.py [py]  symbols: ProceduralMap, generate
... (≤ 400 tokens of top-N entries)

RELEVANT TO YOUR CURRENT TASK ("Implement collision system for destructible walls"):

src/systems/collision.py [py]  symbols: CollisionSystem, on_collide, PlayerHitEnemy, WallHit
src/world/tile.py        [py]  symbols: Tile, Wall, DestructibleWall, hp
src/entities/player.py   [py]  symbols: Player, try_wall_break, charge
... (≤ 300 tokens of task-scored entries)
```

When the agent has no `current_task` set yet (early architect), only the structure section renders. When the task has zero overlap with any entry (very rare — query terms are split into camel/snake components), only structure renders.

---

## Scope

**In scope, four changes:**

- **1d.1 — Lower base budget** for the always-on map: 1500 → **400 tokens** (down from the planned 600 in the earlier 1d draft, because we're now splitting a second 300-token section out of the same overall budget).
- **1d.2 — Cache stability:** rebuild repo_map **only at agent-transition boundaries**, not after every commit. Within an iteration the map is byte-stable → system-prompt cache holds.
- **1d.3 — Task-aware relevance section:** new sub-layer renders task-scored entries via `_score_entry()` against the agent's `_current_task`. Budget ~300 tokens. Suppressed cleanly when no task or no matches.
- **1d.4 *(optional, defer if risky)*** — Use `update_incremental()` ([core/repo_map.py:302](core/repo_map.py:302)) for further cache stability when a rebuild does fire. Skip if it diverges from `build()` in tests.

**Out of scope:**
- Moving Layer 4.5 to a different position. Defer until we see whether 1d.1-3 alone fix the protocol-parse regression.
- Changing how `code-outline` works.
- A `repo-search` or `repo-relevant` tool (option B/C from the architecture discussion). The two-layer prompt approach is what we're shipping; tools are a future option if needed.
- Per-agent cached relevance (re-render per LLM call is fine — it's pure string assembly, < 1ms).

---

## Files to modify

| File | Change |
|---|---|
| [agents/base.py](agents/base.py) | `_render_repo_map_layer()` becomes a structure section renderer; new `_render_task_relevance_layer()` does scored relevance. Both called from `effective_system_prompt()`. |
| [core/repo_map.py](core/repo_map.py) | Expose `score_entries_for_task(repo_map, task) -> list[tuple[score, RepoMapEntry]]` as a public helper. Implementation = lift from `core/context_manager.py:_score_entry` + sort. |
| [core/runner.py](core/runner.py) | Around [line 869-873](core/runner.py:869): replace post-commit rebuild with dirty-flag pattern. Add `_rebuild_repo_map_if_dirty()` method. Wire consumer at [line 2438](core/runner.py:2438) `_drive_agent_run()`. |
| `tests/core/test_repo_map_score.py` | **New file.** Unit-test `score_entries_for_task` against fixtures with known symbol/path overlap. |
| `tests/core/test_repo_map_rebuild.py` | **New file.** Dirty-flag + agent-transition rebuild semantics. |
| `tests/agents/test_base.py` | Extend Track 1c tests: structure section uses 400-token budget; task-relevance section appears when `_current_task` set; absent when not. |
| [tests/integration/test_repo_map_in_prompt.py](tests/integration/test_repo_map_in_prompt.py) | Extend with: (a) byte-stable across two prompt renders within an iteration; (b) task-relevance section reflects `_current_task`. |

---

## Implementation steps

### Step 1 — Public scoring helper
File: [core/repo_map.py](core/repo_map.py)

Lift the scoring logic from [core/context_manager.py:54-102](core/context_manager.py:54) and re-export it from `repo_map.py`:

```python
import re
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

def _split_camel_snake(word: str) -> set[str]:
    """Split CamelCase and snake_case identifiers into component lower tokens."""
    parts = re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", word)
    return {p.lower() for p in parts if p}

def _query_tokens(text: str) -> set[str]:
    out: set[str] = set()
    for m in _WORD_RE.finditer(text):
        word = m.group(0)
        out.add(word.lower())
        out.update(_split_camel_snake(word))
    return out

def score_entries_for_task(
    repo_map: RepoMap,
    task: str,
) -> list[tuple[float, RepoMapEntry]]:
    """Score every entry by symbol/path overlap with task tokens. Returns
    descending-sorted (score, entry) pairs. Score 0 entries are filtered out.

    Same formula as core/context_manager.py: 2·symbol_hits + 1·path_hits + 0.1·rank.
    """
    if not task.strip() or not repo_map.entries:
        return []
    qtokens = _query_tokens(task)
    if not qtokens:
        return []
    scored: list[tuple[float, RepoMapEntry]] = []
    for entry in repo_map.entries:
        path_t = _query_tokens(entry.path)
        sym_t: set[str] = set()
        for s in entry.symbols:
            sym_t.update(_split_camel_snake(s))
        path_hits = len(path_t & qtokens)
        sym_hits = len(sym_t & qtokens)
        if path_hits == 0 and sym_hits == 0:
            continue
        score = 2.0 * sym_hits + 1.0 * path_hits + 0.1 * entry.rank
        scored.append((score, entry))
    scored.sort(key=lambda p: p[0], reverse=True)
    return scored
```

Optionally: refactor `core/context_manager.py:_score_entry` to call this same helper (DRY). Keep the change-set small — leave context_manager alone if the refactor isn't trivial.

### Step 2 — Split the renderer
File: [agents/base.py](agents/base.py)

Replace the existing `_render_repo_map_layer()` with two methods. The first renders structure; the second renders task-relevance. `effective_system_prompt()` calls both.

```python
def _render_repo_map_layer(self, workspace: Any) -> str:
    """Track 1c/1d — codebase structure section (CLAUDE.md §28, always-on).

    PageRank top-N entries, signatures only. Task-blind by design — paired
    with _render_task_relevance_layer for the task-aware half.
    """
    if workspace is None:
        return ""
    rmap = getattr(workspace, "repo_map", None)
    if rmap is None or not getattr(rmap, "entries", None):
        return ""
    from core.repo_map import render_compact
    body = render_compact(rmap, max_tokens=400)  # ↓ from 1500 (Track 1d)
    if not body.strip():
        return ""
    return (
        "CODEBASE STRUCTURE (top files by structural importance — PageRank over imports):\n\n"
        + body
    )

def _render_task_relevance_layer(self, workspace: Any) -> str:
    """Track 1d — files scored relevant to this agent's current task.

    Mixes PageRank with symbol/path keyword overlap (aider-style). Suppressed
    cleanly when there's no task, no entries, or no overlap.
    """
    if workspace is None:
        return ""
    rmap = getattr(workspace, "repo_map", None)
    if rmap is None or not getattr(rmap, "entries", None):
        return ""
    task = (self._current_task or "").strip()
    if not task:
        return ""
    from core.repo_map import score_entries_for_task, _approx_tokens
    scored = score_entries_for_task(rmap, task)
    if not scored:
        return ""
    # Render top-K entries with the same compact format as render_compact,
    # but bounded by a smaller budget.
    BUDGET = 300
    lines: list[str] = []
    used = 0
    for _score, entry in scored:
        sym = ", ".join(entry.symbols[:8])
        imp = ", ".join(entry.imports[:6])
        line = f"{entry.path}  [{entry.type}]"
        if sym:
            line += f"  symbols: {sym}"
        if imp:
            line += f"  imports: {imp}"
        cost = _approx_tokens(line) + 1  # +1 for newline
        if used + cost > BUDGET:
            break
        lines.append(line)
        used += cost
    if not lines:
        return ""
    # Truncate task echo for the header (long goals would dominate).
    task_echo = task if len(task) <= 120 else task[:117] + "..."
    return (
        f"RELEVANT TO YOUR CURRENT TASK ({task_echo!r}):\n\n"
        + "\n".join(lines)
    )
```

### Step 3 — Wire both layers
File: [agents/base.py](agents/base.py)

In `effective_system_prompt()`, where Layer 4.5 currently lives, render *both* sections:

```python
# Layer 4.5 — repo_map structure (Track 1c) + task-aware relevance (Track 1d).
ws = workspace if workspace is not None else self._workspace
structure = self._render_repo_map_layer(ws)
if structure:
    parts.append(structure)
relevance = self._render_task_relevance_layer(ws)
if relevance:
    parts.append(relevance)
```

Update the docstring layer order: 4.5a structure, 4.5b task-relevance.

### Step 4 — Cache stability via dirty flag
Files: [core/runner.py](core/runner.py)

Replace the post-commit rebuild block:
```python
# OLD: full rebuild on every GIT_COMMIT
if signal_type is SignalType.GIT_COMMIT:
    try:
        self.workspace.repo_map = build_repo_map(self.files.working)
    except Exception:
        pass
```

With the dirty-flag pattern:
```python
# NEW: defer rebuild to next agent-transition (Track 1d)
if signal_type is SignalType.GIT_COMMIT:
    self._repo_map_dirty = True
```

Add to runner `__init__`:
```python
self._repo_map_dirty: bool = False
```

Add the consume method:
```python
def _rebuild_repo_map_if_dirty(self) -> None:
    """Track 1d: consume the dirty flag and rebuild before the next agent
    spawns. Within-iteration commits don't bust system-prompt cache because
    the rebuild is deferred to agent-transition boundaries."""
    if not self._repo_map_dirty:
        return
    try:
        self.workspace.repo_map = build_repo_map(self.files.working)
    except Exception:
        pass
    finally:
        self._repo_map_dirty = False
```

### Step 5 — Wire the consumer at agent-spawn

In `_drive_agent_run()` ([core/runner.py:2438](core/runner.py:2438)):

```python
async def _drive_agent_run(self, agent: BaseAgent, cfg: AgentConfig):
    self._rebuild_repo_map_if_dirty()  # NEW: pre-spawn rebuild consume
    cls_run = type(agent).run
    if cls_run is not BaseAgent.run:
        return await agent.run(self.workspace)
    return await self._generic_tool_use_loop(agent, cfg)
```

### Step 6 — Tests

**`tests/core/test_repo_map_score.py`** (new):
1. Build a `RepoMap` with entries `[(src/auth.py, [User, login]), (src/db.py, [Connection]), (src/utils.py, [chunk])]`.
2. `score_entries_for_task(rmap, "implement login flow")` → assert `src/auth.py` ranks first; `src/db.py` and `src/utils.py` either not in result (no overlap) or below.
3. Empty task → empty list.
4. Empty map → empty list.
5. Mixed CamelCase: task `"AuthManager"` matches a symbol named `AuthManager` AND splits to match `auth_manager.py` path.

**`tests/core/test_repo_map_rebuild.py`** (new):
- Mock `build_repo_map` to a counter.
- Force two `GIT_COMMIT` signals → assert dirty=True, build call count = 0.
- Call `_rebuild_repo_map_if_dirty()` → assert build call count = 1, dirty=False.
- Force another `GIT_COMMIT` → dirty=True; rebuild fires on next consume.

**`tests/agents/test_base.py`** (extend):
- `test_repo_map_layer_uses_400_budget` — assert structure section ≤ ~470 tokens (400 + banner).
- `test_task_relevance_layer_present_when_task_set` — set `_current_task = "auth implementation"`, populate map with `src/auth.py [User, login]` and `src/random.py [foo]`. Assert "RELEVANT TO YOUR CURRENT TASK" present, contains `src/auth.py`, does NOT contain `src/random.py`.
- `test_task_relevance_layer_absent_when_no_task` — `_current_task = ""`, populated map. Assert "RELEVANT TO" not present.
- `test_task_relevance_layer_absent_when_no_overlap` — task `"deploy infra"`, no matching symbols/paths. Assert "RELEVANT TO" not present.

**[tests/integration/test_repo_map_in_prompt.py](tests/integration/test_repo_map_in_prompt.py)** (extend):
- `test_repo_map_stable_across_calls_within_iteration` — render prompt twice with no rebuild → byte-identical.
- `test_task_relevance_reflects_current_task_change` — render with task A, switch to task B, re-render. Assert relevance section shifts to entries matching B.

### Step 7 — (Optional) Incremental rebuild
File: [core/runner.py](core/runner.py)

If `update_incremental` proves stable in tests, swap inside `_rebuild_repo_map_if_dirty()`. Skip otherwise.

---

## Acceptance criteria

1. `pytest tests/core/test_repo_map_score.py` — green.
2. `pytest tests/core/test_repo_map_rebuild.py` — green.
3. `pytest tests/agents/test_base.py` — green, including 4 new tests.
4. `pytest tests/integration/test_repo_map_in_prompt.py` — green, including 2 new tests.
5. `pytest tests/` full suite green.
6. Manual: re-run RougePack equivalent on Sonnet across all three agents. Inspect `iteration_metrics.jsonl`:
   - `cache_read_tokens / input_tokens` ratio for any agent ≥ **20×** (was 9× in run_ff1e92d43c).
   - No 600s deadline hits.
   - `tool_token_breakdown[fs-grep]` stays low (Track 1c win preserved or improved).
7. Manual: count `PROTOCOL_PARSE_FAIL` signals in the new run's `board.jsonl`. Should be **≤ 2** total (was 5).
8. Manual: inspect any agent's effective system prompt mid-run. Confirm both sections render — "CODEBASE STRUCTURE" with PageRank-ranked entries AND "RELEVANT TO YOUR CURRENT TASK" with task-scored entries.

---

## Risks / things to watch

- **Within-iteration staleness:** an agent that creates a new file then tries to outline it won't see it in the cached map until next agent spawns. `code-outline` re-parses from disk anyway ([agents/tools/code.py:383](agents/tools/code.py:383)) so it's unaffected. `fs-read` always works on disk. Verified safe.
- **Task echo too long:** truncated to 120 chars in the header. If task descriptions trend longer in practice, raise the cap.
- **Score overlap is shallow:** word-level token matching misses semantic synonyms (`auth` vs `login` vs `signin`). Acceptable for v1 — can layer in embeddings later if needed.
- **Architect with no task:** the meta-agent / architect runs without a current_task in early iterations. Relevance section suppresses cleanly; structure section still renders. Verified by `test_task_relevance_layer_absent_when_no_task`.
- **Cache still busts on commit-then-spawn:** by design — that's when the agent transitions. The win is *not busting mid-iteration*.
- **Layer ordering:** structure + relevance both go between memory and base prompt. If protocol-parse failures persist, position is the issue, and a follow-up moves them below Layer 5.5.

---

## Commit plan

```
feat(repo_map): score_entries_for_task helper for task-aware ranking
fix(agent): lower repo_map structure layer budget 1500 → 400 tokens
feat(agent): _render_task_relevance_layer for task-scored entries
feat(agent): wire both layers into effective_system_prompt
fix(runner): defer repo_map rebuild to agent-transition (dirty-flag)
test(repo_map): score_entries_for_task fixtures (overlap, camel/snake, empties)
test(runner): repo_map rebuilds only once per agent transition regardless of commit count
test(agent): structure budget enforced; task-relevance layer present/absent semantics
test(integration): byte-stable within iteration; task-relevance reflects current_task
[optional] perf(runner): use update_incremental for cache-stable rebuilds
```

---

## After this lands

Two real wins:
- **Cache effectiveness restored.** Within-iteration cache holds. Per-call latency drops back to ~12s. Deadline hits go away.
- **Map becomes useful, not just present.** Each agent sees not only what's structurally important but what's *relevant to the task they were assigned*. Closes the architectural gap that made Track 1c look smarter than it was.

Track 1c's win (fewer fs-grep / fs-list calls) preserved because the structure layer is still always-on. Plus a new effect: agents may stop reading irrelevant files entirely once they see the task-scored section directly tells them which files matter.

If the relevance section proves reliably useful, future tracks could add:
- Per-agent budget tuning (architect needs more structure, dev needs more relevance).
- Embeddings layer for semantic relevance (Track 3a or later).
- "Files mentioned in board signals" boost (aider's "files in the chat").
