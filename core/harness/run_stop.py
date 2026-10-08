"""Why a run stopped before its cards were done (flow hardening plan §4).

When the scheduler ends a task-board run `stuck`, at `max_iterations`, on the deadline or the
budget, the runner writes `stopped.json` and posts a `RUN_STOPPED` signal: every open card with
what it waits on. Studio shows it on the run's review card next to *Continue with the open
cards*, so a run never just ends `stuck` with nothing said.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.workspace import Task

STOP_FILE = "stopped.json"


@dataclass
class OpenCard:
    """One card the run left open and why it could not go on."""

    task_id: str
    title: str
    owner: str
    column: str
    backlog_id: str | None
    waits_on: str


@dataclass
class StopReport:
    """What a stopped run leaves for the person deciding it."""

    status: str                                   # the scheduler's end state
    summary: str
    open_cards: list[OpenCard] = field(default_factory=list)
    open_items: list[str] = field(default_factory=list)   # backlog items with open cards

    def to_dict(self) -> dict[str, Any]:  # Any: JSON
        """JSON form."""
        return asdict(self)


_STATUS_TEXT = {
    "stuck": "nothing could start and nothing was running",
    "max_iterations": "a role reached its turn limit",
    "deadline_exceeded": "the session's time ran out",
    "time_limit": "the session's time ran out",
    "budget_exhausted": "the session's budget ran out",
}


def build_stop_report(status: str, cards: Iterable[Task],
                      spawn_counts: Mapping[str, int] | None = None,
                      max_iterations: int | None = None) -> StopReport | None:
    """The report for a run that ended `status` with cards still open (None if all done)."""
    tasks = {c.task_id: c for c in cards}
    open_cards: list[OpenCard] = []
    for card in tasks.values():
        if card.column == "done":
            continue
        open_cards.append(OpenCard(card.task_id, card.title, card.assigned_to or "",
                                   card.column, card.backlog_id,
                                   _waits_on(card, tasks, spawn_counts or {}, max_iterations)))
    if not open_cards:
        return None
    reason = _STATUS_TEXT.get(status, status)
    items = sorted({c.backlog_id for c in open_cards if c.backlog_id})
    return StopReport(status=status,
                      summary=f"Stopped early: {reason}. {len(open_cards)} card(s) still open.",
                      open_cards=open_cards, open_items=items)


def _waits_on(card: Task, tasks: Mapping[str, Task], spawns: Mapping[str, int],
              cap: int | None) -> str:
    if card.column == "blocked" and card.blocked_on:
        return f"blocked on {', '.join(card.blocked_on)}"
    if card.column == "needs_attention":
        return "needs attention (the overseer or you)"
    pending = [d for d in card.depends_on if d in tasks and tasks[d].column != "done"]
    if pending:
        return "waits for " + ", ".join(
            f"{d} ({tasks[d].assigned_to}, {tasks[d].column})" for d in pending)
    owner = card.assigned_to or ""
    if cap is not None and spawns.get(owner, 0) >= cap:
        return f"{owner} used all {cap} of its turns"
    if card.column == "review":
        return "waiting for review"
    return "not started"


def write_stop(run_root: Path, report: StopReport) -> None:
    """Write `stopped.json` in the run folder."""
    (Path(run_root) / STOP_FILE).write_text(json.dumps(report.to_dict(), indent=1),
                                            encoding="utf-8")


def read_stop(run_root: Path) -> dict[str, Any] | None:  # Any: JSON
    """The stop report of a run, if it stopped early."""
    try:
        data = json.loads((Path(run_root) / STOP_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
