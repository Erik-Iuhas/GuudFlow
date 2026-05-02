"""Storage configuration — single switch between global and in-directory modes.

Track 4. Two modes for where projects, runs, and per-project agent desks live:

  - `global` (default): everything under `data/` at the AgentFlow install
    location. This is today's behaviour.
  - `in-directory`: same shape, rooted at a user-chosen folder. Default
    storage_root is the current working directory; `.flow/` is created
    underneath. The folder is typically a code repo, but no git
    requirement — hence "in-directory", not "in-repo".

Persona memory (episodes / patterns / reputation) is ALWAYS global —
personas cross directories by design (CLAUDE.md §35). Per-project
agent desks (working notes scoped per project) follow the storage mode.

Configuration sources, in precedence order:
  1. Env vars: GUUDFLOW_STORAGE_MODE, GUUDFLOW_STORAGE_ROOT
     (for CI / scripted launches; highest precedence)
  2. User settings file: ~/.guudflow/settings.json
     (edited via the Studio Settings drawer)
  3. Defaults: global mode, `data/` root

The configuration is cached at first read. Changing it requires a
restart of the process. Tests can call `reset_storage_config_for_tests()`
to rebuild the cache after mutating env vars.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


StorageMode = Literal["global", "in-directory"]
WorkingTreeMode = Literal["isolated", "in-place"]


_USER_SETTINGS_PATH = Path.home() / ".guudflow" / "settings.json"


def _load_user_settings() -> dict:
    """Read ~/.guudflow/settings.json. Returns {} on missing or corrupted file.

    Corrupted files are NOT raised — settings are advisory, and a malformed
    file shouldn't prevent the runner from starting in default mode.
    """
    try:
        return json.loads(_USER_SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}


@dataclass(frozen=True)
class StorageConfig:
    mode: StorageMode
    storage_root: Path
    personas_root: Path
    # Track 4.3 — when mode == "in-directory", `in-place` makes the runner
    # use the chosen directory itself as the agents' working tree (instead
    # of copying files into the run's `workspace/working/`). Forced to
    # "isolated" in global mode (no chosen directory to point at).
    working_tree_mode: WorkingTreeMode = "isolated"
    # Resolved base directory the user picked. Same as `storage_root.parent`
    # when mode is "in-directory" (since `.flow/` is created underneath);
    # equal to `storage_root` in global mode. Convenience: agents and the
    # runner reference this when wiring `in-place`.
    storage_base: Path | None = None

    @classmethod
    def from_env(cls) -> "StorageConfig":
        """Resolve effective config: env > settings file > defaults."""
        settings = _load_user_settings()

        # Mode resolution.
        mode_raw = (
            os.environ.get("GUUDFLOW_STORAGE_MODE")
            or settings.get("storage_mode")
            or "global"
        ).lower().strip()
        if mode_raw not in ("global", "in-directory"):
            raise ValueError(
                f"storage mode must be 'global' or 'in-directory', got {mode_raw!r}"
            )
        mode: StorageMode = mode_raw  # type: ignore[assignment]

        # Storage-root resolution.
        storage_base: Path | None = None
        if mode == "global":
            root_raw = (
                os.environ.get("GUUDFLOW_DATA_ROOT")
                or settings.get("data_root")
                or "data"
            )
            root = Path(root_raw)
        else:
            root_raw = (
                os.environ.get("GUUDFLOW_STORAGE_ROOT")
                or settings.get("storage_root")
            )
            base = Path(root_raw) if root_raw else Path.cwd()
            storage_base = base
            root = base / ".flow"

        # Personas root: always global. Independent env var for advanced users
        # who keep persona memory at a custom location (e.g. ~/.guudflow/personas
        # once we ship a CLI install path).
        personas_raw = (
            os.environ.get("GUUDFLOW_PERSONAS_ROOT")
            or settings.get("personas_root")
            or "data/personas"
        )
        personas = Path(personas_raw)

        # Working-tree mode resolution. Only meaningful when mode is
        # "in-directory" — global mode forces "isolated" (no chosen
        # directory to point the working tree at).
        wt_raw = (
            os.environ.get("GUUDFLOW_WORKING_TREE_MODE")
            or settings.get("working_tree_mode")
            or "isolated"
        ).lower().strip()
        if wt_raw not in ("isolated", "in-place"):
            raise ValueError(
                f"working_tree_mode must be 'isolated' or 'in-place', got {wt_raw!r}"
            )
        working_tree_mode: WorkingTreeMode = wt_raw  # type: ignore[assignment]
        if mode == "global" and working_tree_mode == "in-place":
            working_tree_mode = "isolated"

        return cls(
            mode=mode,
            storage_root=root,
            personas_root=personas,
            working_tree_mode=working_tree_mode,
            storage_base=storage_base,
        )


_cached: StorageConfig | None = None


def get_storage_config() -> StorageConfig:
    """Return the cached effective storage config, building it on first call."""
    global _cached
    if _cached is None:
        _cached = StorageConfig.from_env()
    return _cached


def reset_storage_config_for_tests() -> None:
    """Tests that mutate env vars or the settings file should call this to
    rebuild the cached config on the next get_storage_config() call.
    """
    global _cached
    _cached = None


# ── Path helpers — call these instead of constructing paths inline ────────


def data_root() -> Path:
    """Return the storage root. In global mode this is `data/`; in in-directory
    mode this is `<storage_root>/.flow/`."""
    return get_storage_config().storage_root


def projects_root() -> Path:
    """Path under which project dirs live."""
    return data_root() / "projects"


def project_dir(project_id: str) -> Path:
    """Per-project directory."""
    return projects_root() / project_id


def runs_root() -> Path:
    """Path under which run dirs live.

    Both modes keep runs flat under `<storage_root>/runs/`. Reasons:
      1. A run is identified by run_id and may be queried without knowing
         the project (the API does this today).
      2. Cross-project run analytics work the same in either mode.
      3. project_id is recorded on each run's meta.json — discovery
         from project to runs is a list+filter, cheap at our scale.
    """
    return data_root() / "runs"


def run_dir(run_id: str) -> Path:
    """Per-run directory (board.jsonl, iteration_metrics.jsonl, workspace/, …)."""
    return runs_root() / run_id


def personas_root() -> Path:
    """Persona memory root. ALWAYS global — personas cross directories
    (CLAUDE.md §35)."""
    return get_storage_config().personas_root


def persona_dir(persona_id: str) -> Path:
    """Per-persona directory (profile.json, episodes.jsonl, patterns.json, …)."""
    return personas_root() / persona_id


def working_tree_mode() -> WorkingTreeMode:
    """Resolved working-tree mode. `in-place` only takes effect in
    in-directory storage mode; in global mode this is always `isolated`."""
    return get_storage_config().working_tree_mode


def storage_base_dir() -> Path | None:
    """The user-chosen base directory in in-directory mode (parent of
    `.flow/`). None in global mode."""
    return get_storage_config().storage_base


def user_settings_path() -> Path:
    """Where the global user settings file lives.

    Independent of storage_mode. The file controls (among other things)
    the storage_mode itself, so it can't live inside the storage location.
    """
    return _USER_SETTINGS_PATH
