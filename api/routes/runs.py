"""/api/runs — run history, artifacts, and live mid-run interaction."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from api.auth import require_bearer
from core.signals import Signal, SignalType


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


@router.post("/{run_id}/approval/{approval_id}", dependencies=[Depends(require_bearer)])
async def post_approval_decision(
    request: Request,
    run_id: str,
    approval_id: str,
    body: dict = Body(...),
) -> dict:
    """Resolve a pending approval gate (Phase C.0.5).

    The runner posts an APPROVAL_NEEDED signal and parks until the
    user calls this endpoint with `decision: "grant"|"deny"`. On
    success, the matching APPROVAL_GRANTED / APPROVAL_DENIED signal
    is posted to the active workspace and the runner's awaiting
    subscriber resumes.

    Returns 404 if the run isn't active or the approval id isn't pending.
    """
    raw_decision = (body.get("decision") or "").strip().lower()
    if raw_decision not in ("grant", "deny"):
        raise HTTPException(
            status_code=400,
            detail="decision must be 'grant' or 'deny'",
        )
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise HTTPException(status_code=400, detail="note must be a string")

    registry = request.app.state.run_registry
    workspace = await registry.get(run_id)
    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} is not active",
        )

    decision_word = "granted" if raw_decision == "grant" else "denied"
    req = workspace.record_approval(approval_id, decision_word, note=note)
    if req is None:
        raise HTTPException(
            status_code=404,
            detail=f"approval {approval_id!r} not pending on run {run_id!r}",
        )

    sig_type = (
        SignalType.APPROVAL_GRANTED if decision_word == "granted"
        else SignalType.APPROVAL_DENIED
    )
    signal = Signal(
        type=sig_type,
        summary=f"approval {decision_word}: {req.summary}",
        detail=f"approval_id={req.id}; kind={req.kind}",
        from_agent="user",
        iteration=workspace.iteration,
    )
    workspace.post_signal(signal)
    return {
        "ok": True,
        "approval_id": req.id,
        "decision": decision_word,
        "type": sig_type.value,
    }


@router.post("/{run_id}/comment", dependencies=[Depends(require_bearer)])
async def post_user_comment(
    request: Request,
    run_id: str,
    body: dict = Body(...),
) -> dict:
    """Post a USER_COMMENT (or USER_COMMENT_PRIORITY) to the live run's
    workspace. Agents check the user_board at step boundaries and absorb
    new comments — see DESIGN §6.

    Returns 410 if the run isn't active; the user_board is mid-run only.
    Comments on a finished run have no audience.
    """
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    raw_priority = (body.get("priority") or "normal").lower()
    if raw_priority not in ("normal", "high"):
        raise HTTPException(status_code=400, detail="priority must be 'normal' or 'high'")

    registry = request.app.state.run_registry
    workspace = await registry.get(run_id)
    if workspace is None:
        raise HTTPException(
            status_code=410,
            detail=f"run {run_id!r} is not active; comments only land mid-run",
        )

    sig_type = (
        SignalType.USER_COMMENT_PRIORITY if raw_priority == "high"
        else SignalType.USER_COMMENT
    )
    signal = Signal(
        type=sig_type,
        summary=text,
        from_user=True,
        priority=raw_priority,
        iteration=workspace.iteration,
    )
    workspace.post_signal(signal)
    return {
        "ok": True,
        "signal_id": str(signal.id),
        "type": sig_type.value,
        "iteration": workspace.iteration,
    }


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
