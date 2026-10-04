"""/api/runs/{run_id}/cards — humans work the task board of a run (plan 4.6, S34).

Reads work for active and finished runs (finished runs read ``tasks.jsonl``).
Mutations need the run to be active and a bearer token, like the approval route.
New thread entries and cards reach Studio through the workspace board signals
(``TASK_THREAD`` / ``TASK_ASSIGNED``) that the ``/ws/runs/{run_id}`` bridge
already forwards, so there is no separate channel.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth import require_bearer
from core.harness.card_ops import (
    CardOpError,
    card_view,
    human_add_card,
    human_comment,
    human_set_priority,
    thread_entry_view,
)
from core.run_loader import load_persisted_tasks
from core.workspace import Workspace

router = APIRouter(prefix="/api/runs", tags=["cards"])

_BOARD_STORIES = ("plan", "board")


class CardCreate(BaseModel):
    """Body of POST /cards."""

    owner: str
    title: str
    acceptance: list[str] = Field(default_factory=list)
    kind: str = "code"
    size: str = "S"
    priority: int = 0
    depends_on: list[str] = Field(default_factory=list)
    files_hint: list[str] = Field(default_factory=list)


class CardPatch(BaseModel):
    """Body of PATCH /cards/{task_id}."""

    priority: int


class CardComment(BaseModel):
    """Body of POST /cards/{task_id}/comments."""

    text: str


async def _active_workspace(request: Request, run_id: str) -> Workspace:
    workspace = await request.app.state.run_registry.get(run_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} is not active")
    return workspace


@router.get("/{run_id}/cards")
async def list_cards(request: Request, run_id: str) -> dict[str, Any]:
    """The run's board cards. Active runs read live state; finished runs read tasks.jsonl."""
    workspace = await request.app.state.run_registry.get(run_id)
    if workspace is not None:
        tasks = list(workspace.tasks.values())
    else:
        run_root = Path(request.app.state.data_root) / "runs" / run_id
        if not run_root.is_dir():
            raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
        tasks = list(load_persisted_tasks(run_root)[0].values())
    return {"cards": [card_view(t) for t in tasks if t.story_id in _BOARD_STORIES]}


@router.post("/{run_id}/cards", status_code=201, dependencies=[Depends(require_bearer)])
async def add_card(request: Request, run_id: str, body: CardCreate) -> dict[str, Any]:
    """Add a card to the live board. 400 on invalid input, 404 if the run isn't active."""
    workspace = await _active_workspace(request, run_id)
    try:
        card = human_add_card(
            workspace, owner=body.owner, title=body.title, acceptance=body.acceptance,
            kind=body.kind, size=body.size, priority=body.priority,
            depends_on=body.depends_on, files_hint=body.files_hint)
    except CardOpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return card_view(card)


@router.patch("/{run_id}/cards/{task_id}", dependencies=[Depends(require_bearer)])
async def patch_card(request: Request, run_id: str, task_id: str,
                     body: CardPatch) -> dict[str, Any]:
    """Change a card's priority. 400 for a done card, 404 for an unknown card or inactive run."""
    workspace = await _active_workspace(request, run_id)
    try:
        card = human_set_priority(workspace, task_id, body.priority)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"card {task_id!r} not found") from None
    except CardOpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return card_view(card)


@router.post("/{run_id}/cards/{task_id}/comments", status_code=201,
             dependencies=[Depends(require_bearer)])
async def comment_on_card(request: Request, run_id: str, task_id: str,
                          body: CardComment) -> dict[str, Any]:
    """Comment on a card; the entry shows up in the card's next brief."""
    workspace = await _active_workspace(request, run_id)
    try:
        card = human_comment(workspace, task_id, body.text)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"card {task_id!r} not found") from None
    except CardOpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return thread_entry_view(card.thread[-1])
