"""/api/runs — read-only run history + artifacts download."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse


router = APIRouter(prefix="/api/runs", tags=["runs"])


def _store(request: Request):
    return request.app.state.run_store


@router.get("")
async def list_runs(
    request: Request,
    project_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict]:
    return await _store(request).list(project_id=project_id, limit=limit)


@router.get("/{run_id}")
async def get_run(request: Request, run_id: str) -> dict:
    meta = await _store(request).get(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return meta


@router.get("/{run_id}/board")
async def get_run_board(request: Request, run_id: str) -> list[dict]:
    board = await _store(request).get_board(run_id)
    if board is None:
        raise HTTPException(status_code=404, detail=f"board for {run_id!r} not found")
    return board


@router.get("/{run_id}/deliverables/{path:path}")
async def download_deliverable(request: Request, run_id: str, path: str):
    deliverables = _store(request).deliverables_dir(run_id)
    target = (deliverables / path).resolve()
    deliverables_resolved = deliverables.resolve()
    try:
        target.relative_to(deliverables_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes deliverables/")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"deliverable {path!r} not found")
    return FileResponse(target)
