"""/api/oneshot — the generic one-shot demo (harness plan section 10).

POST /api/oneshot {goal, web?, budget?, demo?, repo?, confirm?} builds the
default team's project (`core/oneshot.py`), stores it like any other project
(`oneshot-<8 hex>`) and enqueues the job through the same queue `/api/invoke`
uses. It returns 202 at once (hard constraint #12); poll `status_url`
(`/api/invoke/{job_id}/status`).

Approval, mirroring `/api/invoke`'s `confirm` gate:
  - `demo: true` counts as confirmed. The team and its capabilities are
    auto-approved and the job is enqueued.
  - `confirm: true` also enqueues (the caller has already seen the team).
  - Otherwise the project is stored but NOT enqueued. The 202 carries
    `needs_approval: true`, the cost estimate and `confirm_url`. After the team
    is approved in Studio (or by the caller), POST `{"confirm": true}` to
    `confirm_url` (`/api/invoke/{project_id}`) to start the run.
  - A `repo` (git URL) is never cloned inside the request. The project is
    stored, not enqueued, and `repo_import` says how to import it
    (`POST /api/projects/{id}/inputs/git`) before confirming.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from api.auth import require_bearer
from core.cost_estimate import estimate_run_cost
from core.oneshot import OneShotRequest, build_oneshot_project, project_to_dict
from worker.queue import Job


router = APIRouter(prefix="/api/oneshot", tags=["oneshot"])


class OneShotBody(BaseModel):
    """Request body for POST /api/oneshot (API boundary only)."""

    model_config = ConfigDict(extra="forbid")

    goal: str
    web: Literal["researcher", "off"] = "researcher"
    budget: float = 5.0
    demo: bool = False
    repo: str | None = None
    confirm: bool = False


@router.post("", dependencies=[Depends(require_bearer)])
async def start_oneshot(request: Request, body: dict[str, Any] = Body(...)) -> JSONResponse:
    """Create a one-shot project and (when approved) enqueue its run."""
    try:
        parsed = OneShotBody.model_validate(body)
        req = OneShotRequest(goal=parsed.goal, web=parsed.web, budget_usd=parsed.budget,
                             demo=parsed.demo, repo=(parsed.repo or "").strip() or None)
        project = build_oneshot_project(req)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors(include_url=False, include_context=False))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    store = request.app.state.project_store
    try:
        await store.create(project_to_dict(project))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    project_id = project.project_id
    approved = parsed.demo or parsed.confirm

    if req.repo is not None:
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={
            "accepted": False,
            "needs_approval": True,
            "project_id": project_id,
            "repo_import": {"method": "POST", "url": f"/api/projects/{project_id}/inputs/git",
                            "body": {"git_url": req.repo}},
            "confirm_url": f"/api/invoke/{project_id}",
            "message": "Import the repo, then POST {\"confirm\": true} to confirm_url to start the run.",
        })

    if not approved:
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={
            "accepted": False,
            "needs_approval": True,
            "project_id": project_id,
            "estimated_cost": estimate_run_cost(project).to_dict(),
            "confirm_url": f"/api/invoke/{project_id}",
            "message": ("The team needs approval first. Review it, then POST "
                        "{\"confirm\": true} to confirm_url to start the run."),
        })

    run_id = f"run_{uuid4().hex[:10]}"
    job_id = f"job_{uuid4().hex[:10]}"
    await request.app.state.queue.enqueue(Job(
        job_id=job_id, project_id=project_id, run_id=run_id,
        payload={"oneshot": True, "demo": parsed.demo, "confirm": True},
    ))
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={
        "accepted": True,
        "needs_approval": False,
        "job_id": job_id,
        "run_id": run_id,
        "project_id": project_id,
        "status_url": f"/api/invoke/{job_id}/status",
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    })
