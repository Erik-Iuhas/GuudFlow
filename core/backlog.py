"""The project backlog: work items that outlive runs (plan §13).

A project is ongoing development. Its backlog (`data/projects/{id}/backlog.json`)
holds the work: humans add, edit, reprioritise and drop items at any time; a
run takes a few of them; what is left comes back.

Item lifecycle:

    open ──take_for_run──▶ in_run ──record_outcomes──▶ in_run + pending outcome
      ▲                                                     │
      └──── run rejected / item not finished ◀── decide ────┤
                                                             └─ run accepted and
                                                                item done ──▶ done
    open ──drop──▶ dropped ──reopen──▶ open

An item is only `done` once the run that finished it has been accepted into the
project's main (see `core.project_repo`). Cards a run's planner added beyond the
selected items, and did not finish, become new open items so nothing is lost.
Writes go through `core.json_store.locked_json` (the API and the worker share the file).
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.json_store import locked_json, read_json

ItemStatus = Literal["open", "in_run", "done", "dropped"]
Outcome = Literal["done", "partial", "not_started"]
KINDS = frozenset({"code", "test", "docs", "research", "visual"})
SIZES = frozenset({"S", "M", "L"})
_ID = re.compile(r"^B[0-9]+$")
_STATUS_ORDER = {"in_run": 0, "open": 1, "done": 2, "dropped": 3}
_EDITABLE = frozenset({"title", "acceptance", "kind", "size", "priority", "notes", "files_hint"})


class BacklogError(ValueError):
    """A backlog operation was refused (unknown item, wrong state, bad field)."""


@dataclass
class BacklogItem:
    """One piece of work on the project backlog."""

    id: str
    title: str
    acceptance: list[str] = field(default_factory=list)
    kind: str = "code"
    size: str = "M"
    priority: int = 0
    status: ItemStatus = "open"
    notes: str = ""
    files_hint: list[str] = field(default_factory=list)
    source: str = "human"              # human | run:<run_id>
    created_at: float = 0.0
    updated_at: float = 0.0
    run_id: str | None = None          # the run holding it while in_run
    pending: Outcome | None = None     # outcome waiting for that run's acceptance
    history: list[dict[str, Any]] = field(default_factory=list)  # Any: JSON rows

    def brief_text(self) -> str:
        """The item's own text (title, checklist, notes) for a brief.

        Items a run created (`source` "run:…") carry agent-written text; callers
        building prompts wrap those, and `last_summary()`, as untrusted.
        """
        lines = [f"- **{self.id}** {self.title} ({self.kind}, size {self.size})"]
        lines += [f"    - [ ] {a}" for a in self.acceptance]
        if self.notes:
            lines.append(f"    - notes: {self.notes}")
        return "\n".join(lines)

    def last_summary(self) -> str | None:
        """What the most recent run that worked on it reported (agent text)."""
        last = next((h for h in reversed(self.history) if h.get("summary")), None)
        return f"run {last.get('run_id')}: {last.get('summary')}" if last else None


@dataclass(frozen=True)
class CardOutcome:
    """A finished run's card, as the backlog needs it."""

    title: str
    column: str
    backlog_id: str | None = None
    acceptance: tuple[str, ...] = ()
    kind: str = "code"
    summary: str = ""


def _clean_lines(values: Iterable[object]) -> list[str]:
    return [str(v).strip() for v in values if str(v).strip()]


class Backlog:
    """The backlog of one project."""

    def __init__(self, data_root: Path, project_id: str) -> None:
        self.path = Path(data_root) / "projects" / project_id / "backlog.json"

    # ── reads ───────────────────────────────────────────────────────────────
    def items(self, status: ItemStatus | None = None) -> list[BacklogItem]:
        """Items in working order: in-run first, then open by priority, then the rest."""
        doc = read_json(self.path, {"next": 1, "items": []})
        items = [BacklogItem(**row) for row in doc.get("items", []) if isinstance(row, dict)]
        if status is not None:
            items = [i for i in items if i.status == status]
        return sorted(items, key=lambda i: (_STATUS_ORDER.get(i.status, 9), -i.priority,
                                            int(i.id[1:]) if _ID.match(i.id) else 0))

    def get(self, item_id: str) -> BacklogItem:
        """One item; BacklogError if unknown."""
        for item in self.items():
            if item.id == item_id:
                return item
        raise BacklogError(f"unknown backlog item {item_id!r}")

    # ── human edits ─────────────────────────────────────────────────────────
    def add(self, title: str, acceptance: Sequence[str] = (), *, kind: str = "code",
            size: str = "M", priority: int = 0, notes: str = "",
            files_hint: Sequence[str] = (), source: str = "human") -> BacklogItem:
        """Add an open item."""
        fields = self._validated({"title": title, "acceptance": list(acceptance), "kind": kind,
                                  "size": size, "priority": priority, "notes": notes,
                                  "files_hint": list(files_hint)})
        now = time.time()
        with locked_json(self.path, {"next": 1, "items": []}) as doc:
            item = BacklogItem(id=f"B{int(doc.get('next', 1))}", source=source,
                               created_at=now, updated_at=now, **fields)
            doc["next"] = int(doc.get("next", 1)) + 1
            doc.setdefault("items", []).append(asdict(item))
        return item

    def update(self, item_id: str, **changes: Any) -> BacklogItem:  # Any: validated below
        """Edit fields of an item (any status but `done`)."""
        unknown = set(changes) - _EDITABLE
        if unknown:
            raise BacklogError(f"cannot edit {', '.join(sorted(unknown))}")
        clean = self._validated(changes)
        with locked_json(self.path, {"next": 1, "items": []}) as doc:
            row = self._row(doc, item_id)
            if row["status"] == "done":
                raise BacklogError(f"{item_id} is done; add a new item instead")
            row.update(clean)
            row["updated_at"] = time.time()
            return BacklogItem(**row)

    def drop(self, item_id: str, reason: str = "") -> BacklogItem:
        """Drop an open item (it stays visible as dropped)."""
        return self._set_status(item_id, "dropped", ("open",), reason)

    def reopen(self, item_id: str) -> BacklogItem:
        """Bring a dropped or done item back."""
        return self._set_status(item_id, "open", ("dropped", "done"), "reopened")

    # ── runs ────────────────────────────────────────────────────────────────
    def take_for_run(self, run_id: str, ids: Sequence[str] | None = None,
                     limit: int = 3) -> list[BacklogItem]:
        """Mark items as in this run: the given ids, or the top `limit` open ones."""
        taken: list[BacklogItem] = []
        with locked_json(self.path, {"next": 1, "items": []}) as doc:
            rows = doc.get("items", [])
            if ids is None:
                open_rows = sorted((r for r in rows if r.get("status") == "open"),
                                   key=lambda r: (-int(r.get("priority", 0)),
                                                  int(str(r["id"])[1:])))
                chosen = open_rows[:max(0, limit)]
            else:
                chosen = [self._row(doc, i) for i in dict.fromkeys(ids)]
                busy = [r["id"] for r in chosen if r.get("status") != "open"]
                if busy:
                    raise BacklogError(f"not open: {', '.join(busy)}")
            now = time.time()
            for row in chosen:
                row.update(status="in_run", run_id=run_id, pending=None, updated_at=now)
                taken.append(BacklogItem(**row))
        return taken

    def in_run(self, run_id: str) -> list[BacklogItem]:
        """Items a run holds."""
        return [i for i in self.items() if i.run_id == run_id]

    def record_outcomes(self, run_id: str, cards: Sequence[CardOutcome]) -> list[BacklogItem]:
        """At run end: note each held item's outcome; leftover cards become new items.

        Returns the newly created items. Outcomes stay pending until `decide`.
        """
        now = time.time()
        created: list[BacklogItem] = []
        with locked_json(self.path, {"next": 1, "items": []}) as doc:
            rows = doc.setdefault("items", [])
            for row in rows:
                if row.get("run_id") != run_id or row.get("status") != "in_run":
                    continue
                mine = [c for c in cards if c.backlog_id == row["id"]]
                done = sum(c.column == "done" for c in mine)
                outcome: Outcome = ("done" if mine and done == len(mine)
                                    else "partial" if done else "not_started")
                summary = "; ".join(c.summary for c in mine if c.summary)[:400]
                row["pending"] = outcome
                row["updated_at"] = now
                row.setdefault("history", []).append({
                    "run_id": run_id, "outcome": outcome, "cards_done": done,
                    "cards_open": len(mine) - done, "summary": summary, "at": now})
            known = {str(r.get("title", "")).casefold() for r in rows
                     if r.get("status") in ("open", "in_run")}
            for card in cards:
                if card.backlog_id or card.column == "done":
                    continue
                key = card.title.strip().casefold()
                if not key or key in known:
                    continue
                known.add(key)
                item = BacklogItem(id=f"B{int(doc.get('next', 1))}", title=card.title.strip(),
                                   acceptance=list(card.acceptance), kind=card.kind
                                   if card.kind in KINDS else "code", size="M",
                                   source=f"run:{run_id}", created_at=now, updated_at=now,
                                   notes=f"left open by run {run_id} (was {card.column})")
                doc["next"] = int(doc.get("next", 1)) + 1
                rows.append(asdict(item))
                created.append(item)
        return created

    def decide(self, run_id: str, accepted: bool) -> list[BacklogItem]:
        """The run was accepted or rejected: finished items close, the rest reopen."""
        changed: list[BacklogItem] = []
        now = time.time()
        with locked_json(self.path, {"next": 1, "items": []}) as doc:
            for row in doc.get("items", []):
                if row.get("run_id") != run_id or row.get("status") != "in_run":
                    continue
                finished = accepted and row.get("pending") == "done"
                row["status"] = "done" if finished else "open"
                row["run_id"], row["pending"], row["updated_at"] = None, None, now
                for entry in reversed(row.get("history", [])):
                    if entry.get("run_id") == run_id:
                        entry["decision"] = "accepted" if accepted else "rejected"
                        break
                changed.append(BacklogItem(**row))
        return changed

    def release(self, run_id: str, reason: str) -> list[BacklogItem]:
        """The run ended with nothing to accept: its items go back to open."""
        changed: list[BacklogItem] = []
        with locked_json(self.path, {"next": 1, "items": []}) as doc:
            for row in doc.get("items", []):
                if row.get("run_id") == run_id and row.get("status") == "in_run":
                    row.update(status="open", run_id=None, pending=None, updated_at=time.time())
                    row.setdefault("history", []).append(
                        {"run_id": run_id, "outcome": "not_started", "decision": reason,
                         "at": time.time()})
                    changed.append(BacklogItem(**row))
        return changed

    # ── helpers ─────────────────────────────────────────────────────────────
    def _set_status(self, item_id: str, status: ItemStatus, allowed: tuple[str, ...],
                    note: str) -> BacklogItem:
        with locked_json(self.path, {"next": 1, "items": []}) as doc:
            row = self._row(doc, item_id)
            if row["status"] not in allowed:
                raise BacklogError(f"{item_id} is {row['status']}; cannot make it {status}")
            row["status"], row["updated_at"] = status, time.time()
            if note:
                row.setdefault("history", []).append({"decision": note, "at": time.time()})
            return BacklogItem(**row)

    @staticmethod
    def _row(doc: dict[str, Any], item_id: str) -> dict[str, Any]:  # Any: JSON rows
        for row in doc.get("items", []):
            if row.get("id") == item_id:
                return row
        raise BacklogError(f"unknown backlog item {item_id!r}")

    @staticmethod
    def _validated(fields: dict[str, Any]) -> dict[str, Any]:  # Any: caller-supplied values
        out: dict[str, Any] = {}
        for key, value in fields.items():
            if key == "title":
                title = str(value).strip()
                if len(title) < 3 or len(title) > 200:
                    raise BacklogError("title must be 3-200 characters")
                out[key] = title
            elif key in ("acceptance", "files_hint"):
                if not isinstance(value, (list, tuple)):
                    raise BacklogError(f"{key} must be a list")
                out[key] = _clean_lines(value)[:30]
            elif key == "kind":
                if value not in KINDS:
                    raise BacklogError(f"kind must be one of {', '.join(sorted(KINDS))}")
                out[key] = value
            elif key == "size":
                if value not in SIZES:
                    raise BacklogError("size must be S, M or L")
                out[key] = value
            elif key == "priority":
                if not isinstance(value, int) or isinstance(value, bool):
                    raise BacklogError("priority must be an integer")
                out[key] = value
            elif key == "notes":
                out[key] = str(value)[:2000]
        return out


def card_outcomes(cards: Iterable[Any], summaries: dict[str, str] | None = None
                  ) -> list[CardOutcome]:  # Any: core.workspace.Task (avoids an import cycle)
    """Board cards → CardOutcome (cards from the plan and board stories only)."""
    summaries = summaries or {}
    return [CardOutcome(title=str(getattr(c, "title", "") or getattr(c, "goal", "")),
                        column=str(getattr(c, "column", "todo")),
                        backlog_id=getattr(c, "backlog_id", None) or None,
                        acceptance=tuple(getattr(c, "acceptance_criteria", []) or ()),
                        kind=str(getattr(c, "kind", "code")),
                        summary=summaries.get(str(getattr(c, "task_id", "")), ""))
            for c in cards if getattr(c, "story_id", "") in ("plan", "board")]
