"""Tournament launcher + project builder for training runs (Phase F.1.2).

`build_training_project` constructs an ephemeral project config for a
single (persona, scenario) pair. The caller persists it via the
ProjectStore and enqueues a Job; the worker then runs it through the
normal runner path. Episode tagging + rubric scoring happen
automatically because `mode == "training"`.

`TournamentRegistry` is an in-memory registry mapping a tournament id
to its participants. Tournaments are short-lived bookkeeping — the
authoritative result lives on each run's board (the `RUBRIC_SCORE`
signal). The registry just tracks which runs belong to which
tournament so the result endpoint can aggregate them.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from core.training.scenario import Scenario


# ── Project builder ───────────────────────────────────────────────────────


# Default tool grants per role-type. Keep lean — training scenarios are
# meant to be cheap and self-contained, not full-stack debugging.
_DEFAULT_TOOLS_BY_ROLE: dict[str, list[str]] = {
    "developer": ["fs:read", "fs:list", "fs:write", "git:diff", "git:log"],
    "reviewer": ["fs:read", "fs:list", "git:diff", "git:log"],
    "tester": ["fs:read", "fs:list", "fs:write"],
    "doc_writer": ["fs:read", "fs:list", "fs:write", "doc:read"],
}
_DEFAULT_SCOPES_BY_ROLE: dict[str, list[str]] = {
    "developer": ["fs:write"],
    "reviewer": [],
    "tester": ["fs:write"],
    "doc_writer": ["fs:write"],
}
_REVIEWER_ROLE_TYPES = frozenset({"reviewer", "code_reviewer"})


def build_training_project(
    *,
    scenario: Scenario,
    persona_id: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Build a stored-format project dict for a (persona, scenario) pair.

    The first non-reviewer expected role gets the persona attached;
    other roles run prompt-defined with no persona. Approval gates are
    disabled (training runs are cheap and we don't want to block on a
    cost preview for every tournament participant).
    """
    if not scenario.expected_role_types:
        raise ValueError(
            f"scenario {scenario.scenario_id!r} has no expected_role_types"
        )

    pid = project_id or f"train_{scenario.scenario_id}_{persona_id}_{uuid4().hex[:6]}"

    agents: list[dict[str, Any]] = []
    rbac: dict[str, dict[str, Any]] = {}
    persona_attached = False

    for i, role_type in enumerate(scenario.expected_role_types):
        is_reviewer = role_type.lower() in _REVIEWER_ROLE_TYPES
        # First non-reviewer role gets the persona — the persona is
        # the one being trained; reviewers use the default behaviour.
        attach_persona = (not persona_attached) and (not is_reviewer)
        cfg: dict[str, Any] = {
            "role": role_type,
            "description": f"Training-mode {role_type}.",
            "system_prompt": _DEFAULT_SYSTEM_PROMPT.format(role=role_type),
            "depends_on": list(scenario.expected_role_types[:i]) if i > 0 else [],
            "declared_local_tools": list(_DEFAULT_TOOLS_BY_ROLE.get(
                role_type, ["fs:read", "fs:list"],
            )),
            "model_tier": "fast",  # training runs default to FAST tier for cost
            "is_reviewer": is_reviewer,
            "max_tool_iterations": 5,
        }
        if attach_persona:
            cfg["persona_id"] = persona_id
            persona_attached = True
        if is_reviewer:
            cfg["agent_class_role_type"] = "code_reviewer"

        agents.append(cfg)
        rbac[role_type] = {
            "allowed_scopes": list(_DEFAULT_SCOPES_BY_ROLE.get(role_type, [])),
        }

    return {
        "project_id": pid,
        "goal": scenario.goal,
        "agents": agents,
        "rbac": rbac,
        "max_iterations": 3,
        "mode": "training",
        "training_scenario_id": scenario.scenario_id,
        # Disable approval gates — training runs are cheap and unattended.
        "approval_gates": {
            "pre_run_cost_preview": False,
            "heavy_tier_mid_run_dispatch": False,
            "iteration_breach_at": 0,
            "per_iteration": False,
            "token_budget_usd": None,
        },
    }


_DEFAULT_SYSTEM_PROMPT = (
    "You are a {role} working on a small training scenario. Read the "
    "goal, do the work directly, and keep the implementation focused. "
    "This is a self-contained exercise — there's no broader system to "
    "consider. Write clean, minimal code."
)


# ── Tournament registry ───────────────────────────────────────────────────


@dataclass
class TournamentParticipant:
    persona_id: str
    project_id: str
    run_id: str
    job_id: str


@dataclass
class TournamentEntry:
    tournament_id: str
    scenario_id: str
    participants: list[TournamentParticipant] = field(default_factory=list)


class TournamentRegistry:
    """In-memory registry of active tournaments. The studio polls
    `/api/training/tournaments/{id}` to learn the leaderboard once
    every participant's RUBRIC_SCORE lands.

    Tournaments are short-lived bookkeeping; the authoritative state
    lives on each run's board. Restarting the API resets the registry
    — tournament results are still computable from the run files on
    disk if a follow-up phase needs durable persistence."""

    def __init__(self):
        self._by_id: dict[str, TournamentEntry] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        scenario_id: str,
        participants: list[TournamentParticipant],
    ) -> str:
        async with self._lock:
            tournament_id = f"tour_{uuid4().hex[:10]}"
            self._by_id[tournament_id] = TournamentEntry(
                tournament_id=tournament_id,
                scenario_id=scenario_id,
                participants=list(participants),
            )
            return tournament_id

    async def get(self, tournament_id: str) -> TournamentEntry | None:
        async with self._lock:
            return self._by_id.get(tournament_id)

    async def list(self) -> list[TournamentEntry]:
        async with self._lock:
            return list(self._by_id.values())
