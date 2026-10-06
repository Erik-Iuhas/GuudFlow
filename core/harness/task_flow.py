"""Task-board scheduling for an opencode run (plan §4.6, O23).

`TaskFlow` connects the `TaskBoard` (what to work on) to the run (turns, briefs,
results) and the scheduler (when to spawn). It is active when the team has a
planner: the planner's validated `PlanResult` becomes the cards, and every role
downstream of the planner then works one card per turn.

  - Roles upstream of the planner (e.g. a researcher) run in plain role mode.
  - Card roles lose their role-level `depends_on` on other card roles: card
    dependencies order the work instead, so reviewers and authors can interleave.
  - A card role is spawned only while the board has a card for it
    (`AgentSpec.has_work`). It is complete when it has no open cards, and is
    re-armed if one comes back (failed review, unblock card, human card).
  - A card turn never fails its role: a bad turn sends the card to
    `needs_attention` (the overseer's input, §11.2) and the role moves on.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from core.harness.result_inbox import ResultCheck
from core.harness.results import AgentResult, ReviewResult
from core.harness.spec_brief import SpecBrief, missing_check_lines
from core.harness.task_board import BoardLimits, TaskBoard
from core.harness.tasks import PlanResult, check_plan, check_task_result
from core.harness import visual_review
from core.harness.visual_review import PrecheckResult
from core.signals import Signal, SignalType
from core.workspace import AgentState, HumanRequest, Workspace

if TYPE_CHECKING:
    from core.backlog import BacklogItem
    from core.workspace import Task
    from core.runner import AgentConfig
    from core.scheduler import Scheduler

# Role types that never own cards.
_NON_OWNERS = frozenset({"planner", "reviewer", "overseer", "it_agent"})


@dataclass
class TurnPlan:
    """What one spawn of a role does in task mode."""

    model: type[BaseModel]
    check: ResultCheck | None = None
    task_text: str | None = None
    card_id: str | None = None
    extra_sections: tuple[tuple[str, str], ...] = ()
    skip: bool = False          # nothing to do after all (raced): end the turn quietly


def check_review_items(result: ReviewResult, task_id: str, acceptance: Sequence[str]) -> list[str]:
    """A per-card review must name the card and answer every acceptance item."""
    problems = []
    if result.task_id != task_id:
        problems.append(f"task_id: expected '{task_id}', got '{result.task_id}'")
    answered = {i.item.strip().casefold() for i in result.items}
    for item in acceptance:
        key = item.strip().casefold()
        if not any(a == key or (len(a) >= 8 and (key.startswith(a) or a.startswith(key)))
                   for a in answered):
            problems.append(f"items: missing a verdict for '{item}'")
    return problems


@dataclass
class TaskFlow:
    """Task mode for one run."""

    workspace: Workspace
    planner: str
    card_roles: frozenset[str]
    role_types: dict[str, str]
    board: TaskBoard
    scheduler: Callable[[], "Scheduler"]
    post: Callable[[SignalType, str, str, str | None], None]
    escalate: Callable[[str, str, str | None], None]
    _done_posted: set[str] = field(default_factory=set)
    # Visual review (§5.10, O24); set by the run. Without tool_dir_for, visual
    # cards are reviewed like any other card.
    tool_dir_for: Callable[[str], Path] | None = None
    ref_roots: list[Path] = field(default_factory=list)
    run_root: Path | None = None
    vision_for: Callable[[str], bool] = lambda role: False
    _visual: dict[str, PrecheckResult] = field(default_factory=dict)
    _precheck_fails: dict[str, int] = field(default_factory=dict)
    _feedback_roles: dict[str, str] = field(default_factory=dict)   # request id → role
    # The project backlog items this run took (plan §13); the plan must cover each.
    backlog: list["BacklogItem"] = field(default_factory=list)
    # The project's spec and start mode (project-flow plan §4); None for projects without.
    spec: SpecBrief | None = None

    @classmethod
    def build(cls, agents: Sequence["AgentConfig"], role_types: dict[str, str],
              workspace: Workspace, scheduler: Callable[[], "Scheduler"],
              post: Callable[[SignalType, str, str, str | None], None],
              escalate: Callable[[str, str, str | None], None],
              limits: BoardLimits | None = None) -> "TaskFlow | None":
        """Task mode when the team has a planner; None otherwise."""
        planner = next((a.role for a in agents if role_types.get(a.role) == "planner"), None)
        if planner is None:
            return None
        deps = {a.role: set(a.depends_on) for a in agents}

        def downstream(role: str, seen: frozenset[str] = frozenset()) -> bool:
            if planner in deps.get(role, ()):
                return True
            return any(d not in seen and downstream(d, seen | {role}) for d in deps.get(role, ()))

        card_roles = frozenset(a.role for a in agents
                               if a.role != planner and downstream(a.role)
                               and role_types.get(a.role) != "overseer")
        owners = [r for r in card_roles if role_types.get(r) not in _NON_OWNERS]
        reviewers_of: dict[str, str] = {}
        rounds: dict[str, int] = {}
        for a in agents:
            if a.role not in card_roles or role_types.get(a.role) != "reviewer":
                continue
            rounds[a.role] = a.max_review_rounds
            targets = [r for r in (a.review_of or a.depends_on) if r in owners] or owners
            for owner in targets:
                reviewers_of.setdefault(owner, a.role)
        board = TaskBoard(workspace, reviewers_of=reviewers_of, review_rounds_for=rounds,
                          limits=limits or BoardLimits())
        return cls(workspace=workspace, planner=planner, card_roles=card_roles,
                   role_types=role_types, board=board, scheduler=scheduler, post=post,
                   escalate=escalate)

    # ── scheduling ────────────────────────────────────────────────────────
    def depends_on(self, cfg: "AgentConfig") -> list[str]:
        """Role-level dependencies kept for the scheduler (card roles drop card roles)."""
        if cfg.role not in self.card_roles:
            return list(cfg.depends_on)
        return [d for d in cfg.depends_on if d not in self.card_roles]

    def has_work(self, role: str) -> bool:
        """Is there a card for `role` right now? (AgentSpec.has_work)"""
        if not self.board.planned:
            return False
        if self.role_types.get(role) == "reviewer":
            return self.board.next_review(role) is not None
        return self.board.next_for(role) is not None

    def owners(self) -> list[str]:
        """Roles that may own cards."""
        return sorted(r for r in self.card_roles if self.role_types.get(r) not in _NON_OWNERS)

    # ── turns ─────────────────────────────────────────────────────────────
    def begin_turn(self, role: str) -> TurnPlan | None:
        """Plan one spawn. None → not task-driven (use role mode)."""
        if role == self.planner:
            owners = self.owners()
            items = [i.id for i in self.backlog]
            sections: tuple[tuple[str, str], ...] = (
                ("Your output: the task list", _planner_instructions(owners)),)
            if self.spec is not None:
                text = self.spec.planner_section(self.backlog)
                if text:
                    sections = (("The spec", text),) + sections
            if self.backlog:
                sections = (("This run's backlog items", backlog_text(self.backlog)),) + sections
            return TurnPlan(
                model=PlanResult,
                check=lambda r: (check_plan(r, owners, items) if isinstance(r, PlanResult)
                                 else []),
                extra_sections=sections)
        if role not in self.card_roles:
            return None
        if self.role_types.get(role) == "reviewer":
            card = self.board.next_review(role)
            if card is None:
                return TurnPlan(model=ReviewResult, skip=True)
            self.board.start(role, card.task_id)
            criteria = list(card.acceptance_criteria)
            spec_section, extra = self._card_spec(role, card, reviewer=True)
            criteria += [line for line in extra if line not in criteria]
            visual = self._visual_review_text(role, card.task_id)
            return TurnPlan(
                model=ReviewResult, card_id=card.task_id, extra_sections=spec_section,
                task_text=self.board.brief_for(card.task_id, reviewer=True) + visual
                + f"\n\nThe card's work was done by **{card.assigned_to}** and is merged into "
                  f"your working copy. Call `guudflow_changes` with `task_id` "
                  f"\"{card.task_id}\" to see exactly what it changed.",
                check=lambda r, tid=card.task_id: (check_review_items(r, tid, criteria)
                                                   if isinstance(r, ReviewResult) else []))
        card = self.board.next_for(role)
        if card is None:
            return TurnPlan(model=AgentResult, skip=True)
        self.board.start(role, card.task_id)
        criteria = list(card.acceptance_criteria)
        self_check = ("\n\n" + visual_review.self_check_text(card)
                      if card.kind == "visual" and self.tool_dir_for is not None else "")
        return TurnPlan(
            model=AgentResult, card_id=card.task_id,
            extra_sections=self._card_spec(role, card)[0],
            task_text=self.board.brief_for(card.task_id) + self_check,
            check=lambda r, tid=card.task_id: (_check_done(r, tid, criteria)
                                               if isinstance(r, AgentResult) else []))

    def _card_spec(self, role: str, card: "Task", *, reviewer: bool = False
                   ) -> tuple[tuple[tuple[str, str], ...], list[str]]:
        """The spec section for a card turn, and (reviewers) the rule lines to answer."""
        if self.spec is None:
            return (), []
        item = next((i for i in self.backlog if i.id == card.backlog_id), None)
        owner_type = self.role_types.get(card.assigned_to, "")
        extra = self.spec.reviewer_lines(owner_type) if reviewer else []
        text = self.spec.card_section(self.role_types.get(role, ""), item,
                                      owner_type=owner_type if reviewer else None,
                                      extra_lines=extra)
        return ((("The spec", text),) if text else ()), extra

    def end_turn(self, role: str) -> None:
        """Always called when a spawn ends."""
        self.board.finish_turn(role)

    def plan_accepted(self, plan: PlanResult) -> None:
        """Create the cards and give card roles enough spawns for them."""
        self.board.create_from_plan(plan, self.planner)
        if self.spec is not None:
            missing = missing_check_lines(self.backlog, [t.acceptance for t in plan.tasks])
            if missing:
                # Soft: the plan stands. There is no warning signal type, so a THINKING
                # notice from the planner carries it.
                self.post(SignalType.THINKING, self.planner,
                          f"spec coverage warning: {', '.join(missing)} on no card",
                          json.dumps({"warning": "spec_coverage", "checks": missing}))
        per_role: dict[str, int] = {}
        for t in plan.tasks:
            per_role[t.owner_role] = per_role.get(t.owner_role, 0) + 1
        rounds = max(self.board.review_rounds_for.values(), default=2)
        needed = max(per_role.values(), default=1) * (rounds + 1) + len(plan.tasks) + 4
        scheduler = self.scheduler()
        scheduler.max_iterations = max(scheduler.max_iterations, needed)

    def card_problem(self, role: str, card_id: str, reason: str) -> None:
        """The turn ended badly: the card needs attention; the role carries on."""
        self.board.needs_attention(card_id, reason, author="runner")
        self.post(SignalType.FAILED, role, f"{role}: card {card_id} needs attention", reason)

    def apply_result(self, role: str, card_id: str, result: BaseModel, commit: str,
                     merged: tuple[str, str] = ("", "")) -> None:
        """Move the card according to a merged, validated result."""
        if not isinstance(result, ReviewResult):
            self.board.record_merge(card_id, role, *merged)
        if isinstance(result, ReviewResult):
            outcome = self.board.reviewed(card_id, role, result)
            if outcome.passed and card_id in self._visual and self.run_root is not None:
                visual_review.save_baseline(self._visual[card_id].screenshot, self.run_root,
                                            card_id)
            if outcome.exhausted:
                self.escalate(role, f"review rounds exhausted on {card_id}",
                              json.dumps({"task_id": card_id, "round": outcome.round}))
            elif not outcome.passed:
                card = self.board.card(card_id)
                self.post(SignalType.REJECTED, role,
                          f"{role} sent {card_id} back to {card.assigned_to} "
                          f"(round {card.review_round})",
                          json.dumps({"task_id": card_id, "authors": [card.assigned_to],
                                      "round": card.review_round}))
            return
        assert isinstance(result, AgentResult)
        if result.status == "done":
            if not self._visual_precheck(role, card_id):
                return
            self.board.submitted(card_id, result, commit=commit)
        elif result.status == "blocked":
            assert result.blocked_on is not None
            self._blocked(role, card_id, result.blocked_on.role, result.blocked_on.need)
        else:
            self.card_problem(role, card_id, result.summary)

    def _blocked(self, role: str, card_id: str, on_role: str, need: str) -> None:
        if on_role not in self.role_types or on_role == role:
            self.card_problem(role, card_id, f"blocked on unknown role {on_role!r}: {need}")
            return
        if self.board.blocked_cycle(role, on_role):
            self.board.needs_attention(card_id, f"blocked-on cycle {role} ↔ {on_role}: {need}")
            self.workspace.pending_human = HumanRequest(
                from_agent=role, iteration=self.workspace.iteration,
                question=(f"{role} and {on_role} are blocked on each other ({card_id}: {need}). "
                          "Decide what should happen and answer, or stop the run."))
            self.post(SignalType.HUMAN_NEEDED, role, f"blocked-on cycle: {role} ↔ {on_role}",
                      need)
            return
        unblock = self.board.blocked(card_id, on_role, need)
        self.post(SignalType.BLOCKED, role, f"{role} is blocked on {on_role} ({card_id})",
                  json.dumps({"task_id": card_id, "on": on_role, "need": need,
                              "unblock_card": unblock.task_id}))

    # ── visual review (§5.10, O24) ────────────────────────────────────────
    def _visual_precheck(self, role: str, card_id: str) -> bool:
        """False when the card goes straight back to its owner (no reviewer turn)."""
        card = self.board.card(card_id)
        if card.kind != "visual" or self.tool_dir_for is None or self.run_root is None:
            return True
        result = visual_review.precheck(card, self.tool_dir_for(role), self.ref_roots,
                                        self.run_root / "visual" / card_id)
        self._visual[card_id] = result
        if result.passed:
            self.board.note(card_id, "artifact", "runner", f"visual pre-check passed: {result.detail}",
                            {"fractions": result.fractions})
            return True
        fails = self._precheck_fails.get(card_id, 0) + 1
        self._precheck_fails[card_id] = fails
        if fails > visual_review.MAX_PRECHECK_FAILS:
            self.board.note(card_id, "note", "runner",
                            f"visual pre-check still failing ({result.detail}); the reviewer decides")
            return True
        self.board.note(card_id, "review_issue", "visual-check", result.detail,
                        {"item": "visual match", "fractions": result.fractions})
        self.post(SignalType.REJECTED, "visual-check",
                  f"visual pre-check sent {card_id} back to {role}",
                  json.dumps({"task_id": card_id, "authors": [role], "round": card.review_round,
                              "detail": result.detail}))
        return False

    def _visual_review_text(self, reviewer: str, card_id: str) -> str:
        result = self._visual.get(card_id)
        if result is None or self.tool_dir_for is None:
            return ""
        staged = visual_review.stage_for_reviewer(result, self.tool_dir_for(reviewer), card_id)
        return "\n\n" + visual_review.reviewer_text(result, staged, self.vision_for(reviewer))

    def on_signal(self, sig: "Signal") -> None:
        """Human visual feedback lands on the card its requester is working on."""
        detail = _json_detail(sig.detail)
        if sig.type is SignalType.SCREENSHOT_READY and detail.get("request_id") and sig.from_agent:
            self._feedback_roles[str(detail["request_id"])] = sig.from_agent
        elif sig.type is SignalType.USER_VISUAL_FEEDBACK:
            role = self._feedback_roles.get(str(detail.get("request_id")))
            card_id = self.board.active.get(role or "")
            text = str(detail.get("feedback") or sig.summary or "").strip()
            if card_id and text:
                self.board.note(card_id, "human_comment", "human", text,
                                {"request_id": detail.get("request_id"), "visual": True})

    # ── completion ────────────────────────────────────────────────────────
    def settle(self, current: str | None = None) -> None:
        """Mark card roles complete when they have no open cards; re-arm reopened ones."""
        if not self.board.planned:
            return
        scheduler = self.scheduler()
        for role in sorted(self.card_roles):
            if role != current and scheduler.is_running(role):
                continue
            complete = role in self.workspace.completed_agents
            if self.board.has_open(role):
                if complete:
                    scheduler.rearm(role)
                    self._done_posted.discard(role)
                continue
            if not complete:
                self.workspace.completed_agents.append(role)
                self.workspace.agent_states[role] = AgentState.COMPLETE
            if role not in self._done_posted:
                self._done_posted.add(role)
                cards = self.board.cards(role)
                self.post(SignalType.COMPLETE, role,
                          f"{role} complete ({len(cards)} card(s) done)" if cards
                          else f"{role} complete (no cards)", None)
        scheduler.notify()


def _json_detail(detail: str | None) -> dict[str, object]:
    try:
        value = json.loads(detail or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _check_done(result: AgentResult, task_id: str, criteria: list[str]) -> list[str]:
    """Owners must name their card; a `done` result answers every checklist item."""
    return check_task_result(result, task_id, criteria)


def backlog_text(items: Sequence["BacklogItem"]) -> str:
    """The run's backlog items as a brief section (plan §13)."""
    return ("This project is ongoing work; this run takes these items from its backlog. "
            "Each task that works on an item sets `backlog_id` to it, and every item needs "
            "at least one task. You may add tasks without `backlog_id` for work you find on "
            "the way; unfinished ones go back to the backlog. Earlier runs' notes on an "
            "item tell you what was tried.\n\n" + "\n\n".join(_item_text(i) for i in items))


def _item_text(item: "BacklogItem") -> str:
    """Human-written items as they are; agent-written text (run-created items and
    earlier runs' summaries) wrapped as untrusted data."""
    from core.harness.brief import wrap_untrusted
    text = (item.brief_text() if not item.source.startswith("run:")
            else f"- **{item.id}**\n" + wrap_untrusted(f"backlog item {item.id} (from a run)",
                                                     item.brief_text()))
    review = item.review_note()
    if review:
        text += f"\n    - the human reviewer said {review}"
    summary = item.last_summary()
    if summary:
        text += "\n" + wrap_untrusted(f"earlier work on {item.id}", summary)
    return text


def _planner_instructions(owners: list[str]) -> str:
    return (
        "Break the goal into small cards and submit them as your result (`tasks`). Rules:\n"
        f"- `owner_role` must be one of: {', '.join(owners)}.\n"
        "- ids are T1, T2, …; `depends_on` lists ids that must be done first (no cycles).\n"
        "- every card has an `acceptance` checklist of concrete, testable items. Static pages can "
        "be screenshotted with the screenshot tool's `file` argument (no server needed); never "
        "write acceptance items that need a role to run a server unless it has the capability.\n"
        "- size S (≈10 min) or M (≈25 min); split anything larger. Never use L.\n"
        "- `kind` is code, test, docs, research or visual; give `files_hint` where the work "
        "will land, so cards touching the same files are not run in parallel.\n"
        "- do not write the code yourself; put design notes in `notes`.")


__all__ = ["TaskFlow", "TurnPlan", "check_review_items"]
