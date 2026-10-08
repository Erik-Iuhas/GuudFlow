"""/api/projects/{id}/backlog and /runs-review — the project as ongoing work (plan §13).

The backlog is the project's list of work; a run takes items from it (see
`POST /api/invoke/{id}` with `backlog_items`). Every finished run waits under
`/runs-review` until a human accepts it (merged into the project's main,
finished items closed) or rejects it (main untouched, items reopened).
All routes need the bearer token.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.auth import require_bearer
from core.backlog import Backlog, BacklogError
from core.project_repo import ProjectRepo, ProjectRepoConflict, ProjectRepoError
from core.autopilot import AutopilotConfig, AutopilotError
from core.autopilot import status as autopilot_status
from core.project_remote import RemoteConfig, RemoteError, sync
from core.pm_prompts import Seats
from api.routes.pm import schedule_distill
from core.project_runs import decide_run
from core.harness.spec_brief import resolve_items
from core.spec import Spec
from core.spec_adherence import cosigned, needs_cosign
from core.salvage import interrupted_runs, salvage_run
from core.work_db import WorkDB

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
    effort: Literal["", "low", "medium", "high", "xhigh", "max"] = ""


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
    effort: Literal["", "low", "medium", "high", "xhigh", "max"] | None = None
    status: Literal["open", "dropped"] | None = None


class Decision(BaseModel):
    """Body of POST /runs-review/{run_id}/accept|reject."""

    note: str = ""
    # Manual or visual checks the person confirmed while accepting (the ledger's "person").
    confirmed_checks: list[str] = Field(default_factory=list, max_length=200)


class CosignComment(BaseModel):
    """One comment of a co-sign."""

    file: str | None = None
    line: int | None = None
    text: str
    severity: Literal["note", "should_fix", "blocking"] = "note"


class Cosign(BaseModel):
    """Body of POST /runs-review/{run_id}/cosign (the reviewer seat, plan §1 call 7)."""

    verdict: Literal["cosign", "changes"]
    comments: list[CosignComment] = Field(default_factory=list)


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
    spec = Spec(WorkDB(Path(data_root) / "projects" / project_id).read_spec_doc())
    return {"items": [{**asdict(i), "total_cost_usd": i.total_cost()}
                      for i in resolve_items(spec, Backlog(data_root, project_id).items())]}


@router.post("/{project_id}/backlog", status_code=201)
async def add_backlog_item(request: Request, project_id: str,
                           body: BacklogCreate) -> dict[str, Any]:  # Any: JSON
    """Add an open item."""
    data_root = await _project(request, project_id)
    try:
        item = Backlog(data_root, project_id).add(
            body.title, body.acceptance, kind=body.kind, size=body.size,
            priority=body.priority, notes=body.notes, files_hint=body.files_hint,
            depends_on=body.depends_on, effort=body.effort)
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


# ── interrupted sessions (FIELD_NOTES F3) ──────────────────────────────────
@router.get("/{project_id}/interrupted")
async def list_interrupted(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """Sessions that stopped without finishing (host restart): they hold backlog items, are
    not live and never wrote their end-of-session record."""
    data_root = await _project(request, project_id)
    live = set(request.app.state.run_registry.runs)
    return {"runs": [asdict(r) for r in await asyncio.to_thread(
        interrupted_runs, Path(data_root), project_id, live)]}


@router.post("/{project_id}/interrupted/{run_id}/salvage")
async def salvage_interrupted(request: Request, project_id: str,
                              run_id: str) -> dict[str, Any]:  # Any: JSON
    """Send what an interrupted session merged to review; unfinished cards go back to the
    backlog. Nothing runs again."""
    data_root = await _project(request, project_id)
    live = set(request.app.state.run_registry.runs)
    known = {r.run_id for r in await asyncio.to_thread(
        interrupted_runs, Path(data_root), project_id, live)}
    if run_id not in known:
        raise HTTPException(status_code=409, detail=f"{run_id} is not an interrupted session")
    try:
        state = await asyncio.to_thread(salvage_run, Path(data_root), project_id, run_id)
    except FileNotFoundError as exc:
        raise _bad(exc, 409) from None
    return {"run_id": run_id, "state": state}


# ── runs waiting for review ─────────────────────────────────────────────────
@router.get("/{project_id}/runs-review")
async def list_project_runs(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """The project repo's main and every submitted run (newest first)."""
    data_root = await _project(request, project_id)
    repo = ProjectRepo(data_root, project_id)
    main = repo.sha("main") if repo.exists() else None
    project = await request.app.state.project_store.get(project_id) or {}
    remote = _remote(project)
    try:
        pilot = AutopilotConfig.from_settings((project.get("approval_gates") or {})
                                              .get("autopilot"))
        pilot_status = autopilot_status(data_root, project_id, pilot, time.time())
    except AutopilotError as exc:
        pilot_status = {"enabled": False, "error": str(exc)}
    seat = Seats.from_project(project).reviewer
    return {"main": main,
            "runs": [{**asdict(r), "needs_cosign": r.state == "pending"
                      and needs_cosign(data_root, project_id, r.run_id, seat)}
                     for r in repo.runs()],
            "autopilot": pilot_status,
            "remote": None if remote is None else {
                "url": remote.url, "branch": remote.branch, "publish": remote.publish,
                "token": bool(remote.token())}}


@router.post("/{project_id}/remote/sync")
async def sync_remote(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """Bring the remote branch into the project's main now (plan §14.1)."""
    data_root = await _project(request, project_id)
    remote = _remote(await request.app.state.project_store.get(project_id) or {})
    if remote is None:
        raise HTTPException(status_code=400, detail="this project has no remote configured")
    return asdict(sync(ProjectRepo(data_root, project_id), remote))


@router.get("/{project_id}/runs-review/{run_id}/changes")
async def run_changes(request: Request, project_id: str, run_id: str) -> dict[str, str]:
    """What accepting the run would bring into main (stat + patch)."""
    data_root = await _project(request, project_id)
    try:
        return {"run_id": run_id, "diff": ProjectRepo(data_root, project_id).changes(run_id)}
    except ProjectRepoError as exc:
        raise _bad(exc, 404) from None


def _remote(project: dict[str, Any]) -> RemoteConfig | None:  # Any: project JSON
    try:
        return RemoteConfig.from_settings((project.get("approval_gates") or {})
                                          .get("project_remote"))
    except RemoteError as exc:
        raise HTTPException(status_code=400, detail=f"project_remote: {exc}") from None


async def _decide(request: Request, project_id: str, run_id: str, accept: bool,
                  note: str, confirmed: list[str] | None = None) -> Any:  # Any: dict or JSONResponse
    data_root = await _project(request, project_id)
    remote = _remote(await request.app.state.project_store.get(project_id) or {})
    if await _run_is_live(request, run_id):
        raise HTTPException(status_code=409, detail=f"run {run_id} is still running")
    if accept:
        project = await request.app.state.project_store.get(project_id) or {}
        run = ProjectRepo(data_root, project_id).get(run_id)
        if (run is not None and run.state == "pending" and not cosigned(run.cosign)
                and needs_cosign(data_root, project_id, run_id,
                                 Seats.from_project(project).reviewer)):
            raise HTTPException(status_code=409, detail="co-sign the code first")
        from core.design import DesignDrafts
        waiting = [d["id"] for d in DesignDrafts(data_root, project_id).open()
                   if d.get("run_id") == run_id]
        if waiting:
            raise HTTPException(status_code=409, detail=(
                f"decide the tech lead's design draft first ({', '.join(waiting)})"))
    try:
        decision = decide_run(data_root, project_id, run_id, accept=accept, note=note,
                              remote=remote,
                              http=getattr(request.app.state, "github_http", None),
                              confirmed_checks=confirmed or [])
    except ProjectRepoConflict as exc:
        return JSONResponse(status_code=409, content={
            "detail": (f"main changed since run {run_id} started and the merge conflicts. "
                       "Start a run that continues it to reconcile, or reject it."),
            "files": exc.files})
    except ProjectRepoError as exc:
        raise _bad(exc, 404 if "never submitted" in str(exc) else 400) from None
    schedule_distill(request)
    return {"run": asdict(decision.run), "items": [asdict(i) for i in decision.items]}


@router.post("/{project_id}/runs-review/{run_id}/accept")
async def accept_run(request: Request, project_id: str, run_id: str,
                     body: Decision | None = None) -> Any:  # Any: dict or JSONResponse
    """Merge the run into the project's main; its finished backlog items close."""
    body = body or Decision()
    return await _decide(request, project_id, run_id, True, body.note, body.confirmed_checks)


@router.post("/{project_id}/runs-review/{run_id}/reject")
async def reject_run(request: Request, project_id: str, run_id: str,
                     body: Decision | None = None) -> Any:  # Any: dict or JSONResponse
    """Leave main as it is; the run's backlog items reopen."""
    return await _decide(request, project_id, run_id, False, (body or Decision()).note)


def _changes_note(comments: list[CosignComment]) -> str:
    """The reject note a "changes" co-sign becomes: it reaches the next brief as `review_note`."""
    lines = ["Changes requested by the reviewer:"]
    for c in comments:
        where = (f"{c.file}:{c.line}" if c.file and c.line else c.file or "")
        lines.append(f"- [{c.severity}] {where + ': ' if where else ''}{c.text.strip()}")
    return "\n".join(lines)


@router.post("/{project_id}/runs-review/{run_id}/cosign")
async def cosign_run(request: Request, project_id: str, run_id: str,
                     body: Cosign) -> Any:  # Any: dict or JSONResponse
    """The reviewer seat's decision on a waiting run.

    `cosign` stores the verdict (accepting is then allowed); `changes` stores it and rejects
    the run with a note built from the comments, so they reach the next brief.
    """
    data_root = await _project(request, project_id)
    repo = ProjectRepo(data_root, project_id)
    run = repo.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} was never submitted")
    if await _run_is_live(request, run_id):
        raise HTTPException(status_code=409, detail=f"run {run_id} is still running")
    if run.state != "pending":
        raise HTTPException(status_code=409, detail=f"run {run_id} is {run.state}, not waiting")
    cosign = {"verdict": body.verdict, "by": "human", "at": time.time(),
              "comments": [c.model_dump() for c in body.comments]}
    repo.record(run_id, cosign=cosign)
    if body.verdict == "cosign":
        return {"cosign": cosign, "run": asdict(repo.get(run_id) or run)}
    result = await _decide(request, project_id, run_id, False,
                           _changes_note(body.comments) if body.comments
                           else "Changes requested by the reviewer.")
    if isinstance(result, dict):
        result["cosign"] = cosign
    return result
