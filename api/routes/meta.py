"""/api/meta — proposal generation.

DESIGN §7. The meta-agent reads the goal + inputs + agent catalog +
persona roster and proposes a team. Phase D.2 wires the real
LLM-driven `MetaAgent` (built in D.1) when the request includes a
`project_id`. Without one, falls back to the deterministic skeleton —
the studio's Propose view can still produce a sensible default for
the very-first-project case where there's no project storage yet.

Auth model:
  - Required for the LLM-driven path (real LLM cost; needs the bearer
    token used elsewhere for credentialed routes).
  - Skeleton path is open (no LLM cost; useful as a "what does a
    proposal look like" preview).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from agents.builtin.meta import MetaAgent, TeamProposal
from api.auth import require_bearer
from core.llm import AnthropicClient, LLMClient
from core.runner import AgentConfig


_logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/meta", tags=["meta"])


def _skeleton_response(goal: str) -> dict[str, Any]:
    """The pre-D.1 deterministic skeleton, kept for back-compat when
    no project_id is supplied (the user is just exploring the
    proposal shape, not running anything yet)."""
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
                    "fs:read", "fs:list", "git:diff", "git:log",
                    "git:read", "git:status",
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
        "persona_hires": [],
        "warnings": [],
        "deltas": [],
    }


def _build_meta_llm(request: Request) -> LLMClient:
    """The route's LLM source. Tests inject via `app.state.meta_llm`;
    production falls through to the default Anthropic client (which
    reads ANTHROPIC_API_KEY from env)."""
    injected = getattr(request.app.state, "meta_llm", None)
    if injected is not None:
        return injected
    # Production default. Construction is cheap (no network until the
    # first .complete() call); keeping it lazy here means dev runs
    # without ANTHROPIC_API_KEY don't crash on import.
    return AnthropicClient()


@router.post("/propose")
async def propose(
    request: Request,
    body: dict = Body(...),
    _auth: None = Depends(require_bearer),
) -> dict:
    """Generate a team proposal for a goal.

    Request body:
      {
        "goal": "...",                  # required
        "project_id": "..."             # optional; when given, the
                                        # MetaAgent reads project
                                        # inputs + catalog + roster
                                        # via LLM. Without it, returns
                                        # the deterministic skeleton.
        "prior_run_id": "..."           # optional; triggers the
                                        # continuation prompt (D.3)
      }
    """
    goal = body.get("goal")
    if not goal or not isinstance(goal, str):
        raise HTTPException(status_code=400, detail="goal is required")

    project_id = body.get("project_id")
    if project_id is None:
        # Skeleton path — no LLM, no project lookup.
        return _skeleton_response(goal)

    project_store = request.app.state.project_store
    project = await project_store.get(project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"project {project_id!r} not found",
        )

    persona_store = getattr(request.app.state, "persona_store", None)
    roster: list[dict[str, Any]] = []
    if persona_store is not None:
        try:
            roster = await persona_store.list()
        except Exception:
            roster = []

    prior_run_id = body.get("prior_run_id")
    if prior_run_id is not None and not isinstance(prior_run_id, str):
        raise HTTPException(
            status_code=400, detail="prior_run_id must be a string",
        )

    # When prior_run_id is given, also extract the prior team's roles
    # from the stored project config so the meta-agent's continuation
    # prompt has them. The simpler flow: the project's CURRENT stored
    # `agents` is the prior team (D-prime's "Continue from this run"
    # button stamps `pending_continuation` without overwriting agents
    # until the user approves the new proposal).
    prior_team_roles: list[str] | None = None
    if prior_run_id is not None:
        prior_team_roles = [
            str(a.get("role"))
            for a in (project.get("agents") or [])
            if a.get("role")
        ]

    project_dir = request.app.state.data_root / "projects" / str(project_id)

    meta = MetaAgent()
    meta._llm = _build_meta_llm(request)
    try:
        proposal: TeamProposal = await meta.propose(
            project_dir=project_dir,
            project_id=str(project_id),
            goal=goal,
            roster=roster,
            prior_run_id=prior_run_id,
            prior_team_roles=prior_team_roles,
        )
    except Exception as e:
        _logger.exception("meta.propose crashed")
        # Hard floor: even on an unexpected exception we want a usable
        # response. The MetaAgent itself catches LLM errors; if
        # something else goes wrong, return the skeleton + a warning.
        skeleton = _skeleton_response(goal)
        skeleton["warnings"] = [f"meta-agent crashed: {e}"]
        return skeleton

    return proposal.to_dict()
