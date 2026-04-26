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
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_bearer)],
)
async def invoke_project(
    request: Request,
    project_id: str,
    body: dict | None = Body(default=None),
) -> dict:
    project = await request.app.state.project_store.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")

    run_id = f"run_{uuid4().hex[:10]}"
    job_id = f"job_{uuid4().hex[:10]}"
    job = Job(
        job_id=job_id,
        project_id=project_id,
        run_id=run_id,
        payload=body or {},
    )
    await request.app.state.queue.enqueue(job)

    return {
        "accepted": True,
        "run_id": run_id,
        "job_id": job_id,
        "project_id": project_id,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    }


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
            "name": f"agentflow:{p.get('project_id', 'unknown')}",
            "description": (
                f"Trigger an AgentFlow run of project '{p.get('project_id')}': "
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
