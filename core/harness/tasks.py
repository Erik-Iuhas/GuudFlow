"""Typed schemas for the task board: planner output, per-task checks, card threads.

See docs/OPENCODE_HARNESS_PLAN.md section 4.6. ``AcceptanceCheck`` and
``ItemVerdict`` live in :mod:`core.harness.results` (which this module imports,
never the reverse) and are re-exported here.
"""
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.harness.results import (
    AcceptanceCheck,
    AgentResult,
    ItemVerdict,
    validate_result,
)

__all__ = [
    "AcceptanceCheck",
    "ItemVerdict",
    "PlannedTask",
    "PlanResult",
    "ThreadKind",
    "ThreadEntry",
    "check_plan",
    "validate_plan",
    "check_task_result",
    "render_thread",
]

# Minimum length for a prefix match in check_task_result, so a one-character
# item cannot "match" every acceptance line.
_MIN_PREFIX_LEN = 8


class PlannedTask(BaseModel):
    """One card the planner proposes. The runner creates cards from these."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^T[0-9]+$")  # "T1", "T12"
    title: str = Field(min_length=5)
    owner_role: str = Field(min_length=1)
    acceptance: list[str] = Field(min_length=1)  # checklist, each item testable
    depends_on: list[str] = []
    files_hint: list[str] = []
    size: Literal["S", "M", "L"]
    kind: Literal["code", "test", "docs", "research", "visual"]
    visual_refs: list[str] = []
    # The project backlog item this task works on (plan §13); omit for extra work.
    backlog_id: str | None = Field(default=None, pattern=r"^B[0-9]+$")

    @field_validator("acceptance")
    @classmethod
    def _items_non_empty(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("acceptance items must be non-empty")
        return value


class PlanResult(BaseModel):
    """The planner's structured result: the task list for the run."""

    model_config = ConfigDict(extra="forbid")

    tasks: list[PlannedTask] = Field(min_length=1)
    notes: str = ""


def _find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """Return one dependency cycle as ``[a, b, ..., a]``, or None. Ignores self-loops."""
    white, grey, black = 0, 1, 2
    color = {node: white for node in graph}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = grey
        stack.append(node)
        for dep in graph[node]:
            if dep == node or dep not in graph:
                continue
            if color[dep] == grey:
                return stack[stack.index(dep):] + [dep]
            if color[dep] == white:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        color[node] = black
        return None

    for start in graph:
        if color[start] == white:
            found = visit(start)
            if found:
                return found
    return None


def check_plan(plan: PlanResult, team_roles: Collection[str],
               backlog_ids: Collection[str] = ()) -> list[str]:
    """Check a schema-valid plan against the team; return one problem per line (empty = ok).

    With `backlog_ids` (the run's backlog items, plan §13) every item must be
    covered by at least one task, and tasks may only name those items.
    """
    problems: list[str] = []
    wanted = set(backlog_ids)
    for task in plan.tasks:
        if task.backlog_id is not None and task.backlog_id not in wanted:
            problems.append(f"{task.id}: backlog_id '{task.backlog_id}' is not one of this "
                            f"run's items ({', '.join(sorted(wanted)) or 'none'})")
    covered = {t.backlog_id for t in plan.tasks}
    for item in sorted(wanted - covered):
        problems.append(f"backlog item {item} has no task: add at least one task with "
                        f"backlog_id \"{item}\"")
    roles = set(team_roles)
    seen: set[str] = set()
    for task in plan.tasks:
        if task.id in seen:
            problems.append(f"{task.id}: duplicate task id")
        seen.add(task.id)
    for task in plan.tasks:
        if task.owner_role not in roles:
            problems.append(
                f"{task.id}: unknown owner_role '{task.owner_role}' "
                f"(team roles: {', '.join(sorted(roles)) or 'none'})"
            )
        if task.size == "L":
            problems.append(f"{task.id}: size is 'L'; split it into S/M tasks")
        for dep in task.depends_on:
            if dep == task.id:
                problems.append(f"{task.id}: depends on itself")
            elif dep not in seen:
                problems.append(f"{task.id}: depends_on unknown task id '{dep}'")
    graph: dict[str, list[str]] = {}
    for task in plan.tasks:
        graph.setdefault(task.id, []).extend(task.depends_on)
    cycle = _find_cycle(graph)
    if cycle:
        problems.append(f"dependency cycle: {' -> '.join(cycle)}")
    return problems


def validate_plan(raw: Any, team_roles: Collection[str]) -> tuple[PlanResult | None, str | None]:
    """Validate the planner's raw result and check it against the team.

    Returns ``(plan, None)`` or ``(None, error_text)`` with one problem per line.
    """
    plan, error = validate_result(PlanResult, raw)
    if plan is None:
        return None, error
    problems = check_plan(plan, team_roles)
    if problems:
        return None, "\n".join(problems)
    return plan, None


def _item_matches(item: str, wanted: str) -> bool:
    """Exact text, or a case-insensitive prefix match in either direction."""
    a, b = item.strip().casefold(), wanted.strip().casefold()
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= _MIN_PREFIX_LEN and longer.startswith(shorter)


def check_task_result(result: AgentResult, task_id: str, acceptance: Sequence[str]) -> list[str]:
    """Check a builder's result against the card it was given; one problem per line.

    ``task_id`` must match, and a ``done`` result must report every acceptance
    item as met (matched by exact text or case-insensitive prefix).
    """
    problems: list[str] = []
    if result.task_id != task_id:
        problems.append(f"task_id: expected '{task_id}', got '{result.task_id}'")
    if result.status == "done":
        for wanted in acceptance:
            match = next((c for c in result.acceptance if _item_matches(c.item, wanted)), None)
            if match is None:
                problems.append(f"acceptance: missing an entry for item '{wanted}'")
            elif not match.met:
                problems.append(f"acceptance: item '{wanted}' is reported unmet but status is 'done'")
    return problems


ThreadKind = Literal[
    "note", "blocked_on", "review_issue", "decision", "intervention", "human_comment", "artifact",
    "handoff",
]


@dataclass(frozen=True)
class ThreadEntry:
    """One typed message on a card's thread."""

    task_id: str
    kind: ThreadKind
    author: str
    text: str
    iteration: int = 0
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable payload (stored in ``Task.thread``)."""
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "author": self.author,
            "text": self.text,
            "iteration": self.iteration,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ThreadEntry":
        """Rebuild an entry from :meth:`to_dict` output; optional keys may be absent."""
        return cls(
            task_id=str(payload["task_id"]),
            kind=payload["kind"],
            author=str(payload["author"]),
            text=str(payload["text"]),
            iteration=int(payload.get("iteration") or 0),
            data=dict(payload.get("data") or {}),
        )


def render_thread(entries: Sequence[ThreadEntry], limit: int = 20) -> str:
    """Render the most recent ``limit`` entries as ``- [kind] author: text`` bullets."""
    recent = list(entries)[-limit:] if limit > 0 else []
    return "\n".join(f"- [{e.kind}] {e.author}: {e.text}" for e in recent)
