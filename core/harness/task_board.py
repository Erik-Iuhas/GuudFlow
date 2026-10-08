"""The task board is the work queue (plan §4.6, O23).

Cards are `core.workspace.Task` rows created by the runner from the planner's
validated `PlanResult`. Only scheduler-side events move them; agent text never
does:

  plan validated            → todo
  turn starts for the card  → in_progress
  result done + merged      → review   (done directly if nobody reviews the owner)
  reviewer passes all items → done
  reviewer fails any item   → in_progress, issues on the thread, round + 1
  result blocked            → blocked  (an "unblock" card is created for the
                                        role it waits on; it returns to todo
                                        when that card is done)
  failed / rounds exhausted → needs_attention (the overseer wakes, §11.2)

The board decides *what* each role should work on next (`next_for`,
`next_review`); the scheduler decides *when* (via each role's `has_work`). The
board is plain synchronous state: it owns no tasks or timers.

Built-in improvements from §4.6: a file-overlap guard (two roles never work on
cards whose `files_hint` overlap at the same time), WIP limits (one active card
per role; authors don't start new cards while the Review column is full),
rework first, priority ordering, card aging timestamps, and a definition of
done per kind that goes into every brief.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from core.harness.results import AgentResult, ReviewResult
from core.harness.tasks import PlanResult, ThreadEntry, ThreadKind, card_serves, render_thread
from core.signals import Signal, SignalType
from core.workspace import Task, TaskStatus, Workspace

Column = Literal["todo", "in_progress", "review", "done", "blocked", "needs_attention"]
COLUMNS: tuple[Column, ...] = ("todo", "in_progress", "review", "done", "blocked",
                               "needs_attention")
OPEN_COLUMNS = frozenset({"todo", "in_progress", "review", "blocked", "needs_attention"})

_STATUS_FOR: dict[str, TaskStatus] = {
    "in_progress": "IN_PROGRESS", "review": "IN_PROGRESS", "done": "COMPLETE",
    "blocked": "BLOCKED", "needs_attention": "BLOCKED",
}

# Definition of done per kind (§4.6). Shown to the owner and to the reviewer.
DEFINITION_OF_DONE: dict[str, str] = {
    "code": "The code works and its tests pass when run in your sandbox; the reviewer "
            "passes every acceptance item.",
    "test": "The new tests fail without the change they cover and pass with it; the "
            "reviewer passes every acceptance item.",
    "docs": "The reviewer passes every acceptance item.",
    "research": "Findings are written to research/notes.md with sources; the reviewer "
                "passes every acceptance item.",
    "visual": "A screenshot matches the visual references (visual check, §5.10) and the "
              "reviewer passes every acceptance item.",
}

# Card aging limits in seconds per size (§4.6: S 10 min, M 25 min).
AGE_LIMITS: dict[str, float] = {"S": 600.0, "M": 1500.0, "L": 3600.0}


@dataclass(frozen=True)
class BoardLimits:
    """WIP limits for the board."""

    review_column_cap: int = 3      # authors start no NEW card while Review holds this many
    max_review_rounds: int = 2      # default per card when the reviewer has none


@dataclass
class ReviewOutcome:
    """What a review did to a card."""

    task_id: str
    passed: bool
    round: int
    exhausted: bool = False


def _overlaps(a: Iterable[str], b: Iterable[str]) -> bool:
    """True if two files_hint lists touch the same file or one contains the other."""
    def norm(p: str) -> str:
        return p.strip().strip("/").removesuffix("/**").removesuffix("/*")
    left = [norm(p) for p in a if p.strip()]
    right = [norm(p) for p in b if p.strip()]
    return any(x == y or x.startswith(y + "/") or y.startswith(x + "/")
               for x in left for y in right)


@dataclass
class TaskBoard:
    """Cards for one run. `reviewers_of` maps an owner role to its reviewer role."""

    workspace: Workspace
    reviewers_of: Mapping[str, str] = field(default_factory=dict)
    review_rounds_for: Mapping[str, int] = field(default_factory=dict)  # reviewer → max
    limits: BoardLimits = field(default_factory=BoardLimits)
    clock: Callable[[], float] = time.time
    active: dict[str, str] = field(default_factory=dict)      # role → card in a turn now
    planned: bool = False
    # Cards smaller than this (S < M < L) skip review; visual cards are always reviewed.
    review_min_size: str = "S"

    # ── creation ──────────────────────────────────────────────────────────
    def create_from_plan(self, plan: PlanResult, planner: str,
                         item_serves: Mapping[str, Sequence[str]] | None = None,
                         component_paths: Mapping[str, Sequence[str]] | None = None
                         ) -> list[Task]:
        """Create one card per planned task (the runner calls this, never an agent).

        `item_serves` (backlog item → requirement ids) fills in what a card serves when it
        names nothing itself. `component_paths` (design element id → the paths of the
        component that owns it) fills in `files_hint` for a card that gives none, so cards
        that build the same component are not run side by side."""
        cards = []
        for i, planned in enumerate(plan.tasks):
            card = Task(task_id=planned.id, story_id="plan", assigned_to=planned.owner_role,
                        created_by=planner, created_at_iter=self.workspace.iteration,
                        goal=planned.title, title=planned.title,
                        acceptance_criteria=list(planned.acceptance), kind=planned.kind,
                        size=planned.size,
                        files_hint=list(planned.files_hint) or list(dict.fromkeys(
                            p for ident in planned.implements
                            for p in (component_paths or {}).get(ident, ()))),
                        depends_on=list(planned.depends_on),
                        visual_refs=list(planned.visual_refs), column="todo",
                        priority=-i, column_since=self.clock(),
                        backlog_id=planned.backlog_id,
                        serves=card_serves(planned.acceptance, planned.serves,
                                           (item_serves or {}).get(planned.backlog_id or "", ())),
                        supports=list(planned.supports),
                        implements=list(planned.implements))
            self.workspace.add_task(card)
            cards.append(card)
        self.planned = True
        return cards

    def add_card(self, *, owner: str, title: str, acceptance: Sequence[str], created_by: str,
                 kind: str = "code", size: str = "S", depends_on: Sequence[str] = (),
                 files_hint: Sequence[str] = (), priority: int = 0,
                 serves: Sequence[str] = (), supports: Sequence[str] = ()) -> Task:
        """Add a card outside the plan (unblock cards, overseer splits, humans)."""
        card = Task(task_id=self._next_id(), story_id="board", assigned_to=owner,
                    created_by=created_by, created_at_iter=self.workspace.iteration,
                    goal=title, title=title, acceptance_criteria=list(acceptance), kind=kind,
                    size=size, files_hint=list(files_hint), depends_on=list(depends_on),
                    column="todo", priority=priority, column_since=self.clock(),
                    serves=card_serves(acceptance, serves), supports=list(supports))
        self.workspace.add_task(card)
        self.planned = True
        return card

    def _next_id(self) -> str:
        nums = [int(t[1:]) for t in self.workspace.tasks if t[:1] == "T" and t[1:].isdigit()]
        return f"T{max(nums, default=0) + 1}"

    # ── queries ───────────────────────────────────────────────────────────
    def cards(self, owner: str | None = None) -> list[Task]:
        """Board cards (plan and board stories), optionally for one owner."""
        return [t for t in self.workspace.tasks.values()
                if t.story_id in ("plan", "board")
                and (owner is None or t.assigned_to == owner)]

    def card(self, task_id: str) -> Task:
        """One card by id."""
        card = self.workspace.tasks.get(task_id)
        if card is None or card.story_id not in ("plan", "board"):
            raise KeyError(task_id)
        return card

    def column(self, name: Column) -> list[Task]:
        """Cards currently in one column."""
        return [c for c in self.cards() if c.column == name]

    def _deps_done(self, card: Task) -> bool:
        return all(self.workspace.tasks.get(d) is not None
                   and self.workspace.tasks[d].column == "done" for d in card.depends_on)

    def _overlap_blocked(self, card: Task, role: str) -> bool:
        for other_role, task_id in self.active.items():
            if other_role != role and _overlaps(card.files_hint,
                                                self.workspace.tasks[task_id].files_hint):
                return True
        return False

    def next_for(self, role: str) -> Task | None:
        """The card `role` should work on next, or None (nothing runnable now).

        Rework (in_progress after a failed review) comes first, then todo cards by
        priority. A todo card waits for its dependencies, for any overlapping card
        another role is working on, and while the Review column is full.
        """
        mine = [c for c in self.cards(role) if c.column in ("todo", "in_progress")
                and self.active.get(role) != c.task_id]
        rework = [c for c in mine if c.column == "in_progress"]
        todo = [c for c in mine if c.column == "todo"]
        review_full = len(self.column("review")) >= self.limits.review_column_cap
        candidates = rework + ([] if review_full else
                               sorted(todo, key=lambda c: -c.priority))
        for card in candidates:
            if self._deps_done(card) and not self._overlap_blocked(card, role):
                return card
        return None

    def authors_reviewed_by(self, reviewer: str) -> list[str]:
        """Owner roles whose cards `reviewer` reviews."""
        return [owner for owner, rev in self.reviewers_of.items() if rev == reviewer]

    def next_review(self, reviewer: str) -> Task | None:
        """The oldest card waiting in Review for this reviewer."""
        authors = set(self.authors_reviewed_by(reviewer))
        waiting = [c for c in self.column("review") if c.assigned_to in authors
                   and self.active.get(reviewer) != c.task_id]
        return min(waiting, key=lambda c: c.column_since) if waiting else None

    def has_open(self, role: str) -> bool:
        """True while `role` still owns or must review an unfinished card."""
        if any(c.column in OPEN_COLUMNS for c in self.cards(role)):
            return True
        authors = set(self.authors_reviewed_by(role))
        return any(c.column in OPEN_COLUMNS for c in self.cards() if c.assigned_to in authors)

    def all_done(self) -> bool:
        """Every card is done."""
        return bool(self.cards()) and all(c.column == "done" for c in self.cards())

    def aging(self, now: float | None = None) -> list[tuple[Task, float]]:
        """Open cards older than their size limit in their column: (card, seconds)."""
        now = self.clock() if now is None else now
        out = []
        for c in self.cards():
            if c.column in ("todo", "done"):
                continue
            age = now - c.column_since
            if age > AGE_LIMITS.get(c.size, AGE_LIMITS["M"]):
                out.append((c, age))
        return out

    # ── moves (scheduler events only) ─────────────────────────────────────
    def _move(self, card: Task, to: Column, reason: str, **extra: object) -> None:
        old = card.column
        card.column = to
        card.column_since = self.clock()
        status = _STATUS_FOR.get(to)
        if status is not None and status != card.status:
            kwargs: dict[str, object] = dict(extra)
            self.workspace.mutate_task(card.task_id, status,
                                       iteration=self.workspace.iteration, **kwargs)
        else:
            if to == "todo":
                card.status = "PENDING"
            for key, value in extra.items():
                setattr(card, key, value)
            persister = self.workspace._task_persister  # noqa: SLF001 — same module family
            if persister is not None:
                persister(card)
        self.workspace.post_signal(Signal(
            type=SignalType.TASK_MOVED, from_agent=card.assigned_to or None,
            summary=f"{card.task_id} {old} → {to}: {card.title[:60]}",
            detail=json.dumps({"task_id": card.task_id, "from": old, "to": to,
                               "reason": reason, "owner": card.assigned_to,
                               "round": card.review_round}),
            iteration=self.workspace.iteration))

    def note(self, task_id: str, kind: ThreadKind, author: str, text: str,
             data: dict[str, object] | None = None) -> ThreadEntry:
        """Append a typed entry to a card's thread and post it on the board."""
        card = self.card(task_id)
        entry = ThreadEntry(task_id=task_id, kind=kind, author=author, text=text,
                            iteration=self.workspace.iteration, data=dict(data or {}))
        card.thread.append(entry.to_dict())
        persister = self.workspace._task_persister  # noqa: SLF001
        if persister is not None:
            persister(card)
        self.workspace.post_signal(Signal(
            type=SignalType.TASK_THREAD, from_agent=author,
            summary=f"{task_id} [{kind}] {text[:80]}", detail=json.dumps(entry.to_dict()),
            iteration=self.workspace.iteration))
        return entry

    def thread(self, task_id: str) -> list[ThreadEntry]:
        """A card's thread as typed entries."""
        return [ThreadEntry.from_dict(e) for e in self.card(task_id).thread]

    def start(self, role: str, task_id: str) -> Task:
        """A turn for the card begins."""
        card = self.card(task_id)
        self.active[role] = task_id
        if card.column != "in_progress" and card.assigned_to == role:
            self._move(card, "in_progress", "turn started")
        return card

    def finish_turn(self, role: str) -> None:
        """The role's turn ended (whatever happened)."""
        self.active.pop(role, None)

    def record_merge(self, task_id: str, author: str, base: str, head: str) -> None:
        """Remember which main range a turn on this card merged (for the reviewer's diff)."""
        if base and head and base != head:
            self.note(task_id, "note", author, f"merged {base[:8]}..{head[:8]}",
                      {"merge": [base, head]})

    def merge_ranges(self, task_id: str) -> list[tuple[str, str]]:
        """Every (base, head) range merged for this card, oldest first."""
        out = []
        for entry in self.card(task_id).thread:
            merge = (entry.get("data") or {}).get("merge")
            if isinstance(merge, list) and len(merge) == 2:
                out.append((str(merge[0]), str(merge[1])))
        return out

    def submitted(self, task_id: str, result: AgentResult, *, commit: str = "") -> Column:
        """The owner's `done` result was merged: to review, or done if nobody reviews."""
        card = self.card(task_id)
        for check in result.acceptance:
            self.note(task_id, "note", card.assigned_to,
                      f"{'✓' if check.met else '✗'} {check.item} — {check.evidence}".strip(" —"))
        if card.assigned_to in self.reviewers_of and self._skips_review(card):
            self.note(task_id, "note", "runner",
                      f"review skipped by size (card size {card.size}, "
                      f"review_min_size {self.review_min_size.upper()})")
        elif card.assigned_to in self.reviewers_of:
            self._move(card, "review", "result done and branch merged")
            return "review"
        self._move(card, "done", "result done (no reviewer)", closed_by_commit=commit)
        self._release_unblocked()
        return "done"

    def _skips_review(self, card: Task) -> bool:
        order = {"S": 0, "M": 1, "L": 2}
        return (card.kind != "visual"
                and order.get(card.size, 1) < order.get(str(self.review_min_size).upper(), 0))

    def reviewed(self, task_id: str, reviewer: str, result: ReviewResult) -> ReviewOutcome:
        """Apply a reviewer's per-item verdict to a card."""
        card = self.card(task_id)
        if result.verdict == "pass":
            self.note(task_id, "decision", reviewer, result.summary or "review passed")
            self._move(card, "done", f"{reviewer} passed every acceptance item")
            self._release_unblocked()
            return ReviewOutcome(task_id, True, card.review_round)
        for item in result.items:
            if not item.met:
                self.note(task_id, "review_issue", reviewer, f"{item.item}: {item.detail}",
                          {"item": item.item})
        for issue in result.issues:
            self.note(task_id, "review_issue", reviewer, f"{issue.file}: {issue.detail}",
                      {"file": issue.file})
        limit = self.review_rounds_for.get(reviewer, self.limits.max_review_rounds)
        if card.review_round >= limit:
            self._move(card, "needs_attention", f"review rounds exhausted ({limit})")
            return ReviewOutcome(task_id, False, card.review_round, exhausted=True)
        self._move(card, "in_progress", f"{reviewer} failed the review",
                   review_round=card.review_round + 1)
        return ReviewOutcome(task_id, False, card.review_round)

    def blocked(self, task_id: str, on_role: str, need: str) -> Task:
        """The owner is blocked on `on_role`: park the card behind a new unblock card."""
        card = self.card(task_id)
        unblock = self.add_card(owner=on_role, created_by=card.assigned_to,
                                title=f"Unblock {task_id}: {need[:70]}", acceptance=[need],
                                priority=card.priority + 1, supports=[task_id])
        card.depends_on.append(unblock.task_id)
        self.note(task_id, "blocked_on", card.assigned_to, f"waiting on {on_role}: {need}",
                  {"role": on_role, "unblock_card": unblock.task_id})
        self._move(card, "blocked", f"blocked on {on_role}", blocked_on=[on_role])
        return unblock

    def blocked_cycle(self, role: str, on_role: str) -> bool:
        """True if `on_role` already has a card blocked on `role` (A↔B, §4.4)."""
        return any(c.column == "blocked" and role in c.blocked_on for c in self.cards(on_role))

    def needs_attention(self, task_id: str, reason: str, author: str = "runner") -> None:
        """The card can't continue without the overseer or a human."""
        card = self.card(task_id)
        self.note(task_id, "note", author, reason)
        self._move(card, "needs_attention", reason)

    def reopen(self, task_id: str, reason: str, *, owner: str | None = None) -> Task:
        """Back to todo (overseer reassign/rebrief, human retry)."""
        card = self.card(task_id)
        if owner is not None:
            card.assigned_to = owner
        self._move(card, "todo", reason)
        return card

    def _release_unblocked(self) -> None:
        for card in self.column("blocked"):
            if self._deps_done(card):
                self._move(card, "todo", "unblocked: dependencies done", blocked_on=[])

    # ── briefs ────────────────────────────────────────────────────────────
    def brief_for(self, task_id: str, *, reviewer: bool = False) -> str:
        """The card as brief text: checklist, definition of done, deps, thread."""
        card = self.card(task_id)
        lines = [f"**{card.task_id}: {card.title}** (kind {card.kind}, size {card.size}"
                 + (f", review round {card.review_round}" if card.review_round else "") + ")",
                 "", "Acceptance checklist:"]
        lines += [f"- [ ] {item}" for item in card.acceptance_criteria]
        lines += ["", f"Definition of done: {DEFINITION_OF_DONE.get(card.kind, '')}"]
        if card.files_hint:
            lines.append(f"Files likely involved: {', '.join(card.files_hint)}")
        if card.visual_refs:
            lines.append(f"Visual references to match: {', '.join(card.visual_refs)}")
        deps = [self.workspace.tasks[d] for d in card.depends_on if d in self.workspace.tasks]
        if deps:
            lines += ["", "Cards this depends on (done):"]
            for dep in deps:
                decided = [e for e in self.thread(dep.task_id) if e.kind == "decision"]
                extra = f" — {decided[-1].text}" if decided else ""
                lines.append(f"- {dep.task_id} {dep.title} ({dep.assigned_to}){extra}")
        entries = self.thread(task_id)
        if entries:
            lines += ["", "Card thread (most recent last):", render_thread(entries)]
        lines.append("")
        if reviewer:
            lines.append(f"Review only this card. Submit with `task_id` \"{card.task_id}\" and "
                         "one `items` entry per acceptance item (met, and a specific detail "
                         "for any unmet item). Verdict pass only if every item is met.")
        else:
            lines.append(f"Work only on this card. Submit with `task_id` \"{card.task_id}\" and "
                         "one `acceptance` entry per checklist item (met, with evidence: the "
                         "test or command that shows it).")
        return "\n".join(lines)
