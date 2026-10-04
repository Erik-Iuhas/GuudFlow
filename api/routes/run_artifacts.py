"""/api/runs/{run_id}/artifact-files — read-only serving of a run's visual files (plan §5.10, §12.3).

Serves screenshots, registered artifacts, visual pre-check diff images and per-task
baselines to Studio's gallery. Only these folders under the run root are reachable:
`artifacts/`, `screenshots/`, `visual/`, `baselines/`. Paths are resolved inside the run
root with `safe_resolve` (no `..`, no absolute paths, no symlinks on the way), and only
allow-listed extensions are served. SVG and HTML carry `Content-Security-Policy: sandbox`
and every response carries `X-Content-Type-Options: nosniff`, so a hostile file cannot run
script even if opened directly. Like the deliverables route this is a plain GET: `<img>`
and `<iframe>` cannot send a bearer header.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

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


@router.get("/{run_id}/artifact-files/{path:path}")
async def get_artifact_file(request: Request, run_id: str, path: str) -> FileResponse:
    """Serve one visual file of a run. 404 for anything outside the allow-list."""
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
    return FileResponse(target, media_type=MEDIA_TYPES[ext], headers=headers)
