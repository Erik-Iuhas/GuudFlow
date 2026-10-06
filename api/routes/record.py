"""/api/projects/{id}/record: trace any id, export the record, preview the repo files (P8).

All routes need the bearer token and only read.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from api.auth import require_bearer
from core.record import INCLUDE, RecordError, export, spec_files, trace

router = APIRouter(prefix="/api/projects", tags=["record"],
                   dependencies=[Depends(require_bearer)])

_DEFAULT_INCLUDE = "spec,decisions,sessions"
_MEDIA = {"md": "text/markdown; charset=utf-8", "json": "application/json"}


async def _root(request: Request, project_id: str) -> Path:
    if await request.app.state.project_store.get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return Path(request.app.state.data_root)


@router.get("/{project_id}/record")
async def get_trace(request: Request, project_id: str,
                    ref: str = Query(min_length=1, max_length=300)) -> dict[str, Any]:  # Any: JSON
    """`{ref, events}`: the life of a requirement, decision, item, session or file."""
    root = await _root(request, project_id)
    try:
        events = await asyncio.to_thread(trace, root, project_id, ref)
    except RecordError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {"ref": ref.strip(), "events": [e.to_dict() for e in events]}


@router.get("/{project_id}/record/export")
async def get_export(request: Request, project_id: str,
                     format: Literal["md", "json"] = "md",  # noqa: A002 — the query name
                     include: str = _DEFAULT_INCLUDE) -> Response:
    """The record as a download (`include` is a comma list of spec, decisions, sessions,
    plan, rules, costs)."""
    root = await _root(request, project_id)
    parts = {p.strip() for p in include.split(",") if p.strip()}
    try:
        body = await asyncio.to_thread(export, root, project_id, include=parts, fmt=format)
    except RecordError as exc:
        raise HTTPException(status_code=400, detail=f"{exc} (known: {', '.join(INCLUDE)})") from None
    return Response(content=body, media_type=_MEDIA[format], headers={
        "Content-Disposition": f'attachment; filename="{project_id}-record.{format}"'})


@router.get("/{project_id}/record/spec-files")
async def get_spec_files(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """`{files: {path: content}}`: SPEC.md and the decision records, as a preview."""
    root = await _root(request, project_id)
    return {"files": await asyncio.to_thread(spec_files, root, project_id)}
