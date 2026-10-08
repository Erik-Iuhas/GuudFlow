"""The design for Studio (role contracts plan §4.2): read it, decide the tech lead's drafts when
a human holds the Lead seat, and edit it directly as that human."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from api.auth import require_bearer
from core.design import Design, DesignDrafts, DesignError, DesignTurn, design_view, render_markdown
from core.project_runs import write_record
from core.spec import Spec
from core.work_db import WorkDB

router = APIRouter(prefix="/api/projects", tags=["design"], dependencies=[Depends(require_bearer)])
APPROVER = "human"


class DecideBody(BaseModel):
    """Body of POST .../design/drafts/{id}/approve: reasons left out of the approval."""

    reject_groups: list[str] = Field(default_factory=list, max_length=40)


class EditBody(BaseModel):
    """Body of POST .../design/edit: a design turn written by a person."""

    turn: dict[str, Any]  # Any: a DesignTurn, validated below


async def _root(request: Request, project_id: str) -> Path:
    if await request.app.state.project_store.get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"no project {project_id}")
    return Path(request.app.state.data_root)


def _requirements(data_root: Path, project_id: str) -> list[str]:
    spec = Spec(WorkDB(data_root / "projects" / project_id).read_spec_doc())
    return [r.id for r in spec.requirements if r.status == "active"]


@router.get("/{project_id}/design")
async def get_design(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """The design, its drafts (open ones first) and DESIGN.md as rendered."""
    data_root = await _root(request, project_id)
    design = await asyncio.to_thread(Design.load, data_root, project_id)
    drafts = await asyncio.to_thread(DesignDrafts(data_root, project_id).all)
    return {"design": design_view(design),
            "drafts": sorted(drafts, key=lambda d: d.get("status") != "open"),
            "markdown": render_markdown(design) if not design.empty else ""}


@router.post("/{project_id}/design/drafts/{draft_id}/approve")
async def approve(request: Request, project_id: str, draft_id: str,
                  body: DecideBody | None = None) -> dict[str, Any]:  # Any: JSON
    """The Lead approves a design draft (optionally without some of its reasons)."""
    data_root = await _root(request, project_id)
    try:
        out = await asyncio.to_thread(
            DesignDrafts(data_root, project_id).approve, draft_id, APPROVER,
            (body or DecideBody()).reject_groups, _requirements(data_root, project_id))
    except DesignError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    await asyncio.to_thread(write_record, data_root, project_id,
                            f"after design {draft_id} was approved")
    return asdict(out)


@router.post("/{project_id}/design/drafts/{draft_id}/discard")
async def discard(request: Request, project_id: str, draft_id: str) -> dict[str, Any]:  # Any: JSON
    """The Lead turns a design draft down."""
    data_root = await _root(request, project_id)
    try:
        out = await asyncio.to_thread(DesignDrafts(data_root, project_id).discard, draft_id,
                                      APPROVER)
    except DesignError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return asdict(out)


@router.post("/{project_id}/design/edit")
async def edit(request: Request, project_id: str, body: EditBody) -> dict[str, Any]:  # Any: JSON
    """A person's direct design change: applied at once and kept as an approved draft."""
    data_root = await _root(request, project_id)
    try:
        turn = DesignTurn.model_validate(body.turn)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        out = await asyncio.to_thread(
            DesignDrafts(data_root, project_id).propose, turn, by=APPROVER, auto_apply=True,
            requirement_ids=_requirements(data_root, project_id))
    except DesignError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    await asyncio.to_thread(write_record, data_root, project_id,
                            f"after a design edit ({out.draft_id})")
    return asdict(out)


# ── the knowledge base (role contracts plan §4.3) ───────────────────────────
@router.get("/{project_id}/knowledge")
async def get_knowledge(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """The generated facts and every note (approved, proposed, stale, dropped)."""
    from core.knowledge import load_facts, notes
    data_root = await _root(request, project_id)
    facts = await asyncio.to_thread(load_facts, data_root, project_id)
    rows = await asyncio.to_thread(notes, data_root, project_id,
                                   ("approved", "proposed", "stale", "dropped"))
    return {"facts": facts, "notes": rows}


@router.post("/{project_id}/knowledge/rebuild")
async def rebuild_facts(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """Regenerate the facts layer from the project repo's main."""
    from core.knowledge import build_facts
    data_root = await _root(request, project_id)
    doc = await request.app.state.project_store.get(project_id) or {}
    command = str((doc.get("approval_gates") or {}).get("check_command") or "")
    facts = await asyncio.to_thread(build_facts, data_root, project_id, check_command=command)
    if facts is None:
        raise HTTPException(status_code=409, detail="the project has no code on main yet")
    return {"facts": facts}


@router.post("/{project_id}/knowledge/{note_id}/approve")
async def approve_note(request: Request, project_id: str, note_id: str) -> dict[str, Any]:  # Any: JSON
    """A person approves a proposed or stale note."""
    return await _decide_note(request, project_id, note_id, True)


@router.post("/{project_id}/knowledge/{note_id}/drop")
async def drop_note(request: Request, project_id: str, note_id: str) -> dict[str, Any]:  # Any: JSON
    """A person drops a note."""
    return await _decide_note(request, project_id, note_id, False)


async def _decide_note(request: Request, project_id: str, note_id: str,
                       approve: bool) -> dict[str, Any]:  # Any: JSON
    from core.knowledge import decide_note
    data_root = await _root(request, project_id)
    try:
        return await asyncio.to_thread(decide_note, data_root, project_id, note_id,
                                       approve=approve, by=APPROVER)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no note {note_id}") from None
