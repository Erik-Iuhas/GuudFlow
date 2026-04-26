"""/api/projects/{project_id}/inputs — project-scoped input ingestion.

DESIGN §33. Uploads land in `data/projects/{id}/inputs/uploads/`,
git/local-path source repos land in `inputs/source_repo/` (with a
`source_repo.meta.json` sidecar describing origin), and continuation
copies (Chunk 3) land in `inputs/from_run_{run_id}/`. Everything stays
attached to the project across runs; the runner seeds a run's
`workspace/inputs/` from this storage at run start (Chunks 4-5).
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse

from api.auth import require_bearer
from core import source_repo as source_repo_lib
from core.secret_scan import scan_directory, strip_secrets


router = APIRouter(prefix="/api/projects/{project_id}/inputs", tags=["inputs"])

_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _allowlist_permits(path: Path) -> bool:
    """If AGENTFLOW_LOCAL_PATH_ALLOWLIST is set (comma-separated path
    prefixes), `path` must resolve under one of them. Unset = single-user
    dev posture, no restriction."""
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
    store = _store(request)
    uploads_dir = store.uploads_dir(project_id)

    uploads: list[dict] = []
    if uploads_dir.exists():
        for entry in sorted(uploads_dir.iterdir()):
            if entry.is_file():
                uploads.append({"filename": entry.name, "size": entry.stat().st_size})

    source_repo: dict | None = None
    meta_path = store.source_repo_meta_path(project_id)
    if meta_path.exists() and store.source_repo_dir(project_id).exists():
        try:
            source_repo = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            source_repo = None

    return {
        "uploads": uploads,
        "source_repo": source_repo,
        "prior_runs": store.iter_from_runs(project_id),
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


def _finalize_source_repo(
    target: Path,
    meta_path: Path,
    meta: dict,
    force: bool,
) -> JSONResponse | dict:
    """Run secret scan; refuse if findings + not force, otherwise strip + write
    metadata. Returns the success body or a 409 JSONResponse on refusal."""
    findings = scan_directory(target)
    if findings and not force:
        shutil.rmtree(target, ignore_errors=True)
        if meta_path.exists():
            meta_path.unlink()
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": "secrets detected in source — pass force=true to import with stripping",
                "findings": [f.to_dict() for f in findings],
            },
        )
    if findings:
        strip_secrets(target, findings)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {
        "source_repo": meta,
        "warnings": [f.to_dict() for f in findings],
    }


@router.post("/git", dependencies=[Depends(require_bearer)])
async def import_git(
    request: Request,
    project_id: str,
    body: dict = Body(...),
):
    await _require_project(request, project_id)
    git_url = (body.get("git_url") or "").strip()
    if not git_url:
        raise HTTPException(status_code=400, detail="git_url is required")
    branch = (body.get("branch") or "main").strip() or "main"
    shallow = bool(body.get("shallow", True))
    force = bool(body.get("force", False))

    store = _store(request)
    target = store.source_repo_dir(project_id)
    meta_path = store.source_repo_meta_path(project_id)

    try:
        await source_repo_lib.clone_git_repo(git_url, branch, shallow, target)
    except source_repo_lib.SourceCloneError as e:
        raise HTTPException(status_code=400, detail=f"git clone failed: {e}")

    meta = {
        "kind": "git_url",
        "git_url": git_url,
        "branch": branch,
        "shallow": shallow,
        "imported_at": _now_iso(),
    }
    return _finalize_source_repo(target, meta_path, meta, force)


@router.post("/local_path", dependencies=[Depends(require_bearer)])
async def import_local_path(
    request: Request,
    project_id: str,
    body: dict = Body(...),
):
    await _require_project(request, project_id)
    raw_path = (body.get("path") or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="path is required")
    src = Path(raw_path)
    if not src.is_absolute():
        raise HTTPException(status_code=400, detail="path must be absolute")
    if not _allowlist_permits(src):
        raise HTTPException(
            status_code=403,
            detail="path is not under AGENTFLOW_LOCAL_PATH_ALLOWLIST",
        )
    if not src.is_dir():
        raise HTTPException(status_code=400, detail=f"path is not a directory: {src}")
    force = bool(body.get("force", False))

    store = _store(request)
    target = store.source_repo_dir(project_id)
    meta_path = store.source_repo_meta_path(project_id)

    source_repo_lib.copy_local_dir(src, target)

    meta = {
        "kind": "local_path",
        "path": str(src),
        "imported_at": _now_iso(),
    }
    return _finalize_source_repo(target, meta_path, meta, force)


# ── Continuation: attach a prior run's deliverables ────────────────────────


@router.post("/from_run/{run_id}", dependencies=[Depends(require_bearer)])
async def attach_prior_run(
    request: Request,
    project_id: str,
    run_id: str,
    body: dict = Body(default_factory=dict),
):
    await _require_project(request, project_id)
    run_store = request.app.state.run_store
    run_meta = await run_store.get(run_id)
    if run_meta is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")

    deliverables = run_store.deliverables_dir(run_id)
    if not deliverables.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} has no deliverables to attach",
        )

    store = _store(request)
    target = store.from_run_dir(project_id, run_id)
    meta_path = store.from_run_meta_path(project_id, run_id)

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    target_deliverables = target / "deliverables"
    shutil.copytree(deliverables, target_deliverables)

    feedback = (body.get("feedback") or "").strip()
    has_feedback = bool(feedback)
    if has_feedback:
        (target / "feedback.md").write_text(feedback + "\n", encoding="utf-8")

    meta = {
        "kind": "prior_run",
        "run_id": run_id,
        "project_id_of_origin": run_meta.get("project_id"),
        "attached_at": _now_iso(),
        "has_feedback": has_feedback,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"prior_run": meta}


@router.delete(
    "/from_run/{run_id}",
    dependencies=[Depends(require_bearer)],
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def detach_prior_run(request: Request, project_id: str, run_id: str):
    await _require_project(request, project_id)
    store = _store(request)
    target = store.from_run_dir(project_id, run_id)
    meta_path = store.from_run_meta_path(project_id, run_id)
    if not target.exists() and not meta_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"prior run {run_id!r} not attached to this project",
        )
    if target.exists():
        shutil.rmtree(target)
    if meta_path.exists():
        meta_path.unlink()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
