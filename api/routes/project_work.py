"""/api/projects/{id}/backlog and /runs-review — the project as ongoing work (plan §13).

The backlog is the project's list of work; a run takes items from it (see
`POST /api/invoke/{id}` with `backlog_items`). Every finished run waits under
`/runs-review` until a human accepts it (merged into the project's main,
finished items closed) or rejects it (main untouched, items reopened).
All routes need the bearer token.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.auth import require_bearer
from core.backlog import Backlog, BacklogError
from core.project_repo import ProjectRepo, ProjectRepoConflict, ProjectRepoError
from core.project_runs import decide_run

router = APIRouter(prefix="/api/projects", tags=["project-work"],
                   dependencies=[Depends(require_bearer)])


class BacklogCreate(BaseModel):
    """Body of POST /backlog."""

    title: str
    acceptance: list[str] = Field(default_factory=list)
    kind: str = "code"
    size: str = "M"
    priority: int = 0
    notes: str = ""
    files_hint: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class GroomRequest(BaseModel):
    """Body of POST /backlog/groom."""

    meta_model: str | None = None


class BacklogPatch(BaseModel):
    """Body of PATCH /backlog/{item_id}: any subset; `status` drops, reopens or releases."""

    title: str | None = None
    acceptance: list[str] | None = None
    kind: str | None = None
    size: str | None = None
    priority: int | None = None
    notes: str | None = None
    files_hint: list[str] | None = None
    depends_on: list[str] | None = None
    status: Literal["open", "dropped"] | None = None


class Decision(BaseModel):
    """Body of POST /runs-review/{run_id}/accept|reject."""

    note: str = ""


async def _project(request: Request, project_id: str) -> Path:
    store = request.app.state.project_store
    if await store.get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return Path(request.app.state.data_root)


def _bad(exc: Exception, code: int = 400) -> HTTPException:
    return HTTPException(status_code=code, detail=str(exc))


async def _run_is_live(request: Request, run_id: str) -> bool:
    return await request.app.state.run_registry.get(run_id) is not None


# ── backlog ─────────────────────────────────────────────────────────────────
@router.get("/{project_id}/backlog")
async def list_backlog(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """Every item in working order (in a run, open by priority, done, dropped)."""
    data_root = await _project(request, project_id)
    return {"items": [{**asdict(i), "total_cost_usd": i.total_cost()}
                      for i in Backlog(data_root, project_id).items()]}


@router.post("/{project_id}/backlog", status_code=201)
async def add_backlog_item(request: Request, project_id: str,
                           body: BacklogCreate) -> dict[str, Any]:  # Any: JSON
    """Add an open item."""
    data_root = await _project(request, project_id)
    try:
        item = Backlog(data_root, project_id).add(
            body.title, body.acceptance, kind=body.kind, size=body.size,
            priority=body.priority, notes=body.notes, files_hint=body.files_hint,
            depends_on=body.depends_on)
    except BacklogError as exc:
        raise _bad(exc) from None
    return asdict(item)


@router.post("/{project_id}/backlog/groom")
async def groom_backlog(request: Request, project_id: str,
                        body: GroomRequest | None = None) -> dict[str, Any]:  # Any: JSON
    """Proposed new items from the roadmap and the backlog. Nothing is added: a human
    adds the ones they want with POST /backlog."""
    from api.routes.meta import _build_meta_llm
    from core.groom import propose_items

    data_root = await _project(request, project_id)
    project = await request.app.state.project_store.get(project_id) or {}
    llm = _build_meta_llm(request, meta_model=(body or GroomRequest()).meta_model)
    try:
        proposals = await propose_items(llm, data_root, project_id, str(project.get("goal", "")))
    except Exception as exc:  # noqa: BLE001 — an LLM failure is a 502, not a crash
        raise HTTPException(status_code=502, detail=f"the model could not groom: {exc}") from None
    return {"proposals": [asdict(p) for p in proposals]}


@router.patch("/{project_id}/backlog/{item_id}")
async def edit_backlog_item(request: Request, project_id: str, item_id: str,
                            body: BacklogPatch) -> dict[str, Any]:  # Any: JSON
    """Edit fields; `status` "dropped" drops, "open" reopens (or frees an item whose run is gone)."""
    data_root = await _project(request, project_id)
    backlog = Backlog(data_root, project_id)
    changes = body.model_dump(exclude_none=True, exclude={"status"})
    try:
        item = backlog.get(item_id)
        if changes:
            item = backlog.update(item_id, **changes)
        if body.status == "dropped":
            item = backlog.drop(item_id, "dropped by a human")
        elif body.status == "open" and item.status == "in_run":
            run_id = item.run_id or ""
            if await _run_is_live(request, run_id):
                raise HTTPException(status_code=409, detail=f"{item_id} is in live run {run_id}")
            if ProjectRepo(data_root, project_id).get(run_id) is not None:
                raise HTTPException(status_code=409, detail=(
                    f"{item_id} waits for run {run_id}: accept or reject that run instead"))
            backlog.release(run_id, "freed by a human")
            item = backlog.get(item_id)
        elif body.status == "open" and item.status in ("dropped", "done"):
            item = backlog.reopen(item_id)
    except BacklogError as exc:
        raise _bad(exc, 404 if "unknown" in str(exc) else 400) from None
    return asdict(item)


# ── runs waiting for review ─────────────────────────────────────────────────
@router.get("/{project_id}/runs-review")
async def list_project_runs(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """The project repo's main and every submitted run (newest first)."""
    data_root = await _project(request, project_id)
    repo = ProjectRepo(data_root, project_id)
    main = repo.sha("main") if repo.exists() else None
    return {"main": main, "runs": [asdict(r) for r in repo.runs()]}


@router.get("/{project_id}/runs-review/{run_id}/changes")
async def run_changes(request: Request, project_id: str, run_id: str) -> dict[str, str]:
    """What accepting the run would bring into main (stat + patch)."""
    data_root = await _project(request, project_id)
    try:
        return {"run_id": run_id, "diff": ProjectRepo(data_root, project_id).changes(run_id)}
    except ProjectRepoError as exc:
        raise _bad(exc, 404) from None


async def _decide(request: Request, project_id: str, run_id: str, accept: bool,
                  note: str) -> Any:  # Any: dict or JSONResponse
    data_root = await _project(request, project_id)
    if await _run_is_live(request, run_id):
        raise HTTPException(status_code=409, detail=f"run {run_id} is still running")
    try:
        decision = decide_run(data_root, project_id, run_id, accept=accept, note=note)
    except ProjectRepoConflict as exc:
        return JSONResponse(status_code=409, content={
            "detail": (f"main changed since run {run_id} started and the merge conflicts. "
                       "Start a run that continues it to reconcile, or reject it."),
            "files": exc.files})
    except ProjectRepoError as exc:
        raise _bad(exc, 404 if "never submitted" in str(exc) else 400) from None
    return {"run": asdict(decision.run), "items": [asdict(i) for i in decision.items]}


@router.post("/{project_id}/runs-review/{run_id}/accept")
async def accept_run(request: Request, project_id: str, run_id: str,
                     body: Decision | None = None) -> Any:  # Any: dict or JSONResponse
    """Merge the run into the project's main; its finished backlog items close."""
    return await _decide(request, project_id, run_id, True, (body or Decision()).note)


@router.post("/{project_id}/runs-review/{run_id}/reject")
async def reject_run(request: Request, project_id: str, run_id: str,
                     body: Decision | None = None) -> Any:  # Any: dict or JSONResponse
    """Leave main as it is; the run's backlog items reopen."""
    return await _decide(request, project_id, run_id, False, (body or Decision()).note)
