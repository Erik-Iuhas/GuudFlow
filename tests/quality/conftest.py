"""Keep plain `pytest` away from the merged trees the quality gate inspects.

A finished run lives in `{run_dir}/workspace/working/` and contains its own
`test_*.py` files. They are the gate's *subject*: it runs them in a subprocess, in
a throwaway copy. They must never be collected as part of GuudFlow's own suite.
"""

from __future__ import annotations

from pathlib import Path


def pytest_ignore_collect(collection_path: Path) -> bool | None:
    """Ignore anything under a `workspace/working` directory or a recorded `runs/` fixture dir."""
    parts = collection_path.parts
    for i, part in enumerate(parts[:-1]):
        if part == "workspace" and parts[i + 1] == "working":
            return True
    if "recorded_runs" in parts:
        return True
    return None
