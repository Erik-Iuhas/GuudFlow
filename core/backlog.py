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
Stored in the project's SQLite work database (`core.work_db`); every change is one
transaction, so the API and the worker never interleave writes.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.work_db import WorkDB

ItemStatus = Literal["open", "in_run", "done", "dropped"]
Outcome = Literal["done", "partial", "not_started"]
KINDS = frozenset({"code", "test", "docs", "research", "visual"})
SIZES = frozenset({"S", "M", "L"})
_ID = re.compile(r"^B[0-9]+$")
_STATUS_ORDER = {"in_run": 0, "open": 1, "done": 2, "dropped": 3}
_EDITABLE = frozenset({"title", "acceptance", "kind", "size", "priority", "notes", "files_hint",
                       "depends_on"})


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
    depends_on: list[str] = field(default_factory=list)   # items that must be done first
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
        after = f", after {', '.join(self.depends_on)}" if self.depends_on else ""
        lines = [f"- **{self.id}** {self.title} ({self.kind}, size {self.size}{after})"]
        lines += [f"    - [ ] {a}" for a in self.acceptance]
        if self.notes:
            lines.append(f"    - notes: {self.notes}")
        return "\n".join(lines)

    def total_cost(self) -> float:
        """Gateway spend on this item across every run that worked on it (USD)."""
        return round(sum(float(h.get("cost_usd") or 0.0) for h in self.history), 4)

    def review_note(self) -> str | None:
        """The human's note on the latest decided run that held this item (human text)."""
        last = next((h for h in reversed(self.history) if h.get("decision") in
                     ("accepted", "rejected")), None)
        if not last or not last.get("note"):
            return None
        return f"when {last['decision']} run {last.get('run_id')}: {last['note']}"

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
    cost_usd: float = 0.0


def _clean_lines(values: Iterable[object]) -> list[str]:
    return [str(v).strip() for v in values if str(v).strip()]


class Backlog:
    """The backlog of one project."""

    def __init__(self, data_root: Path, project_id: str) -> None:
        self.db = WorkDB(Path(data_root) / "projects" / project_id)

    # ── reads ───────────────────────────────────────────────────────────────
    def items(self, status: ItemStatus | None = None) -> list[BacklogItem]:
        """Items in working order: in-run first, then open by priority, then the rest."""
        doc = self.db.read_backlog_doc()
        items = [BacklogItem(**row) for row in doc.get("items", []) if isinstance(row, dict)]
        if status is not None:
            items = [i for i in items if i.status == status]
        return sorted(items, key=lambda i: (_STATUS_ORDER.get(i.status, 9), -i.priority,
                                            int(i.id[1:]) if _ID.match(i.id) else 0))

    def render_for_meta(self, limit: int = 12) -> str:
        """The open work for the meta-agent's team proposal ("" when there is none).

        Items a run created carry agent-written titles, so they are wrapped as
        untrusted data; human items are shown as they are.
        """
        from core.harness.brief import wrap_untrusted

        items = [i for i in self.items() if i.status in ("in_run", "open")][:limit]
        lines: list[str] = []
        for i in items:
            state = "in a run" if i.status == "in_run" else f"priority {i.priority}"
            text = f"{i.title} ({i.kind}, size {i.size}, {len(i.acceptance)} checks, {state})"
            if i.source.startswith("run:"):
                text = wrap_untrusted(f"backlog item {i.id} (from a run)", text)
            lines.append(f"- {i.id}: {text}")
        return "\n".join(lines)

    def get(self, item_id: str) -> BacklogItem:
        """One item; BacklogError if unknown."""
        for item in self.items():
            if item.id == item_id:
                return item
        raise BacklogError(f"unknown backlog item {item_id!r}")

    # ── human edits ─────────────────────────────────────────────────────────
    def add(self, title: str, acceptance: Sequence[str] = (), *, kind: str = "code",
            size: str = "M", priority: int = 0, notes: str = "",
            files_hint: Sequence[str] = (), depends_on: Sequence[str] = (),
            source: str = "human") -> BacklogItem:
        """Add an open item."""
        fields = self._validated({"title": title, "acceptance": list(acceptance), "kind": kind,
                                  "size": size, "priority": priority, "notes": notes,
                                  "files_hint": list(files_hint),
                                  "depends_on": list(depends_on)})
        now = time.time()
        with self.db.backlog_doc() as doc:
            _check_deps(doc, f"B{int(doc.get('next', 1))}", fields["depends_on"])
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
        with self.db.backlog_doc() as doc:
            row = self._row(doc, item_id)
            if row["status"] == "done":
                raise BacklogError(f"{item_id} is done; add a new item instead")
            if "depends_on" in clean:
                _check_deps(doc, item_id, clean["depends_on"])
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
        """Mark items as in this run: the given ids, or the top `limit` open ones.

        An item whose dependencies are not done is skipped by "top" and refused in an
        explicit selection unless the selection also takes those dependencies.
        """
        taken: list[BacklogItem] = []
        with self.db.backlog_doc() as doc:
            rows = doc.get("items", [])
            done = {r["id"] for r in rows if r.get("status") == "done"}
            if ids is None:
                open_rows = sorted((r for r in rows if r.get("status") == "open"
                                    and set(r.get("depends_on") or []) <= done),
                                   key=lambda r: (-int(r.get("priority", 0)),
                                                  int(str(r["id"])[1:])))
                chosen = open_rows[:max(0, limit)]
            else:
                chosen = _check_selection(doc, ids)
            now = time.time()
            for row in chosen:
                row.update(status="in_run", run_id=run_id, pending=None, updated_at=now)
                taken.append(BacklogItem(**row))
        return taken

    def check_selection(self, ids: Sequence[str]) -> None:
        """Raise BacklogError unless these items could start a run together now."""
        _check_selection(self.db.read_backlog_doc(), ids)

    def in_run(self, run_id: str) -> list[BacklogItem]:
        """Items a run holds."""
        return [i for i in self.items() if i.run_id == run_id]

    def record_outcomes(self, run_id: str, cards: Sequence[CardOutcome]) -> list[BacklogItem]:
        """At run end: note each held item's outcome; leftover cards become new items.

        Returns the newly created items. Outcomes stay pending until `decide`.
        """
        now = time.time()
        created: list[BacklogItem] = []
        with self.db.backlog_doc() as doc:
            rows = doc.setdefault("items", [])
            for row in rows:
                if row.get("run_id") != run_id or row.get("status") != "in_run":
                    continue
                mine = [c for c in cards if c.backlog_id == row["id"]]
                done = sum(c.column == "done" for c in mine)
                outcome: Outcome = ("done" if mine and done == len(mine)
                                    else "partial" if done else "not_started")
                summary = "; ".join(c.summary for c in mine if c.summary)[:400]
                cost = round(sum(c.cost_usd for c in mine), 4)
                row["pending"] = outcome
                row["updated_at"] = now
                row.setdefault("history", []).append({
                    "run_id": run_id, "outcome": outcome, "cards_done": done,
                    "cards_open": len(mine) - done, "summary": summary, "cost_usd": cost,
                    "at": now})
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

    def decide(self, run_id: str, accepted: bool, note: str = "") -> list[BacklogItem]:
        """The run was accepted or rejected: finished items close, the rest reopen.

        The human's `note` is kept on each item's history entry for that run and is
        shown to the next run that works on the item (plan §14.2).
        """
        changed: list[BacklogItem] = []
        now = time.time()
        with self.db.backlog_doc() as doc:
            for row in doc.get("items", []):
                if row.get("run_id") != run_id or row.get("status") != "in_run":
                    continue
                finished = accepted and row.get("pending") == "done"
                row["status"] = "done" if finished else "open"
                row["run_id"], row["pending"], row["updated_at"] = None, None, now
                for entry in reversed(row.get("history", [])):
                    if entry.get("run_id") == run_id:
                        entry["decision"] = "accepted" if accepted else "rejected"
                        if note.strip():
                            entry["note"] = note.strip()[:2000]
                        break
                changed.append(BacklogItem(**row))
        return changed

    def release(self, run_id: str, reason: str) -> list[BacklogItem]:
        """The run ended with nothing to accept: its items go back to open."""
        changed: list[BacklogItem] = []
        with self.db.backlog_doc() as doc:
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
        with self.db.backlog_doc() as doc:
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
            elif key == "depends_on":
                if not isinstance(value, (list, tuple)) or not all(
                        isinstance(v, str) and _ID.match(v) for v in value):
                    raise BacklogError("depends_on must be a list of item ids like B3")
                out[key] = list(dict.fromkeys(value))
        return out


def _check_selection(doc: dict[str, Any], ids: Sequence[str]
                     ) -> list[dict[str, Any]]:  # Any: JSON rows
    """Rows for `ids`: every one open, and its dependencies done or selected too."""
    rows = {str(r["id"]): r for r in doc.get("items", [])}
    unknown = [i for i in ids if i not in rows]
    if unknown:
        raise BacklogError(f"unknown backlog item {unknown[0]!r}")
    chosen = [rows[i] for i in dict.fromkeys(ids)]
    busy = [r["id"] for r in chosen if r.get("status") != "open"]
    if busy:
        raise BacklogError(f"not open: {', '.join(busy)}")
    done = {k for k, r in rows.items() if r.get("status") == "done"}
    picked = {r["id"] for r in chosen}
    waiting = [f"{r['id']} needs {', '.join(sorted(missing))}" for r in chosen
               if (missing := set(r.get("depends_on") or []) - done - picked)]
    if waiting:
        raise BacklogError("dependencies not done: " + "; ".join(waiting))
    return chosen


def _check_deps(doc: dict[str, Any], item_id: str, deps: Sequence[str]) -> None:  # Any: JSON
    """Dependencies must exist, not include the item, and not form a cycle."""
    graph = {str(r["id"]): list(r.get("depends_on") or []) for r in doc.get("items", [])}
    unknown = [d for d in deps if d not in graph]
    if unknown:
        raise BacklogError(f"unknown dependencies: {', '.join(unknown)}")
    if item_id in deps:
        raise BacklogError(f"{item_id} cannot depend on itself")
    graph[item_id] = list(deps)
    stack, seen = list(deps), set()
    while stack:
        node = stack.pop()
        if node == item_id:
            raise BacklogError(f"dependency cycle through {item_id}")
        if node not in seen:
            seen.add(node)
            stack.extend(graph.get(node, []))


def card_outcomes(cards: Iterable[Any], summaries: dict[str, str] | None = None,
                  costs: dict[str, float] | None = None
                  ) -> list[CardOutcome]:  # Any: core.workspace.Task (avoids an import cycle)
    """Board cards → CardOutcome (cards from the plan and board stories only)."""
    summaries = summaries or {}
    costs = costs or {}
    return [CardOutcome(title=str(getattr(c, "title", "") or getattr(c, "goal", "")),
                        column=str(getattr(c, "column", "todo")),
                        backlog_id=getattr(c, "backlog_id", None) or None,
                        acceptance=tuple(getattr(c, "acceptance_criteria", []) or ()),
                        kind=str(getattr(c, "kind", "code")),
                        summary=summaries.get(str(getattr(c, "task_id", "")), ""),
                        cost_usd=float(costs.get(str(getattr(c, "task_id", "")), 0.0)))
            for c in cards if getattr(c, "story_id", "") in ("plan", "board")]
