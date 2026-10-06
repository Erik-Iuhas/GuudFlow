"""Drafts of spec and backlog changes, and the plan conversation (project-flow plan §3).

Every spec change goes through a draft. The project manager proposes a `PlanTurn` (a reply, up
to 20 typed `Change`s and questions); `validate_turn` checks it; `PlanDrafts.merge_pm_turn`
puts the changes into the project's one open draft; a person ticks or rejects each change;
`PlanDrafts.approve` applies the ticked ones in a single `work.db` transaction (spec, drafts and
backlog together). A human's direct edit is `apply_direct`: a one-change draft approved on the
spot, which also records the edited fields as pinned so the PM cannot overwrite them.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from core.backlog import BacklogError, _check_deps, add_to_doc, update_in_doc
from core.spec import (
    REQ_ID, Check, Decision, Requirement, Rule, Spec, SpecError,
    find_row, highest_check_number, make_checks, next_id, sync_decision_rule)
from core.work_db import WorkDB, WorkTxn

MAX_CHANGES = 20
MIN_REASON = 20
_REF = re.compile(r"^new:[0-9]+$")
_BACKLOG_ID = re.compile(r"^B[0-9]+$")
_DECISION_ID = re.compile(r"^D[0-9]+$")
_DECISION_OPS = ("add_decision", "supersede_decision")

ReqKind = Literal["feature", "constraint", "quality", "non_goal"]
ReqPriority = Literal["must", "should", "could", "wont"]
ItemKind = Literal["code", "test", "docs", "research", "visual"]
ItemSize = Literal["S", "M", "L"]


class PlanDraftError(ValueError):
    """A draft operation was refused (wrong state, unticked changes, a change that failed)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddRequirement(_Model):
    """Add a requirement; it needs at least one check unless it is a non-goal."""

    op: Literal["add_requirement"]
    ref: str = Field(pattern=_REF.pattern)
    text: str = Field(min_length=3)
    kind: ReqKind
    priority: ReqPriority
    checks: list[str] = Field(default_factory=list)
    assumed: bool = False
    experiment: bool = False


class EditRequirement(_Model):
    """Change fields of a requirement (`checks` is the full new list)."""

    op: Literal["edit_requirement"]
    id: str = Field(pattern=REQ_ID.pattern)
    text: str | None = None
    priority: ReqPriority | None = None
    kind: ReqKind | None = None
    checks: list[str] | None = None
    assumed: bool | None = None
    experiment: bool | None = None


class DropRequirement(_Model):
    """Drop a requirement."""

    op: Literal["drop_requirement"]
    id: str = Field(pattern=REQ_ID.pattern)
    reason: str = Field(min_length=3)


class AddDecision(_Model):
    """Record a decision; the reason is at least 20 characters."""

    op: Literal["add_decision"]
    ref: str = Field(pattern=_REF.pattern)
    title: str = Field(min_length=3)
    choice: str = Field(min_length=1)
    reason: str
    serves: list[str] = Field(default_factory=list)
    binding: bool = False


class SupersedeDecision(_Model):
    """Replace decision `id` with a new one."""

    op: Literal["supersede_decision"]
    id: str = Field(pattern=_DECISION_ID.pattern)
    ref: str = Field(pattern=_REF.pattern)
    title: str = Field(min_length=3)
    choice: str = Field(min_length=1)
    reason: str
    serves: list[str] = Field(default_factory=list)
    binding: bool = False


class AddItem(_Model):
    """Add a backlog item; `serves` copies those requirements' checks into its acceptance."""

    op: Literal["add_item"]
    ref: str = Field(pattern=_REF.pattern)
    title: str = Field(min_length=3)
    acceptance: list[str] = Field(default_factory=list)
    serves: list[str] = Field(default_factory=list)
    kind: ItemKind = "code"
    size: ItemSize = "M"
    depends_on: list[str] = Field(default_factory=list)   # B ids or refs


class EditItem(_Model):
    """Edit editable fields of a backlog item."""

    op: Literal["edit_item"]
    id: str = Field(pattern=_BACKLOG_ID.pattern)
    title: str | None = None
    acceptance: list[str] | None = None
    kind: ItemKind | None = None
    size: ItemSize | None = None
    priority: int | None = None
    notes: str | None = None
    files_hint: list[str] | None = None
    depends_on: list[str] | None = None
    serves: list[str] | None = None


class AddRule(_Model):
    """Add a project rule (QUESTION: `ref` is optional and not in plan §3)."""

    op: Literal["add_rule"]
    text: str = Field(min_length=3)
    applies_to: list[str] = Field(default_factory=lambda: ["*"])
    strength: Literal["binding", "preference"] = "binding"
    ref: str | None = Field(default=None, pattern=_REF.pattern)


class DropRule(_Model):
    """Drop a rule that no decision owns."""

    op: Literal["drop_rule"]
    id: str = Field(pattern=r"^rule-[0-9]+$")
    reason: str = Field(min_length=3)


Change = Annotated[
    AddRequirement | EditRequirement | DropRequirement | AddDecision | SupersedeDecision
    | AddItem | EditItem | AddRule | DropRule, Field(discriminator="op")]
_CHANGE_ADAPTER: TypeAdapter[Any] = TypeAdapter(Change)  # Any: the union members above
_REF_KIND = {"add_requirement": "requirement", "add_decision": "decision",
             "supersede_decision": "decision", "add_item": "item", "add_rule": "rule"}


class PlanTurn(_Model):
    """The project manager's tool payload for one turn."""

    reply: str
    changes: list[Change] = Field(default_factory=list, max_length=MAX_CHANGES)
    questions: list[str] = Field(default_factory=list)


def parse_change(row: dict[str, Any]) -> Any:  # Any: one member of the `Change` union
    """A stored change dict → its model."""
    return _CHANGE_ADAPTER.validate_python(row)


def _set_fields(change: Any, skip: tuple[str, ...] = ("op", "id")) -> set[str]:  # Any: Change
    """Fields an edit sets (not None)."""
    return {k for k, v in change.model_dump().items() if k not in skip and v is not None}


# ── validation ──────────────────────────────────────────────────────────────
def validate_turn(turn: PlanTurn, spec: Spec, backlog_doc: dict[str, Any]  # Any: JSON doc
                  ) -> list[str]:
    """Problems with a PM turn, one line each (empty when it is fine).

    Checks that ids exist, refs resolve within the turn, dependencies have no cycle, a
    decision's reason is long enough, a requirement has a check (unless a non-goal) and no
    edit touches a field a human pinned.
    """
    problems: list[str] = []
    refs: dict[str, str] = {}
    for n, ch in enumerate(turn.changes, 1):
        ref = getattr(ch, "ref", None)
        if ref:
            if ref in refs:
                problems.append(f"change {n} ({ch.op}): ref {ref} is used twice")
            refs[ref] = _REF_KIND[ch.op]
    items = {str(r["id"]): r for r in backlog_doc.get("items", [])}
    sim: dict[str, Any] = {"items": [{"id": i, "depends_on": list(r.get("depends_on") or [])}
                                     for i, r in items.items()]
                           + [{"id": r, "depends_on": []} for r, k in refs.items()
                              if k == "item"]}

    def serves_ok(n: int, ch: Any, serves: list[str]) -> None:  # Any: Change
        for s in serves:
            if _REF.match(s):
                if refs.get(s) != "requirement":
                    problems.append(f"change {n} ({ch.op}): serves {s}, which is not a "
                                    f"requirement added in this turn")
            elif not REQ_ID.match(s):
                problems.append(f"change {n} ({ch.op}): serves {s!r} is not a requirement id")
            else:
                try:
                    if spec.requirement(s).status != "active":
                        problems.append(f"change {n} ({ch.op}): {s} is dropped")
                except SpecError:
                    problems.append(f"change {n} ({ch.op}): unknown requirement {s}")

    def deps_ok(n: int, ch: Any, item_id: str, deps: list[str]) -> None:  # Any: Change
        bad = False
        for d in deps:
            if _REF.match(d):
                if refs.get(d) != "item":
                    problems.append(f"change {n} ({ch.op}): depends_on {d} is not an item "
                                    f"added in this turn")
                    bad = True
            elif d not in items:
                problems.append(f"change {n} ({ch.op}): unknown item {d}")
                bad = True
        if bad:
            return
        try:
            _check_deps(sim, item_id, deps)
        except BacklogError as e:
            problems.append(f"change {n} ({ch.op}): {e}")
            return
        next(r for r in sim["items"] if r["id"] == item_id)["depends_on"] = list(deps)

    def pinned(n: int, ch: Any, pins: list[str], fields: set[str]) -> None:  # Any: Change
        hit = sorted(set(pins) & fields)
        if hit:
            problems.append(f"change {n} ({ch.op}): {ch.id} has human-set "
                            f"{', '.join(hit)}; do not change it")

    def reason_ok(n: int, ch: Any) -> None:  # Any: Change
        if len(ch.reason.strip()) < MIN_REASON:
            problems.append(f"change {n} ({ch.op}): reason must be at least {MIN_REASON} "
                            f"characters and say why")

    for n, ch in enumerate(turn.changes, 1):
        if isinstance(ch, AddRequirement):
            if ch.kind != "non_goal" and not [c for c in ch.checks if c.strip()]:
                problems.append(f"change {n} ({ch.op}): needs at least one check "
                                f"(only a non_goal may have none)")
        elif isinstance(ch, EditRequirement):
            fields = _set_fields(ch)
            try:
                req = spec.requirement(ch.id)
            except SpecError:
                problems.append(f"change {n} ({ch.op}): unknown requirement {ch.id}")
                continue
            if req.status != "active":
                problems.append(f"change {n} ({ch.op}): {ch.id} is dropped")
            if not fields:
                problems.append(f"change {n} ({ch.op}): sets no field")
            pinned(n, ch, req.pinned_fields, fields)
            kind = ch.kind or req.kind
            if ch.checks is not None and kind != "non_goal" and not [
                    c for c in ch.checks if c.strip()]:
                problems.append(f"change {n} ({ch.op}): needs at least one check")
            if ch.kind is not None and ch.kind != "non_goal" and ch.checks is None \
                    and not req.checks:
                problems.append(f"change {n} ({ch.op}): kind {ch.kind} needs at least one check")
        elif isinstance(ch, DropRequirement):
            try:
                if spec.requirement(ch.id).status != "active":
                    problems.append(f"change {n} ({ch.op}): {ch.id} is already dropped")
            except SpecError:
                problems.append(f"change {n} ({ch.op}): unknown requirement {ch.id}")
        elif isinstance(ch, (AddDecision, SupersedeDecision)):
            reason_ok(n, ch)
            serves_ok(n, ch, ch.serves)
            if isinstance(ch, SupersedeDecision):
                try:
                    if spec.decision(ch.id).status != "accepted":
                        problems.append(f"change {n} ({ch.op}): {ch.id} is not an accepted "
                                        f"decision")
                except SpecError:
                    problems.append(f"change {n} ({ch.op}): unknown decision {ch.id}")
        elif isinstance(ch, AddItem):
            serves_ok(n, ch, ch.serves)
            deps_ok(n, ch, ch.ref, ch.depends_on)
        elif isinstance(ch, EditItem):
            row = items.get(ch.id)
            if row is None:
                problems.append(f"change {n} ({ch.op}): unknown item {ch.id}")
                continue
            fields = _set_fields(ch)
            if row.get("status") == "done":
                problems.append(f"change {n} ({ch.op}): {ch.id} is done; add a new item")
            if not fields:
                problems.append(f"change {n} ({ch.op}): sets no field")
            pinned(n, ch, list(row.get("pinned_fields") or []), fields)
            if ch.serves is not None:
                serves_ok(n, ch, ch.serves)
            if ch.depends_on is not None:
                deps_ok(n, ch, ch.id, ch.depends_on)
        elif isinstance(ch, DropRule):
            try:
                rule = spec.rule(ch.id)
            except SpecError:
                problems.append(f"change {n} ({ch.op}): unknown rule {ch.id}")
                continue
            if rule.status != "active":
                problems.append(f"change {n} ({ch.op}): {ch.id} is already dropped")
            if _DECISION_ID.match(rule.source):
                problems.append(f"change {n} ({ch.op}): {ch.id} belongs to decision "
                                f"{rule.source}; supersede the decision instead")
    return problems


# ── draft records ───────────────────────────────────────────────────────────
@dataclass
class DraftChange:
    """One change in a draft."""

    cid: str                       # "c3", unique within the draft
    change: dict[str, Any]         # Any: a dumped `Change`
    accepted: bool | None = None   # None: undecided, True: ticked, False: rejected
    by: str = "pm"                 # who proposed it: pm | human
    touched: bool = False          # a human set `accepted` (the PM may not replace it)

    @property
    def op(self) -> str:
        """The change's op."""
        return str(self.change.get("op", ""))


@dataclass
class Draft:
    """A set of proposed changes awaiting a human's ticks."""

    id: str
    base_version: int
    status: Literal["open", "approved", "discarded"] = "open"
    changes: list[DraftChange] = field(default_factory=list)
    next_cid: int = 1
    created_at: float = 0.0
    closed_at: float | None = None
    approved_by: str | None = None
    version: int | None = None                     # the spec version approval produced
    created: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:  # Any: JSON row
        """JSON form."""
        return asdict(self)

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> Draft:  # Any: JSON row
        """Load a draft row."""
        data = dict(row)
        data["changes"] = [DraftChange(**c) for c in row.get("changes", [])]
        return cls(**data)

    def get(self, cid: str) -> DraftChange:
        """One change by cid; PlanDraftError if unknown."""
        for c in self.changes:
            if c.cid == cid:
                return c
        raise PlanDraftError(f"draft {self.id} has no change {cid!r}")


@dataclass
class ApproveResult:
    """What approving a draft did."""

    version: int                    # the new spec version
    applied: list[str]              # cids applied
    created: dict[str, str]         # ref → new id (R4, D2, B7, rule-3)


def _draft_row(doc: dict[str, Any], draft_id: str) -> dict[str, Any]:  # Any: JSON rows
    for row in doc.get("drafts", []):
        if row.get("id") == draft_id:
            return row
    raise PlanDraftError(f"unknown draft {draft_id!r}")


def _rename_refs(change: dict[str, Any], mapping: dict[str, str]) -> None:  # Any: JSON row
    if change.get("ref") in mapping:
        change["ref"] = mapping[change["ref"]]
    for key in ("serves", "depends_on"):
        if isinstance(change.get(key), list):
            change[key] = [mapping.get(v, v) for v in change[key]]


class PlanDrafts:
    """Drafts, approval and the plan conversation of one project."""

    def __init__(self, data_root: Path, project_id: str) -> None:
        self.db = WorkDB(Path(data_root) / "projects" / project_id)

    # ── reads ───────────────────────────────────────────────────────────────
    def get(self, draft_id: str) -> Draft:
        """One draft (any status)."""
        return Draft.from_dict(_draft_row(self.db.read_drafts_doc(), draft_id))

    def current(self) -> Draft | None:
        """The open draft, if any."""
        for row in self.db.read_drafts_doc().get("drafts", []):
            if row.get("status") == "open":
                return Draft.from_dict(row)
        return None

    def all(self) -> list[Draft]:
        """Every draft, oldest first (discarded ones are kept for the record)."""
        return [Draft.from_dict(r) for r in self.db.read_drafts_doc().get("drafts", [])]

    # ── draft lifecycle ─────────────────────────────────────────────────────
    def open_draft(self) -> Draft:
        """The open draft, creating an empty one (based on the current spec version)."""
        with self.db.transaction() as txn:
            for row in txn.drafts.get("drafts", []):
                if row.get("status") == "open":
                    return Draft.from_dict(row)
            draft = self._new_draft(txn)
            return Draft.from_dict(draft)

    @staticmethod
    def _new_draft(txn: WorkTxn) -> dict[str, Any]:  # Any: JSON row
        n = int(txn.drafts.get("next", 1))
        txn.drafts["next"] = n + 1
        row = Draft(id=f"draft-{n}", base_version=int(txn.spec.get("version", 0)),
                    created_at=time.time()).to_dict()
        txn.drafts.setdefault("drafts", []).append(row)
        return row

    def merge_pm_turn(self, draft: Draft | str, turn: PlanTurn, lead_held: bool) -> Draft:
        """Put a PM turn's changes into the draft.

        The PM's earlier proposals a human has not touched are replaced; changes a human
        ticked or rejected (and changes a human wrote) stay. With `lead_held` False the PM's
        decision changes start ticked; otherwise every change starts undecided.

        # QUESTION: plan §3 lets the PM "explicitly revise" a human-decided change; there is no
        # syntax for that yet, so a touched change is always kept.
        """
        draft_id = draft if isinstance(draft, str) else draft.id
        with self.db.drafts_doc() as doc:
            row = _draft_row(doc, draft_id)
            d = Draft.from_dict(row)
            if d.status != "open":
                raise PlanDraftError(f"{draft_id} is {d.status}, not open")
            kept = [c for c in d.changes if c.touched or c.by != "pm"]
            incoming = [c.model_dump(mode="json") for c in turn.changes]
            taken = {c.change["ref"] for c in kept if c.change.get("ref")}
            top = max([int(r.split(":")[1]) for r in taken]
                      + [int(c["ref"].split(":")[1]) for c in incoming if c.get("ref")] + [0])
            mapping: dict[str, str] = {}
            for c in incoming:
                if c.get("ref") in taken and c["ref"] not in mapping:
                    top += 1
                    mapping[c["ref"]] = f"new:{top}"
            for c in incoming:
                _rename_refs(c, mapping)
            for c in incoming:
                ticked = True if (c["op"] in _DECISION_OPS and not lead_held) else None
                kept.append(DraftChange(cid=f"c{d.next_cid}", change=c, accepted=ticked,
                                        by="pm"))
                d.next_cid += 1
            d.changes = kept
            row.clear()
            row.update(d.to_dict())
            return d

    def set_change(self, draft_id: str, cid: str, accepted: bool | None) -> Draft:
        """A human ticks (True), rejects (False) or resets (None) one change."""
        with self.db.drafts_doc() as doc:
            row = _draft_row(doc, draft_id)
            d = Draft.from_dict(row)
            if d.status != "open":
                raise PlanDraftError(f"{draft_id} is {d.status}, not open")
            change = d.get(cid)
            change.accepted = accepted
            change.touched = accepted is not None
            row.clear()
            row.update(d.to_dict())
            return d

    def discard(self, draft_id: str) -> Draft:
        """Close the draft without applying anything (it stays for the record)."""
        with self.db.drafts_doc() as doc:
            row = _draft_row(doc, draft_id)
            if row.get("status") != "open":
                raise PlanDraftError(f"{draft_id} is {row.get('status')}, not open")
            row["status"], row["closed_at"] = "discarded", time.time()
            return Draft.from_dict(row)

    def approve(self, draft_id: str, by: str) -> ApproveResult:
        """Apply the ticked changes in one transaction (spec, backlog and draft together).

        Undecided changes block approval; rejected ones are skipped. If any change fails
        nothing is saved.
        """
        with self.db.transaction() as txn:
            return self._approve(txn, _draft_row(txn.drafts, draft_id), by, pin=False)

    def apply_direct(self, change: Any, by: str) -> ApproveResult:  # Any: a `Change` member
        """A human's direct edit: a one-change draft approved on the spot.

        The fields the human edited are recorded as pinned on the object.
        """
        with self.db.transaction() as txn:
            row = self._new_draft(txn)
            d = Draft.from_dict(row)
            d.changes = [DraftChange(cid="c1", change=change.model_dump(mode="json"),
                                     accepted=True, by="human", touched=True)]
            d.next_cid = 2
            row.clear()
            row.update(d.to_dict())
            return self._approve(txn, row, by, pin=True)

    # ── plan conversation ───────────────────────────────────────────────────
    def add_message(self, author: str, text: str, draft_id: str | None = None,
                    mode: str = "", cost_usd: float | None = None,
                    tokens: int | None = None) -> dict[str, Any]:  # Any: JSON row
        """Append a message to the plan conversation."""
        return self.db.add_plan_message(author, text, draft_id, mode, cost_usd, tokens)

    def messages(self, limit: int = 100) -> list[dict[str, Any]]:  # Any: JSON rows
        """The latest messages, oldest first."""
        return self.db.list_plan_messages(limit)

    # ── applying ────────────────────────────────────────────────────────────
    def _approve(self, txn: WorkTxn, row: dict[str, Any], by: str,  # Any: JSON row
                 *, pin: bool) -> ApproveResult:
        d = Draft.from_dict(row)
        if d.status != "open":
            raise PlanDraftError(f"{d.id} is {d.status}, not open")
        undecided = [f"{c.cid} ({c.op})" for c in d.changes if c.accepted is None]
        if undecided:
            raise PlanDraftError("tick or reject these changes first: " + ", ".join(undecided))
        chosen = [c for c in d.changes if c.accepted is True]
        if not chosen:
            raise PlanDraftError(f"{d.id} has no ticked change; discard it instead")
        now = time.time()
        applier = _Applier(txn, d.id, by, pin, now)
        created = applier.apply(chosen)
        txn.spec["version"] = int(txn.spec.get("version", 0)) + 1
        d.status, d.closed_at, d.approved_by = "approved", now, by
        d.version, d.created = int(txn.spec["version"]), created
        row.clear()
        row.update(d.to_dict())
        return ApproveResult(version=d.version, applied=[c.cid for c in chosen], created=created)


class _Applier:
    """Applies a draft's ticked changes to the documents of one transaction."""

    def __init__(self, txn: WorkTxn, draft_id: str, by: str, pin: bool, now: float) -> None:
        self.txn, self.draft_id, self.by, self.pin, self.now = txn, draft_id, by, pin, now
        self.ids: dict[str, str] = {}
        self.current: DraftChange | None = None
        self.created: dict[str, str] = {}

    def apply(self, chosen: list[DraftChange]) -> dict[str, str]:
        """Apply in phases (requirements, decisions, rules, new items, item edits, item
        dependencies) so refs may point forward; any failure raises PlanDraftError."""
        parsed = [(dc, parse_change(dc.change)) for dc in chosen]
        phases: list[tuple[type, ...]] = [
            (AddRequirement, EditRequirement, DropRequirement),
            (AddDecision, SupersedeDecision), (AddRule, DropRule), (AddItem,), (EditItem,)]
        try:
            for dc, ch in parsed:                      # ids for everything but items
                self.current = dc
                ref = getattr(ch, "ref", None)
                if isinstance(ch, AddRequirement):
                    self._reserve(ref, "R")
                elif isinstance(ch, (AddDecision, SupersedeDecision)):
                    self._reserve(ref, "D")
                elif isinstance(ch, AddRule) and ref:
                    self._reserve(ref, "rule")
            for kinds in phases:
                for dc, ch in parsed:
                    if isinstance(ch, kinds):
                        self.current = dc
                        self._one(dc, ch)
            for dc, ch in parsed:                      # dependencies, once every item exists
                self.current = dc
                if isinstance(ch, AddItem) and ch.depends_on:
                    update_in_doc(self.txn.backlog, self.ids[ch.ref],
                                  depends_on=[self._res(r) for r in ch.depends_on])
                elif isinstance(ch, EditItem) and ch.depends_on is not None:
                    update_in_doc(self.txn.backlog, ch.id, pin=self.pin,
                                  depends_on=[self._res(r) for r in ch.depends_on])
        except (SpecError, BacklogError, KeyError) as e:
            raise PlanDraftError(f"{self.draft_id}: {_where(self.current)}: {e}") from e
        return self.created

    # ── helpers ─────────────────────────────────────────────────────────────
    def _reserve(self, ref: str | None, counter: Literal["R", "D", "rule"]) -> None:
        new_id = next_id(self.txn.spec, counter)
        if ref:
            self.ids[ref] = new_id
            self.created[ref] = new_id

    def _res(self, value: str) -> str:
        if _REF.match(value):
            if value not in self.ids:
                raise SpecError(f"{value} does not refer to a change in this draft")
            return self.ids[value]
        return value

    def _serves(self, serves: list[str]) -> list[str]:
        out = list(dict.fromkeys(self._res(s) for s in serves))
        for s in out:
            if not REQ_ID.match(s):
                raise SpecError(f"{s!r} is not a requirement id")
            row = find_row(self.txn.spec, "requirements", s)
            if row.get("status") != "active":
                raise SpecError(f"{s} is dropped")
        return out

    def _who(self, dc: DraftChange) -> str:
        return "human" if dc.by == "human" else "pm"

    # ── one change ──────────────────────────────────────────────────────────
    def _one(self, dc: DraftChange, ch: Any) -> None:  # Any: a Change member
        spec = self.txn.spec
        if isinstance(ch, AddRequirement):
            rid = self.ids[ch.ref]
            if ch.kind != "non_goal" and not [c for c in ch.checks if c.strip()]:
                raise SpecError("a requirement needs at least one check")
            req = Requirement(id=rid, text=ch.text.strip(), kind=ch.kind, priority=ch.priority,
                              checks=make_checks(rid, ch.checks), assumed=ch.assumed,
                              experiment=ch.experiment, source=self._who(dc))
            spec.setdefault("requirements", []).append(req.to_dict())
        elif isinstance(ch, EditRequirement):
            self._edit_requirement(ch)
        elif isinstance(ch, DropRequirement):
            row = find_row(spec, "requirements", ch.id)
            if row.get("status") != "active":
                raise SpecError(f"{ch.id} is already dropped")
            self._push_history(row, reason=ch.reason)
            row["status"] = "dropped"
        elif isinstance(ch, AddDecision):
            self._add_decision(dc, ch.ref, ch, supersedes=None)
        elif isinstance(ch, SupersedeDecision):
            old = find_row(spec, "decisions", ch.id)
            if old.get("status") != "accepted":
                raise SpecError(f"{ch.id} is {old.get('status')}, not accepted")
            self._push_history(old, title=old.get("title"), choice=old.get("choice"),
                               reason=old.get("reason"), superseded_by=self.ids[ch.ref])
            old["status"] = "superseded"
            sync_decision_rule(spec, Decision.from_dict(old))
            self._add_decision(dc, ch.ref, ch, supersedes=ch.id)
        elif isinstance(ch, AddRule):
            rule = Rule(id=self.ids[ch.ref] if ch.ref else next_id(spec, "rule"),
                        text=ch.text.strip(), source=self._who(dc),
                        applies_to=list(ch.applies_to) or ["*"], strength=ch.strength)
            spec.setdefault("rules", []).append(rule.to_dict())
            self.created[ch.ref or f"rule:{rule.id}"] = rule.id
        elif isinstance(ch, DropRule):
            row = find_row(spec, "rules", ch.id)
            if _DECISION_ID.match(str(row.get("source", ""))):
                raise SpecError(f"{ch.id} belongs to decision {row['source']}")
            if row.get("status") != "active":
                raise SpecError(f"{ch.id} is already dropped")
            row["status"] = "dropped"
        elif isinstance(ch, AddItem):
            self._add_item(dc, ch)
        elif isinstance(ch, EditItem):
            fields = {k: v for k, v in ch.model_dump().items()
                      if k not in ("op", "id", "depends_on") and v is not None}
            if "serves" in fields:
                fields["serves"] = self._serves(fields["serves"])
            if fields:
                update_in_doc(self.txn.backlog, ch.id, pin=self.pin, **fields)

    def _push_history(self, row: dict[str, Any], **extra: Any) -> None:  # Any: JSON row
        entry = {"version": row.get("version", 1), "at": self.now, "by": self.by,
                 "draft": self.draft_id}
        for key in ("text", "checks", "priority", "kind", "assumed"):
            if key in row:
                entry[key] = [dict(c) for c in row[key]] if key == "checks" else row[key]
        entry.update(extra)
        row.setdefault("history", []).append(entry)
        row["version"] = int(row.get("version", 1)) + 1

    def _edit_requirement(self, ch: EditRequirement) -> None:
        row = find_row(self.txn.spec, "requirements", ch.id)
        if row.get("status") != "active":
            raise SpecError(f"{ch.id} is dropped")
        fields = _set_fields(ch)
        if not fields:
            raise SpecError("the edit sets no field")
        self._push_history(row)
        reserved = highest_check_number(row)
        for key in ("text", "priority", "kind", "assumed", "experiment"):
            if key in fields:
                row[key] = getattr(ch, key).strip() if key == "text" else getattr(ch, key)
        if ch.checks is not None:
            existing = [Check.from_dict(c) for c in row.get("checks", [])]
            row["checks"] = [c.to_dict() for c in
                             make_checks(ch.id, ch.checks, existing, reserved)]
        if row["kind"] != "non_goal" and not row["checks"]:
            raise SpecError("a requirement needs at least one check")
        if self.pin:
            row["pinned_fields"] = sorted(set(row.get("pinned_fields") or []) | fields)

    def _add_decision(self, dc: DraftChange, ref: str,
                      ch: Any, supersedes: str | None) -> None:  # Any: Add/SupersedeDecision
        if len(ch.reason.strip()) < MIN_REASON:
            raise SpecError(f"a decision's reason must be at least {MIN_REASON} characters")
        human = dc.by == "human" or dc.touched
        dec = Decision(id=self.ids[ref], title=ch.title.strip(), choice=ch.choice.strip(),
                       reason=ch.reason.strip(), serves=self._serves(ch.serves),
                       binding=ch.binding, decided_by="human" if human else "pm",
                       supersedes=supersedes)
        self.txn.spec.setdefault("decisions", []).append(dec.to_dict())
        sync_decision_rule(self.txn.spec, dec)

    def _add_item(self, dc: DraftChange, ch: AddItem) -> None:
        serves = self._serves(ch.serves)
        lines: list[str] = []
        seen: set[str] = set()
        for rid in serves:
            for c in find_row(self.txn.spec, "requirements", rid).get("checks", []):
                line = f"{c['id']}: {c['text']}"
                if line.casefold() not in seen:
                    seen.add(line.casefold())
                    lines.append(line)
        for own in ch.acceptance:
            if own.strip() and own.strip().casefold() not in seen:
                seen.add(own.strip().casefold())
                lines.append(own.strip())
        item = add_to_doc(self.txn.backlog, ch.title, lines, kind=ch.kind, size=ch.size,
                          source=self._who(dc), serves=serves)
        self.ids[ch.ref] = item.id
        self.created[ch.ref] = item.id


def _where(dc: DraftChange | None) -> str:
    return f"change {dc.cid} ({dc.op})" if dc else "draft"
