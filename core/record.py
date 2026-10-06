"""The project record: trace any id through the spec, plan, work and sessions, and export it.

Everything here is a pure function of what the project already stores (spec history in
`work.db`, approved drafts and the plan conversation, backlog item history, run states and
the project repo's git log). Nothing is written; `spec_files` only renders the files the
accept path may commit (plan P8).

    trace(data_root, project_id, "R3")      -> list[RecordEvent], oldest first
    export(data_root, project_id, include={"spec", "decisions"}, fmt="md")
    spec_files(data_root, project_id)       -> {"SPEC.md": ..., "docs/decisions/0004-x.md": ...}

Review verdicts: a requirement's checks keep the state and the session that set it
(`Check.state`, `Check.run_id`), so those show as `review` events. The per-card review
signals on a run's board are not read here.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.backlog import BacklogItem
from core.plan_drafts import Draft
from core.project_repo import ProjectRepoError, RunBranch, _git
from core.spec import Decision, Requirement, Spec, SpecError
from core.work_db import WorkDB

EventKind = Literal["spec", "decision", "plan", "work", "session", "review", "status"]
INCLUDE: tuple[str, ...] = ("spec", "decisions", "sessions", "plan", "rules", "costs")
_REQ = re.compile(r"^R[0-9]+$")
_CHECK = re.compile(r"^(R[0-9]+)\.[0-9]+$")
_DEC = re.compile(r"^D[0-9]+$")
_ITEM = re.compile(r"^B[0-9]+$")
_IDS_IN_TEXT = re.compile(r"\b(?:R[0-9]+(?:\.[0-9]+)?|D[0-9]+|B[0-9]+)\b")
_TEXT_CAP = 600
_COMMIT_CAP = 20
_FILES_SHOWN = 12

Row = dict[str, Any]  # Any: a JSON row from work.db


class RecordError(ValueError):
    """The ref is unknown, or an export option is not valid."""


@dataclass
class RecordEvent:
    """One moment in the life of a requirement, decision, item, session or file."""

    at: float
    kind: EventKind
    ref: str                       # the id traced (R3, D4, B7, a run id, a path)
    text: str
    by: str = ""
    links: list[str] = field(default_factory=list)   # other ids mentioned (re-trace targets)

    def to_dict(self) -> Row:
        """JSON form."""
        return asdict(self)


def _cap(text: object, n: int = _TEXT_CAP) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= n else flat[: n - 1] + "…"


def _when(at: float | None) -> str:
    if not at:
        return "?"
    return datetime.fromtimestamp(float(at), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].strip("-")
    return slug or "decision"


def _by(source: str) -> str:
    return "runner" if str(source).startswith("run:") else str(source or "human")


def _num(obj_id: str) -> int:
    return int(obj_id[1:]) if obj_id[1:].isdigit() else 0


def _more(files: list[str]) -> str:
    return ", ".join(files[:_FILES_SHOWN]) + (" …" if len(files) > _FILES_SHOWN else "")


class _Data:
    """One read of everything the record is built from."""

    def __init__(self, data_root: Path, project_id: str) -> None:
        self.project_dir = Path(data_root) / "projects" / project_id
        self.db = WorkDB(self.project_dir)
        self.doc = self.db.read_spec_doc()
        self.spec = Spec(self.doc)
        self.drafts = [Draft.from_dict(r) for r in self.db.read_drafts_doc().get("drafts", [])]
        self.messages = self.db.list_plan_messages(100_000)
        self.items = [BacklogItem(**r) for r in self.db.read_backlog_doc().get("items", [])
                      if isinstance(r, dict)]
        self.runs = sorted((RunBranch(**r) for r in self.db.read_runs_doc().values()
                            if isinstance(r, dict)), key=lambda r: r.submitted_at)
        self.run_by_id = {r.run_id: r for r in self.runs}
        self.repo = self.project_dir / "repo.git"

    def row(self, key: str, obj_id: str) -> Row:
        """The raw spec row (with its history)."""
        return next(r for r in self.doc.get(key, []) if r["id"] == obj_id)

    def creating_draft(self, obj_id: str) -> Draft | None:
        """The approved draft that created `obj_id` (R4, D2, B7)."""
        return next((d for d in self.drafts
                     if d.status == "approved" and obj_id in d.created.values()), None)

    def items_serving(self, req_id: str) -> list[BacklogItem]:
        return [i for i in self.items if req_id in i.serves]

    def runs_of(self, item: BacklogItem) -> list[str]:
        return list(dict.fromkeys(str(h["run_id"]) for h in item.history if h.get("run_id")))

    def run_files(self, run_ids: list[str]) -> list[str]:
        return sorted({f for r in dict.fromkeys(run_ids) if r in self.run_by_id
                       for f in self.run_by_id[r].files})

    # ── event builders ──────────────────────────────────────────────────────
    def plan_events(self, ref: str, draft_ids: list[str]) -> list[RecordEvent]:
        """The PM's messages of these drafts, each with the human message before it."""
        out: list[RecordEvent] = []
        seen: set[int] = set()
        for draft_id in dict.fromkeys(draft_ids):
            for i, m in enumerate(self.messages):
                if m.get("draft_id") != draft_id or m.get("author") != "pm":
                    continue
                before = next((p for p in reversed(self.messages[:i])
                               if p.get("author") == "human"), None)
                for msg in (before, m):
                    if msg is None or int(msg["id"]) in seen:
                        continue
                    seen.add(int(msg["id"]))
                    who = "human" if msg["author"] == "human" else "pm"
                    out.append(RecordEvent(
                        at=float(msg["at"]), kind="plan", ref=ref, by=who, links=[draft_id],
                        text=f"{'You' if who == 'human' else 'PM'} (plan, {draft_id}): "
                             f"{_cap(msg['text'])}"))
        return out

    def item_events(self, item: BacklogItem, ref: str) -> list[RecordEvent]:
        """An item's creation and history, as work events."""
        serves = f", serves {', '.join(item.serves)}" if item.serves else ""
        out = [RecordEvent(at=item.created_at, kind="work", ref=ref, by=_by(item.source),
                           text=f"{item.id} added: {item.title}{serves}",
                           links=[item.id, *item.serves])]
        for h in item.history:
            at = float(h.get("at") or 0.0)
            run_id = h.get("run_id")
            if h.get("outcome"):
                bits = [f"{item.id} in session {run_id}: {h['outcome']}"]
                if h.get("summary"):
                    bits.append(_cap(h["summary"], 200))
                if h.get("cost_usd"):
                    bits.append(f"${float(h['cost_usd']):.2f}")
                out.append(RecordEvent(at=at, kind="work", ref=ref, by="runner",
                                       text="; ".join(bits),
                                       links=[item.id, *([str(run_id)] if run_id else [])]))
            elif h.get("decision"):
                out.append(RecordEvent(at=at, kind="work", ref=ref, by="human",
                                       text=f"{item.id}: {_cap(h['decision'], 200)}",
                                       links=[item.id]))
        return out

    def run_events(self, run: RunBranch, ref: str, *, commits: bool = True) -> list[RecordEvent]:
        """A session's submit, its commits and the human's decision."""
        held = [i.id for i in self.items if run.run_id in self.runs_of(i)]
        out = [RecordEvent(
            at=run.submitted_at, kind="session", ref=ref, by="runner",
            text=f"Session {run.run_id} submitted: {run.commits} commit(s), "
                 f"{len(run.files)} file(s)" + (f", items {', '.join(held)}" if held else ""),
            links=[run.run_id, *held])]
        if run.decided_at:
            note = f": {_cap(run.note, 300)}" if run.note else ""
            out.append(RecordEvent(at=run.decided_at, kind="session", ref=ref, by="human",
                                   text=f"Session {run.run_id} {run.state}{note}",
                                   links=[run.run_id]))
        if commits:
            for sha, at, author, subject in self.commits(run):
                out.append(RecordEvent(at=at, kind="session", ref=ref, by=author,
                                       text=f"commit {sha[:8]} in {run.run_id}: {_cap(subject, 160)}",
                                       links=[run.run_id]))
        return out

    def review_events(self, ref: str, req: Requirement, *, run_id: str | None = None,
                      only: str | None = None) -> list[RecordEvent]:
        """Check verdicts of a requirement (optionally one check, or one session's)."""
        out = []
        for c in req.checks:
            run = self.run_by_id.get(c.run_id or "")
            if c.state == "unknown" or run is None or (only and c.id != only):
                continue
            if run_id and run.run_id != run_id:
                continue
            out.append(RecordEvent(
                at=run.decided_at or run.submitted_at, kind="review", ref=ref, by="reviewer",
                text=f"{c.id} {c.state} in session {run.run_id}: {_cap(c.text, 160)}",
                links=[run.run_id]))
        return out

    # ── git ─────────────────────────────────────────────────────────────────
    def commits(self, run: RunBranch, path: str | None = None) -> list[tuple[str, float, str, str]]:
        """(sha, time, author, subject) of the run's own commits, newest first, capped."""
        if not (self.repo / "HEAD").is_file() or not run.head or not run.base:
            return []
        args = ["log", f"-{_COMMIT_CAP}", "--format=%H%x1f%at%x1f%an%x1f%s",
                f"{run.base}..{run.head}"]
        if path:
            args += ["--", path]
        try:
            raw = _git(*args, cwd=self.repo)
        except ProjectRepoError:
            return []
        rows = []
        for line in raw.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                rows.append((parts[0], float(parts[1]), parts[2], parts[3]))
        return rows


def _finish(ref: str, events: list[RecordEvent], status: str) -> list[RecordEvent]:
    """Sort, fill `links` from ids in the text, and add the closing status event."""
    for e in events:
        e.links = [x for x in dict.fromkeys([*e.links, *_IDS_IN_TEXT.findall(e.text)]) if x != ref]
    events.sort(key=lambda e: e.at)
    last = events[-1].at if events else 0.0
    closing = RecordEvent(at=max(time.time(), last), kind="status", ref=ref, text=status)
    closing.links = [x for x in dict.fromkeys(_IDS_IN_TEXT.findall(status)) if x != ref]
    return [*events, closing]


def _item_state(items: list[BacklogItem]) -> str:
    return ", ".join(f"{i.id} ({i.status})" for i in items) or "none"


def _versions(row: Row, ref: str, kind: EventKind,
              describe: Callable[[Row, Row], str]) -> tuple[list[RecordEvent], list[str]]:
    """One event per history entry of a spec row, and the drafts they came from.

    History entry n holds the state *before* change n, so the change reads old vs next.
    """
    hist = row.get("history", [])
    events: list[RecordEvent] = []
    drafts: list[str] = []
    for n, old in enumerate(hist):
        new = hist[n + 1] if n + 1 < len(hist) else row
        draft = old.get("draft")
        if draft:
            drafts.append(str(draft))
        events.append(RecordEvent(
            at=float(old.get("at") or 0.0), kind=kind, ref=ref, by=str(old.get("by") or ""),
            text=f"{row['id']} v{int(old.get('version', n + 1)) + 1}: {describe(old, new)}"
                 + (f" (draft {draft})" if draft else ""),
            links=[str(draft)] if draft else []))
    return events, drafts


# ── requirement ─────────────────────────────────────────────────────────────
def _version_text(old: Row, new: Row) -> str:
    if "reason" in old:
        return f"dropped: {_cap(old['reason'], 300)}"
    bits: list[str] = []
    if old.get("text") != new.get("text"):
        bits.append(f'text "{_cap(old.get("text", ""), 120)}" → "{_cap(new.get("text", ""), 120)}"')
    for key in ("priority", "kind", "assumed"):
        if key in old and old.get(key) != new.get(key):
            bits.append(f"{key} {old.get(key)} → {new.get(key)}")
    before = {c["text"] for c in old.get("checks", [])}
    after = {c["text"] for c in new.get("checks", [])}
    bits += [f'check added {c["id"]}: {_cap(c["text"], 100)}'
             for c in new.get("checks", []) if c["text"] not in before]
    bits += [f'check removed {c["id"]}: {_cap(c["text"], 100)}'
             for c in old.get("checks", []) if c["text"] not in after]
    return "; ".join(bits) or "edited"


def _trace_requirement(d: _Data, ref: str, req: Requirement,
                       only_check: str | None) -> list[RecordEvent]:
    row = d.row("requirements", req.id)
    events: list[RecordEvent] = []
    drafts: list[str] = []
    made = d.creating_draft(req.id)
    if made:
        drafts.append(made.id)
        first = req.history[0] if req.history else row
        events.append(RecordEvent(
            at=made.closed_at or 0.0, kind="spec", ref=ref, by=made.approved_by or "",
            text=f"{req.id} v1 added ({first.get('priority')} {first.get('kind')}, "
                 f"{_by(req.source)}): {_cap(first.get('text', ''), 300)}",
            links=[made.id]))
    ev, dr = _versions(row, ref, "spec", _version_text)
    events += ev
    events += d.plan_events(ref, drafts + dr)
    for dec in d.spec.decisions:
        if req.id in dec.serves:
            at = d.creating_draft(dec.id)
            events.append(RecordEvent(at=(at.closed_at or 0.0) if at else 0.0, kind="decision",
                                      ref=ref, by=dec.decided_by, links=[dec.id],
                                      text=f"{dec.id} serves {req.id}: {dec.title} ({dec.status})"))
    items = d.items_serving(req.id)
    run_ids = [r for i in items for r in d.runs_of(i)]
    for item in items:
        events += d.item_events(item, ref)
    reviews = d.review_events(ref, req, only=only_check)
    events += reviews
    run_ids += [e.links[0] for e in reviews]
    for run_id in dict.fromkeys(run_ids):
        if run_id in d.run_by_id:
            events += d.run_events(d.run_by_id[run_id], ref, commits=False)
    met = sum(c.state == "met" for c in req.checks)
    status = (f"{req.id} is {req.status}, v{req.version}; {met}/{len(req.checks)} checks met; "
              f"items: {_item_state(items)}")
    files = d.run_files(run_ids)
    if files:
        status += f"; files touched: {_more(files)}"
    if only_check:
        status = f"{only_check} is {d.spec.check(only_check).state}. {status}"
    return _finish(ref, events, status)


# ── decision ────────────────────────────────────────────────────────────────
def _decision_change(old: Row, new: Row) -> str:
    if old.get("superseded_by"):
        return f"superseded by {old['superseded_by']}"
    if "dropped" in old:
        return "dropped" + (f": {_cap(old['dropped'], 300)}" if old.get("dropped") else "")
    return "edited"


def _trace_decision(d: _Data, ref: str, dec: Decision) -> list[RecordEvent]:
    row = d.row("decisions", dec.id)
    events: list[RecordEvent] = []
    drafts: list[str] = []
    made = d.creating_draft(dec.id)
    rule = next((r for r in d.spec.rules if r.source == dec.id), None)
    if made:
        drafts.append(made.id)
        extra = f"; supersedes {dec.supersedes}" if dec.supersedes else ""
        extra += f"; became {rule.id}" if rule else ""
        events.append(RecordEvent(
            at=made.closed_at or 0.0, kind="decision", ref=ref, by=made.approved_by or "",
            links=[made.id, *dec.serves],
            text=f"{dec.id} accepted ({dec.decided_by}): {_cap(dec.title, 120)} — "
                 f"{_cap(dec.choice, 160)} because {_cap(dec.reason, 240)}{extra}"))
    ev, dr = _versions(row, ref, "decision", _decision_change)
    events += ev
    events += d.plan_events(ref, drafts + dr)
    items = [i for i in d.items if made and i.id in made.created.values()]
    for item in items:
        events += d.item_events(item, ref)
    for run_id in dict.fromkeys(r for i in items for r in d.runs_of(i)):
        if run_id in d.run_by_id:
            events += d.run_events(d.run_by_id[run_id], ref, commits=False)
    status = (f"{dec.id} is {dec.status}, v{dec.version}, "
              f"{'binding' if dec.binding else 'not binding'}, decided by {dec.decided_by}; "
              f"serves {', '.join(dec.serves) or 'nothing'}; items: {_item_state(items)}")
    return _finish(ref, events, status)


# ── item, session, file ─────────────────────────────────────────────────────
def _trace_item(d: _Data, ref: str, item: BacklogItem) -> list[RecordEvent]:
    events = d.item_events(item, ref)
    made = d.creating_draft(item.id)
    if made:
        events += d.plan_events(ref, [made.id])
    runs = d.runs_of(item)
    for rid in runs:
        if rid in d.run_by_id:
            events += d.run_events(d.run_by_id[rid], ref)
    for req in d.spec.requirements:
        if req.id in item.serves:
            for rid in runs:
                events += d.review_events(ref, req, run_id=rid)
    status = f"{item.id} is {item.status}" + (f", serves {', '.join(item.serves)}" if item.serves else "")
    files = d.run_files(runs)
    if files:
        status += f"; files touched: {_more(files)}"
    return _finish(ref, events, status)


def _trace_run(d: _Data, ref: str, run: RunBranch) -> list[RecordEvent]:
    events = d.run_events(run, ref)
    held = [i for i in d.items if run.run_id in d.runs_of(i)]
    for item in held:
        events += [e for e in d.item_events(item, ref)
                   if run.run_id in e.links or e.text.startswith(f"{item.id} added")]
    for req in d.spec.requirements:
        events += d.review_events(ref, req, run_id=run.run_id)
    if run.check:
        events.append(RecordEvent(at=run.submitted_at, kind="review", ref=ref, by="checks",
                                  text=f"check: {_cap(json.dumps(run.check, default=str), 240)}"))
    status = (f"Session {run.run_id} is {run.state}; {run.commits} commit(s); "
              f"items: {_item_state(held)}" + (f"; files: {_more(run.files)}" if run.files else ""))
    return _finish(ref, events, status)


def _trace_file(d: _Data, ref: str) -> list[RecordEvent]:
    events: list[RecordEvent] = []
    touched: list[str] = []
    for run in d.runs:
        commits = d.commits(run, ref)
        if not commits:
            continue
        touched.append(run.run_id)
        events += [RecordEvent(at=at, kind="session", ref=ref, by=author, links=[run.run_id],
                               text=f"commit {sha[:8]} in {run.run_id}: {_cap(subject, 160)}")
                   for sha, at, author, subject in commits]
        events.append(RecordEvent(at=run.submitted_at, kind="session", ref=ref, by="runner",
                                  text=f"Session {run.run_id} changed {ref} ({run.state})",
                                  links=[run.run_id]))
        if run.decided_at:
            events.append(RecordEvent(at=run.decided_at, kind="session", ref=ref, by="human",
                                      links=[run.run_id],
                                      text=f"Session {run.run_id} {run.state}"
                                           + (f": {_cap(run.note, 200)}" if run.note else "")))
    return _finish(ref, events, f"{ref} was changed by {len(touched)} session(s)"
                   + (f": {', '.join(touched)}" if touched else ""))


def trace(data_root: Path, project_id: str, ref: str) -> list[RecordEvent]:
    """How `ref` came to be and what it touched, oldest first, ending with a status event.

    `ref` is a requirement (R3) or check (R3.1), a decision (D4), a backlog item (B7), a
    session's run id, or a file path in the project repo. Raises RecordError for an unknown
    id; a path nothing touched gives only the status event.
    """
    ref = ref.strip()
    if not ref:
        raise RecordError("give an id, a session or a file to trace")
    d = _Data(data_root, project_id)
    try:
        if _REQ.match(ref):
            return _trace_requirement(d, ref, d.spec.requirement(ref), None)
        found = _CHECK.match(ref)
        if found:
            d.spec.check(ref)
            return _trace_requirement(d, ref, d.spec.requirement(found.group(1)), ref)
        if _DEC.match(ref):
            return _trace_decision(d, ref, d.spec.decision(ref))
    except SpecError as e:
        raise RecordError(str(e)) from None
    if _ITEM.match(ref):
        item = next((i for i in d.items if i.id == ref), None)
        if item is None:
            raise RecordError(f"unknown backlog item {ref!r}")
        return _trace_item(d, ref, item)
    if ref in d.run_by_id:
        return _trace_run(d, ref, d.run_by_id[ref])
    if ref.startswith("-") or ".." in Path(ref).parts:
        raise RecordError(f"{ref!r} is not a path inside the project")
    return _trace_file(d, ref)


# ── export ──────────────────────────────────────────────────────────────────
def _req_block(d: _Data, req: Row) -> list[str]:
    r = Requirement.from_dict(req)
    flags = "".join([", assumed" if r.assumed else "", ", experiment" if r.experiment else "",
                     ", dropped" if r.status == "dropped" else ""])
    made = d.creating_draft(r.id)
    who = f"; added by {made.approved_by} in {made.id}, {_when(made.closed_at)}" if made else ""
    lines = [f"### {r.id}. {r.text}", f"{r.kind}, {r.priority}{flags}; version {r.version}{who}", ""]
    for c in r.checks:
        extra = f" ({c.state}, session {c.run_id})" if c.state == "unmet" and c.run_id else (
            " (unmet)" if c.state == "unmet" else "")
        lines.append(f"- [{'x' if c.state == 'met' else ' '}] {c.id} {c.text}{extra}")
    items = d.items_serving(r.id)
    if items:
        lines += ["", "Work: " + "; ".join(f"{i.id} {i.title} ({i.status})" for i in items)]
    if r.history:
        lines += ["", "History:"]
        for n, old in enumerate(r.history):
            nxt = r.history[n + 1] if n + 1 < len(r.history) else req
            lines.append(f"- v{int(old.get('version', n + 1)) + 1}, {_when(old.get('at'))}, "
                         f"{old.get('by', '?')}"
                         f"{', ' + str(old['draft']) if old.get('draft') else ''}: "
                         f"{_version_text(old, nxt)}")
    lines.append("")
    return lines


def _decision_block(d: _Data, dec: Decision) -> list[str]:
    made = d.creating_draft(dec.id)
    lines = [f"### {dec.id}. {dec.title}",
             f"{dec.status}{', binding' if dec.binding else ''}; decided by {dec.decided_by}"
             + (f", approved by {made.approved_by} in {made.id}, {_when(made.closed_at)}"
                if made else "") + f"; version {dec.version}",
             f"Choice: {dec.choice}", f"Reason: {dec.reason}"]
    if dec.serves:
        lines.append(f"Serves: {', '.join(dec.serves)}")
    if dec.supersedes:
        lines.append(f"Supersedes: {dec.supersedes}")
    if dec.history:
        lines += ["", "History:"]
        lines += [f"- {_when(h.get('at'))}, {h.get('by', '?')}: {_decision_change(h, {})}"
                  for h in dec.history]
    lines.append("")
    return lines


def _session_rows(d: _Data) -> list[Row]:
    out = []
    for run in d.runs:
        held = [i for i in d.items if run.run_id in d.runs_of(i)]
        reviews = [{"check": c.id, "state": c.state, "text": c.text}
                   for req in d.spec.requirements for c in req.checks
                   if c.run_id == run.run_id and c.state != "unknown"]
        cost = round(sum(float(h.get("cost_usd") or 0.0) for i in held for h in i.history
                         if h.get("run_id") == run.run_id), 4)
        out.append({"run_id": run.run_id, "state": run.state, "submitted_at": run.submitted_at,
                    "decided_at": run.decided_at, "note": run.note, "commits": run.commits,
                    "files": run.files, "items": [i.id for i in held], "reviews": reviews,
                    "cost_usd": cost})
    return out


def export(data_root: Path, project_id: str, *, include: set[str],
           fmt: Literal["md", "json"] = "md") -> str:
    """The record as one document. `include` is drawn from spec, decisions, sessions, plan,
    rules and costs. Spec and decisions carry their version histories."""
    bad = sorted(include - set(INCLUDE))
    if bad:
        raise RecordError(f"unknown section(s): {', '.join(bad)}")
    if fmt not in ("md", "json"):
        raise RecordError(f"unknown format {fmt!r}")
    d = _Data(data_root, project_id)
    sessions = _session_rows(d)
    plan_usd = round(sum(float(m.get("cost_usd") or 0.0) for m in d.messages), 4)
    data: Row = {"project_id": project_id, "spec_version": d.spec.version}
    if "spec" in include:
        data["requirements"] = d.doc.get("requirements", [])
    if "decisions" in include:
        data["decisions"] = d.doc.get("decisions", [])
    if "rules" in include:
        data["rules"] = d.doc.get("rules", [])
    if "sessions" in include:
        data["sessions"] = sessions
    if "plan" in include:
        data["plan"] = d.messages
    if "costs" in include:
        data["costs"] = {"sessions": {s["run_id"]: s["cost_usd"] for s in sessions},
                         "plan_usd": plan_usd,
                         "total_usd": round(sum(s["cost_usd"] for s in sessions) + plan_usd, 4)}
    if fmt == "json":
        return json.dumps(data, indent=2, sort_keys=True, default=str)
    lines = [f"# Project record: {project_id}", "", f"Spec version {d.spec.version}.", ""]
    if "spec" in include:
        lines += ["## Requirements", ""]
        for r in d.doc.get("requirements", []):
            lines += _req_block(d, r)
    if "decisions" in include:
        lines += ["## Decisions", ""]
        for dec in d.spec.decisions:
            lines += _decision_block(d, dec)
    if "rules" in include:
        lines += ["## Rules", ""]
        lines += [f"- {r.id} ({r.strength}, {r.status}, from {r.source}): {r.text}"
                  for r in d.spec.rules] + [""]
    if "sessions" in include:
        lines += ["## Sessions and reviews", ""]
        for s in sessions:
            note = f": {s['note']}" if s["note"] else ""
            lines.append(f"- {s['run_id']} {s['state']} ({_when(s['submitted_at'])}, "
                         f"{s['commits']} commit(s), items {', '.join(s['items']) or 'none'}){note}")
            lines += [f"    - {r['check']} {r['state']}: {r['text']}" for r in s["reviews"]]
        lines.append("")
    if "plan" in include:
        lines += ["## Plan conversations", ""]
        lines += [f"- {_when(m['at'])} {m['author']}"
                  f"{' [' + m['draft_id'] + ']' if m.get('draft_id') else ''}: "
                  f"{_cap(m['text'], 1500)}" for m in d.messages] + [""]
    if "costs" in include:
        c = data["costs"]
        lines += ["## Costs", "", f"- Plan conversation: ${c['plan_usd']:.2f}"]
        lines += [f"- Session {k}: ${v:.2f}" for k, v in c["sessions"].items()]
        lines += [f"- Total: ${c['total_usd']:.2f}", ""]
    return "\n".join(lines).rstrip() + "\n"


# ── files kept in the repo ──────────────────────────────────────────────────
def _adr_path(dec: Decision) -> str:
    return f"docs/decisions/{_num(dec.id):04d}-{_slug(dec.title)}.md"


def _adr(d: _Data, dec: Decision) -> str:
    made = d.creating_draft(dec.id)
    served = [r for r in d.spec.requirements if r.id in dec.serves]
    approved = (made.approved_by if made else None) or "unknown"
    lines = [f"# {dec.id}. {dec.title}", "",
             f"Status: {dec.status}{' (binding)' if dec.binding else ''}"
             + (f", supersedes {dec.supersedes}" if dec.supersedes else ""),
             f"Proposed by: {'the project manager' if dec.decided_by == 'pm' else 'a person'}",
             f"Approved by: {approved}" + (f" ({_when(made.closed_at)})" if made else ""), "",
             "## Context", ""]
    lines += [f"- {r.id}: {r.text}" for r in served] or ["- No requirement is attached."]
    lines += ["", "## Decision", "", dec.choice, "", "## Consequences", "", dec.reason, ""]
    rule = next((r for r in d.spec.rules if r.source == dec.id), None)
    if dec.binding and rule:
        lines += [f"This is binding: it is a project rule ({rule.id}) that reviewers check.", ""]
    if dec.history:
        lines += ["## History", ""]
        lines += [f"- {_when(h.get('at'))}, {h.get('by', '?')}: {_decision_change(h, {})}"
                  for h in dec.history] + [""]
    lines += ["## Serves", ""] + ([f"- {r}" for r in dec.serves] or ["- nothing yet"]) + [""]
    items = [i for i in d.items if made and i.id in made.created.values()]
    evidence = []
    for run_id in dict.fromkeys(r for i in items for r in d.runs_of(i)):
        run = d.run_by_id.get(run_id)
        if run:
            evidence.append(f"- Session {run_id}: {run.state}" + (f", {run.note}" if run.note else ""))
    lines += ["## Evidence", ""] + (evidence or ["- No session has worked on it yet."]) + [""]
    return "\n".join(lines)


def spec_files(data_root: Path, project_id: str) -> dict[str, str]:
    """`SPEC.md` plus one ADR per decision at `docs/decisions/NNNN-<slug>.md` (D4 → 0004)."""
    d = _Data(data_root, project_id)
    lines = ["# Spec", "", f"Version {d.spec.version}. Kept by GuudFlow; edit it in Studio.", "",
             "## Requirements", ""]
    if not d.doc.get("requirements"):
        lines += ["No requirements yet.", ""]
    for r in d.doc.get("requirements", []):
        lines += _req_block(d, r)
    lines += ["## Decisions", ""]
    if not d.spec.decisions:
        lines += ["No decisions yet."]
    lines += [f"- [{dec.id}]({_adr_path(dec)}) {dec.title} ({dec.status}, decided by {dec.decided_by})"
              for dec in d.spec.decisions]
    rules = [r for r in d.spec.rules if r.status == "active"]
    if rules:
        lines += ["", "## Rules", ""]
        lines += [f"- {r.id} ({r.strength}, from {r.source}): {r.text}" for r in rules]
    files = {"SPEC.md": "\n".join(lines).rstrip() + "\n"}
    for dec in d.spec.decisions:
        files[_adr_path(dec)] = _adr(d, dec)
    return files
