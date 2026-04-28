"""Architect — final-stage design reviewer (Phase C.4).

The architect is a `Reviewer` (extends `agents/abstract.Reviewer`)
that runs LAST in a project: after every regular agent has produced
its work and after any per-step reviewers have approved, the
architect does a holistic pass on the merged result against the
project's original goal. This is the "office loops back with the
architect" pattern — distinct from `tech_lead` (which fires on
*failure* escalation) because the architect runs on the *success*
path to catch "looks fine but misses the point" failures that no
per-step reviewer would catch.

Use by setting `is_final_reviewer=True` on the AgentConfig. The
runner then:
  1. Auto-fills `depends_on` to every non-final-reviewer role at
     `_build_agents` time (so it runs last in the scheduler), unless
     the user explicitly set `depends_on` in the config.
  2. Fires the C.0.5 `heavy_tier_mid_run_dispatch` approval gate
     before the architect's tool-use loop runs (HEAVY tier by default;
     the gate exists because final review is real spend).

Architect rejection flows through the same REJECTED-counting path as
any reviewer: depends_on-targeted rejections increment the project's
rejection counter, which can in turn trigger `tech_lead` escalation.
That coupling is intentional — the architect's job is to catch what
slipped, not to be the rescue team.
"""

from __future__ import annotations

from typing import ClassVar

from agents.abstract import Reviewer


ARCHITECT_SYSTEM_PROMPT = """\
You are the project's senior architect. The team has finished their
work and the per-step reviewers have approved. Your job is the final
design check: does the merged result actually solve the project's
goal, or does it miss the point in some structural way that no
per-step reviewer would have caught?

Process:
  1. Read `inputs/README.md` to remind yourself of the original goal,
     constraints, and any non-goals or open questions.
  2. Use `git:log` and `git:diff` to see the full set of changes that
     landed on `main`.
  3. Use `fs:read_range` / `fs:grep` to spot-check key deliverables
     against the goal — not for line-level bugs (per-step reviewers
     handle those) but for design coherence: did we solve the right
     problem? Did we cut the right corner? Are obvious next-step
     concerns surfaced?
  4. Decide PASS or FAIL on the design as a whole.
  5. End with the [VERIFY] block per the protocol layer. On FAIL, the
     `detail:` is your one-line summary of the structural issue.

Be skeptical. The team is competent at execution; your value is in
spotting the kind of subtle "we built the wrong thing" miss that only
a step back can see.

TASK ASSIGNMENT: When you produce work that another agent should
implement, decompose it into one or more tasks via the `assign-task`
tool. Always populate acceptance criteria. Reference your own
artifacts (files, commits, parent stories) so the assignee has the
source of truth. Do not assign work outside the team's roster.
"""


class Architect(Reviewer):
    """Final-stage architect reviewer. Set `is_final_reviewer=True`
    on the AgentConfig to enable runtime auto-deps + gate hook.
    HEAVY tier by default."""

    role: str = "architect"

    # Architects use HEAVY tier — the design check is worth real
    # tokens, and they only fire once per run on the success path.
    default_model_tier: ClassVar[str] = "heavy"

    # M3.5 — architects are planners: they decompose goals into tasks
    # for downstream agents (DESIGN §38.12 D2).
    default_tools: ClassVar[tuple[str, ...]] = (
        *Reviewer.default_tools, "assign-task",
    )
    default_rbac_scopes: ClassVar[tuple[str, ...]] = ("assign-task",)

    archetype_description: ClassVar[str] = (
        "Final-stage design reviewer — runs after every other agent "
        "and reviews the merged result against the original goal as a "
        "whole. Distinct from per-step reviewers (which check one "
        "agent's output at a time)."
    )
    archetype_when_to_use: ClassVar[str] = (
        "Include in projects where 'we built the wrong thing' is a "
        "real risk — anything ambiguous, anything user-facing, "
        "anything where the per-step reviewers can't see the forest. "
        "Set is_final_reviewer=True on the AgentConfig so the runner "
        "auto-deps it on every other role and fires the heavy-tier "
        "approval gate before it runs."
    )

    def __init__(self, **kwargs):
        super().__init__(
            role=kwargs.pop("role", "architect"),
            description=(
                "Final-stage design reviewer. Reviews the merged result "
                "against the project goal as a whole."
            ),
            system_prompt=ARCHITECT_SYSTEM_PROMPT,
            **kwargs,
        )
