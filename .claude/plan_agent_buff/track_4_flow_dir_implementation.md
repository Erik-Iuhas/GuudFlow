# Track 4 — `.flow/` Storage Mode (Implementation Plan)

**Status:** Ready for implementation. Replaces the original `track_4_flow_dir_design.md` (which is now retained for design-question history).
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 400–700k for the full ship in one branch.
**Sprint slot:** Sprint 3.

## Reframed scope

Per Erik's clarifications (2026-05-02):

- **Storage mode toggle, not replacement.** `global` mode preserves today's behaviour (everything under `data/`). `in-directory` mode redirects projects/runs/desks to a `.flow/` folder at a configurable location.
- **"in-directory", not "in-repo".** No git-repo assumption. Mode is just "I want flow data to live at this folder."
- **Working directory and storage location are orthogonal.** You can point AgentFlow at any working directory (as today) AND independently choose where flow data lives. In-directory mode defaults `storage_root` to the working directory for ergonomics, but they can differ.
- **No "workflows" concept.** I had over-designed this. Projects ARE the unit of work today. Track 4 just MOVES where projects live.
- **PR mode per project.** New `pr_mode: "manual" | "auto"` field on ProjectConfig. `manual` (default) merges agent branches into a story/integration branch and stops. `auto` merges to default branch (today's behaviour). The user PRs themselves from the integration branch in `manual`.

## What changes

| Today | After Track 4 (in-directory mode) |
|---|---|
| `data/projects.json` (single file, all configs) | `.flow/projects/{name}/config.json` (one per project) |
| `data/projects/{id}/inputs/` | `.flow/projects/{name}/inputs/` |
| `data/runs/{run_id}/` | `.flow/projects/{name}/runs/{run_id}/` |
| `data/personas/{id}/desks/{project}/` (per-project working notes) | `.flow/projects/{name}/desks/{persona_id}/` |
| Persona memory at `data/personas/{id}/{episodes,patterns,reputation}` | **Unchanged** — stays global; personas cross directories |
| Runner merges agent branches → main | `pr_mode=manual`: merges → `flow/story-{slug}` or `flow/run-{id}`, stops. `pr_mode=auto`: merges → main (today). |
| Studio reads project list from `data/projects.json` | Reads from `.flow/projects/*/config.json` (in-directory) or stays as today (global) |

## Configuration surface

Three sources, in precedence order (highest first):

1. **Env vars** (for CI / scripted launches):
   - `GUUDFLOW_STORAGE_MODE`: `global` or `in-directory`
   - `GUUDFLOW_STORAGE_ROOT`: path
2. **User settings file** at `~/.guudflow/settings.json` (the GLOBAL user config — independent of any storage location). Edited via the Studio Settings panel (see below).
3. **Defaults**: `global` mode, `data/` root.

Cached at process start. Mode change → restart required (Studio shows "Restart backend to apply" banner after Save). Storage-root change in the same mode → reload effective on next run start.

Persona memory location is independent and stays global at `data/personas/` (or `~/.guudflow/personas/` once we add a CLI install path — not in this track). Personas cross directories by design (CLAUDE.md §35).

## Studio Settings panel — first instance

Erik's call: this also kicks off the Settings UI as the home for future user-configurable variables (connections to external services, internal limits, etc.). Track 4 ships the **first widget** (storage config) in a panel structured to host future ones.

**Placement:** far-right header gear icon → opens drawer (right slide-in, ~400px wide).

**Drawer structure (extensible):**
```
SETTINGS
├── Storage  ← v1 (this track)
│   ├── Mode: [Global ▾] / [In-directory ▾]
│   ├── Storage root: /path/to/folder  [Validate] [Browse later]
│   └── Status: ✓ Valid path, writable | ✗ Path doesn't exist
├── (future) Connections
├── (future) Models & Limits
└── (future) Personas
```

**Save flow:**
1. User changes mode/root, clicks Save.
2. Frontend POSTs to `/api/settings`.
3. Backend validates the path (exists, is writable, not a file).
4. Writes to `~/.guudflow/settings.json` atomically.
5. Returns 200 + a `restart_required: true` flag if mode changed.
6. Studio shows confirmation toast, plus a persistent banner if restart required.

**Path picker:** plain text input + Validate button for v1. No server-side directory browser yet (would require listing directories on the host — security and ergonomics concerns; defer). User types or pastes a path; backend tells them if it works.

**Backend Settings API:**
- `GET /api/settings` → current settings (env-or-file-or-default precedence applied).
- `PUT /api/settings` → write to `~/.guudflow/settings.json`. Returns the new effective config + `restart_required` flag.
- `POST /api/settings/validate-storage-root` → `{ "path": "/path" }` → returns `{ valid: bool, reason: str | None, writable: bool, exists: bool }`.

## Files to create

| File | Purpose |
|---|---|
| `core/storage.py` | **New module.** `StorageConfig` reads env vars; provides `data_root()`, `project_dir(name)`, `runs_dir()`, `run_dir(name, run_id)`, `persona_desks_dir(name)`, `personas_root()` (always global). |
| `core/flow_dir.py` | **New module.** `init_flow_dir(target: Path)` scaffolds a minimal `.flow/` (config.toml + empty `projects/`). Used by future CLI; not auto-invoked. |
| `tests/core/test_storage.py` | Mode toggle, env var precedence, default storage_root, path resolution per mode. |
| `tests/core/test_flow_dir.py` | `init_flow_dir` creates the right shape; idempotent; refuses if non-empty `.flow/` exists. |
| `tests/integration/test_in_directory_mode.py` | End-to-end: spin up runner with `GUUDFLOW_STORAGE_MODE=in-directory`, run a 1-agent flow, assert `.flow/projects/{name}/runs/{run_id}/` is populated. |
| `api/routes/settings.py` | **New module.** `GET /api/settings`, `PUT /api/settings`, `POST /api/settings/validate-storage-root`. |
| `core/user_settings.py` | **New module.** Read/write `~/.guudflow/settings.json`. Atomic write via tempfile rename. |
| `tests/api/test_settings.py` | GET returns defaults, PUT persists, validate-storage-root catches bad paths, restart_required flag fires on mode change. |
| `studio/src/components/SettingsDrawer.tsx` | **New component.** Right slide-in drawer with section structure for future expansion. |
| `studio/src/components/SettingsHeaderButton.tsx` | **New component.** Gear icon in the far-right of the app header; opens the drawer. |
| `studio/src/api/settings.ts` | Typed client functions for the three settings endpoints. |
| `studio/src/views/AppHeader.tsx` (or equivalent) | Mount the settings button at far-right. |

## Files to modify

| File | Change |
|---|---|
| [api/storage.py](api/storage.py) | `ProjectStore` constructor reads default root from `core.storage.data_root()`. In in-directory mode, the structure shifts to per-project config.json instead of one projects.json. Adapter at the read/write boundary. |
| [core/runner.py](core/runner.py) | Replace hardcoded `data/personas/...` paths with `core.storage.personas_root() / persona_id`. `run_root` arg already accepts a path; the API/worker layer routes through `storage.run_dir()`. New `pr_mode` field on `ProjectConfig` + new branch logic in the merge step. |
| [core/workspace.py](core/workspace.py) `ProjectConfig` | Add `pr_mode: Literal["manual", "auto"] = "manual"` field. |
| [api/main.py](api/main.py) | Pass `core.storage.data_root()` to ProjectStore + JobRunner construction. |
| [worker/job_runner.py](worker/job_runner.py) | Receive run_root from storage helper, not hardcoded. |
| [agents/persona.py](agents/persona.py), `agents/persona_desk.py` | Use `core.storage.persona_desks_dir(project_name)` for per-project desks. Persona memory stays at `core.storage.personas_root()` (always global). |
| Tests touching `data/...` paths | Audit ~25 files, update those that hardcode `data/runs/...` to use `core.storage.run_dir(...)` instead. Tests that pass an explicit `tmp_path` are unaffected. |

## Implementation steps

### Step 1 — `core/storage.py`

```python
"""Storage configuration — single switch between global and in-directory modes.

Two modes:
  - global (default): projects/runs/desks live under `data/` at the
    AgentFlow install location. This is today's behaviour.
  - in-directory: same structure, but rooted at a user-chosen folder
    under `.flow/`. The folder is typically a code repo; the runner
    operates on it as the working directory by default.

Persona memory (episodes/patterns/reputation) is ALWAYS global —
personas cross directories by design (CLAUDE.md §35). Per-project
working notes ("desks") move with the storage mode.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


StorageMode = Literal["global", "in-directory"]


@dataclass(frozen=True)
class StorageConfig:
    mode: StorageMode
    storage_root: Path
    personas_root: Path

    @classmethod
    def from_env(cls) -> "StorageConfig":
        mode_raw = os.environ.get("GUUDFLOW_STORAGE_MODE", "global").lower().strip()
        if mode_raw not in ("global", "in-directory"):
            raise ValueError(
                f"GUUDFLOW_STORAGE_MODE must be 'global' or 'in-directory', got {mode_raw!r}"
            )
        mode: StorageMode = mode_raw  # type: ignore

        if mode == "global":
            root = Path(os.environ.get("GUUDFLOW_DATA_ROOT", "data"))
        else:
            root_raw = os.environ.get("GUUDFLOW_STORAGE_ROOT")
            root = Path(root_raw) if root_raw else Path.cwd()
            root = root / ".flow"

        # Personas always global (cross-directory).
        personas = Path(os.environ.get("GUUDFLOW_PERSONAS_ROOT", "data/personas"))

        return cls(mode=mode, storage_root=root, personas_root=personas)


_cached: StorageConfig | None = None


def get_storage_config() -> StorageConfig:
    global _cached
    if _cached is None:
        _cached = StorageConfig.from_env()
    return _cached


def reset_storage_config_for_tests() -> None:
    """Tests that mutate env vars should call this to rebuild the cached config."""
    global _cached
    _cached = None


# Path helpers — call these instead of constructing paths inline.

def data_root() -> Path:
    return get_storage_config().storage_root


def projects_root() -> Path:
    cfg = get_storage_config()
    return cfg.storage_root / "projects"


def project_dir(name: str) -> Path:
    return projects_root() / name


def runs_root() -> Path:
    """Path under which run dirs live.
    Global mode: data/runs/. In-directory mode: <storage>/runs/ (flat) for now.

    NOTE: in-directory mode keeps runs flat under .flow/runs/ rather than
    nesting under each project. Reasons:
      1. A run is identified by its run_id and may be queried without
         knowing the project (the API does this today).
      2. Cross-project run analytics work the same in either mode.
      3. project_id is recorded on each run's meta.json — discovery
         from project to runs is a list+filter, cheap on the scales we
         care about.
    """
    return get_storage_config().storage_root / "runs"


def run_dir(run_id: str) -> Path:
    return runs_root() / run_id


def personas_root() -> Path:
    """Always global. Personas cross directories (CLAUDE.md §35)."""
    return get_storage_config().personas_root


def persona_dir(persona_id: str) -> Path:
    return personas_root() / persona_id


def persona_desks_root(persona_id: str) -> Path:
    """Per-persona desks (working notes scoped per project).
    Global mode: data/personas/{id}/desks/. In-directory mode: SAME — desks
    follow the persona, not the directory. Per-project SCOPING within the
    desk dir is by project name, unchanged."""
    return persona_dir(persona_id) / "desks"
```

### Step 2 — Wire callers through storage

Find every `Path("data/...")` and `data/runs/{...}` style construction. Replace with the helper. Most call sites are already parameterized (see `api/storage.py:ProjectStore.__init__(root)`); the change is "where does that root come from."

Starting points found:
- `api/main.py` — constructs `ProjectStore(root=Path("data"))`. Change to `ProjectStore(root=storage.data_root())`.
- `core/runner.py` — finds `data/personas/{id}` for persona loading. Replace with `storage.persona_dir(...)`.
- `worker/job_runner.py` — constructs `run_root`. Route through `storage.run_dir(run_id)`.
- `agents/persona.py`, `agents/persona_desk.py` — persona file paths. Use `storage.persona_dir(...)`.
- Tests — most pass explicit `tmp_path`; only the ones that resolve absolute `data/...` paths need updating.

### Step 3 — `pr_mode` on ProjectConfig

```python
# core/workspace.py — ProjectConfig
pr_mode: Literal["manual", "auto"] = "manual"
```

In the runner's merge-completion step (the existing `_merge_to_main` or equivalent path), branch on `pr_mode`:

```python
if cfg.pr_mode == "auto":
    # Today's behaviour — merge integration branch into default branch.
    self.git.merge_to_default_branch(integration_branch)
else:
    # manual: stop at the integration branch. Surface in completion summary.
    self.completion_summary["integration_branch"] = integration_branch
    self.completion_summary["pr_command"] = (
        f"gh pr create --base {self.git.default_branch} "
        f"--head {integration_branch} --title {self._suggest_pr_title()!r}"
    )
```

The integration branch name:
- If a story is open → `flow/story-{slug}` (slug = lowercase story id)
- Else → `flow/run-{run_id_short}`

### Step 4 — `init_flow_dir`

```python
# core/flow_dir.py
def init_flow_dir(target: Path) -> None:
    """Create a minimal `.flow/` skeleton at `target / .flow/`.
    Idempotent — safe to call on existing valid `.flow/`. Refuses to overwrite
    a non-empty `.flow/` that's missing config.toml (potential damage)."""
    flow = target / ".flow"
    if flow.exists():
        if not (flow / "config.toml").exists() and any(flow.iterdir()):
            raise FlowDirError(
                f"{flow} exists but isn't a valid flow directory; "
                "refusing to overwrite. Move or delete it first."
            )
        # Already initialised — no-op.
        return

    flow.mkdir()
    (flow / "projects").mkdir()
    (flow / "runs").mkdir()
    (flow / "config.toml").write_text(
        "# AgentFlow project storage — schema_version 1\n"
        "schema_version = 1\n"
        f"created_at = \"{_utcnow_iso()}\"\n",
        encoding="utf-8",
    )
    # .flow/.gitignore so scratch files aren't committed.
    (flow / ".gitignore").write_text(
        "# AgentFlow scratch — not committed\n"
        "scratch/\n"
        "*.tmp\n",
        encoding="utf-8",
    )
```

### Step 5 — Studio API change

If in-directory mode, project listing reads from filesystem (`projects/*/config.json`) instead of `projects.json`. Implement as a method on `ProjectStore` that branches on the configured mode. Existing `/api/projects` route signature unchanged.

### Step 6 — Tests

- `tests/core/test_storage.py`:
  - `global` mode default; `data/` is the root.
  - `in-directory` mode requires `GUUDFLOW_STORAGE_ROOT` or defaults to cwd.
  - Bad mode value raises.
  - `personas_root()` always global regardless of mode.
  - `reset_storage_config_for_tests` correctly clears cache.

- `tests/core/test_flow_dir.py`:
  - `init_flow_dir` creates expected structure.
  - Idempotent on already-initialised dir.
  - Refuses to overwrite non-flow-shaped `.flow/`.
  - Resulting `config.toml` parses correctly.

- `tests/integration/test_in_directory_mode.py`:
  - Set env → in-directory mode at tmp_path.
  - Init `.flow/`.
  - Create a project + 1-agent run.
  - Assert: `.flow/projects/{name}/config.json` exists; `.flow/runs/{id}/iteration_metrics.jsonl` exists; persona memory still at `data/personas/`.

- Existing tests that hardcode `data/runs/...`: route through `storage.run_dir(...)`. Quick audit pass.

## Acceptance criteria

1. `pytest tests/core/test_storage.py` — green.
2. `pytest tests/core/test_flow_dir.py` — green.
3. `pytest tests/integration/test_in_directory_mode.py` — green.
4. `pytest tests/api/test_settings.py` — green.
5. `pytest tests/` — full suite green; `global` mode behavior identical to before this track.
6. Manual: launch backend with `GUUDFLOW_STORAGE_MODE=in-directory GUUDFLOW_STORAGE_ROOT=/tmp/playground`, hit `/api/projects` — returns empty list (no projects yet). Create one via Studio, run it, confirm `/tmp/playground/.flow/projects/{name}/runs/{id}/iteration_metrics.jsonl` is populated.
7. Manual: run with default env vars (no GUUDFLOW_STORAGE_*), confirm everything still lands at `data/...` exactly as today.
8. `pr_mode=manual`: confirm runner stops at the integration branch and the completion summary surfaces a `gh pr create` command.
9. Manual: open Studio, click far-right settings gear, change storage root in the drawer, click Save, confirm `~/.guudflow/settings.json` is written and the "restart to apply" banner appears (if mode changed).

## Risks

- **Test churn from path changes.** Mitigation: only update tests that hardcode `data/...`; leave `tmp_path`-based tests alone.
- **Personas in in-directory mode confusion.** Some users might expect personas-per-directory. Document explicitly: personas are global, desks are per-project.
- **Running both modes simultaneously**: not supported. The cached `StorageConfig` reads env once. Doc note: restart to switch modes.
- **Migration**: not auto-migrated. A user moving from global to in-directory has to manually move project configs. Out of scope for v1; document.

## Commit plan

```
feat(storage): core/storage.py — mode-toggleable storage paths
feat(user_settings): ~/.guudflow/settings.json read/write with atomic save
feat(flow_dir): init_flow_dir scaffolder for .flow/
refactor(api): ProjectStore reads default root from storage helper
refactor(runner): persona paths route through storage.persona_dir()
refactor(worker): run_root from storage.run_dir()
feat(workspace): pr_mode field on ProjectConfig
feat(runner): pr_mode=manual stops at integration branch with PR command
feat(api): /api/settings GET/PUT + validate-storage-root endpoint
feat(studio): SettingsDrawer + far-right header button (first widget: storage)
test(storage): mode toggle + env var resolution
test(user_settings): atomic write + corrupted file handling
test(flow_dir): init scaffold + idempotency + safety
test(api): settings endpoints
test(integration): in-directory mode end-to-end
docs(claude): document storage mode toggle in CLAUDE.md
```
