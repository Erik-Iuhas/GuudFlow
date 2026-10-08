"""Role contracts for Studio (role contracts plan §2): the library, and a project's team with
the contract each role works under."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import require_bearer
from core.contracts import ContractError, contract_id_for, load_for_project, load_library
from core.harness.capabilities import infer_role_type

router = APIRouter(prefix="/api", tags=["contracts"], dependencies=[Depends(require_bearer)])


@router.get("/contracts")
async def library() -> dict[str, Any]:  # Any: JSON
    """Every shipped contract."""
    try:
        lib = await asyncio.to_thread(load_library)
    except ContractError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None
    return {"contracts": [c.to_dict() for c in lib.values()]}


@router.get("/projects/{project_id}/contracts")
async def project_contracts(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """The contracts this project's team works under (library plus the project's overrides)."""
    doc = await request.app.state.project_store.get(project_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"no project {project_id}")
    data_root = Path(request.app.state.data_root)
    try:
        contracts = await asyncio.to_thread(load_for_project, data_root, project_id)
    except ContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    team = []
    for row in doc.get("agents") or []:
        role = str(row.get("role") or "")
        role_type = infer_role_type(role, is_reviewer=bool(row.get("is_reviewer")),
                                    declared=str(row.get("role_type") or ""))
        cid = contract_id_for(row, role_type)
        team.append({"role": role, "name": row.get("name"), "role_type": role_type,
                     "contract": cid if cid in contracts else None})
    return {"contracts": [c.to_dict() for c in contracts.values()], "team": team}


# ── project kinds (role contracts plan §3) ──────────────────────────────────
@router.get("/kinds")
async def kinds() -> dict[str, Any]:  # Any: JSON
    """Every shipped project kind, with the team it starts from."""
    from core.kinds import KindError, load_kinds, team_template
    try:
        lib = await asyncio.to_thread(load_kinds)
    except KindError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None
    return {"kinds": [{**k.to_dict(), "team_template": team_template(k)} for k in lib.values()]}


@router.get("/projects/{project_id}/phases")
async def phases(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """Where the project is in its kind's lifecycle, and the phase of each backlog item."""
    from worker.queue import JOB_QUEUED, JOB_RUNNING
    doc = await request.app.state.project_store.get(project_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"no project {project_id}")
    jobs = await request.app.state.queue.list(project_id=project_id, limit=500)
    running = any(j.status in (JOB_QUEUED, JOB_RUNNING) for j in jobs)
    return await asyncio.to_thread(phase_view, Path(request.app.state.data_root), project_id,
                                   doc, running)


def phase_view(data_root: Path, project_id: str, doc: dict[str, Any],  # Any: project doc
               running: bool) -> dict[str, Any]:  # Any: JSON
    """The phase strip and item phases for a project (blocking; run in a thread)."""
    from core.design import Design, DesignDrafts
    from core.kinds import ProjectState, item_phase, kind_of, project_phases
    from core.project_repo import ProjectRepo
    from core.start_modes import normalise_mode
    from core.work_db import WorkDB
    kind = kind_of(doc)
    folder = data_root / "projects" / project_id
    db = WorkDB(folder) if folder.is_dir() else None
    lead = kind.lead_contract
    has_lead = False
    for row in doc.get("agents") or []:
        role_type = infer_role_type(str(row.get("role") or ""),
                                    is_reviewer=bool(row.get("is_reviewer")),
                                    declared=str(row.get("role_type") or ""))
        has_lead = has_lead or contract_id_for(row, role_type) == lead
    runs = ProjectRepo(data_root, project_id).runs() if db is not None else []
    state = ProjectState(
        spec_version=int((db.read_spec_doc() if db else {}).get("version", 0)),
        design_empty=Design.load(data_root, project_id).empty if db else True,
        has_lead=has_lead, running=running,
        pending=any(r.state == "pending" for r in runs),
        accepted=sum(1 for r in runs if r.state == "accepted"),
        start_mode=normalise_mode(doc.get("start_mode")),
        open_design_drafts=len(DesignDrafts(data_root, project_id).open()) if db else 0)
    strip = project_phases(kind, state)
    items = db.read_backlog_doc().get("items", []) if db is not None else []
    return {**strip.to_dict(), "kind_title": kind.title,
            "items": {str(i.get("id")): item_phase(i, strip) for i in items
                      if isinstance(i, dict)}}
