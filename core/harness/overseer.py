"""The overseer's structured result, intervention menu, validators and caps.

Schemas and pure validation only: the scheduler applies interventions elsewhere.
See docs/OPENCODE_HARNESS_PLAN.md sections 11.1 to 11.3.
"""
import json
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from core.harness.results import result_json_schema, validate_result
from core.signals import Signal, SignalType


class _Intervention(BaseModel):
    """Shared base: every intervention carries a rationale and forbids extras."""

    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=15)


class Rebrief(_Intervention):
    """Prepend an addendum to the next spawn of ``role``."""

    type: Literal["rebrief"]
    role: str = Field(min_length=1)
    addendum: str = Field(min_length=20)
    task_id: str | None = None


class Decide(_Intervention):
    """Record a decision note that unblocks roles waiting on it."""

    type: Literal["decide"]
    question: str = Field(min_length=10)
    decision: str = Field(min_length=10)
    task_id: str | None = None


class Reassign(_Intervention):
    """Move a task between two existing roles of the same type."""

    type: Literal["reassign"]
    task_id: str = Field(min_length=1)
    from_role: str = Field(min_length=1)
    to_role: str = Field(min_length=1)


class SplitPart(BaseModel):
    """One sequential part of a split task."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=5)
    acceptance: list[str] = Field(min_length=1)


class Split(_Intervention):
    """Split a task into 2 to 4 sequential parts within the same role."""

    type: Literal["split"]
    task_id: str = Field(min_length=1)
    parts: list[SplitPart] = Field(min_length=2, max_length=4)


class ExtendReview(_Intervention):
    """Grant one extra review round on an author to reviewer edge."""

    type: Literal["extend_review"]
    author_role: str = Field(min_length=1)
    reviewer_role: str = Field(min_length=1)


class RestartRole(_Intervention):
    """Reset a role's clone to a known-good ref and re-arm it."""

    type: Literal["restart_role"]
    role: str = Field(min_length=1)
    from_ref: str = Field(default="main", min_length=1)


class Pause(_Intervention):
    """Raise HUMAN_NEEDED with a plain-language question."""

    type: Literal["pause"]
    question: str = Field(min_length=15)


class ProposeTeamChange(_Intervention):
    """Propose a team change; always goes through the human approval gate."""

    type: Literal["propose_team_change"]
    # Any: the change is a free-form team diff whose shape is owned by the approval gate.
    change: dict[str, Any]
    summary: str = Field(min_length=15)


Intervention = Annotated[
    Union[
        Rebrief,
        Decide,
        Reassign,
        Split,
        ExtendReview,
        RestartRole,
        Pause,
        ProposeTeamChange,
    ],
    Field(discriminator="type"),
]


class OverseerResult(BaseModel):
    """The overseer's structured output: an assessment and a bounded list of actions."""

    model_config = ConfigDict(extra="forbid")

    assessment: str = Field(min_length=30)
    interventions: list[Intervention] = []


NEEDS_HUMAN: frozenset[str] = frozenset({"pause", "propose_team_change"})


@dataclass
class OverseerCaps:
    """Limits that bound how much the overseer may do (plan 11.2 and 11.3)."""

    max_per_wake: int = 3
    max_wakes_per_run: int = 5
    max_restarts_per_role: int = 1
    review_round_hard_cap: int = 5
    wake_cooldown_seconds: int = 120


@dataclass(frozen=True)
class TeamView:
    """Read-only snapshot of the team that interventions are checked against."""

    roles: dict[str, str]  # role -> role_type
    tasks: dict[str, str]  # task_id -> owner role
    review_edges: dict[str, str] = field(default_factory=dict)  # author -> reviewer
    review_rounds: dict[str, tuple[int, int]] = field(default_factory=dict)  # author -> (used, max)
    restarts_used: dict[str, int] = field(default_factory=dict)
    overseer_role: str = "overseer"


def _check_role(label: str, role: str, team: TeamView, problems: list[str]) -> bool:
    """Append a problem if ``role`` is unknown or is the overseer; return True if valid."""
    if role == team.overseer_role:
        problems.append(f"{label}: the overseer cannot act on itself ('{role}')")
        return False
    if role not in team.roles:
        problems.append(f"{label}: unknown role '{role}'")
        return False
    return True


def _check_task(label: str, task_id: str | None, team: TeamView, problems: list[str]) -> bool:
    """Append a problem if ``task_id`` is set but unknown; return True if valid."""
    if task_id is not None and task_id not in team.tasks:
        problems.append(f"{label}: unknown task '{task_id}'")
        return False
    return True


def _check_one(
    label: str, iv: _Intervention, team: TeamView, caps: OverseerCaps, problems: list[str]
) -> None:
    """Check a single intervention against the team and caps."""
    if isinstance(iv, Rebrief):
        _check_role(f"{label} rebrief.role", iv.role, team, problems)
        _check_task(f"{label} rebrief.task_id", iv.task_id, team, problems)
    elif isinstance(iv, Decide):
        _check_task(f"{label} decide.task_id", iv.task_id, team, problems)
    elif isinstance(iv, Reassign):
        task_ok = _check_task(f"{label} reassign.task_id", iv.task_id, team, problems)
        from_ok = _check_role(f"{label} reassign.from_role", iv.from_role, team, problems)
        to_ok = _check_role(f"{label} reassign.to_role", iv.to_role, team, problems)
        if task_ok and from_ok and team.tasks[iv.task_id] != iv.from_role:
            problems.append(
                f"{label} reassign: task '{iv.task_id}' is owned by "
                f"'{team.tasks[iv.task_id]}', not '{iv.from_role}'"
            )
        if from_ok and to_ok:
            if iv.from_role == iv.to_role:
                problems.append(f"{label} reassign: to_role must differ from from_role")
            elif team.roles[iv.from_role] != team.roles[iv.to_role]:
                problems.append(
                    f"{label} reassign: role types differ "
                    f"('{team.roles[iv.from_role]}' vs '{team.roles[iv.to_role]}')"
                )
    elif isinstance(iv, Split):
        _check_task(f"{label} split.task_id", iv.task_id, team, problems)
    elif isinstance(iv, ExtendReview):
        author_ok = _check_role(f"{label} extend_review.author_role", iv.author_role, team, problems)
        rev_ok = _check_role(f"{label} extend_review.reviewer_role", iv.reviewer_role, team, problems)
        if author_ok and rev_ok:
            if team.review_edges.get(iv.author_role) != iv.reviewer_role:
                problems.append(
                    f"{label} extend_review: no review edge "
                    f"'{iv.author_role}' -> '{iv.reviewer_role}'"
                )
            else:
                used = team.review_rounds.get(iv.author_role, (0, 0))[0]
                if used + 1 > caps.review_round_hard_cap:
                    problems.append(
                        f"{label} extend_review: would exceed the hard cap of "
                        f"{caps.review_round_hard_cap} review rounds"
                    )
    elif isinstance(iv, RestartRole):
        if _check_role(f"{label} restart_role.role", iv.role, team, problems):
            if team.restarts_used.get(iv.role, 0) >= caps.max_restarts_per_role:
                problems.append(
                    f"{label} restart_role: '{iv.role}' already used its "
                    f"{caps.max_restarts_per_role} restart(s)"
                )
    elif isinstance(iv, ProposeTeamChange):
        if team.overseer_role in json.dumps(iv.change, default=str):
            problems.append(
                f"{label} propose_team_change: must not mention the overseer role "
                f"'{team.overseer_role}'"
            )


def check_interventions(result: OverseerResult, team: TeamView, caps: OverseerCaps) -> list[str]:
    """Check an overseer result against the team and caps.

    Returns one problem line per violation, each naming the intervention index
    (``interventions[i]``); an empty list means the result is acceptable.
    """
    problems: list[str] = []
    ivs = result.interventions
    if len(ivs) > caps.max_per_wake:
        problems.append(
            f"interventions: {len(ivs)} given, at most {caps.max_per_wake} allowed per wake"
        )
    pauses = [i for i, iv in enumerate(ivs) if isinstance(iv, Pause)]
    if len(pauses) > 1:
        problems.append(
            "interventions: at most one pause allowed, found at "
            + ", ".join(f"interventions[{i}]" for i in pauses)
        )
    for i, iv in enumerate(ivs):
        _check_one(f"interventions[{i}]", iv, team, caps, problems)
    return problems


def validate_overseer(
    raw: Any,  # Any: raw structured output straight from opencode
    team: TeamView,
    caps: OverseerCaps,
) -> tuple[OverseerResult | None, str | None]:
    """Validate raw output as an ``OverseerResult`` and check it against the team.

    Returns ``(result, None)`` or ``(None, error_text)`` with one line per problem.
    """
    result, error = validate_result(OverseerResult, raw)
    if result is None:
        return None, error
    problems = check_interventions(result, team, caps)
    if problems:
        return None, "\n".join(problems)
    return result, None


def _intervention_headline(iv: _Intervention) -> str:
    """Return the text a signal summary shows after the intervention type."""
    if isinstance(iv, Rebrief):
        return f"{iv.role}: {iv.addendum[:60]}"
    if isinstance(iv, Decide):
        return iv.decision[:60]
    if isinstance(iv, Reassign):
        return f"{iv.task_id} {iv.from_role} -> {iv.to_role}"
    if isinstance(iv, Split):
        return f"{iv.task_id} into {len(iv.parts)} parts"
    if isinstance(iv, ExtendReview):
        return f"{iv.author_role} -> {iv.reviewer_role}"
    if isinstance(iv, RestartRole):
        return f"{iv.role} from {iv.from_ref}"
    if isinstance(iv, Pause):
        return iv.question[:60]
    if isinstance(iv, ProposeTeamChange):
        return iv.summary[:60]
    return iv.rationale[:60]


def intervention_signal(iv: Intervention, iteration: int = 0) -> Signal:
    """Build the ``OVERSEER_INTERVENTION`` board signal for an intervention.

    The summary reads like ``rebrief builder: <first 60 chars>``; the detail is
    JSON holding the full intervention plus ``needs_human``.
    """
    detail = json.dumps({**iv.model_dump(), "needs_human": iv.type in NEEDS_HUMAN})
    return Signal(
        type=SignalType.OVERSEER_INTERVENTION,
        summary=f"{iv.type} {_intervention_headline(iv)}",
        from_agent="overseer",
        detail=detail,
        iteration=iteration,
    )


def _drop_discriminator(node: Any) -> Any:  # Any: recursive walk over a JSON schema
    """Remove ``discriminator`` keys, whose mapping holds refs dangling after inlining."""
    if isinstance(node, list):
        return [_drop_discriminator(item) for item in node]
    if isinstance(node, dict):
        return {k: _drop_discriminator(v) for k, v in node.items() if k != "discriminator"}
    return node


def overseer_json_schema() -> dict[str, Any]:  # Any: JSON schema is an open mapping
    """Return the inlined JSON Schema for ``OverseerResult``.

    The union stays a ``oneOf`` of the eight interventions, each pinned by a
    ``type`` const; the pydantic ``discriminator`` mapping is dropped because it
    points at ``$defs`` that inlining removes.
    """
    schema: dict[str, Any] = _drop_discriminator(result_json_schema(OverseerResult))
    return schema
