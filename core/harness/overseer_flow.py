"""The overseer in a run: wake on stalls, decide, and have the run act (plan §11, O18).

The overseer decides; the run (on the scheduler's side) acts. This module:

  - feeds `RunView` snapshots to the detectors and queues wakes (`StallWatch`),
  - plans the overseer's turn (situation report + intervention menu, result
    validated against the team with `check_interventions`),
  - applies each accepted intervention as card and scheduler operations, posting
    an `OVERSEER_INTERVENTION` signal with its rationale,
  - falls back to `HUMAN_NEEDED` when the overseer fails or its wakes run out.

The overseer replaces the tech lead as the single escalation handler: an
ESCALATION signal is one of its detectors.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from core.harness.detectors import DetectorConfig, RunView, Stall, StallWatch, detect, situation_report
from core.harness.overseer import (
    NEEDS_HUMAN,
    Decide,
    ExtendReview,
    Intervention,
    OverseerCaps,
    OverseerResult,
    Pause,
    ProposeTeamChange,
    Reassign,
    Rebrief,
    RestartRole,
    Split,
    TeamView,
    check_interventions,
    intervention_signal,
)
from core.harness.task_board import TaskBoard
from core.signals import SignalType
from core.workspace import ApprovalRequest, HumanRequest, Workspace

if TYPE_CHECKING:
    from core.harness.git_roles import RoleClones
    from core.harness.task_flow import TurnPlan
    from core.scheduler import Scheduler

_REF = re.compile(r"^(main|[0-9a-f]{7,40})$")

MENU = """Return an `assessment` (what is wrong and why, in plain words) and at most 3
`interventions`. Each needs a `rationale`. The run applies them; you change nothing yourself.

- `rebrief(role, addendum, task_id?)`: guidance prepended to the role's next brief; a stuck
  card of that role is reopened.
- `decide(question, decision, task_id?)`: settle a question; recorded on the card and in
  docs/decisions.md; a blocked or stuck card is reopened.
- `reassign(task_id, from_role, to_role)`: move a card to another role of the same type.
- `split(task_id, parts)`: replace a card with 2-4 smaller sequential cards (same owner).
- `extend_review(author_role, reviewer_role)`: one more review round, within the hard cap.
- `restart_role(role, from_ref)`: reset the role's working copy to `main` (or a commit) and
  reopen its stuck cards. Once per role per run.
- `pause(question)`: stop new work and ask the human a plain-language question.
- `propose_team_change(change, summary)`: a team change; a human must approve it. Make `change`
  one op, `{"op": "add_role"|"edit_role"|"remove_role", "role": ..., ...}` (add_role also needs
  role_type, description, model_tier), so it can also appear in the project plan draft.

Prefer the smallest intervention that unsticks the work. An empty list is fine if the run
will recover on its own."""


@dataclass
class OverseerFlow:
    """Stall watching and intervention handling for one run."""

    workspace: Workspace
    overseer_role: str
    roles: dict[str, str]                          # role → role type
    board: TaskBoard | None
    scheduler: Callable[[], "Scheduler"]
    post: Callable[[SignalType, str, str, str | None], None]
    clones: "RoleClones | None" = None
    main_dir: Path | None = None
    # Called with each `propose_team_change` so the run can also record it in the project's
    # draft (plan §10.2); the approval request below stays the in-run human flow.
    on_team_change: Callable[[ProposeTeamChange], None] | None = None
    caps: OverseerCaps = field(default_factory=OverseerCaps)
    detector_cfg: DetectorConfig = field(default_factory=DetectorConfig)
    watch: StallWatch = field(default_factory=StallWatch)
    addenda: dict[str, list[str]] = field(default_factory=dict)
    # The detectors of the wake each pending addendum came from (same order as `addenda`),
    # so a budget raise can withdraw guidance that only answered a budget wake (§11.4).
    _addenda_from: dict[str, list[frozenset[str]]] = field(default_factory=dict)
    restarts_used: dict[str, int] = field(default_factory=dict)
    paused_for_cap: bool = False
    _current: list[Stall] = field(default_factory=list)
    # (spent, budget) at the last burn_without_output wake; debounces re-wakes.
    _burn_woken: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        self.watch.cooldown_seconds = self.caps.wake_cooldown_seconds
        self.watch.max_wakes = self.caps.max_wakes_per_run

    # ── watching ──────────────────────────────────────────────────────────
    def check(self, view: RunView) -> bool:
        """Run the detectors; True when the overseer should be (re)armed now."""
        if self.scheduler().is_running(self.overseer_role):
            return False
        stalls = [s for s in detect(view, self.detector_cfg)
                  if s.detector != "burn_without_output" or not self._burn_suppressed(view)]
        self.watch.offer(stalls, now=view.now)
        if not self.watch.pending:
            return False
        if self.watch.exhausted and not self.watch.free_only:
            self._pause_for_cap(view)
            return False
        return True

    def _burn_suppressed(self, view: RunView) -> bool:
        """A burn wake already happened and spend has not risen enough since (same budget)."""
        if self._burn_woken is None:
            return False
        spent, budget = self._burn_woken
        if view.budget_usd != budget:
            return False
        return view.spent_usd < spent + self.detector_cfg.burn_rewake_fraction * budget

    def _pause_for_cap(self, view: RunView) -> None:
        if self.paused_for_cap or self.workspace.pending_human:
            return
        self.paused_for_cap = True
        stalls = "; ".join(f"{s.detector} {s.subject}".strip() for s in self.watch.pending)
        self.workspace.pending_human = HumanRequest(
            from_agent=self.overseer_role, iteration=self.workspace.iteration,
            question=(f"The overseer used all {self.caps.max_wakes_per_run} of its wakes and the "
                      f"run is still stuck ({stalls}). Please decide how to continue."))
        self.post(SignalType.HUMAN_NEEDED, self.overseer_role,
                  "overseer wake cap reached; a human must decide", stalls)

    # ── the overseer's turn ───────────────────────────────────────────────
    def team_view(self) -> TeamView:
        """What `check_interventions` validates against."""
        board = self.board
        tasks = {c.task_id: c.assigned_to for c in board.cards()} if board else {}
        edges = dict(board.reviewers_of) if board else {}
        rounds: dict[str, tuple[int, int]] = {}
        if board:
            for owner, reviewer in edges.items():
                used = max((c.review_round for c in board.cards(owner)), default=0)
                rounds[owner] = (used, board.review_rounds_for.get(reviewer, 2))
        return TeamView(roles=dict(self.roles), tasks=tasks, review_edges=edges,
                        review_rounds=rounds, restarts_used=dict(self.restarts_used),
                        overseer_role=self.overseer_role)

    def begin_turn(self, view: RunView, last_results: dict[str, str]) -> "TurnPlan":
        """Plan a wake: hand the pending stalls to the overseer with a situation report."""
        from core.harness.task_flow import TurnPlan

        self._current = self.watch.take()
        # Say on the board why the overseer woke (Studio shows it; it was invisible before).
        self.post(SignalType.OVERSEER_WAKE, self.overseer_role,
                  "overseer woken: " + "; ".join(
                      f"{s.detector}{' ' + s.subject if s.subject else ''}" for s in self._current),
                  json.dumps({"stalls": [{"detector": s.detector, "subject": s.subject,
                                          "detail": s.detail} for s in self._current],
                              "spent_usd": round(view.spent_usd, 4), "budget_usd": view.budget_usd,
                              "wakes_used": self.watch.wakes,
                              "wakes_max": self.watch.max_wakes}))
        if any(s.detector == "burn_without_output" for s in self._current):
            self._burn_woken = (view.spent_usd, view.budget_usd or 0.0)
        report = situation_report(view, self._current, last_results)
        team, caps = self.team_view(), self.caps
        return TurnPlan(
            model=OverseerResult,
            check=lambda r: (check_interventions(r, team, caps)
                             if isinstance(r, OverseerResult) else []),
            extra_sections=(("Situation report", report), ("What you can do", MENU)))

    def failed(self, reason: str) -> None:
        """The overseer produced nothing usable: a human decides (§11.2)."""
        stalls = "; ".join(s.detail for s in self._current) or reason
        self.workspace.pending_human = HumanRequest(
            from_agent=self.overseer_role, iteration=self.workspace.iteration,
            question=f"The overseer could not resolve: {stalls}. How should the run continue?")
        self.post(SignalType.HUMAN_NEEDED, self.overseer_role,
                  "overseer failed; a human must decide", reason)

    # ── applying interventions ────────────────────────────────────────────
    def apply(self, result: OverseerResult) -> None:
        """Apply an accepted result. Every intervention is posted with its rationale."""
        self.post(SignalType.AGENT_RESULT, self.overseer_role,
                  f"overseer: {result.assessment[:120]}",
                  json.dumps({"result": result.model_dump(),
                              "stalls": [s.detail for s in self._current]}))
        for iv in result.interventions:
            sig = intervention_signal(iv, iteration=self.workspace.iteration)
            self.workspace.post_signal(sig)
            self._apply_one(iv)
        self.scheduler().notify()

    def _apply_one(self, iv: Intervention) -> None:
        board = self.board
        if isinstance(iv, Rebrief):
            self.addenda.setdefault(iv.role, []).append(iv.addendum)
            self._addenda_from.setdefault(iv.role, []).append(
                frozenset(s.detector for s in self._current))
            for card_id in self._stuck_cards(iv.role, iv.task_id):
                assert board is not None
                board.note(card_id, "intervention", self.overseer_role, f"rebrief: {iv.addendum}")
                board.reopen(card_id, "overseer rebrief")
        elif isinstance(iv, Decide):
            self._record_decision(iv)
            if iv.task_id and board is not None:
                board.note(iv.task_id, "decision", self.overseer_role,
                           f"{iv.question} → {iv.decision}")
                if board.card(iv.task_id).column in ("blocked", "needs_attention"):
                    board.reopen(iv.task_id, "overseer decision")
        elif isinstance(iv, Reassign) and board is not None:
            board.note(iv.task_id, "intervention", self.overseer_role,
                       f"reassigned from {iv.from_role} to {iv.to_role}: {iv.rationale}")
            board.reopen(iv.task_id, f"reassigned to {iv.to_role}", owner=iv.to_role)
        elif isinstance(iv, Split) and board is not None:
            self._split(board, iv)
        elif isinstance(iv, ExtendReview) and board is not None:
            limit = board.review_rounds_for.get(iv.reviewer_role, board.limits.max_review_rounds)
            board.review_rounds_for = {**board.review_rounds_for,
                                       iv.reviewer_role: min(limit + 1,
                                                             self.caps.review_round_hard_cap)}
            for card in board.cards(iv.author_role):
                if card.column == "needs_attention":
                    board.note(card.task_id, "intervention", self.overseer_role,
                               "one more review round granted")
                    board.reopen(card.task_id, "review extended")
                    card.column = "in_progress"     # rework, not a fresh start
        elif isinstance(iv, RestartRole):
            self._restart(iv)
        elif isinstance(iv, Pause):
            self.workspace.pending_human = HumanRequest(
                from_agent=self.overseer_role, question=iv.question,
                iteration=self.workspace.iteration)
            self.post(SignalType.HUMAN_NEEDED, self.overseer_role, iv.question[:120],
                      iv.rationale)
        elif isinstance(iv, ProposeTeamChange):
            request = ApprovalRequest(
                id=f"team_{len(self.workspace.pending_approvals) + 1}", kind="team_change",
                summary=iv.summary, context={"change": iv.change, "rationale": iv.rationale},
                triggered_by=self.overseer_role, iteration=self.workspace.iteration)
            self.workspace.pending_approvals.append(request)
            self.post(SignalType.APPROVAL_NEEDED, self.overseer_role, iv.summary,
                      f"approval_id={request.id}; kind=team_change; "
                      f"context={json.dumps(request.context)}")
            if self.on_team_change is not None:
                self.on_team_change(iv)

    def _stuck_cards(self, role: str, task_id: str | None) -> list[str]:
        if self.board is None:
            return []
        if task_id is not None:
            card = self.board.card(task_id)
            return [task_id] if card.column in ("blocked", "needs_attention") else []
        return [c.task_id for c in self.board.cards(role) if c.column == "needs_attention"]

    def _split(self, board: TaskBoard, iv: Split) -> None:
        original = board.card(iv.task_id)
        previous: list[str] = list(original.depends_on)
        new_ids: list[str] = []
        for part in iv.parts:
            card = board.add_card(owner=original.assigned_to, title=part.title,
                                  acceptance=part.acceptance, created_by=self.overseer_role,
                                  kind=original.kind, size="S", depends_on=previous,
                                  files_hint=original.files_hint, priority=original.priority)
            previous = [card.task_id]
            new_ids.append(card.task_id)
        for other in board.cards():
            if iv.task_id in other.depends_on:
                other.depends_on = [new_ids[-1] if d == iv.task_id else d
                                    for d in other.depends_on]
        board.note(iv.task_id, "intervention", self.overseer_role,
                   f"split into {', '.join(new_ids)}: {iv.rationale}")
        board._move(original, "done", f"split into {', '.join(new_ids)}")  # noqa: SLF001

    def _restart(self, iv: RestartRole) -> None:
        if not _REF.match(iv.from_ref):
            self.post(SignalType.FAILED, self.overseer_role,
                      f"restart_role refused: bad ref {iv.from_ref!r}", None)
            return
        self.restarts_used[iv.role] = self.restarts_used.get(iv.role, 0) + 1
        if self.clones is not None and (self.clones.git_dir(iv.role) / "HEAD").exists():
            main = str(self.clones.main.working_dir)
            self.clones.git(iv.role, "fetch", "--quiet", main, "+main:refs/heads/main")
            self.clones.git(iv.role, "reset", "--quiet", "--hard", iv.from_ref)
            self.clones.git(iv.role, "clean", "-fdq")
        for card_id in self._stuck_cards(iv.role, None):
            assert self.board is not None
            self.board.note(card_id, "intervention", self.overseer_role,
                            f"working copy reset to {iv.from_ref}")
            self.board.reopen(card_id, "role restarted")

    def _record_decision(self, iv: Decide) -> None:
        """Append to docs/decisions.md on main (the board already has the intervention)."""
        if self.main_dir is None:
            return
        from core.harness.git_roles import _run  # noqa: PLC2701 — same safe git runner

        path = self.main_dir / "docs" / "decisions.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("# Decisions\n\nRecorded by the overseer (plan §11.3).\n")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n## {iv.question}\n\n**Decision:** {iv.decision}\n\n"
                    f"**Why:** {iv.rationale}\n")
        safe = ["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false"]
        _run(["git", *safe, "add", "docs/decisions.md"], cwd=self.main_dir)
        _run(["git", *safe, "commit", "--quiet", "-m",
              f"docs(decisions): {iv.decision[:60]}"], cwd=self.main_dir)

    def withdraw_budget_guidance(self) -> int:
        """The budget was raised: drop pending guidance that came only from budget wakes
        ("money is nearly gone, finish fast"). Returns how many notes were dropped."""
        dropped = 0
        for role in list(self.addenda):
            sources = self._addenda_from.get(role, [])
            keep = [(n, src) for n, src in zip(self.addenda[role], sources)
                    if src and src != frozenset({"burn_without_output"})]
            keep += [(n, frozenset()) for n in self.addenda[role][len(sources):]]
            dropped += len(self.addenda[role]) - len(keep)
            self.addenda[role] = [n for n, _ in keep]
            self._addenda_from[role] = [src for _, src in keep]
            if not self.addenda[role]:
                del self.addenda[role]
        self._burn_woken = None          # a new budget: a burn wake may fire again later
        return dropped

    def brief_addenda(self, role: str) -> tuple[tuple[str, str], ...]:
        """Overseer guidance for the role's next brief (consumed once)."""
        notes = self.addenda.pop(role, [])
        self._addenda_from.pop(role, None)
        if not notes:
            return ()
        return (("Guidance from the overseer (read first)", "\n".join(f"- {n}" for n in notes)),)


def needs_human(iv: Intervention) -> bool:
    """True for interventions only a human can complete."""
    return iv.type in NEEDS_HUMAN
