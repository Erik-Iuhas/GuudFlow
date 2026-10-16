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
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from core.design import Design, DesignDrafts, DesignError, render_for_brief as design_brief
from core.harness.handoff import HostFacts, render_handoffs, select_handoffs, thread_data
from core.harness.result_inbox import ResultCheck
from core.harness.results import AgentResult, ReviewResult
from core.harness.spec_brief import SpecBrief
from core.harness.task_board import BoardLimits, TaskBoard
from core.harness.tasks import PlanResult, check_link_problems, check_plan, check_task_result
from core.harness import visual_review
from core.harness.visual_review import PrecheckResult
from core.signals import Signal, SignalType
from core.workspace import AgentState, HumanRequest, Workspace

log = logging.getLogger(__name__)

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
    # The card's item effort (set by the project manager by difficulty); None: the role's own.
    effort: str | None = None


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
    # Planner sizing: (session budget, measured average finished-card cost).
    size_budget: tuple[float | None, float | None] = (None, None)
    # Card handoffs (plan §11.1): git facts for a merged (base, head) range, and worker names.
    facts_for: Callable[[str, str], HostFacts] | None = None
    # Every exported name of the code files a merged (base, head) range changed (drift check).
    exports_for: Callable[[str, str], dict[str, list[str]]] | None = None
    names: dict[str, str] = field(default_factory=dict)
    # The design (role contracts plan §4.2) and who changes it. `designer`: the planner is a
    # tech lead (its contract owns the design); `design_auto`: no human holds the Lead seat,
    # so the tech lead's design changes apply at once. `adopt`: the project joined with
    # existing code (plan §8), so an empty design is recovered before cards are planned.
    design: Design | None = None
    designer: bool = False
    design_auto: bool = True
    adopt: bool = False
    data_root: Path | None = None
    project_id: str | None = None
    run_id: str | None = None
    # The knowledge base (role contracts plan §4.3): generated facts and notes, and the roles'
    # notebooks from the last accepted session (the tech lead promotes from them).
    facts: dict[str, object] | None = None
    # Owner pings (worker context plan F3): role -> the message, for a turn with no card.
    pinged: dict[str, str] = field(default_factory=dict)
    # A merged range's diff, for the reviewer brief (worker context plan E3); set by the run.
    diff_for: Callable[[list[tuple[str, str]]], str] | None = None
    notebooks: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls, agents: Sequence["AgentConfig"], role_types: dict[str, str],
              workspace: Workspace, scheduler: Callable[[], "Scheduler"],
              post: Callable[[SignalType, str, str, str | None], None],
              escalate: Callable[[str, str, str | None], None],
              limits: BoardLimits | None = None,
              planner_role: str | None = None) -> "TaskFlow | None":
        """Task mode when the team has a planner; None otherwise.

        `planner_role` is the role whose contract owns the session plan (a tech lead); without
        it, the first role of type planner plans."""
        planner = planner_role if planner_role in {a.role for a in agents} else next(
            (a.role for a in agents if role_types.get(a.role) == "planner"), None)
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
        """Is there a card for `role` right now, or an owner's ping? (AgentSpec.has_work)"""
        if role in self.pinged:
            return True
        if not self.board.planned:
            return False
        if self.role_types.get(role) == "reviewer":
            return self.board.next_review(role) is not None
        return self.board.next_for(role) is not None

    def _sizing_text(self) -> str:
        """The planner's "Budget and card size" section, from measured card cost."""
        from core.project_runs import target_card_count

        budget, avg = self.size_budget
        target = target_card_count(budget, avg)
        lines = [f"- This session's budget: " + (f"${budget:.2f}" if budget else "not set"),
                 "- Measured average cost of a finished card in recent sessions: "
                 + (f"${avg:.2f}" if avg else "no history yet"),
                 f"- Target: plan at most {target} cards."]
        lines.append("Every card costs a fresh turn plus review, so prefer fewer, larger cards "
                     "that each deliver a testable vertical slice over many tiny ones.")
        return "\n".join(lines)

    def owners(self) -> list[str]:
        """Roles that may own cards."""
        return sorted(r for r in self.card_roles if self.role_types.get(r) not in _NON_OWNERS)

    # ── turns ─────────────────────────────────────────────────────────────
    def begin_turn(self, role: str) -> TurnPlan | None:
        """Plan one spawn. None → not task-driven (use role mode)."""
        if role == self.planner and self.board.planned:
            return self._answer_turn()
        if role == self.planner:
            owners = self.owners()
            # Items a continued run already finished need no card (flow hardening §5).
            items = [i.id for i in self.backlog if i.pending != "done"]
            sections: tuple[tuple[str, str], ...] = (
                ("Budget and card size", self._sizing_text()),
                ("Your output: the task list", _planner_instructions(owners)))
            spec_ids: list[str] | None = None
            if self.spec is not None:
                text = self.spec.planner_section(self.backlog)
                if text:
                    sections = (("The spec", text),) + sections
                spec_ids = self.spec.ids()
            design = self.design or Design({})
            recovering = self.designer and self.adopt and design.empty
            if self.designer:
                sections = (("The design", self._design_text(design)),
                            ("Your design work", design_instructions(recovering))) + sections
                knowledge = self._knowledge_for_lead()
                if knowledge:
                    sections = sections + (("The knowledge base", knowledge),)
            if self.backlog:
                sections = (("This run's backlog items", backlog_text(self.backlog)),) + sections
            item_serves = self._item_serves()
            designer = self.designer
            if recovering and self.spec is not None:
                unlinked = self._unlinked_checks()
                if unlinked:
                    sections = sections + (("Checks with no test yet", unlinked),)
            test_files = (list((self.facts or {}).get("tests") or [])
                          if self.facts is not None else None)
            return TurnPlan(
                model=PlanResult,
                check=lambda r: ((check_plan(r, owners, items, spec_ids=spec_ids,
                                             item_serves=item_serves, design=design,
                                             require_tasks=not recovering,
                                             require_design_links=designer)
                                  + check_link_problems(r.check_links, spec_ids or [],
                                                        test_files))
                                 if isinstance(r, PlanResult) else []),
                extra_sections=sections)
        if role not in self.card_roles:
            return None
        if self.role_types.get(role) == "reviewer":
            card = self.board.next_review(role)
            if card is None:
                return TurnPlan(model=ReviewResult, skip=True)
            self.board.start(role, card.task_id)
            criteria = self.board.split_checks(card)[0]
            spec_section, extra = self._card_spec(role, card, reviewer=True)
            design_section, design_lines = self._card_design(card, reviewer=True)
            extra = extra + design_lines
            criteria += [line for line in extra if line not in criteria]
            visual = self._visual_review_text(role, card.task_id)
            return TurnPlan(
                model=ReviewResult, card_id=card.task_id, effort=self._item_effort(card),
                extra_sections=self._change_in_hand(card) + spec_section + design_section
                + self._handoff_section(role, card.task_id, reviewer=True),
                task_text=self.board.brief_for(card.task_id, reviewer=True) + visual
                + f"\n\nThe card's work was done by **{card.assigned_to}** and is merged into "
                  f"your working copy. Call `guudflow_changes` with `task_id` "
                  f"\"{card.task_id}\" to see exactly what it changed.",
                check=lambda r, tid=card.task_id: (check_review_items(r, tid, criteria)
                                                   if isinstance(r, ReviewResult) else []))
        card = self.board.next_for(role)
        if card is None:
            message = self.pinged.pop(role, None)
            if message:
                return TurnPlan(model=AgentResult, task_text=(
                    "The owner messaged you directly and no card is waiting for you:\n\n"
                    f"> {message}\n\nDo what it asks within your job, in your own folder, then "
                    "finish as usual (`task_id` may stay empty). If it needs a card or another "
                    "role, say so in your summary instead."))
            return TurnPlan(model=AgentResult, skip=True)
        self.pinged.pop(role, None)          # the message reaches the card turn as a comment
        self.board.start(role, card.task_id)
        criteria, owner = self.board.split_checks(card)
        self_check = ("\n\n" + visual_review.self_check_text(card)
                      if card.kind == "visual" and self.tool_dir_for is not None else "")
        checks = (self.spec.checks_section(criteria) if self.spec is not None else None)
        methods = self.spec.check_methods() if self.spec is not None else {}
        return TurnPlan(
            model=AgentResult, card_id=card.task_id, effort=self._item_effort(card),
            extra_sections=((("Checks this card closes", checks),) if checks else ())
            + self._start_here(card)
            + self._card_spec(role, card)[0] + self._card_design(card)[0]
            + self._card_knowledge(card) + self._handoff_section(role, card.task_id),
            task_text=self.board.brief_for(card.task_id) + self_check,
            check=lambda r, tid=card.task_id: ((_check_done(r, tid, criteria, owner)
                                                + _check_evidence(r, methods))
                                               if isinstance(r, AgentResult) else []))

    # ── the planner after planning (worker context plan C3) ─────────────────
    def waiting_on_planner(self) -> list["Task"]:
        """Open unblock cards the planner owns: the questions cards are blocked on."""
        return [c for c in self.board.cards(self.planner)
                if c.column not in ("done", "cancelled")]

    def _answer_turn(self) -> TurnPlan:
        """A planner turn once the session is planned: answer the cards waiting on it by
        changing cards, never by re-planning the session."""
        waiting = self.waiting_on_planner()
        if not waiting:
            return TurnPlan(model=PlanResult, skip=True)
        lines: list[str] = []
        for unblock in waiting:
            for tid in unblock.supports or []:
                try:
                    blocked = self.board.card(tid)
                except KeyError:
                    continue
                lines.append(f"- **{tid} {blocked.title}** ({blocked.assigned_to}) waits on you "
                             f"for: {'; '.join(unblock.acceptance_criteria)}")
                lines += [f"    - [ ] {a}" for a in blocked.acceptance_criteria]
        owners = self.owners()
        text = (
            "The session is already planned. These cards are blocked on you:\n" + "\n".join(lines)
            + "\n\nAnswer them by changing cards, not by re-planning: decide each open question "
              "in `notes` (it is posted on the waiting cards), and add a card in `tasks` only for "
              "missing work (ids T1, T2, … in your answer; GuudFlow renumbers them; `supports` names the "
              f"waiting card it is for, and `owner_role` is one of: {', '.join(owners)}). Leave `tasks` empty when a "
              "decision is enough. The waiting cards go on when you submit.")
        return TurnPlan(
            model=PlanResult, extra_sections=(("Cards waiting on you", text),),
            check=lambda r: ((check_plan(_without_board_supports(r), owners, [],
                                         require_tasks=False)
                              + [f"{t.id}: supports '{x}', which is not a card on the board"
                                 for t in r.tasks for x in t.supports
                                 if x not in self.workspace.tasks]
                              + ([] if r.notes.strip() or r.tasks else
                                 ["notes: say what the waiting cards should do (at least one "
                                  "decision), or add the missing cards in `tasks`"]))
                             if isinstance(r, PlanResult) else []))

    def answer_accepted(self, plan: PlanResult) -> list[str]:
        """Apply a planner's answer: new cards (renumbered), its notes on the waiting cards, and
        the unblock cards settled so the waiting cards go on. Returns the new card ids."""
        waiting = self.waiting_on_planner()
        if self.spec is not None:
            plan, _ = self.spec.with_check_lines(plan, [])
        ids: dict[str, str] = {}
        created: list[str] = []
        for t in plan.tasks:
            card = self.board.add_card(
                owner=t.owner_role, title=t.title, acceptance=list(t.acceptance),
                created_by=self.planner, kind=t.kind, size=t.size,
                files_hint=list(t.files_hint), serves=list(t.serves), supports=list(t.supports))
            ids[t.id] = card.task_id
            created.append(card.task_id)
        for t in plan.tasks:
            card = self.board.card(ids[t.id])
            card.depends_on = [ids.get(d, d) for d in t.depends_on]
            for tid in t.supports:          # a new card a waiting card needs: it waits on it
                try:
                    waiting_card = self.board.card(tid)
                except KeyError:
                    continue
                if card.task_id not in waiting_card.depends_on:
                    waiting_card.depends_on.append(card.task_id)
        for unblock in waiting:
            for tid in unblock.supports or []:
                if plan.notes.strip():
                    self.board.note(tid, "decision", self.planner, plan.notes.strip())
                try:
                    self.board.reopen(tid, f"{self.planner} answered: "
                                      + (plan.notes.strip()[:200] or "see the new cards"))
                except KeyError:
                    continue
        if created:
            self.scheduler().max_iterations += len(created) * 2
        return created

    def _item_effort(self, card: "Task") -> str | None:
        """The effort the card's backlog item asks for, if any."""
        item = next((i for i in self.backlog if i.id == card.backlog_id), None)
        return (getattr(item, "effort", "") or None) if item is not None else None

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
                                      extra_lines=extra, serves=card.serves)
        return ((("The spec", text),) if text else ()), extra

    def _knowledge_rows(self, status: Sequence[str] = ("approved",)) -> list[dict[str, object]]:
        from core.knowledge import notes
        try:
            return notes(self.data_root, self.project_id, status)  # type: ignore[arg-type]
        except Exception as e:  # noqa: BLE001 — knowledge is context, never a failure
            log.warning("knowledge notes unreadable: %s", e)
            return []

    def _card_knowledge(self, card: "Task") -> tuple[tuple[str, str], ...]:
        """The "Project knowledge" section for a card's files."""
        from core.knowledge import render_for_brief as knowledge_brief
        text = knowledge_brief(self.facts, self._knowledge_rows(), card.files_hint)  # type: ignore[arg-type]
        return (("Project knowledge", text),) if text else ()

    def _knowledge_for_lead(self) -> str | None:
        """The tech lead's view: the notes (stale and proposed ones flagged), the notebooks
        to promote from, and what to do with them."""
        from core.harness.brief import wrap_untrusted
        rows = self._knowledge_rows(("approved", "stale", "proposed"))
        parts = []
        if rows:
            parts.append("Notes:\n" + "\n".join(
                f"- **{r['id']}** [{r['status']}] {r['topic']}: {r['text']}" for r in rows))
        if self.notebooks:
            parts.append("Notebooks the roles wrote at the end of the last accepted session:\n"
                         + "\n\n".join(wrap_untrusted(f"notebook of {role}", text)
                                        for role, text in sorted(self.notebooks.items())))
        if not parts:
            return None
        parts.append("Promote what more than one role needs (conventions, gotchas, how things "
                     "are wired) into `knowledge` notes with the paths they are about. Rewrite "
                     "a stale note with `replaces`. Keep notes short and true to the code.")
        return "\n\n".join(parts)

    def _design_text(self, design: Design) -> str:
        """The planner's "The design" section: the whole map, open debt and open drafts."""
        text = design_brief(design) or "There is no design yet."
        debt = design.open_debt()
        if debt:
            text += "\n\nKnown debt:\n" + "\n".join(
                f"- **{d['id']}** {d['text']}" for d in debt)
        return text

    def _card_design(self, card: "Task", *, reviewer: bool = False
                     ) -> tuple[tuple[tuple[str, str], ...], list[str]]:
        """The design section for a card turn and (reviewers) the interface lines to answer:
        one per interface the card builds or whose component it builds, one per binding
        technical decision."""
        design = self.design
        if design is None or design.empty:
            return (), []
        text = design_brief(design, focus=card.implements, files=card.files_hint)
        lines: list[str] = []
        if reviewer:
            ifaces = design.interfaces()
            comps = {i for i in card.implements if i.startswith("C")}
            comps |= {c.id for f in card.files_hint if (c := design.component_of(f))}
            for i in ifaces:
                if i.id in card.implements or i.provider in comps:
                    lines.append(f"design-{i.id}: the change keeps to {i.id} `{i.signature}` "
                                 f"(or the card says why the design must change)")
            for d in design.binding_decisions():
                lines.append(f"design-{d['id']}: {d['choice']}")
            if lines and text:
                text += ("\n\nDesign checklist. Besides the card's acceptance items, answer each "
                         "line with its own entry in `items` (`item` is the line, or at least its "
                         "leading id). Code that exports names the design does not declare for "
                         "its component fails the line, with a specific `detail`:\n"
                         + "\n".join(f"- [ ] {line}" for line in lines))
        return ((("The design", text),) if text else ()), lines

    def continues(self, previous: str | None, card_id: str | None) -> bool:
        """Does `card_id` continue `previous` (same card, depends on it, or shares files)?"""
        if previous is None or card_id is None:
            return True     # role mode: no cards to compare
        if previous == card_id:
            return True
        tasks = self.workspace.tasks
        if previous not in tasks or card_id not in tasks:
            return False
        nxt, prev = tasks[card_id], tasks[previous]
        return previous in nxt.depends_on or bool(set(nxt.files_hint) & set(prev.files_hint))

    # ── orientation (worker context plan E2, E3) ────────────────────────────
    REVIEW_DIFF_LINES = 300

    def check_command(self) -> str:
        """The project's check command: the generated facts, else the stack's test command."""
        commands = list((self.facts or {}).get("commands") or [])  # type: ignore[call-overload]
        if commands:
            return str(commands[0])
        return self.spec.spec.stack.test_command if self.spec is not None else ""

    def _start_here(self, card: "Task") -> tuple[tuple[str, str], ...]:
        """"Start here": the card's files and what the cards it depends on changed."""
        lines: list[str] = []
        if card.files_hint:
            lines.append("Your card's files: " + ", ".join(f"`{f}`" for f in card.files_hint))
        for dep in card.depends_on:
            task = self.workspace.tasks.get(dep)
            if task is None:
                continue
            files: list[str] = []
            exports: list[str] = []
            for entry in task.thread:
                facts = ((entry.get("data") or {}).get("facts") or {})
                files += [str(f[0]) for f in facts.get("files") or [] if f]
                exports += [str(e) for e in facts.get("exports") or []]
            if files:
                lines.append(f"{dep} ({task.title}) changed: "
                             + ", ".join(f"`{f}`" for f in dict.fromkeys(files))
                             + (f"; it exports {', '.join(exports[:8])}" if exports else ""))
        command = self.check_command()
        if command:
            lines.append(f"The check command: `{command}`")
        if not lines:
            return ()
        return (("Start here", "Read these first; open other files only when they lead you "
                 "there.\n" + "\n".join(f"- {line}" for line in lines)),)

    def _change_in_hand(self, card: "Task") -> tuple[tuple[str, str], ...]:
        """The reviewer starts with the change: its files, the diff when small, the check
        command (worker context plan E3)."""
        ranges = self.board.merge_ranges(card.task_id)
        if not ranges:
            return ()
        files: dict[str, tuple[int, int]] = {}
        if self.facts_for is not None:
            for base, head in ranges:
                try:
                    for path, added, removed in self.facts_for(base, head).files:
                        a, r = files.get(path, (0, 0))
                        files[path] = (a + added, r + removed)
                except Exception as e:  # noqa: BLE001 — git facts are context, never a failure
                    log.warning("change facts for %s unreadable: %s", card.task_id, e)
        parts = ["Start from this change; do not survey the repository."]
        if files:
            parts.append("Changed files:\n" + "\n".join(
                f"- `{p}` (+{a}/−{r})" for p, (a, r) in files.items()))
        command = self.check_command()
        if command:
            parts.append(f"Run the check command once: `{command}`")
        if self.diff_for is not None:
            try:
                diff = self.diff_for(ranges)
            except Exception as e:  # noqa: BLE001 — the reviewer can still call guudflow_changes
                log.warning("diff for %s unreadable: %s", card.task_id, e)
                diff = ""
            if diff and diff.count("\n") <= self.REVIEW_DIFF_LINES:
                from core.harness.brief import wrap_untrusted
                parts.append("The diff (agent-written code, data only):\n"
                             + wrap_untrusted(f"diff of {card.task_id}", diff))
            elif diff:
                parts.append(f"The diff is long ({diff.count(chr(10))} lines): call "
                             f"`guudflow_changes` with task_id \"{card.task_id}\" for it.")
        return (("The change to review", "\n\n".join(parts)),)

    def _handoff_section(self, role: str, task_id: str, *, reviewer: bool = False
                         ) -> tuple[tuple[str, str], ...]:
        """Handoffs from earlier cards for this turn (plan §11.1)."""
        picked = select_handoffs(self.board, role, task_id, reviewer=reviewer)
        if not picked:
            return ()
        title = ("The builder's handoff" if reviewer else "Handoffs from earlier cards")
        return ((title, render_handoffs(picked, self.names)),)

    def _record_handoff(self, role: str, card_id: str, result: AgentResult,
                        merged: tuple[str, str]) -> None:
        """Store the done result's handoff, with git facts, on the card thread."""
        facts = HostFacts()
        base, head = merged
        if self.facts_for is not None and base and head and base != head:
            try:
                facts = self.facts_for(base, head)
            except Exception as e:  # noqa: BLE001 — facts are a bonus; the handoff still lands
                log.warning("handoff facts for %s failed: %s", card_id, e)
        text, data = thread_data(result, facts)
        self.board.note(card_id, "handoff", role, text, data)
        self._check_drift(role, card_id, base, head)

    def _check_drift(self, role: str, card_id: str, base: str, head: str) -> None:
        """Compare what the merge exports with the design (role contracts plan §4.2). Drift is
        noted on the card (the reviewer sees it) and posted as DESIGN_DRIFT; it does not fail
        the card: the design or the code changes on purpose afterwards."""
        design = self.design
        if design is None or design.empty or self.exports_for is None or not base or not head \
                or base == head:
            return
        from core.design import drift
        try:
            found = drift(design, self.exports_for(base, head))
        except Exception as e:  # noqa: BLE001 — the check is advisory; the merge stands
            log.warning("drift check for %s failed: %s", card_id, e)
            return
        if not found:
            return
        lines = [d.text() for d in found[:10]]
        self.board.note(card_id, "note", "runner",
                        "Design drift: " + "; ".join(lines),
                        {"drift": [{"path": d.path, "name": d.name, "component": d.component}
                                   for d in found]})
        self.post(SignalType.DESIGN_DRIFT, role,
                  f"{card_id}: {len(found)} export(s) the design does not declare",
                  json.dumps({"task_id": card_id, "drift": [d.text() for d in found]}))

    def end_turn(self, role: str) -> None:
        """Always called when a spawn ends."""
        self.board.finish_turn(role)

    def _item_serves(self) -> dict[str, list[str]]:
        """Backlog item id → the requirement ids it serves."""
        return {i.id: list(i.serves) for i in self.backlog}

    def plan_accepted(self, plan: PlanResult) -> None:
        """Apply (or draft) the plan's design changes, pass its feasibility notes to the
        product manager, then create the cards and give card roles enough spawns."""
        attached: list[str] = []
        if self.spec is not None:
            plan, attached = self.spec.with_check_lines(plan, self.backlog)
        created = self._apply_design(plan)
        self._spec_notes(plan)
        self._knowledge_notes(plan)
        self._store_check_links(plan)
        if created:
            plan = plan.model_copy(update={"tasks": [
                t.model_copy(update={"implements": [created.get(i, i) for i in t.implements]})
                for t in plan.tasks]})
        self.board.create_from_plan(plan, self.planner, self._item_serves(),
                                    self._component_paths())
        if attached:
            # Coverage is GuudFlow's job, not the model's (worker context plan D1, D2).
            self.post(SignalType.THINKING, self.planner,
                      f"spec checks added to their item's card: {', '.join(attached)}",
                      json.dumps({"notice": "spec_coverage", "checks": attached}))
        per_role: dict[str, int] = {}
        for t in plan.tasks:
            per_role[t.owner_role] = per_role.get(t.owner_role, 0) + 1
        rounds = max(self.board.review_rounds_for.values(), default=2)
        needed = max(per_role.values(), default=1) * (rounds + 1) + len(plan.tasks) + 4
        scheduler = self.scheduler()
        scheduler.max_iterations = max(scheduler.max_iterations, needed)

    def _apply_design(self, plan: PlanResult) -> dict[str, str]:
        """Apply the tech lead's design changes (or store them as a draft for the Lead)."""
        turn = plan.design
        if turn is None or not turn.changes or self.data_root is None or not self.project_id:
            return {}
        reqs = [i for i in (self.spec.ids() if self.spec else []) if "." not in i]
        try:
            out = DesignDrafts(self.data_root, self.project_id).propose(
                turn, by=self.planner, auto_apply=self.design_auto, requirement_ids=reqs,
                run_id=self.run_id)
        except DesignError as e:
            # The plan check validated the same turn; only a concurrent edit gets here.
            log.warning("design changes of the plan were refused: %s", e)
            self.post(SignalType.THINKING, self.planner, "design changes were refused",
                      json.dumps({"warning": "design_refused", "problems": str(e)}))
            return {}
        self.design = Design.load(self.data_root, self.project_id)
        summary = (f"{out.draft_id} applied: {len(turn.changes)} design change(s), "
                   f"design v{out.version}" if out.status == "applied"
                   else f"{out.draft_id}: {len(turn.changes)} design change(s) wait for the "
                        f"Lead seat")
        self.post(SignalType.DESIGN_UPDATED, self.planner, summary, json.dumps({
            "draft": out.draft_id, "status": out.status, "created": out.created,
            "version": out.version, "reasons": [g.name for g in turn.groups]}))
        return out.created

    def _spec_notes(self, plan: PlanResult) -> None:
        """The tech lead's feasibility notes go to the plan conversation for the PM."""
        if not plan.spec_notes or self.data_root is None or not self.project_id:
            return
        from core.work_db import WorkDB
        text = ("Feasibility notes from the tech lead:\n"
                + "\n".join(f"- {n.strip()}" for n in plan.spec_notes))
        WorkDB(self.data_root / "projects" / self.project_id).add_plan_message(
            "tech_lead", text, None, "note")
        self.post(SignalType.THINKING, self.planner,
                  f"{len(plan.spec_notes)} feasibility note(s) for the product manager",
                  json.dumps({"spec_notes": list(plan.spec_notes)}))

    def _unlinked_checks(self) -> str:
        """Unit checks of active requirements that have no linked test (for adopt)."""
        assert self.spec is not None
        lines = [f"- {c.id}: {c.text}" for r in self.spec.spec.requirements
                 if r.status == "active" for c in r.checks
                 if not c.test and c.method in (None, "unit")]
        if not lines:
            return ""
        shown = lines[:80]
        more = len(lines) - len(shown)
        return ("\n".join(shown) + (f"\n- … and {more} more" if more else "")
                + "\n\nWhere an existing test already proves one of these, add it to "
                "`check_links` (`check`, `test` as \"file\" or \"file::test name\"). The "
                "session's check run then proves it and accepting the session records it. "
                "Link only tests you have read; leave a check out when no test covers it.")

    def _store_check_links(self, plan: PlanResult) -> None:
        """Keep the plan's check links with the run (read when its ledger is built)."""
        if not plan.check_links or self.data_root is None or not self.run_id:
            return
        from core.spec_adherence import store_check_links
        links = {link.check: link.test for link in plan.check_links}
        store_check_links(self.data_root, self.run_id, links)
        self.post(SignalType.THINKING, self.planner,
                  f"{len(links)} existing test(s) linked to spec checks",
                  json.dumps({"check_links": links}))

    def _knowledge_notes(self, plan: PlanResult) -> None:
        """The plan's knowledge notes: approved at once when no human holds the Lead seat."""
        if not plan.knowledge or self.data_root is None or not self.project_id:
            return
        from core.knowledge import add_notes
        out = add_notes(self.data_root, self.project_id, plan.knowledge, by=self.planner,
                        approve=self.design_auto, run_id=self.run_id)
        self.post(SignalType.THINKING, self.planner,
                  f"{len(out.added)} knowledge note(s) "
                  + ("added" if self.design_auto else "proposed for the Lead"),
                  json.dumps({"knowledge": out.added, "replaced": out.replaced}))

    def _component_paths(self) -> dict[str, list[str]]:
        """Design element id → the paths of the component that owns it."""
        design = self.design
        if design is None:
            return {}
        out = {c.id: list(c.paths) for c in design.components()}
        for i in design.interfaces():
            if i.provider in out:
                out[i.id] = out[i.provider]
        return out

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
            self._record_handoff(role, card_id, result, merged)
            self._record_owner_checks(role, card_id, result)
            if not self._visual_precheck(role, card_id):
                return
            self.board.submitted(card_id, result, commit=commit)
        elif result.status == "blocked":
            assert result.blocked_on is not None
            self._blocked(role, card_id, result.blocked_on.role, result.blocked_on.need)
        else:
            self.card_problem(role, card_id, result.summary)

    def _record_owner_checks(self, role: str, card_id: str, result: AgentResult) -> None:
        """Keep the card's owner checks, with what the worker says to try, for acceptance."""
        card = self.board.card(card_id)
        owner = self.board.split_checks(card)[1]
        if not owner or self.run_root is None:
            return
        rows = read_owner_checks(self.run_root)
        rows = [r for r in rows if r.get("task_id") != card_id]
        for line in owner:
            how = next((v for k, v in result.owner_checks.items()
                        if k.strip().casefold() == line.strip().casefold()
                        or line.strip().casefold().startswith(k.strip().casefold())), "")
            rows.append({"task_id": card_id, "backlog_id": card.backlog_id, "item": line,
                         "how": how, "by": role})
            if how:
                self.board.note(card_id, "note", role, f"owner check: {line} — try: {how}")
        write_owner_checks(self.run_root, rows)

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
        if on_role == self.planner:
            # The tech lead answers by changing cards (worker context plan C3).
            self.scheduler().rearm(self.planner)

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


OWNER_CHECKS_FILE = "owner_checks.json"


def read_owner_checks(run_root: Path) -> list[dict[str, Any]]:  # Any: JSON rows
    """The owner checks a run left for acceptance (empty when none)."""
    path = Path(run_root) / OWNER_CHECKS_FILE
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def write_owner_checks(run_root: Path, rows: list[dict[str, Any]]) -> None:  # Any: JSON rows
    """Replace the run's owner-check list."""
    (Path(run_root) / OWNER_CHECKS_FILE).write_text(json.dumps(rows, indent=1),
                                                    encoding="utf-8")


def _check_done(result: AgentResult, task_id: str, criteria: list[str],
                owner: Sequence[str] = ()) -> list[str]:
    """Owners must name their card; a `done` result answers every item they must meet, and
    nobody blocks on an owner check."""
    return check_task_result(result, task_id, criteria, owner)


_TEST_EVIDENCE = re.compile(r"test|spec\b|pytest|vitest|jest|bun\b", re.IGNORECASE)


def _check_evidence(result: AgentResult, methods: Mapping[str, str]) -> list[str]:
    """A spec check verified by a test needs evidence that names the test (plan D4)."""
    from core.spec_adherence import CHECK_LINE
    if result.status != "done":
        return []
    problems: list[str] = []
    for entry in result.acceptance:
        found = CHECK_LINE.match(entry.item)
        if not found or not entry.met:
            continue
        if methods.get(found.group(1).upper(), "unit") == "unit" \
                and not _TEST_EVIDENCE.search(entry.evidence):
            problems.append(f"acceptance: '{found.group(1).upper()}' is a unit check; its "
                            f"evidence must name the test that proves it and say it passed")
    return problems


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
    if item.pending == "done":
        return (f"- **{item.id}** {item.title}: DONE in the run this one continues. Plan no "
                "card for it; it closes when this run is accepted.")
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


def design_instructions(recovering: bool) -> str:
    """What a tech lead does with the design in its planning turn (role contracts plan §2.2)."""
    if recovering:
        return (
            "This project joined GuudFlow with existing code and no design. This session "
            "recovers it: read the code and submit the design as it is today in `design`.\n"
            "- Components: the parts of the code with one job each, the paths they own "
            "(`web/src/logic/**`) and the requirements they serve. No path has two owners.\n"
            "- Interfaces: what each component exports for others, with the real signature "
            "and the components that use it.\n"
            "- Data types that cross interfaces, and Mermaid flows for the main paths.\n"
            "- Technical decisions the code already follows (with why), and debt: anything "
            "with two owners, a file everything goes through, code left from dropped ideas "
            "(`add_debt`, with paths).\n"
            "Every change names its reason in `design.groups` (summary, because, evidence: the "
            "files it rests on). Link existing tests to the spec checks they prove in "
            "`check_links` (see \"Checks with no test yet\"). Plan no product cards this "
            "session unless a backlog item needs one; an empty `tasks` list is fine.")
    return (
        "You own the design. Before planning cards:\n"
        "1. Compare the design with the code. Where they differ, change one of them on purpose: "
        "a design change in `design`, or a card that changes the code.\n"
        "2. Add or change the components, interfaces, data types and flows this session's "
        "cards need, before any card depends on them. One behaviour has one owning "
        "component; record debt you find (`add_debt`) and resolve it when a card fixes it.\n"
        "3. Every card names what it builds in `implements` (C#, I#, T#, F#, or the `new:N` "
        "ref of a design change in this plan), or the cards it `supports`. Its files follow "
        "from the component when you give no `files_hint`.\n"
        "4. If a requirement cannot be built or tested as written, write a short note in "
        "`spec_notes` for the product manager instead of planning around it.\n"
        "Every design change names its reason in `design.groups` (summary, because, evidence). "
        "Your changes apply when the plan is accepted (or, when a person holds the Lead seat, "
        "wait for their approval while the cards already follow them).")


def _without_board_supports(plan: PlanResult) -> PlanResult:
    """An answer turn's `supports` name cards on the board, not cards of the answer: leave them
    out of the plan check (they are checked against the board instead)."""
    return plan.model_copy(update={"tasks": [t.model_copy(update={"supports": []})
                                             for t in plan.tasks]})


def _planner_instructions(owners: list[str]) -> str:
    return (
        "Break the goal into small cards and submit them as your result (`tasks`). Rules:\n"
        f"- `owner_role` must be one of: {', '.join(owners)}.\n"
        "- ids are T1, T2, …; `depends_on` lists ids that must be done first (no cycles).\n"
        "- keep every builder busy: avoid long chains where one role waits for another. When a "
        "card needs another's work, agree the interface first (function names, events, data "
        "shapes) in the earlier card's acceptance and the plan `notes`, so cards that only use "
        "that interface can start in parallel. Use `depends_on` only where the work truly "
        "cannot start without the other card's code.\n"
        "- every card has an `acceptance` checklist of concrete, testable items. Static pages can "
        "be screenshotted with the screenshot tool's `file` argument (no server needed); never "
        "write acceptance items that need a role to run a server unless it has the capability.\n"
        "- size S (≈10 min) or M (≈25 min); split anything larger. Never use L.\n"
        "- `kind` is code, test, docs, research or visual; give `files_hint` where the work "
        "will land, so cards touching the same files are not run in parallel.\n"
        "- every card says why it exists: `serves` lists the spec checks (R4.2) or "
        "requirements (R4) it works towards, when the project has a spec. A card that serves "
        "none (a shared helper, a refactor) lists the cards it helps in `supports`.\n"
        "- do not write the code yourself; put design notes in `notes`.")


__all__ = ["TaskFlow", "TurnPlan", "check_review_items"]
