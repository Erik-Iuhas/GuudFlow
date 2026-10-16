"""Prepare the session with the tech lead before it starts (worker context plan H).

`GET  /api/projects/{id}/preflight`                      the open plan (or null)
`POST /api/projects/{id}/preflight/prepare`              a new plan for items and/or a goal
`POST /api/projects/{id}/preflight/messages`             talk to the tech lead; the plan is revised
`PUT  /api/projects/{id}/preflight/cards`                replace the cards (same checks as a run)
`POST /api/projects/{id}/preflight/assumptions/{aid}`    answer an assumption (becomes a decision)
`POST /api/projects/{id}/preflight/approve`              approve (refused while owner questions are open)
`POST /api/projects/{id}/preflight/discard`              drop it

Start a session from it with `POST /api/invoke/{id}` and `preflight: "<plan id>"`.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from api.auth import require_bearer
from core.preflight import Preflight, PreflightError

router = APIRouter(prefix="/api/projects", tags=["preflight"],
                   dependencies=[Depends(require_bearer)])


class PrepareBody(BaseModel):
    """Body of POST .../preflight/prepare."""

    items: list[str] = Field(default_factory=list, max_length=50)
    goal: str = Field(default="", max_length=4000)


class MessageBody(BaseModel):
    """Body of POST .../preflight/messages."""

    text: str = Field(min_length=1, max_length=4000)


class CardsBody(BaseModel):
    """Body of PUT .../preflight/cards: the plan's whole task list."""

    tasks: list[dict[str, Any]] = Field(max_length=60)  # Any: PlannedTask rows, validated in core


class AnswerBody(BaseModel):
    """Body of POST .../preflight/assumptions/{aid}."""

    answer: str = Field(min_length=1, max_length=2000)


async def _preflight(request: Request, project_id: str) -> Preflight:
    from pathlib import Path
    doc = await request.app.state.project_store.get(project_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return Preflight(Path(request.app.state.data_root), project_id, doc)


def _refused(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/{project_id}/preflight")
async def get_preflight(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """`{plan}`: the open prepared session, or null."""
    pf = await _preflight(request, project_id)
    return {"plan": await asyncio.to_thread(pf.current)}


async def _llm(request: Request, project_id: str) -> tuple[Any, str | None, str | None]:  # Any: LLMClient
    """The project's planning model (the one Jack uses)."""
    from api.routes.plan import _manager
    pm = await _manager(request, project_id, None)
    return pm.llm, pm.model, pm.effort


@router.post("/{project_id}/preflight/prepare")
async def prepare(request: Request, project_id: str, body: PrepareBody) -> dict[str, Any]:  # Any: JSON
    """Ask the tech lead to prepare the session for `items` (or the `goal`)."""
    if not body.items and not body.goal.strip():
        raise HTTPException(status_code=400, detail="pick backlog items or give a goal")
    pf = await _preflight(request, project_id)
    llm, model, effort = await _llm(request, project_id)
    try:
        plan = await pf.prepare(llm, body.items, body.goal, model=model, effort=effort)
    except PreflightError as exc:
        raise _refused(exc) from None
    except Exception as exc:  # noqa: BLE001 — an LLM failure is a 502, not a crash
        raise HTTPException(status_code=502,
                            detail=f"the tech lead could not answer: {exc}") from None
    return {"plan": plan}


@router.post("/{project_id}/preflight/messages")
async def message(request: Request, project_id: str, body: MessageBody) -> dict[str, Any]:  # Any: JSON
    """Talk to the tech lead; it revises the plan."""
    pf = await _preflight(request, project_id)
    llm, model, effort = await _llm(request, project_id)
    try:
        plan = await pf.message(llm, body.text, model=model, effort=effort)
    except PreflightError as exc:
        raise _refused(exc) from None
    except Exception as exc:  # noqa: BLE001 — an LLM failure is a 502, not a crash
        raise HTTPException(status_code=502,
                            detail=f"the tech lead could not answer: {exc}") from None
    return {"plan": plan}


@router.put("/{project_id}/preflight/cards")
async def edit_cards(request: Request, project_id: str, body: CardsBody) -> dict[str, Any]:  # Any: JSON
    """Replace the cards; refused (422) when they fail the planner's checks."""
    pf = await _preflight(request, project_id)
    try:
        plan = await asyncio.to_thread(pf.edit_cards, body.tasks)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except PreflightError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"plan": plan}


@router.post("/{project_id}/preflight/assumptions/{assumption_id}")
async def answer(request: Request, project_id: str, assumption_id: str,
                 body: AnswerBody) -> dict[str, Any]:  # Any: JSON
    """Answer an assumption; the answer is recorded as a spec decision."""
    pf = await _preflight(request, project_id)
    try:
        plan = await asyncio.to_thread(pf.answer, assumption_id, body.answer)
    except PreflightError as exc:
        raise _refused(exc) from None
    return {"plan": plan}


@router.post("/{project_id}/preflight/approve")
async def approve(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """Approve the plan (409 while an owner question is open or the plan has problems)."""
    pf = await _preflight(request, project_id)
    try:
        plan = await asyncio.to_thread(pf.approve)
    except PreflightError as exc:
        raise _refused(exc) from None
    return {"plan": plan}


@router.post("/{project_id}/preflight/discard")
async def discard(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """Drop the open plan."""
    pf = await _preflight(request, project_id)
    try:
        await asyncio.to_thread(pf.discard)
    except PreflightError as exc:
        raise _refused(exc) from None
    return {"plan": None}
