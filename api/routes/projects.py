"""/api/projects — CRUD for project configs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status

from api.auth import require_bearer


router = APIRouter(prefix="/api/projects", tags=["projects"])


def _store(request: Request):
    return request.app.state.project_store


@router.get("", response_model=list[dict])
async def list_projects(request: Request) -> list[dict]:
    return await _store(request).list()


@router.get("/{project_id}")
async def get_project(request: Request, project_id: str) -> dict:
    project = await _store(request).get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return project


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_bearer)])
async def create_project(request: Request, project: dict = Body(...)) -> dict:
    if "project_id" not in project or not project["project_id"]:
        raise HTTPException(status_code=400, detail="project_id is required")
    if "goal" not in project:
        raise HTTPException(status_code=400, detail="goal is required")
    try:
        return await _store(request).create(project)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/{project_id}", dependencies=[Depends(require_bearer)])
async def update_project(request: Request, project_id: str, project: dict = Body(...)) -> dict:
    result = await _store(request).update(project_id, project)
    if result is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return result


@router.delete(
    "/{project_id}",
    dependencies=[Depends(require_bearer)],
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_project(request: Request, project_id: str):
    ok = await _store(request).delete(project_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Phase K.0 — project roadmap document ──────────────────────────────────


@router.get("/{project_id}/roadmap")
async def get_roadmap(request: Request, project_id: str) -> dict:
    """Return the project's roadmap.md (Phase K.0).

    Empty / missing file is a valid first-class state — projects
    without a roadmap behave exactly as today's runs do, just with
    no extra prompt layer for the team. Returns
    `{ "content": "...", "exists": bool }` so the caller can
    distinguish empty-file from never-created.

    404 if the project itself doesn't exist.
    """
    store = _store(request)
    if await store.get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")

    path = store.roadmap_path(project_id)
    if not path.is_file():
        return {"content": "", "exists": False}
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"failed to read roadmap: {e}")
    return {"content": content, "exists": True}


@router.put(
    "/{project_id}/roadmap",
    dependencies=[Depends(require_bearer)],
)
async def update_roadmap(
    request: Request,
    project_id: str,
    body: dict = Body(...),
) -> dict:
    """Write the project's roadmap.md. Empty body → file is removed
    so an empty roadmap is indistinguishable from never-created
    (the layer is omitted in either case).

    Body: `{ "content": "..." }`.
    """
    store = _store(request)
    if await store.get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")

    content = body.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content must be a string")

    path = store.roadmap_path(project_id)
    if content.strip() == "":
        # Empty roadmap — remove the file so the runner skips the layer.
        # Idempotent: missing file is fine.
        if path.is_file():
            try:
                path.unlink()
            except OSError as e:
                raise HTTPException(
                    status_code=500, detail=f"failed to clear roadmap: {e}"
                )
        return {"content": "", "exists": False}

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"failed to write roadmap: {e}")
    return {"content": content, "exists": True}
