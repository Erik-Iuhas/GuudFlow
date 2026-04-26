# Stage 2 — Workspace & state

**Budget:** 350-500k tokens
**Status:** done (2026-04-25)
**Exit criteria (CLAUDE.md):** Workspace initialises; FileWorkspace path safety enforced; git repo inits; repo_map builds and renders compact signatures; context_manager returns AgentContext within token budget; iteration-aware diff logic works correctly.

DESIGN sections to read: §3.1, §17, §19, §26, §28.

## Files

### Order (per CLAUDE.md key files list)

- [x] `core/workspace.py` ✅
  - [x] `Artifact` dataclass (typed `str | dict`, never `dict[str, str]`)
  - [x] `Workspace` dataclass — all §3.1 fields plus `AgentState` enum, `HumanRequest`, `PendingScreenshot`, `CommitRecord`, `GitState`, `PackageRequest`, `RepoMap`/`RepoMapEntry`
  - [x] `post_signal()` append-only with user_board mirroring; `mark_seen(signal_id, role)`
  - [x] `unseen_user_comments(role)` filters by `seen_by`; `signals_of_type(*types)` read-only filter
  - [x] `tests/core/test_workspace.py` — 18 tests covering initialisation, user_board mirroring, HUMAN_RESPONDED carve-out, mark_seen idempotency, append order, artifact typing
  - [x] tests green (18/18)

- [x] `core/file_workspace.py` ✅
  - [x] `FileWorkspace(root)` creates `working/`, `inputs/`, `deliverables/` idempotently
  - [x] `_safe_path()` rejects absolute, drive/rooted, parent-traversal, symlink escapes
  - [x] async API: `read`, `write`, `append`, `read_binary`, `write_binary`, `list` (recursive opt), `exists` (returns False on unsafe rather than raising), `delete` (file or dir)
  - [x] inputs API: `read_input`, `list_inputs`
  - [x] `promote(working_path, deliverable_path=None)` copies into `deliverables/`
  - [x] `absolute(path)` for tools that genuinely need an absolute path (still safety-checked)
  - [x] `tests/core/test_file_workspace.py` — 32 tests covering happy paths, all path-escape vectors, symlink escape (real symlink created via `_try_symlink`), promote roundtrips
  - [x] tests green (32/32)

- [x] `core/git_workspace.py` ✅
  - [x] `GitWorkspace` wrapping gitpython, local user.name/email, gpgsign off
  - [x] `init()` (idempotent, empty initial commit on `main`); `agent_branch(role)` lazy creation
  - [x] `commit(role, message, files=None)` returns `GitCommitInfo`; raises if nothing staged or empty message
  - [x] `read_at(branch, path)`, `diff(branch_a, branch_b)`, `log(branch, limit)`
  - [x] `merge(branch, into="main")` returns True on clean / False on conflict (auto-aborts)
  - [x] `SignalEmitter` callback for GIT_COMMIT / GIT_MERGE / GIT_CONFLICT (decoupled from Workspace)
  - [x] tests/core/test_git_workspace.py — 19 tests covering init, branch isolation, commit, diff, log, clean merge, conflict
  - [x] tests green (19/19)

- [x] `core/repo_map.py` ✅
  - [x] tree-sitter Python + JS/TS parsing with `Language(grammar, name)` API
  - [x] symbol extraction: classes, functions, decorated defs, JS arrow-fn const declarations
  - [x] import resolution (Python dotted, JS relative `./foo`, external dropped)
  - [x] PageRank with damping=0.85, 30 iterations, "imports → ranks higher" semantics
  - [x] `build(root)`, `update_incremental(existing, root, changed_paths)`, `render_compact(max_tokens)` with truncation marker
  - [x] dotfile/.git/.venv directory skipping
  - [x] tests/core/test_repo_map.py — 16 tests covering parsing, decorators, ranking, incremental new/delete/edit, render budget
  - [x] tests green (16/16)

- [x] `core/context_manager.py` ✅
  - [x] `AgentContext` (repo_map, relevant_files, git_diff, recent_signals, token_count); `SearchResult`
  - [x] `TIER_BUDGETS = {heavy:12k, solid:6k, fast:2k}`
  - [x] `get_context(task, role, max_tokens, tier, include_diff, recent_signals_limit)` — repo map always included, ranks by camel/snake-aware token overlap, diff-preferred on iter ≥ 2
  - [x] `search(query, limit)` — symbol search via repo_map, ready for embedding fan-out later
  - [x] `_recent_signals` excludes own output and already-seen signals
  - [x] tests/core/test_context_manager.py — 18 tests covering budget, ranking, iteration-2 diff, signal filtering, search, empty repo
  - [x] tests green (18/18)

- [x] `data/personas/_example/` skeleton ✅
  - [x] profile.json, episodes.jsonl (empty), patterns.json, style.json, reputation.json, history.json

## Stage 2 verification
- Full core suite: **132/132 passing**
- End-to-end smoke: `python` script wires FileWorkspace + GitWorkspace + repo_map.build + ContextManager.get_context → returns non-empty context with token count → ✅

## Stage smoke test (run before ticking Stage 2 done)

```python
# in a scratch script or REPL
from pathlib import Path
from core.workspace import Workspace, Artifact
from core.file_workspace import FileWorkspace
from core.git_workspace import GitWorkspace
import tempfile
with tempfile.TemporaryDirectory() as d:
    fw = FileWorkspace(root=Path(d))
    fw.write("hello.py", "print('hi')")
    gw = GitWorkspace(root=Path(d))
    gw.init()
    gw.commit("developer", "feat: hello", ["hello.py"])
    print("ok")
```

## Notes
- workspace.py is built first so file/git/repo_map can be imported by it via forward reference (avoid circular imports — workspace.py keeps `files: "FileWorkspace | None" = None` as a slot).
- Embeddings stay OFF by default (DESIGN §28). sentence-transformers import is lazy.
- All file I/O must go through FileWorkspace (DESIGN rule 13).
