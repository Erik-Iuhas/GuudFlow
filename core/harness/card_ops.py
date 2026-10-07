"""Human operations on a live run's task board (plan section 4.6, S34).

"Humans work the same board": a human can add a card, change a card's
priority, or comment on a card. Every human entry lands in the card's thread
(a ``human_comment`` :class:`~core.harness.tasks.ThreadEntry`), so the next
brief for that card (``TaskBoard.brief_for``) includes it.

These are pure functions over a :class:`~core.workspace.Workspace`: no FastAPI,
no async. They validate, mutate through :class:`~core.harness.task_board.TaskBoard`
(never moving a card between columns; only the scheduler does that), then call
:func:`notify_board_changed` so the run can re-arm roles.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from core.harness.task_board import TaskBoard
from core.harness.tasks import ThreadEntry
from core.workspace import Task, Workspace

__all__ = [
    "CardOpError",
    "MAX_COMMENT_CHARS",
    "card_view",
    "human_add_card",
    "human_comment",
    "human_set_priority",
    "notify_board_changed",
    "thread_entry_view",
]

logger = logging.getLogger(__name__)

MAX_COMMENT_CHARS = 4000
MIN_TITLE_CHARS = 5
_KINDS = frozenset({"code", "test", "docs", "research", "visual"})


class CardOpError(ValueError):
    """A human card operation was refused (bad input or an illegal state)."""


def notify_board_changed(ws: Workspace) -> None:
    """Call every callable in ``ws.board_listeners`` (the run re-arms roles there).

    Posts nothing itself. A failing listener is logged and does not stop the
    others or undo the already-applied mutation.
    """
    for listener in list(ws.board_listeners):
        try:
            listener()
        except Exception:  # noqa: BLE001 - a listener must not undo a committed edit
            logger.exception("board listener failed for run %s", ws.run_id)


def _known_owner_roles(ws: Workspace) -> set[str]:
    """Roles a human may assign a card to.

    A role is known if it owns at least one board card (plan/board story) or has
    an entry in ``ws.agent_states`` (the runner registers every team member
    there). Unknown roles are refused so a typo cannot create an orphan card no
    scheduler will ever pick up.
    """
    owners = {t.assigned_to for t in TaskBoard(ws).cards() if t.assigned_to}
    return owners | set(ws.agent_states)


def _clean_items(items: Sequence[str]) -> list[str]:
    return [i.strip() for i in items if isinstance(i, str) and i.strip()]


def human_add_card(
    ws: Workspace,
    *,
    owner: str,
    title: str,
    acceptance: Sequence[str],
    kind: str = "code",
    size: str = "S",
    priority: int = 0,
    depends_on: Sequence[str] = (),
    files_hint: Sequence[str] = (),
    author: str = "human",
) -> Task:
    """Add a todo card on behalf of a human and return it.

    Validation (raises :class:`CardOpError`): ``owner`` must be a known role
    (owns a card or is in ``ws.agent_states``); ``title`` at least 5 characters;
    at least one non-empty acceptance item; ``kind`` one of code/test/docs/
    research/visual; ``size`` S or M (L is refused, "split it"); every
    ``depends_on`` id must be an existing board card.
    """
    owner = (owner or "").strip()
    title = (title or "").strip()
    known = _known_owner_roles(ws)
    if not owner or owner not in known:
        raise CardOpError(
            f"unknown owner '{owner}' (roles on this run: {', '.join(sorted(known)) or 'none'})")
    if len(title) < MIN_TITLE_CHARS:
        raise CardOpError(f"title must be at least {MIN_TITLE_CHARS} characters")
    items = _clean_items(acceptance)
    if not items:
        raise CardOpError("at least one non-empty acceptance item is required")
    if kind not in _KINDS:
        raise CardOpError(f"kind must be one of {', '.join(sorted(_KINDS))}")
    if size == "L":
        raise CardOpError("size 'L' is too big for one card: split it into S/M cards")
    if size not in ("S", "M"):
        raise CardOpError("size must be 'S' or 'M'")
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise CardOpError("priority must be an integer")
    board = TaskBoard(ws)
    deps = [d.strip() for d in depends_on if isinstance(d, str) and d.strip()]
    for dep in deps:
        try:
            board.card(dep)
        except KeyError:
            raise CardOpError(f"depends_on unknown card id '{dep}'") from None
    card = board.add_card(owner=owner, title=title, acceptance=items, created_by=author,
                          kind=kind, size=size, depends_on=deps,
                          files_hint=_clean_items(files_hint), priority=priority)
    board.note(card.task_id, "human_comment", author, "added by a human")
    notify_board_changed(ws)
    return card


def human_set_priority(ws: Workspace, task_id: str, priority: int,
                       author: str = "human") -> Task:
    """Set a card's priority (higher runs first) and note it on the thread.

    Raises :class:`KeyError` for an unknown card and :class:`CardOpError` for a
    done card or a non-integer priority.
    """
    board = TaskBoard(ws)
    card = board.card(task_id)
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise CardOpError("priority must be an integer")
    if card.column == "done":
        raise CardOpError(f"{task_id} is done; its priority can no longer change")
    card.priority = priority
    persister = ws._task_persister  # noqa: SLF001 - same pattern as TaskBoard._move
    if persister is not None:
        persister(card)
    board.note(task_id, "human_comment", author, f"priority set to {priority}")
    notify_board_changed(ws)
    return card


def human_comment(ws: Workspace, task_id: str, text: str, author: str = "human") -> Task:
    """Append a human comment (1..4000 chars after stripping) to a card's thread.

    Raises :class:`KeyError` for an unknown card and :class:`CardOpError` for
    empty or oversized text. The new entry is the last element of
    ``card.thread`` (see :func:`thread_entry_view`).
    """
    body = (text or "").strip()
    if not body:
        raise CardOpError("comment text is required")
    if len(body) > MAX_COMMENT_CHARS:
        raise CardOpError(f"comment is too long (max {MAX_COMMENT_CHARS} characters)")
    board = TaskBoard(ws)
    board.note(task_id, "human_comment", author, body)
    notify_board_changed(ws)
    return board.card(task_id)


def thread_entry_view(entry: dict[str, Any] | ThreadEntry) -> dict[str, Any]:
    """JSON-able view of one thread entry."""
    return entry.to_dict() if isinstance(entry, ThreadEntry) else dict(entry)


def card_view(task: Task) -> dict[str, Any]:
    """JSON-able view of a board card (what the Studio board renders)."""
    return {
        "id": task.task_id,
        "title": task.title or task.goal,
        "owner": task.assigned_to,
        "column": task.column,
        "kind": task.kind,
        "size": task.size,
        "priority": task.priority,
        "review_round": task.review_round,
        "acceptance": list(task.acceptance_criteria),
        "depends_on": list(task.depends_on),
        "files_hint": list(task.files_hint),
        "visual_refs": list(task.visual_refs),
        "column_since": task.column_since,
        "thread": [thread_entry_view(e) for e in task.thread],
        "backlog_id": task.backlog_id,
        "serves": list(task.serves),
        "supports": list(task.supports),
    }
