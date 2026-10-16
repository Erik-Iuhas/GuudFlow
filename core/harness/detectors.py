"""Stuck detectors and the situation report that wakes the overseer (plan §11.2, O18).

Detectors are cheap and deterministic: they read a `RunView` snapshot (board
signals, cards, spend) and never call a model. Each finding is a `Stall`. The
`StallWatch` dedupes findings per (detector, subject), applies a cooldown and a
per-run wake cap; what survives becomes an overseer wake carrying a rendered
situation report.

| detector              | fires when                                                      |
|-----------------------|-----------------------------------------------------------------|
| no_progress           | nothing completed, merged or moved to done for N minutes while  |
|                       | roles are running or waiting                                    |
| same_review_issue     | a card failed review on the same item/file in two rounds        |
| blocked_chain         | a chain of blocked cards longer than 2 roles, or a cycle        |
| burn_without_output   | ≥ 50% of the budget spent with fewer than half the cards done   |
| permission_thrash     | one role's permission asks were denied 3+ times                 |
| validation_failures   | a role ended 2 consecutive turns without a valid result         |
| needs_attention       | a card is in needs_attention (failed, rounds exhausted, …)      |
| card_aging            | a card sat in one column past its size limit (§4.6)             |
| escalation            | an ESCALATION signal was posted (replaces the tech lead)        |
| time_limit            | a soft time limit is 80% used, then reached (once each; these   |
|                       | wakes do not count against the overseer's cap, plan §11.6)      |
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from core.harness.task_board import AGE_LIMITS
from core.signals import Signal, SignalType
from core.workspace import Task

Detector = Literal["no_progress", "same_review_issue", "blocked_chain", "burn_without_output",
                   "permission_thrash", "validation_failures", "needs_attention", "card_aging",
                   "escalation", "time_limit", "no_plan"]
# Wakes caused only by these do not count against the per-run wake cap.
FREE_DETECTORS: frozenset[str] = frozenset({"time_limit"})
# These fire once per subject, never again after their cooldown.
ONCE_DETECTORS: frozenset[str] = frozenset({"time_limit"})

_PROGRESS = frozenset({SignalType.COMPLETE, SignalType.GIT_MERGE})


@dataclass(frozen=True)
class Stall:
    """One detector finding."""

    detector: Detector
    subject: str            # role or card id the finding is about ("" for run-wide)
    detail: str

    @property
    def key(self) -> tuple[str, str]:
        """Dedupe key."""
        return (self.detector, self.subject)


@dataclass
class RunView:
    """What the detectors look at. Built by the run; plain data."""

    signals: Sequence[Signal]
    cards: Sequence[Task]
    role_states: dict[str, str]
    running: Sequence[str]
    spent_usd: float = 0.0
    budget_usd: float | None = None
    now: float = field(default_factory=time.time)
    started_at: float = field(default_factory=time.time)
    deadline_at: float | None = None
    deadline_mode: str = "hard"


@dataclass(frozen=True)
class DetectorConfig:
    """Thresholds (defaults from plan §11.2)."""

    no_progress_minutes: float = 10.0
    burn_fraction: float = 0.5
    # After a burn wake, wake again only once spend rose by this fraction of the budget.
    burn_rewake_fraction: float = 0.25
    permission_denials: int = 3
    validation_failures: int = 2
    max_chain: int = 2
    time_warn_fraction: float = 0.8


def _ts(sig: Signal) -> float:
    return sig.timestamp.timestamp()


def _detail(sig: Signal) -> dict[str, object]:
    try:
        value = json.loads(sig.detail or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def no_progress(view: RunView, cfg: DetectorConfig) -> list[Stall]:
    """Nothing completed or merged and no card done for N minutes while work is pending."""
    if not view.running and not any(s in ("ready", "running", "blocked")
                                     for s in view.role_states.values()):
        return []
    marks = [view.started_at] + [_ts(s) for s in view.signals if s.type in _PROGRESS
                                 or (s.type is SignalType.TASK_MOVED
                                     and _detail(s).get("to") == "done")]
    idle = view.now - max(marks)
    if idle < cfg.no_progress_minutes * 60:
        return []
    return [Stall("no_progress", "", f"no completion, merge or finished card for "
                                     f"{idle / 60:.0f} min; running: {', '.join(view.running) or 'none'}")]


def same_review_issue(view: RunView, cfg: DetectorConfig) -> list[Stall]:
    """A card failed review on the same item or file in two different rounds."""
    out = []
    for card in view.cards:
        seen: dict[str, set[int]] = {}
        round_no = 0
        for entry in card.thread:
            if entry.get("kind") == "review_issue":
                data = entry.get("data") or {}
                subject = str(data.get("item") or data.get("file") or "").strip().casefold()
                if subject:
                    seen.setdefault(subject, set()).add(round_no)
            elif entry.get("kind") == "note" and "merge" in (entry.get("data") or {}):
                round_no += 1
        repeated = sorted(s for s, rounds in seen.items() if len(rounds) >= 2)
        if repeated:
            out.append(Stall("same_review_issue", card.task_id,
                             f"{card.task_id} ({card.assigned_to}) failed review again on: "
                             + "; ".join(repeated)))
    return out


def blocked_chain(view: RunView, cfg: DetectorConfig) -> list[Stall]:
    """Blocked cards form a chain of more than `max_chain` roles, or a cycle, or a card is
    blocked while no role is running (whoever it waits on has finished, so nobody will act
    and the run would end stuck)."""
    waits: dict[str, str] = {}
    for card in view.cards:
        if card.column == "blocked" and card.blocked_on:
            waits.setdefault(card.assigned_to, card.blocked_on[0])
    if waits and not view.running:
        start = sorted(waits)[0]
        return [Stall("blocked_chain", start, "blocked with nothing running: " + "; ".join(
            f"{c.task_id} ({c.assigned_to}) waits on {c.blocked_on[0]}" for c in view.cards
            if c.column == "blocked" and c.blocked_on))]
    out = []
    # Waiting on a role that has finished for the run (the tech lead after planning) and has
    # no open card of its own: nobody will act, so the overseer decides now.
    # Unblock cards don't count: the one made for a blocked card is exactly what nobody runs.
    blocked_ids = {c.task_id for c in view.cards if c.column == "blocked"}
    open_owners = {c.assigned_to for c in view.cards if c.column in ("todo", "in_progress")
                   and not blocked_ids & set(c.supports or [])}
    for card in view.cards:
        if card.column != "blocked" or not card.blocked_on:
            continue
        on = card.blocked_on[0]
        if (view.role_states.get(on) == "complete" and on not in view.running
                and on not in open_owners):
            out.append(Stall("blocked_chain", card.assigned_to,
                             f"{card.task_id} ({card.assigned_to}) waits on {on}, which has "
                             f"finished for this run"))
    for start in sorted(waits):
        chain = [start]
        nxt = waits.get(start)
        while nxt is not None and nxt not in chain:
            chain.append(nxt)
            nxt = waits.get(nxt)
        if nxt is not None:
            out.append(Stall("blocked_chain", start, "blocked cycle: "
                             + " → ".join(chain + [nxt])))
        elif len(chain) > cfg.max_chain:
            out.append(Stall("blocked_chain", start, "blocked chain: " + " → ".join(chain)))
    return out


def burn_without_output(view: RunView, cfg: DetectorConfig) -> list[Stall]:
    """Half the budget is gone and fewer than half the cards are done."""
    if not view.budget_usd or not view.cards:
        return []
    done = sum(1 for c in view.cards if c.column == "done")
    if view.spent_usd >= cfg.burn_fraction * view.budget_usd and done < len(view.cards) / 2:
        return [Stall("burn_without_output", "",
                      f"spent ${view.spent_usd:.2f} of ${view.budget_usd:.2f} with "
                      f"{done}/{len(view.cards)} cards done")]
    return []


def permission_thrash(view: RunView, cfg: DetectorConfig) -> list[Stall]:
    """One role's permission asks were denied repeatedly."""
    asked_by: dict[str, str] = {}
    for sig in view.signals:
        if sig.type is SignalType.APPROVAL_NEEDED and sig.from_agent:
            for part in (sig.detail or "").split(";"):
                if part.strip().startswith("approval_id="):
                    asked_by[part.strip().removeprefix("approval_id=")] = sig.from_agent
    denials: dict[str, int] = {}
    for sig in view.signals:
        if sig.type is not SignalType.APPROVAL_DENIED:
            continue
        approval = next((p.strip().removeprefix("approval_id=")
                         for p in (sig.detail or "").split(";")
                         if p.strip().startswith("approval_id=")), "")
        role = asked_by.get(approval) or sig.from_agent or ""
        if role:
            denials[role] = denials.get(role, 0) + 1
    return [Stall("permission_thrash", role, f"{role}: {n} permission asks denied")
            for role, n in sorted(denials.items()) if n >= cfg.permission_denials]


def validation_failures(view: RunView, cfg: DetectorConfig) -> list[Stall]:
    """A role ended its last N turns without an accepted result."""
    streak: dict[str, int] = {}
    for sig in view.signals:
        role = sig.from_agent or ""
        if sig.type is SignalType.AGENT_RESULT:
            streak[role] = 0
        elif sig.type is SignalType.FAILED and ("without a valid result" in sig.summary
                                                or "no valid result" in (sig.detail or "")):
            streak[role] = streak.get(role, 0) + 1
    return [Stall("validation_failures", role, f"{role}: {n} turns in a row without a valid result")
            for role, n in sorted(streak.items()) if n >= cfg.validation_failures]


def needs_attention(view: RunView, cfg: DetectorConfig) -> list[Stall]:
    """Cards that can't continue on their own."""
    out = []
    for card in view.cards:
        if card.column == "needs_attention":
            last = next((e.get("text", "") for e in reversed(card.thread)), "")
            out.append(Stall("needs_attention", card.task_id,
                             f"{card.task_id} ({card.assigned_to}, round {card.review_round}): {last}"))
    return out


def card_aging(view: RunView, cfg: DetectorConfig) -> list[Stall]:
    """Cards stuck in one column past their size-based limit."""
    out = []
    for card in view.cards:
        if card.column in ("todo", "done", "needs_attention") or not card.column_since:
            continue
        age = view.now - card.column_since
        if age > AGE_LIMITS.get(card.size, AGE_LIMITS["M"]):
            out.append(Stall("card_aging", card.task_id,
                             f"{card.task_id} has been in {card.column} for {age / 60:.0f} min "
                             f"(size {card.size})"))
    return out


def escalation(view: RunView, cfg: DetectorConfig) -> list[Stall]:
    """Explicit escalations (what used to wake the tech lead)."""
    return [Stall("escalation", f"{s.from_agent}:{s.id}", f"{s.from_agent}: {s.summary}")
            for s in view.signals if s.type is SignalType.ESCALATION]


def time_limit(view: RunView, cfg: DetectorConfig) -> list[Stall]:
    """A soft time limit is close or reached: look for loops before time runs out."""
    if view.deadline_mode != "soft" or view.deadline_at is None:
        return []
    total = view.deadline_at - view.started_at
    used = view.now - view.started_at
    if total <= 0:
        return []
    open_cards = [c.task_id for c in view.cards if c.column not in ("done",)]
    running = ", ".join(view.running) or "nothing"
    left = f"{len(open_cards)} card(s) not done ({', '.join(open_cards[:8])})" if open_cards \
        else "every card is done"
    if used >= total:
        return [Stall("time_limit", "reached",
                      f"The session's soft time limit is reached ({total / 60:.0f} min). No new "
                      f"cards start; running: {running}; {left}. Look for loops or cards going "
                      f"nowhere and redirect them (split, rebrief, restart_role, decide). If "
                      f"nothing is stuck, change nothing: running work finishes on its own.")]
    if used >= cfg.time_warn_fraction * total:
        return [Stall("time_limit", "warning",
                      f"{used / 60:.0f} of {total / 60:.0f} minutes used; {left}; running: "
                      f"{running}. Check for loops or cards going nowhere while there is time to "
                      f"redirect them. If nothing is stuck, change nothing.")]
    return []


def no_plan(view: RunView, cfg: DetectorConfig) -> list[Stall]:
    """A role has run, nothing is running, and the board has no card at all: the planner
    finished without a plan, so the run would end stuck before any work started."""
    if view.cards or view.running:
        return []
    if any(s.type is SignalType.TASK_ASSIGNED for s in view.signals):
        return []        # there was a plan: its cards were sent back and a re-plan is due
    ran = [s for s in view.signals
           if s.type in (SignalType.AGENT_RESULT, SignalType.FAILED, SignalType.COMPLETE)]
    if not ran:
        return []
    last = "; ".join(f"{s.from_agent}: {s.summary}"[:200] for s in ran[-3:])
    return [Stall("no_plan", "", "no card was created and nothing is running (the planner "
                  f"finished without a plan). Last results: {last}. Restart or rebrief the "
                  "planner with what is missing.")]


DETECTORS: tuple[Callable[[RunView, DetectorConfig], list[Stall]], ...] = (
    no_plan, needs_attention, escalation, same_review_issue, blocked_chain, validation_failures,
    permission_thrash, burn_without_output, card_aging, no_progress, time_limit)


def detect(view: RunView, cfg: DetectorConfig | None = None) -> list[Stall]:
    """Run every detector."""
    cfg = cfg or DetectorConfig()
    return [stall for fn in DETECTORS for stall in fn(view, cfg)]


@dataclass
class StallWatch:
    """Dedupe, cooldown and per-run cap in front of the overseer (§11.2)."""

    cooldown_seconds: float = 120.0
    max_wakes: int = 5
    wakes: int = 0
    _last: dict[tuple[str, str], float] = field(default_factory=dict)
    pending: list[Stall] = field(default_factory=list)

    def offer(self, stalls: Sequence[Stall], now: float | None = None) -> list[Stall]:
        """Queue the findings that are new or out of cooldown; returns them."""
        now = time.time() if now is None else now
        fresh = []
        for stall in stalls:
            last = self._last.get(stall.key)
            if last is not None and (stall.detector in ONCE_DETECTORS
                                     or now - last < self.cooldown_seconds):
                continue
            if any(p.key == stall.key for p in self.pending):
                continue
            self._last[stall.key] = now
            fresh.append(stall)
        self.pending.extend(fresh)
        return fresh

    @property
    def exhausted(self) -> bool:
        """The run used all its overseer wakes."""
        return self.wakes >= self.max_wakes

    @property
    def free_only(self) -> bool:
        """Everything pending is a wake that does not count against the cap."""
        return bool(self.pending) and all(s.detector in FREE_DETECTORS for s in self.pending)

    def take(self) -> list[Stall]:
        """Start a wake: hand over everything pending."""
        stalls, self.pending = self.pending, []
        if any(s.detector not in FREE_DETECTORS for s in stalls):
            self.wakes += 1
        return stalls


def situation_report(view: RunView, stalls: Sequence[Stall],
                     last_results: dict[str, str] | None = None) -> str:
    """Markdown the overseer is woken with: why, the roles, the board, burn."""
    lines = ["### Why you were woken"]
    lines += [f"- **{s.detector}** {s.subject}: {s.detail}".replace(" : ", ": ") for s in stalls]
    lines += ["", "### Roles"]
    for role, state in sorted(view.role_states.items()):
        running = " (running now)" if role in view.running else ""
        result = (last_results or {}).get(role)
        lines.append(f"- {role}: {state}{running}" + (f" — last result: {result}" if result else ""))
    lines += ["", "### Cards"]
    for card in view.cards:
        extra = f", blocked on {', '.join(card.blocked_on)}" if card.column == "blocked" else ""
        lines.append(f"- {card.task_id} [{card.column}] {card.title} — owner {card.assigned_to}, "
                     f"round {card.review_round}{extra}")
        issues = [e.get("text", "") for e in card.thread if e.get("kind") == "review_issue"][-3:]
        lines += [f"    - review issue: {text}" for text in issues]
    lines += ["", "### Burn",
              f"- elapsed {(view.now - view.started_at) / 60:.0f} min, spent ${view.spent_usd:.2f}"
              + (f" of ${view.budget_usd:.2f}" if view.budget_usd else "")]
    web = sum(1 for s in view.signals if s.type is SignalType.WEB_REQUEST)
    denied = sum(1 for s in view.signals if s.type is SignalType.APPROVAL_DENIED)
    lines.append(f"- web requests {web}, permission denials {denied}")
    return "\n".join(lines)
