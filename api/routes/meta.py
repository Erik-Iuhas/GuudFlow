"""/api/meta — proposal generation.

DESIGN §7. The meta-agent reads the goal + inputs and proposes a team.
Stage 6 ships the API surface; the actual proposal logic lives in
`agents/builtin/meta.py` and is replaceable.

For Stage 6 MVP this returns a deterministic skeleton proposal
(developer + reviewer) that the user can edit. A real LLM-driven
proposal lands as the meta agent's run() is fleshed out.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from api.auth import require_bearer


router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.post("/propose", dependencies=[Depends(require_bearer)])
async def propose(request: Request, body: dict = Body(...)) -> dict:
    """Generate a team proposal for a goal.

    Request body:
      { "goal": "...", "project_id": "..." (optional, for naming) }

    Response:
      { "rationale": "...", "agents": [...], "rbac": {...} }
    """
    goal = body.get("goal")
    if not goal or not isinstance(goal, str):
        raise HTTPException(status_code=400, detail="goal is required")

    # Skeleton proposal — replace with real meta-agent invocation when ready.
    return {
        "rationale": (
            f"Proposing a developer + reviewer team for '{goal}'. "
            "This is a generic baseline — adjust roles, tools, and RBAC scopes "
            "before approving."
        ),
        "agents": [
            {
                "role": "developer",
                "description": "Writes the code.",
                "depends_on": [],
                "declared_local_tools": [
                    "fs:read", "fs:list", "fs:write", "fs:edit",
                    "code:exec", "git:diff", "git:log", "git:read",
                ],
                "declared_mcp_servers": [],
                "model_tier": "solid",
            },
            {
                "role": "reviewer",
                "description": "Reviews the developer's work.",
                "depends_on": ["developer"],
                "declared_local_tools": [
                    "fs:read", "fs:list", "git:diff", "git:log", "git:read", "git:status",
                ],
                "declared_mcp_servers": [],
                "model_tier": "solid",
                "agent_class_role_type": "code_reviewer",
                "is_reviewer": True,
            },
        ],
        "rbac": {
            "developer": {
                "allowed_scopes": ["fs:write", "fs:edit", "code:exec"],
            },
            "reviewer": {
                "allowed_scopes": [],
            },
        },
    }
