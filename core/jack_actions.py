"""The buttons Jack may attach to a reply (docs/JACK_UNSTUCK_PLAN.md §5).

Jack never acts. He proposes typed actions; Studio renders each as a button that calls an
existing route when the owner presses it. Every kind here is something only the owner decides,
or is outside a running session. The overseer's menu (rebrief, decide, reassign, split, extend
review, restart a role) is never offered (§2). `check_actions` drops what does not fit the
project, and says why in the log.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)

ActionKind = Literal[
    "answer",             # answer what paused the run (POST /runs/{id}/respond)
    "message_worker",     # pass an owner decision to one worker
    "plan_approve",       # approve the plan at the checkpoint
    "plan_send_back",     # send the plan back with a note
    "raise_limits",       # more budget and/or time (confirm in the button)
    "stop",               # stop the run (confirm in the button)
    "continue_run",       # continue a stopped or finished run with a note
    "start_session",      # start a session with backlog items
    "release_item",       # free an item stuck "in run"
    "approve_draft",      # approve the open draft (small, one reason)
    "open",               # go to a page: a card, a tab, Settings
]
CONFIRM = frozenset({"raise_limits", "stop"})
RUN_KINDS = frozenset({"answer", "message_worker", "plan_approve", "plan_send_back",
                       "raise_limits", "stop", "continue_run"})
OPEN_TARGETS = ("work", "plan", "spec", "design", "code", "sessions", "team", "settings",
                "models", "run", "card", "review")
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


class JackAction(BaseModel):
    """One button under a reply."""

    model_config = ConfigDict(extra="forbid")

    kind: ActionKind
    label: str = Field(min_length=2, max_length=60, description="The button text, a verb first.")
    run_id: str | None = None
    role: str | None = None
    text: str | None = Field(default=None, max_length=2000,
                             description="The answer, message or note to send.")
    items: list[str] = Field(default_factory=list, max_length=20)
    item: str | None = None
    budget_usd: float | None = Field(default=None, gt=0, le=1000)
    extend_minutes: int | None = Field(default=None, gt=0, le=600)
    target: str | None = Field(default=None, description="For open: " + ", ".join(OPEN_TARGETS))
    card: str | None = None


def check_actions(actions: Sequence[JackAction], *, roles: Collection[str],
                  run_ids: Collection[str], items: Collection[str],
                  has_draft: bool) -> list[JackAction]:
    """The actions that fit the project; the rest are dropped (logged), never failing a turn."""
    kept: list[JackAction] = []
    for a in actions[:4]:
        problem = _problem(a, roles, run_ids, items, has_draft)
        if problem:
            log.warning("jack: dropped action %s: %s", a.kind, problem)
            continue
        kept.append(a)
    return kept


def _problem(a: JackAction, roles: Collection[str], run_ids: Collection[str],
             items: Collection[str], has_draft: bool) -> str | None:
    for value in (a.run_id, a.role, a.item, a.card):
        if value is not None and not _ID.match(value):
            return "bad id"
    if a.kind in RUN_KINDS and (not a.run_id or a.run_id not in run_ids):
        return f"unknown run {a.run_id!r}"
    if a.kind == "message_worker" and (a.role not in roles or not (a.text or "").strip()):
        return "needs a team role and a message"
    if a.kind in ("answer", "plan_send_back") and not (a.text or "").strip():
        return "needs text"
    if a.kind == "raise_limits" and a.budget_usd is None and a.extend_minutes is None:
        return "raises nothing"
    if a.kind == "start_session" and (not a.items or any(i not in items for i in a.items)):
        return "needs open backlog items"
    if a.kind == "release_item" and a.item not in items:
        return "unknown item"
    if a.kind == "approve_draft" and not has_draft:
        return "no open draft"
    if a.kind == "open" and a.target not in OPEN_TARGETS:
        return "unknown target"
    return None


__all__ = ["CONFIRM", "JackAction", "OPEN_TARGETS", "check_actions"]
