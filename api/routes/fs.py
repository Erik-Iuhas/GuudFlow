"""/api/fs — server-side directory browser for "Local path" pickers.

Read-only. Honours AGENTFLOW_LOCAL_PATH_ALLOWLIST when set; otherwise
single-user dev posture (no restriction). Bearer-gated.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import require_bearer

router = APIRouter(prefix="/api/fs", tags=["fs"])


def _allowlist_permits(path: Path) -> bool:
    raw = os.environ.get("AGENTFLOW_LOCAL_PATH_ALLOWLIST", "").strip()
    if not raw:
        return True
    resolved = path.resolve()
    for raw_prefix in raw.split(","):
        prefix = raw_prefix.strip()
        if not prefix:
            continue
        try:
            resolved.relative_to(Path(prefix).resolve())
            return True
        except ValueError:
            continue
    return False


def _drive_roots() -> list[dict]:
    """On Windows return drive letters; on Unix return /."""
    if os.name == "nt":
        import string
        out = []
        for letter in string.ascii_uppercase:
            root = Path(f"{letter}:\\")
            if root.exists():
                out.append({"name": f"{letter}:\\", "path": str(root)})
        return out
    return [{"name": "/", "path": "/"}]


@router.get("/list", dependencies=[Depends(require_bearer)])
def list_directory(path: str = Query("")) -> dict:
    """List immediate subdirectories of `path`. Empty path returns
    drive roots (Windows) or `/` (Unix), plus the user's home dir as a
    convenience starting point.

    Response shape:
      {
        "path": "<resolved abs path or empty>",
        "parent": "<parent abs path or null>",
        "dirs": [{"name": "...", "path": "<abs>"}, ...]
      }
    """
    path = (path or "").strip()

    if not path:
        roots = _drive_roots()
        home = Path.home()
        if home.exists() and not any(r["path"] == str(home) for r in roots):
            roots.append({"name": f"~ ({home.name})", "path": str(home)})
        return {"path": "", "parent": None, "dirs": roots}

    p = Path(path)
    try:
        p = p.resolve(strict=False)
    except Exception:
        raise HTTPException(status_code=400, detail=f"invalid path: {path!r}")

    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=404, detail=f"not a directory: {p}")

    if not _allowlist_permits(p):
        raise HTTPException(
            status_code=403,
            detail=(
                f"path {p} is outside AGENTFLOW_LOCAL_PATH_ALLOWLIST"
            ),
        )

    dirs: list[dict] = []
    try:
        for entry in sorted(p.iterdir(), key=lambda e: e.name.lower()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            dirs.append({"name": entry.name, "path": str(entry)})
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"permission denied: {p}")

    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "parent": parent, "dirs": dirs}
