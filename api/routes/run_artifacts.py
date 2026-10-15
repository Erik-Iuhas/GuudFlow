"""/api/runs/{run_id}/artifact-files — read-only serving of a run's visual files (plan §5.10, §12.3).

Serves screenshots, registered artifacts, visual pre-check diff images and per-task
baselines to Studio's gallery. Only these folders under the run root are reachable:
`artifacts/`, `screenshots/`, `visual/`, `baselines/`. Paths are resolved inside the run
root with `safe_resolve` (no `..`, no absolute paths, no symlinks on the way), and only
allow-listed extensions are served. SVG and HTML carry `Content-Security-Policy: sandbox`
and every response carries `X-Content-Type-Options: nosniff`, so a hostile file cannot run
script even if opened directly. `<img>` and `<iframe>` cannot send a bearer header, so
reads accept either the bearer token or the run's artifact cookie, which Studio gets from
`POST /api/runs/{run_id}/artifact-access` (bearer; signed, HttpOnly, one hour, scoped to
this run's `artifact-files` path). With no API token configured (dev) both are open.
"""

from __future__ import annotations

from pathlib import Path

import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse

from api.auth import (
    ARTIFACT_COOKIE,
    ARTIFACT_TTL_SECONDS,
    artifact_cookie_path,
    issue_artifact_cookie,
    require_bearer,
    require_bearer_or_artifact_cookie,
)

from core.harness.artifacts import safe_resolve

router = APIRouter(prefix="/api/runs", tags=["artifacts"])

SERVED_DIRS = frozenset({"artifacts", "screenshots", "visual", "baselines"})
MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".pdf": "application/pdf",
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
}
_ACTIVE_EXTS = frozenset({".svg", ".html", ".htm"})
# `sandbox` with no allow-* tokens: no scripts, no forms, unique origin. The rest cuts the
# network off for anything the document tries to load.
_ACTIVE_CSP = ("sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
               "font-src data:")


@router.post("/{run_id}/artifact-access", dependencies=[Depends(require_bearer)])
async def grant_artifact_access(run_id: str, response: Response) -> dict[str, int | None]:
    """Trade the bearer token for this run's artifact cookie (see the module docstring)."""
    issued = issue_artifact_cookie(run_id)
    if issued is None:
        return {"expires_at": None}
    value, expires = issued
    response.set_cookie(ARTIFACT_COOKIE, value, max_age=ARTIFACT_TTL_SECONDS,
                        path=artifact_cookie_path(run_id), httponly=True, samesite="strict",
                        secure=os.environ.get("GUUDFLOW_MODE") == "production")
    return {"expires_at": expires}


@router.get("/{run_id}/artifact-files/{path:path}",
            dependencies=[Depends(require_bearer_or_artifact_cookie)])
async def get_artifact_file(request: Request, run_id: str, path: str,
                            download: bool = False) -> FileResponse:
    """Serve one visual file of a run. 404 for anything outside the allow-list.

    `?download=1` sends it as an attachment (Studio's Download link) instead of inline.
    """
    runs_root = Path(request.app.state.data_root) / "runs"
    run_root = safe_resolve(runs_root, run_id)
    if run_root is None or len(Path(run_id).parts) != 1:
        raise HTTPException(status_code=404, detail="not found")
    parts = Path(path).parts
    if not parts or parts[0] not in SERVED_DIRS or Path(path).is_absolute():
        raise HTTPException(status_code=404, detail="not found")
    target = safe_resolve(run_root, path)
    ext = Path(path).suffix.lower()
    if target is None or ext not in MEDIA_TYPES or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"}
    if ext in _ACTIVE_EXTS:
        headers["Content-Security-Policy"] = _ACTIVE_CSP
    return FileResponse(target, media_type=MEDIA_TYPES[ext], headers=headers,
                        filename=target.name if download else None)
