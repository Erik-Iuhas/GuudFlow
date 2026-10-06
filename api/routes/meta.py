"""/api/meta — proposal generation.

DESIGN §7. The meta-agent reads the goal + inputs + agent catalog +
persona roster and proposes a team. Always calls the LLM (post-Phase
D bug fix: the studio's Propose view doesn't have a project_id at
proposal time — the project hasn't been created yet — so gating the
LLM on project_id meant every greenfield Propose got the same static
skeleton).

When `project_id` is provided + exists, the meta-agent additionally
reads the project's `inputs/` (uploads, source repo, prior runs).
Without one, it proposes from goal + roster + archetype catalog
alone, which is the normal greenfield flow.

The deterministic skeleton is still the floor — it fires only when
the LLM call itself crashes (no key, network error, malformed
response). That keeps the route resilient.

Auth: bearer required (real LLM cost on every call).
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
                    "fs-read", "fs-list", "fs-write", "fs-edit",
                    "code-exec", "git-diff", "git-log", "git-read",
                ],
                "declared_mcp_servers": [],
                "model_tier": "solid",
            },
            {
                "role": "reviewer",
                "description": "Reviews the developer's work.",
                "depends_on": ["developer"],
                "declared_local_tools": [
                    "fs-read", "fs-list", "git-diff", "git-log",
                    "git-read", "git-status",
                ],
                "declared_mcp_servers": [],
                "model_tier": "solid",
                "agent_class_role_type": "code_reviewer",
                "is_reviewer": True,
            },
        ],
        "rbac": {
            "developer": {
                "allowed_scopes": ["fs-write", "fs-edit", "code-exec"],
            },
            "reviewer": {
                "allowed_scopes": [],
            },
        },
        "persona_hires": [],
        "warnings": [],
        "deltas": [],
    }


# Team-template hints per start mode (project flow plan §5).
START_MODE_HINTS: dict[str, str] = {
    "spec_first": (
        "start mode spec_first: project manager, one or two builders and a reviewer. "
        "Every card should implement named requirement checks."
    ),
    "prototype_first": (
        "start mode prototype_first: project manager, one builder and a reviewer. "
        "The first session builds the thinnest end-to-end slice; skip polish."
    ),
    "test_first": (
        "start mode test_first: include a tester role ahead of builders. The tester "
        "writes failing tests for each item's checks first; builders depend on the tester."
    ),
    "research_first": (
        "start mode research_first: project manager, a researcher with web access and a "
        "reviewer. Research produces a decision note with options, evidence and a "
        "recommendation; no product code yet."
    ),
    "maintain": (
        "start mode maintain: project manager, one builder and a reviewer. Existing code: "
        "the smallest safe change, and a regression test per fix."
    ),
}


_VALID_META_MODEL_TIERS = frozenset({"heavy", "solid", "fast"})


def _build_meta_llm(request: Request, meta_model: str | None = None) -> LLMClient:
    """The route's LLM source. Tests inject via `app.state.meta_llm`;
    production resolves through the ModelRegistry when meta_model tier is given,
    otherwise falls through to the default Anthropic client."""
    injected = getattr(request.app.state, "meta_llm", None)
    if injected is not None:
        return injected
    if meta_model is not None:
        registry = getattr(request.app.state, "model_registry", None)
        if registry is not None:
            try:
                model_info = registry.resolve(meta_model)
                return registry.get_client(model_info)
            except Exception:
                pass
    # Production default.
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
        "start_mode": "test_first"      # optional; spec_first | prototype_first |
                                        # test_first | research_first | maintain.
                                        # Adds a team-template hint.
      }
    """
    goal = body.get("goal")
    if not goal or not isinstance(goal, str):
        raise HTTPException(status_code=400, detail="goal is required")

    project_id = body.get("project_id")
    if project_id is not None and not isinstance(project_id, str):
        raise HTTPException(
            status_code=400, detail="project_id must be a string",
        )

    prior_run_id = body.get("prior_run_id")
    if prior_run_id is not None and not isinstance(prior_run_id, str):
        raise HTTPException(
            status_code=400, detail="prior_run_id must be a string",
        )

    meta_model: str | None = body.get("meta_model")
    if meta_model is not None:
        if not isinstance(meta_model, str) or meta_model not in _VALID_META_MODEL_TIERS:
            raise HTTPException(
                status_code=400,
                detail=f"meta_model must be one of: {sorted(_VALID_META_MODEL_TIERS)}",
            )

    start_mode = body.get("start_mode")
    if start_mode is not None and (
        not isinstance(start_mode, str) or start_mode not in START_MODE_HINTS
    ):
        raise HTTPException(
            status_code=400,
            detail=f"start_mode must be one of: {sorted(START_MODE_HINTS)}",
        )

    # Greenfield (no project_id) is the normal Propose-view path: the
    # user is asking the meta-agent to design a team for a goal before
    # the project even exists. We still call the LLM; we just don't
    # have project inputs to read. read_meta_inputs() handles a
    # missing inputs/ dir gracefully (returns an empty MetaInputs).
    project: dict[str, Any] | None = None
    if project_id is not None:
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

    # When prior_run_id is given, the project's CURRENT stored agents
    # config IS the prior team (D-prime's "Continue from this run"
    # button stamps pending_continuation without overwriting agents
    # until the user approves the new proposal). Continuation is only
    # meaningful when project_id is also provided.
    prior_team_roles: list[str] | None = None
    prior_team: list[dict[str, Any]] | None = None
    if prior_run_id is not None and project is not None:
        stored_agents = list(project.get("agents") or [])
        prior_team_roles = [
            str(a.get("role")) for a in stored_agents if a.get("role")
        ]
        prior_team = stored_agents

    # `project_dir` is used to locate `inputs/`. For greenfield
    # proposals (no project_id) we point at a path that doesn't exist
    # yet — read_meta_inputs returns empty inputs in that case.
    if project_id is not None:
        project_dir = request.app.state.data_root / "projects" / project_id
        meta_project_id = project_id
    else:
        project_dir = request.app.state.data_root / "projects" / "_greenfield"
        meta_project_id = "_greenfield"

    # Harness: the stored project decides. Greenfield proposals have no project
    # yet, so the request body may name it (default legacy).
    # opencode is the default harness (plan P6, O15); "legacy" only on request.
    raw_harness = (project or {}).get("harness") or body.get("harness")
    harness = "legacy" if raw_harness == "legacy" else "opencode"
    playbook_text = ""
    playbook_entries_used: list[str] = []
    if project_id is not None:
        try:
            from core.playbook import Playbook
            playbook = Playbook.load(request.app.state.data_root, project_id)
            playbook_text = playbook.render_for_meta()
            # render_for_meta shows active entries only; report the same ids so
            # Studio can show which lessons shaped the team (nothing is applied).
            playbook_entries_used = [
                e.id for e in playbook.entries() if e.state == "active"
            ] if playbook_text else []
        except Exception as e:  # a corrupt playbook must not break proposals
            _logger.warning("playbook for %s unreadable: %s", project_id, e)

    # The project's open backlog (plan §13): the team should fit the ongoing work.
    backlog_text = ""
    if project_id is not None:
        try:
            from core.backlog import Backlog
            backlog_text = Backlog(request.app.state.data_root, project_id).render_for_meta()
        except Exception as e:  # a corrupt backlog must not break proposals
            _logger.warning("backlog for %s unreadable: %s", project_id, e)

    meta = MetaAgent()
    meta._llm = _build_meta_llm(request, meta_model=meta_model)
    try:
        proposal: TeamProposal = await meta.propose(
            project_dir=project_dir,
            project_id=meta_project_id,
            goal=goal,
            roster=roster,
            prior_run_id=prior_run_id,
            prior_team_roles=prior_team_roles,
            prior_team=prior_team,
            harness=harness,
            playbook_text=playbook_text,
            backlog_text=backlog_text,
            start_mode_hint=START_MODE_HINTS.get(start_mode or "", ""),
            model_registry=getattr(request.app.state, "model_registry", None),
        )
    except Exception as e:
        _logger.exception("meta.propose crashed")
        # Hard floor: even on an unexpected exception we want a usable
        # response. The MetaAgent itself catches LLM errors; if
        # something else goes wrong, return the skeleton + a warning.
        skeleton = _skeleton_response(goal)
        skeleton["warnings"] = [f"meta-agent crashed: {e}"]
        return skeleton

    result = proposal.to_dict()
    # Ids of the active playbook entries rendered into the prompt. The proposal
    # carries no machine-readable playbook diff, so this is "what the model saw".
    result["playbook_entries_used"] = playbook_entries_used
    return result
