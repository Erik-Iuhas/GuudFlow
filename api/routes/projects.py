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
