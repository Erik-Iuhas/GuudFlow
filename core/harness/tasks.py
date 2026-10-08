"""Typed schemas for the task board: planner output, per-task checks, card threads.

See docs/OPENCODE_HARNESS_PLAN.md section 4.6. ``AcceptanceCheck`` and
``ItemVerdict`` live in :mod:`core.harness.results` (which this module imports,
never the reverse) and are re-exported here.
"""
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.design import Design, DesignTurn, validate_design_turn
from core.knowledge import KnowledgeNote
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
    "card_serves",
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
    # Why the card exists: the spec checks (R4.2) or requirements (R4) it serves. A card that
    # serves none names the cards it helps in `supports` (a shared helper for T7 and T8).
    serves: list[Annotated[str, Field(pattern=r"^R[0-9]+(\.[0-9]+)?$")]] = []
    supports: list[Annotated[str, Field(pattern=r"^T[0-9]+$")]] = []
    # The design elements the card builds (role contracts plan §4.2): components (C3),
    # interfaces (I5), data types (T2) or flows (F1), or refs of design changes in this plan.
    implements: list[Annotated[str, Field(pattern=r"^((C|I|T|F)[0-9]+|new:[0-9]+)$")]] = []

    @field_validator("acceptance")
    @classmethod
    def _items_non_empty(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("acceptance items must be non-empty")
        return value


class CheckLink(BaseModel):
    """An existing test that proves a spec check (adopt: tests written before GuudFlow)."""

    model_config = ConfigDict(extra="forbid")

    check: str = Field(pattern=r"^R[0-9]+\.[0-9]+$")
    test: str = Field(min_length=3, max_length=300)     # "tests/x.test.js" or "file::name"


class PlanResult(BaseModel):
    """The planner's structured result: the task list for the run.

    A tech lead's plan (role contracts plan §2.2) may also carry design changes, applied before
    the cards are created, and feasibility notes for the product manager. A plan needs tasks
    unless it is a design-only session (`check_plan(..., require_tasks=False)`)."""

    model_config = ConfigDict(extra="forbid")

    tasks: list[PlannedTask] = Field(default_factory=list)
    notes: str = ""
    design: DesignTurn | None = None
    spec_notes: list[Annotated[str, Field(min_length=20, max_length=600)]] = Field(
        default_factory=list, max_length=10)
    # Notes for the project knowledge base (role contracts plan §4.3), typically promoted from
    # the last session's notebooks.
    knowledge: list[KnowledgeNote] = Field(default_factory=list, max_length=20)
    # Adopt (role contracts plan §8): existing tests that prove existing checks. The session's
    # check run proves them; accepting the session records the link and the state.
    check_links: list[CheckLink] = Field(default_factory=list, max_length=200)


def check_link_problems(links: Sequence[CheckLink], check_ids: Collection[str],
                        test_files: Collection[str] | None) -> list[str]:
    """Each link names a known check and (when the test files are known) an existing file."""
    problems: list[str] = []
    files = [f.replace("\\", "/").lstrip("./") for f in test_files] if test_files else None
    for link in links:
        if link.check not in check_ids:
            problems.append(f"check_links: {link.check} is not a check in the spec")
        path = link.test.partition("::")[0].strip().replace("\\", "/").lstrip("./")
        if files is not None and not any(f == path or f.endswith("/" + path) for f in files):
            problems.append(f"check_links: {link.test} names no test file in the repo")
    return problems


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


_CHECK_LINE = re.compile(r"^\s*(R[0-9]+\.[0-9]+)\s*:", re.IGNORECASE)


def card_serves(acceptance: Sequence[str], serves: Sequence[str],
                item_serves: Sequence[str] = ()) -> list[str]:
    """What a card serves: its own `serves`, the checks its `R#.#:` acceptance lines carry and,
    when it names nothing itself, the requirements of its backlog item."""
    out = [s.upper() for s in serves]
    for line in acceptance:
        found = _CHECK_LINE.match(line)
        if found:
            out.append(found.group(1).upper())
    if not out:
        out = [s.upper() for s in item_serves]
    return list(dict.fromkeys(out))


def check_plan(plan: PlanResult, team_roles: Collection[str],
               backlog_ids: Collection[str] = (), *,
               spec_ids: Collection[str] | None = None,
               item_serves: Mapping[str, Sequence[str]] | None = None,
               design: Design | None = None, require_tasks: bool = True,
               require_design_links: bool = False) -> list[str]:
    """Check a schema-valid plan against the team; return one problem per line (empty = ok).

    With `backlog_ids` (the run's backlog items, plan §13) every item must be
    covered by at least one task, and tasks may only name those items.

    With `spec_ids` (the active requirement and check ids, when the project has a spec) every
    card must say why it exists: it serves a requirement or check (its `serves`, an `R#.#:`
    acceptance line or its backlog item's requirements), or it names the cards it `supports`.
    """
    problems: list[str] = []
    if not plan.tasks and (require_tasks or plan.design is None or not plan.design.changes):
        problems.append("the plan has no tasks: add at least one card")
    problems += _check_design(plan, design, spec_ids, require_design_links)
    ids = {t.id for t in plan.tasks}
    for task in plan.tasks:
        for sid in task.supports:
            if sid == task.id or sid not in ids:
                problems.append(f"{task.id}: supports '{sid}', which is not another card "
                                f"in this plan")
    if spec_ids is not None:
        known = {i.upper() for i in spec_ids}
        for task in plan.tasks:
            for sid in task.serves:
                if sid.upper() not in known:
                    problems.append(f"{task.id}: serves '{sid}', which is not an active "
                                    f"requirement or check of the spec")
            served = card_serves(task.acceptance, task.serves,
                                 (item_serves or {}).get(task.backlog_id or "", ()))
            if known and not served and not task.supports:
                problems.append(f"{task.id}: say why this card exists: set `serves` to the "
                                f"checks (R4.2) or requirements (R4) it serves, or `supports` "
                                f"to the cards it helps")
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


def _check_design(plan: PlanResult, design: Design | None, spec_ids: Collection[str] | None,
                  require_links: bool) -> list[str]:
    """The design part of a plan: its design changes are valid, and the cards' `implements`
    name design elements that exist (or are added by this plan). With `require_links` (the
    planner is a tech lead and there is a design), every card implements something or
    supports another card (tech lead contract, `done_when`)."""
    problems: list[str] = []
    current = design or Design({})
    turn = plan.design
    refs: set[str] = set()
    if turn is not None and turn.changes:
        reqs = [i for i in (spec_ids or ()) if "." not in i]
        problems += [f"design: {p}" for p in validate_design_turn(turn, current, reqs)]
        refs = {getattr(c, "ref") for c in turn.changes if getattr(c, "ref", None)
                and c.op in ("add_component", "add_interface", "add_datatype", "add_flow")}
    known = current.ids()
    for task in plan.tasks:
        for ident in task.implements:
            if ident not in known and ident not in refs:
                problems.append(f"{task.id}: implements {ident}, which is not in the design "
                                f"(or added by this plan's design changes)")
    has_design = bool(known) or bool(refs)
    if require_links and has_design:
        for task in plan.tasks:
            if not task.implements and not task.supports:
                problems.append(f"{task.id}: name the design element it builds in `implements` "
                                f"(C#, I#, T#, F# or a new:N ref), or the cards it supports")
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
