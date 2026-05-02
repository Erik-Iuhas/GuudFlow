"""Flow directory scaffolder — creates `.flow/` skeleton.

Track 4. Used during in-directory mode setup. The scaffold is minimal:
just enough structure for a fresh Studio session to find empty `projects/`
and `runs/` folders. Future tracks (Track 5 doc manager, etc.) will
populate optional sub-trees.

Idempotent — calling on an already-initialised `.flow/` is a no-op.
Refuses to operate on an existing non-empty `.flow/` that's missing
`config.toml` (potential damage to whatever was there).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1


class FlowDirError(Exception):
    """Raised when init_flow_dir cannot safely scaffold (would damage data)."""


def init_flow_dir(target: Path) -> Path:
    """Create `<target>/.flow/` if missing. Returns the created (or existing) path.

    Args:
        target: parent directory under which `.flow/` should live.

    Raises:
        FlowDirError: if `<target>/.flow/` exists, is non-empty, and lacks a
            `config.toml` (suggests it's not a flow directory and we'd
            damage existing data by overwriting).
    """
    target = Path(target)
    if not target.exists():
        raise FlowDirError(f"target directory does not exist: {target}")
    if not target.is_dir():
        raise FlowDirError(f"target must be a directory, got: {target}")

    flow = target / ".flow"

    if flow.exists():
        config = flow / "config.toml"
        if config.exists():
            return flow  # already initialised — idempotent no-op.
        # Exists but lacks our marker file. If it's empty we'll initialise.
        # If it has content we don't recognise, refuse — could be user data.
        try:
            has_content = any(flow.iterdir())
        except OSError:
            has_content = True
        if has_content:
            raise FlowDirError(
                f"{flow} exists but is not a flow directory (no config.toml). "
                "Refusing to overwrite. Move or delete it first."
            )

    flow.mkdir(exist_ok=True)
    (flow / "projects").mkdir(exist_ok=True)
    (flow / "runs").mkdir(exist_ok=True)

    config = flow / "config.toml"
    if not config.exists():
        config.write_text(
            "# AgentFlow project storage — managed by core/flow_dir.py\n"
            f"schema_version = {SCHEMA_VERSION}\n"
            f'created_at = "{datetime.now(timezone.utc).isoformat()}"\n',
            encoding="utf-8",
        )

    gitignore = flow / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "# AgentFlow scratch — not committed\n"
            "scratch/\n"
            "*.tmp\n",
            encoding="utf-8",
        )

    return flow
