"""/api/invoke — external triggers.

DESIGN §10. POST /api/invoke/{project_id} returns 202 immediately with a
run_id; the worker (Stage 7 or in-process for Stage 6) drains the queue
and executes the run. Auth is required.

Also exposes `/api/invoke/mcp/manifest` so any MCP client can discover
the project as a callable tool.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from api.auth import require_bearer
from worker.queue import Job


router = APIRouter(prefix="/api/invoke", tags=["invoke"])


@router.post(
    "/{project_id}",
    dependencies=[Depends(require_bearer)],
)
async def invoke_project(
    request: Request,
    project_id: str,
    body: dict | None = Body(default=None),
):
    """Trigger a run.

    Phase C.0.5: when the project's `approval_gates.pre_run_cost_preview`
    is on (default), the first POST returns the cost estimate with HTTP
    200 and `requires_approval: true`. The client (studio) shows the
    user the cost; on confirm, the client POSTs again with
    `confirm: true` and the run actually enqueues with HTTP 202.
    """
    from fastapi.responses import JSONResponse
    from worker.job_runner import serialise_project
    from core.cost_estimate import estimate_run_cost

    project = await request.app.state.project_store.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")

    body = body or {}
    confirm = bool(body.get("confirm", False))
    _check_project_work(request, project_id, body)
    if not body.get("allow_parallel"):
        # One active run per project (plan §14.5): a second run would build on the
        # same main and collide at review time.
        from worker.queue import JOB_QUEUED, JOB_RUNNING
        jobs = await request.app.state.queue.list(project_id=project_id, limit=500)
        active = [j for j in jobs if j.status in (JOB_QUEUED, JOB_RUNNING)]
        if active:
            raise HTTPException(status_code=409, detail=(
                f"run {active[0].run_id} is still {active[0].status} for this project; wait for "
                "it, or pass allow_parallel for work that does not overlap"))

    project_cfg = serialise_project(project)
    gates = project_cfg.approval_gates or {}
    preview_on = bool(gates.get("pre_run_cost_preview", True))

    if preview_on and not confirm:
        estimate = estimate_run_cost(project_cfg)
        return JSONResponse(
            status_code=200,
            content={
                "accepted": False,
                "requires_approval": True,
                "gate": "pre_run_cost_preview",
                "estimated_cost": estimate.to_dict(),
                "project_id": project_id,
                "message": (
                    "Pre-run cost preview is on for this project. "
                    "POST again with `confirm: true` (and any other "
                    "payload fields) to actually start the run."
                ),
            },
        )

    run_id = f"run_{uuid4().hex[:10]}"
    job_id = f"job_{uuid4().hex[:10]}"
    job = Job(
        job_id=job_id,
        project_id=project_id,
        run_id=run_id,
        payload=body,
    )
    await request.app.state.queue.enqueue(job)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "accepted": True,
            "run_id": run_id,
            "job_id": job_id,
            "project_id": project_id,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _check_project_work(request: Request, project_id: str, body: dict) -> None:
    """Refuse a run whose backlog selection or continued run cannot be honoured (plan §13).

    `backlog_items`: a list of open item ids, or "top" (with optional `backlog_limit`).
    `continue_run`: a run of this project still waiting for review.
    """
    from pathlib import Path

    from core.backlog import Backlog, BacklogError
    from core.project_repo import ProjectRepo

    data_root = Path(request.app.state.data_root)
    wanted = body.get("backlog_items")
    if wanted not in (None, "top") and not (
            isinstance(wanted, list) and all(isinstance(i, str) for i in wanted)):
        raise HTTPException(status_code=400,
                            detail="backlog_items must be a list of item ids or \"top\"")
    if isinstance(wanted, list):
        backlog = Backlog(data_root, project_id)
        try:
            busy = [i for i in wanted if backlog.get(i).status != "open"]
        except BacklogError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if busy:
            raise HTTPException(status_code=400, detail=f"not open: {', '.join(busy)}")
    parent = body.get("continue_run")
    if parent is not None:
        run = ProjectRepo(data_root, project_id).get(str(parent))
        if run is None or run.state != "pending":
            raise HTTPException(status_code=400,
                                detail=f"continue_run: {parent!r} is not waiting for review")


@router.get("/mcp/manifest")
async def mcp_manifest(request: Request) -> dict:
    """All projects exposed as MCP tool definitions.

    A user can paste this manifest into an MCP-aware client (Claude
    Code, Cursor) and call any project as a tool that triggers a run.
    """
    projects = await request.app.state.project_store.list()
    tools = []
    for p in projects:
        tools.append({
            "name": f"guudflow:{p.get('project_id', 'unknown')}",
            "description": (
                f"Trigger an GuudFlow run of project '{p.get('project_id')}': "
                f"{p.get('goal', '')}"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "context": {
                        "type": "string",
                        "description": "free-form payload passed as the run's context",
                    },
                },
            },
        })
    return {"version": 1, "tools": tools}


@router.get("/{job_id}/status", dependencies=[Depends(require_bearer)])
async def job_status(request: Request, job_id: str) -> dict:
    job = await request.app.state.queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
    return {
        "job_id": job.job_id,
        "project_id": job.project_id,
        "run_id": job.run_id,
        "status": job.status,
        "enqueued_at": job.enqueued_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
    }
