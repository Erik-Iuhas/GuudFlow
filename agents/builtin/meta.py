"""MetaAgent — proposes a project team from a goal + inputs.

DESIGN §7. Reads `inputs/` (uploads + source repo + attached prior
runs), the agent catalog, and the persona roster, then makes ONE LLM
call with a structured tool-use schema and parses the result into a
`TeamProposal`. Output is what the human reviews and approves before
the run starts.

Phase D.1 ships the actual `run()` method. Earlier stages had only
the system prompt + dataclass shells.

Helpers (input reading, prompt rendering, tool-use schema, parsing,
fallback skeleton) live in `core/meta_proposal.py` to keep this file
focused on the agent class itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.base import BaseAgent
from agents.catalog import list_all_archetypes
from core.llm import LLMClient, LLMMessage
from core.meta_proposal import (
    PROPOSE_TEAM_TOOL,
    PersonaHire,
    compute_deltas,
    fallback_skeleton,
    parse_proposal_args,
    read_meta_inputs,
    render_user_message,
)
from core.runner import AgentConfig, ProjectConfig
from core.tool_manifest import RoleRBAC


_logger = logging.getLogger(__name__)


META_SYSTEM_PROMPT = """\
You are a meta-agent. Your job: read the project goal and the
attached inputs, then propose a small team (1-5 specialists) to
accomplish the goal. Use the `propose_team` tool — do not respond in
free text.

Rules:
  - Pick archetypes from the catalog the user provides. Use the
    archetype's `name` exactly.
  - Every role name must be unique. Use descriptive names
    (`auth_dev`, `api_dev`) over numeric suffixes when you have more
    than one of the same archetype.
  - Use `depends_on` to wire the pipeline. Reviewers depend on the
    developers they review. The architect (if any) is set with
    `is_final_reviewer=true` and the runner auto-fills its deps —
    don't list them yourself.
  - Use `manages` to express direct reports — who this role can assign
    tasks to. Use `escalates_to` when you want a non-default escalation
    target (default is tech_lead). Leave both empty if the role has no
    hierarchy responsibilities.
  - For each role, recommend a persona: either `hire_existing` with
    a `persona_id` from the roster, or `create_new` with an optional
    `name_hint` like 'Mike' or 'Sarah' (the meta-agent's
    naming preference) so quips read like real people.
  - Justify each role's existence in the rationale. Skip roles that
    wouldn't add value.

Be conservative. Smaller teams ship faster. A 2-3 agent team is
usually right for greenfield projects; 4-5 for mixed front+back+ops.

Story seeds in `stories`: if the goal has 2+ natural phases, add one
story slug per phase (e.g. "backend-api", "frontend-ui"). Single-phase
goals need no stories. Stories let agents filter docs to their scope
so each agent reads only the tokens relevant to their work.

Archetype selection — authoring vs. coordination:
  - For roles whose responsibility is to AUTHOR an artifact (design
    docs, requirements, specs, READMEs), prefer `requirements_writer`
    (or `architect` for final design-coherence checks). The `tech_lead`
    archetype is for escalation and coordination; assigning it an
    "author X" responsibility produces a role that delegates instead
    of writing.
  - Use `tech_lead` only when the primary job is: breaking stuck
    rejection cycles, routing peer ASK/ANSWER, or triaging escalations.
"""


META_CONTINUATION_PROMPT = """\
You are a meta-agent producing a CONTINUATION proposal. The user is
running this project a second time, building on the prior run's
output. Your job is to evaluate whether the team should change for
the new ask.

Rules (in addition to the standard meta-agent rules):
  - The prior team and the new ask are in the user message. Read
    them carefully.
  - Default to keeping the prior team if the new ask is small or
    refinement-shaped. Persona memory carries forward when you
    re-hire (recommend hire_existing for the same persona_ids).
  - Add a role only if the new ask introduces a domain the prior
    team didn't cover (e.g. "now add a UI" → add a frontend_dev).
  - Drop a role only if its work is locked in and the new ask
    doesn't touch it.
  - Swap a model tier (down or up) when the new ask is much smaller
    or much harder than the prior one.
  - Replace a persona only when the prior persona's specialty is a
    bad fit for the new ask. Re-hire is the default.

Use the `propose_team` tool. Your rationale should open with a
one-sentence summary of what's CHANGING between the prior team and
the proposed team.

Story seeds in `stories`: extract one story per distinct theme from
the user feedback (e.g. "auth-flow", "dashboard-ui", "bug-fixes").
Each story needs a slug `name` and a one-sentence `goal`. Assign
`owner_role` when a story belongs to a specific role. 2-4 stories
maximum. Stories scope doc reads so agents only consume tokens relevant
to their current theme.
"""


# ── TeamProposal (Phase D.1 extends shape) ─────────────────────────────────


@dataclass
class TeamProposal:
    """What the meta-agent emits. The studio renders this for human
    approval; on approval it is converted into the `ProjectConfig` the
    runner uses.

    Phase D.1 adds `persona_hires` (per-role persona recommendations)
    and `warnings` (sanitisation notes from parsing). Phase D.3 adds
    `deltas` for continuation runs.
    """

    rationale: str
    roles: list[AgentConfig] = field(default_factory=list)
    rbac: dict[str, RoleRBAC] = field(default_factory=dict)
    persona_hires: list[PersonaHire] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    deltas: list[dict[str, Any]] = field(default_factory=list)
    # M2: project-overlay desk seeds. dict[persona_id → list[DeskFileSeed dicts]].
    desk_seeds: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # M1 §7b: story seeds proposed for this run.
    stories: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API response. AgentConfig dataclass →
        dict (depends_on, declared_local_tools, etc. round-trip cleanly
        because they're plain types)."""
        from dataclasses import asdict
        return {
            "rationale": self.rationale,
            "agents": [asdict(a) for a in self.roles],
            "rbac": {
                r: {"allowed_scopes": list(v.allowed_scopes)}
                for r, v in self.rbac.items()
            },
            "persona_hires": [
                {
                    "role": h.role,
                    "action": h.action,
                    "persona_id": h.persona_id,
                    "display_name": h.display_name,
                    "rationale": h.rationale,
                }
                for h in self.persona_hires
            ],
            "warnings": list(self.warnings),
            "deltas": list(self.deltas),
            "desk_seeds": dict(self.desk_seeds),
            "stories": list(self.stories),
        }


# ── MetaAgent ──────────────────────────────────────────────────────────────


class MetaAgent(BaseAgent):
    """Pre-pipeline planner. Runs OUTSIDE the scheduler — invoked
    directly by the API route layer at proposal time."""

    role: str = "meta"

    def __init__(self, **kwargs):
        super().__init__(
            role=kwargs.pop("role", "meta"),
            description="Proposes a project team from goal + inputs.",
            system_prompt=META_SYSTEM_PROMPT,
            **kwargs,
        )

    async def propose(
        self,
        *,
        project_dir: Path,
        project_id: str,
        goal: str,
        roster: list[dict[str, Any]] | None = None,
        prior_run_id: str | None = None,
        prior_team_roles: list[str] | None = None,
        prior_team: list[dict[str, Any]] | None = None,
    ) -> TeamProposal:
        """Generate a team proposal for `goal` using inputs in
        `project_dir/inputs/`.

        The route layer is responsible for:
          - Setting `self._llm` (and optionally `self._model`) before
            calling. Tests inject a `MockLLMClient`; production
            injects the real `AnthropicClient`.
          - Passing the persona roster (from `PersonaStore.list()`).
          - Passing `prior_run_id` + `prior_team_roles` for
            continuation runs.

        On any LLM error or malformed tool call, returns the
        deterministic skeleton so `/api/meta/propose` never breaks.
        """
        if self._llm is None:
            return self._wrap_fallback(goal, "no LLM configured")

        roster = roster or []
        archetypes = list_all_archetypes()

        # 1. Read project inputs.
        inputs = read_meta_inputs(project_dir, project_id, goal)

        # 2. Continuation context, if applicable.
        if prior_run_id is not None:
            from core.meta_proposal import attach_continuation_context
            inputs = attach_continuation_context(
                inputs, project_dir, prior_run_id, prior_team_roles,
            )

        # 3. Render the user message + pick the system prompt.
        user_message = render_user_message(inputs, archetypes, roster)
        system = (
            META_CONTINUATION_PROMPT
            if inputs.continuation is not None
            else META_SYSTEM_PROMPT
        )

        # 4. LLM call with structured tool-use.
        try:
            response = await self._llm.complete(
                system=system,
                messages=[LLMMessage(role="user", content=user_message)],
                tools=[PROPOSE_TEAM_TOOL],
                max_tokens=4096,
                model=self._model or "claude-sonnet-4-6",
            )
        except Exception as e:
            _logger.warning("meta-agent LLM call failed: %s", e)
            return self._wrap_fallback(goal, f"LLM error: {e}")

        # 5. Parse the tool call. The LLM should emit exactly one
        # `propose_team` tool call; defensive on shape.
        tool_calls = list(response.tool_calls)
        if not tool_calls:
            return self._wrap_fallback(
                goal, "LLM returned text instead of a propose_team tool call",
            )
        first = tool_calls[0]
        if first.name != "propose_team":
            return self._wrap_fallback(
                goal, f"LLM called unexpected tool {first.name!r}",
            )

        result = parse_proposal_args(first.arguments, archetypes, roster)
        if not result.agents:
            return self._wrap_fallback(
                goal, "parsed proposal had no agents",
            )

        # Phase D.3: compute deltas vs the prior team for continuation
        # runs. The studio renders these as a side-by-side diff so the
        # user approves *what's changing* rather than the whole team.
        deltas: list[dict[str, Any]] = []
        if inputs.continuation is not None and prior_team:
            deltas = compute_deltas(prior_team, result.agents)

        return TeamProposal(
            rationale=result.rationale,
            roles=result.agents,
            rbac=result.rbac,
            persona_hires=result.persona_hires,
            warnings=result.warnings,
            deltas=deltas,
            desk_seeds=result.desk_seeds,
            stories=result.stories,
        )

    @staticmethod
    def _wrap_fallback(goal: str, reason: str) -> TeamProposal:
        result = fallback_skeleton(goal, reason)
        return TeamProposal(
            rationale=result.rationale,
            roles=result.agents,
            rbac=result.rbac,
            persona_hires=result.persona_hires,
            warnings=result.warnings,
        )
