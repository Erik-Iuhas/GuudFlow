"""/api/projects/{project_id}/inputs — project-scoped input ingestion.

DESIGN §33. Uploads land in `data/projects/{id}/inputs/uploads/` and stay
attached to the project across runs. The runner copies them into a run's
`workspace/inputs/uploads/` at run start (Chunks 4-5).

This module ships uploads + a list view that returns stable placeholders for
`source_repo` (Chunk 2) and `prior_runs` (Chunk 3) so the response shape is
forward-compatible.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status

from api.auth import require_bearer


router = APIRouter(prefix="/api/projects/{project_id}/inputs", tags=["inputs"])

_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def _store(request: Request):
    return request.app.state.project_store


async def _require_project(request: Request, project_id: str) -> None:
    project = await _store(request).get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")


def _safe_filename(uploads_dir: Path, filename: str) -> Path:
    """Return the resolved path for `filename` under `uploads_dir`, or raise
    HTTPException(400) if the name escapes the directory or is unusable."""
    if not filename or "/" in filename or "\\" in filename or "\x00" in filename:
        raise HTTPException(status_code=400, detail=f"invalid filename: {filename!r}")
    target = (uploads_dir / filename).resolve()
    try:
        target.relative_to(uploads_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid filename: {filename!r}")
    return target


@router.post("/upload", dependencies=[Depends(require_bearer)])
async def upload_inputs(
    request: Request,
    project_id: str,
    files: list[UploadFile] = File(...),
) -> dict:
    await _require_project(request, project_id)
    uploads_dir = _store(request).uploads_dir(project_id)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    written: list[dict] = []
    for upload in files:
        target = _safe_filename(uploads_dir, upload.filename or "")
        size = 0
        with target.open("wb") as out:
            while True:
                chunk = await upload.read(_CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
                size += len(chunk)
        written.append({"filename": target.name, "size": size})
    return {"uploaded": written}


@router.get("")
async def list_inputs(request: Request, project_id: str) -> dict:
    await _require_project(request, project_id)
    uploads_dir = _store(request).uploads_dir(project_id)

    uploads: list[dict] = []
    if uploads_dir.exists():
        for entry in sorted(uploads_dir.iterdir()):
            if entry.is_file():
                uploads.append({"filename": entry.name, "size": entry.stat().st_size})

    return {
        "uploads": uploads,
        "source_repo": None,
        "prior_runs": [],
    }


@router.delete(
    "/uploads/{filename}",
    dependencies=[Depends(require_bearer)],
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_upload(request: Request, project_id: str, filename: str):
    await _require_project(request, project_id)
    uploads_dir = _store(request).uploads_dir(project_id)
    target = _safe_filename(uploads_dir, filename)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"upload {filename!r} not found")
    target.unlink()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
