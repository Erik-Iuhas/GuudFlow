"""The design artifact (role contracts plan §4.2): how the software is built, owned by the tech
lead, versioned like the spec, changed only through reasoned changes.

- **Components** (`C3`): a name, the paths it owns, its responsibility, the requirements it serves.
  One behaviour has one owning component; two components owning the same path is refused.
- **Interfaces** (`I5`): what a component provides to others (a function, an event, a data shape,
  an endpoint): its name, signature and the components that use it.
- **Data types** (`T2`): the shapes that cross interfaces, with field meanings.
- **Flows** (`F1`): short Mermaid diagrams (sequence, state, flowchart, class) for paths that matter.
- **Technical decisions** (`TD1`): choices with their reason and the options rejected; binding ones
  become review lines.
- **Debt** (`DB1`): known problems in the code the design does not want (two owners of one
  behaviour, a hub file, dead code); resolved by a later change.

A tech lead proposes a `DesignTurn`: reasons (`groups`) and changes that each name their reason.
`validate_design_turn` checks it; `apply_design_turn` applies it in one transaction. When no human
holds the Lead seat the tech lead's changes apply at once; otherwise they wait as a design draft
(`DesignDrafts`) for the Lead to approve (decided 2026-10-08, plan §9).
"""
from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from core.work_db import WorkDB

MAX_DESIGN_CHANGES = 40
MIN_REASON = 20
_REF = re.compile(r"^new:[0-9]+$")
_IDENT = re.compile(r"^[A-Za-z_$][\w$.]*$")
DIAGRAM_START = ("sequenceDiagram", "stateDiagram", "stateDiagram-v2", "flowchart", "graph",
                 "classDiagram", "erDiagram")
_ID_RE = {"component": re.compile(r"^C[0-9]+$"), "interface": re.compile(r"^I[0-9]+$"),
          "datatype": re.compile(r"^T[0-9]+$"), "flow": re.compile(r"^F[0-9]+$"),
          "tech_decision": re.compile(r"^TD[0-9]+$"), "debt": re.compile(r"^DB[0-9]+$")}


class DesignError(ValueError):
    """A design change was refused."""


# ── the change language (what a tech lead submits) ──────────────────────────
class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DesignReason(_Model):
    """Why a set of design changes is made: what changes, why, and the evidence (paths in the
    code, check ids, debt ids) it rests on."""

    name: str = Field(min_length=1, max_length=40)
    summary: str = Field(min_length=10, max_length=400)
    because: str = Field(min_length=10, max_length=400)
    evidence: list[str] = Field(default_factory=list, max_length=12)


class _Op(_Model):
    group: str = Field(min_length=1, max_length=40)


class FieldSpec(_Model):
    """One field of a data type."""

    name: str = Field(min_length=1, max_length=60)
    type: str = Field(min_length=1, max_length=120)
    meaning: str = ""


class AddComponent(_Op):
    op: Literal["add_component"]
    ref: str = Field(pattern=_REF.pattern)
    name: str = Field(min_length=2, max_length=60)
    paths: list[str] = Field(min_length=1)
    responsibility: str = Field(min_length=10)
    serves: list[str] = Field(default_factory=list)


class EditComponent(_Op):
    op: Literal["edit_component"]
    id: str = Field(pattern=r"^C[0-9]+$")
    name: str | None = Field(default=None, min_length=2, max_length=60)
    paths: list[str] | None = None
    responsibility: str | None = Field(default=None, min_length=10)
    serves: list[str] | None = None


class DropComponent(_Op):
    op: Literal["drop_component"]
    id: str = Field(pattern=r"^C[0-9]+$")
    reason: str = Field(min_length=10)


InterfaceKind = Literal["function", "event", "data", "endpoint", "class", "other"]


class AddInterface(_Op):
    op: Literal["add_interface"]
    ref: str = Field(pattern=_REF.pattern)
    name: str = Field(min_length=1, max_length=80)          # the exported name: "step"
    provider: str                                           # C# or a component ref
    kind: InterfaceKind = "function"
    signature: str = Field(min_length=1, max_length=400)    # "step(input: Input) -> Event[]"
    description: str = ""
    users: list[str] = Field(default_factory=list)           # C# or refs


class EditInterface(_Op):
    op: Literal["edit_interface"]
    id: str = Field(pattern=r"^I[0-9]+$")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    provider: str | None = None
    kind: InterfaceKind | None = None
    signature: str | None = Field(default=None, min_length=1, max_length=400)
    description: str | None = None
    users: list[str] | None = None


class DropInterface(_Op):
    op: Literal["drop_interface"]
    id: str = Field(pattern=r"^I[0-9]+$")
    reason: str = Field(min_length=10)


class AddDatatype(_Op):
    op: Literal["add_datatype"]
    ref: str = Field(pattern=_REF.pattern)
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    fields: list[FieldSpec] = Field(min_length=1)


class EditDatatype(_Op):
    op: Literal["edit_datatype"]
    id: str = Field(pattern=r"^T[0-9]+$")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = None
    fields: list[FieldSpec] | None = None


class DropDatatype(_Op):
    op: Literal["drop_datatype"]
    id: str = Field(pattern=r"^T[0-9]+$")
    reason: str = Field(min_length=10)


FlowKind = Literal["sequence", "state", "flowchart", "class"]


class AddFlow(_Op):
    op: Literal["add_flow"]
    ref: str = Field(pattern=_REF.pattern)
    title: str = Field(min_length=3, max_length=100)
    kind: FlowKind = "sequence"
    diagram: str = Field(min_length=10, max_length=4000)    # Mermaid text


class EditFlow(_Op):
    op: Literal["edit_flow"]
    id: str = Field(pattern=r"^F[0-9]+$")
    title: str | None = Field(default=None, min_length=3, max_length=100)
    kind: FlowKind | None = None
    diagram: str | None = Field(default=None, min_length=10, max_length=4000)


class DropFlow(_Op):
    op: Literal["drop_flow"]
    id: str = Field(pattern=r"^F[0-9]+$")
    reason: str = Field(min_length=10)


class AddTechDecision(_Op):
    op: Literal["add_tech_decision"]
    ref: str = Field(pattern=_REF.pattern)
    title: str = Field(min_length=3, max_length=100)
    choice: str = Field(min_length=3)
    reason: str                                              # >= MIN_REASON, options rejected
    binding: bool = False
    components: list[str] = Field(default_factory=list)


class SupersedeTechDecision(_Op):
    op: Literal["supersede_tech_decision"]
    id: str = Field(pattern=r"^TD[0-9]+$")
    ref: str = Field(pattern=_REF.pattern)
    title: str = Field(min_length=3, max_length=100)
    choice: str = Field(min_length=3)
    reason: str
    binding: bool = False
    components: list[str] = Field(default_factory=list)


class AddDebt(_Op):
    op: Literal["add_debt"]
    ref: str = Field(pattern=_REF.pattern)
    text: str = Field(min_length=10)
    paths: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)


class ResolveDebt(_Op):
    op: Literal["resolve_debt"]
    id: str = Field(pattern=r"^DB[0-9]+$")
    how: str = Field(min_length=10)


DesignChange = Annotated[
    AddComponent | EditComponent | DropComponent | AddInterface | EditInterface | DropInterface
    | AddDatatype | EditDatatype | DropDatatype | AddFlow | EditFlow | DropFlow
    | AddTechDecision | SupersedeTechDecision | AddDebt | ResolveDebt,
    Field(discriminator="op")]
_CHANGE = TypeAdapter(DesignChange)

_KIND_OF = {"add_component": "component", "add_interface": "interface",
            "add_datatype": "datatype", "add_flow": "flow", "add_tech_decision": "tech_decision",
            "supersede_tech_decision": "tech_decision", "add_debt": "debt"}
_COUNTER = {"component": "C", "interface": "I", "datatype": "T", "flow": "F",
            "tech_decision": "TD", "debt": "DB"}
_LIST = {"component": "components", "interface": "interfaces", "datatype": "datatypes",
         "flow": "flows", "tech_decision": "decisions", "debt": "debt"}


class DesignTurn(_Model):
    """A tech lead's design proposal: the reasons first, then the changes."""

    groups: list[DesignReason] = Field(default_factory=list, max_length=MAX_DESIGN_CHANGES)
    changes: list[DesignChange] = Field(default_factory=list, max_length=MAX_DESIGN_CHANGES)


def parse_design_change(row: Mapping[str, Any]) -> Any:  # Any: one DesignChange member
    """A stored change dict → its model."""
    return _CHANGE.validate_python(dict(row))


# ── the design as read ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class Component:
    """One component."""

    id: str
    name: str
    paths: tuple[str, ...]
    responsibility: str
    serves: tuple[str, ...] = ()
    status: str = "active"


@dataclass(frozen=True)
class Interface:
    """One interface a component provides."""

    id: str
    name: str
    provider: str
    kind: str
    signature: str
    description: str = ""
    users: tuple[str, ...] = ()
    status: str = "active"


@dataclass
class Design:
    """The design document, read-only view (`Design.load`)."""

    doc: dict[str, Any] = field(default_factory=dict)  # Any: JSON document

    @classmethod
    def load(cls, data_root: Path | None, project_id: str | None) -> "Design":
        """The project's design; empty when it has none."""
        if data_root is None or not project_id:
            return cls({})
        project_dir = Path(data_root) / "projects" / project_id
        if not (project_dir / "work.db").is_file():
            return cls({})
        return cls(WorkDB(project_dir).read_design_doc())

    @property
    def version(self) -> int:
        """Bumped by every applied design turn."""
        return int(self.doc.get("version", 0))

    def _active(self, key: str) -> list[dict[str, Any]]:  # Any: JSON rows
        return [r for r in self.doc.get(key, []) if r.get("status", "active") in
                ("active", "accepted", "open")]

    @property
    def empty(self) -> bool:
        """No components, interfaces or decisions yet."""
        return not (self._active("components") or self._active("interfaces")
                    or self._active("decisions"))

    def components(self) -> list[Component]:
        """Active components."""
        return [Component(id=r["id"], name=r["name"], paths=tuple(r.get("paths", [])),
                          responsibility=r.get("responsibility", ""),
                          serves=tuple(r.get("serves", []))) for r in self._active("components")]

    def interfaces(self) -> list[Interface]:
        """Active interfaces."""
        return [Interface(id=r["id"], name=r["name"], provider=r["provider"],
                          kind=r.get("kind", "function"), signature=r.get("signature", ""),
                          description=r.get("description", ""), users=tuple(r.get("users", [])))
                for r in self._active("interfaces")]

    def ids(self) -> set[str]:
        """Every active component, interface, data type and flow id."""
        return {r["id"] for key in ("components", "interfaces", "datatypes", "flows")
                for r in self._active(key)}

    def component(self, cid: str) -> Component | None:
        """One active component."""
        return next((c for c in self.components() if c.id == cid), None)

    def component_of(self, path: str) -> Component | None:
        """The active component that owns `path` (the most specific pattern wins)."""
        best: tuple[int, Component] | None = None
        for c in self.components():
            for pattern in c.paths:
                if path_matches(path, pattern):
                    score = len(pattern.replace("*", ""))
                    if best is None or score > best[0]:
                        best = (score, c)
        return best[1] if best else None

    def binding_decisions(self) -> list[dict[str, Any]]:  # Any: JSON rows
        """Accepted binding technical decisions."""
        return [d for d in self.doc.get("decisions", [])
                if d.get("binding") and d.get("status", "accepted") == "accepted"]

    def open_debt(self) -> list[dict[str, Any]]:  # Any: JSON rows
        """Debt not resolved yet."""
        return [d for d in self.doc.get("debt", []) if d.get("status", "open") == "open"]


def path_matches(path: str, pattern: str) -> bool:
    """Does `path` fall under `pattern`? `dir/**` matches everything below `dir`; `*` stays
    within one path segment; a plain path matches itself or what is under it."""
    from fnmatch import fnmatchcase
    path, pattern = path.strip("/"), pattern.strip("/")
    if pattern.endswith("/**"):
        base = pattern[:-3]
        return path == base or path.startswith(base + "/") or fnmatchcase(path, pattern[:-1] + "*")
    if any(ch in pattern for ch in "*?["):
        return fnmatchcase(path, pattern) and path.count("/") == pattern.count("/")
    return path == pattern or path.startswith(pattern + "/")


# ── validation ──────────────────────────────────────────────────────────────
def _bad_path(path: str) -> bool:
    p = path.strip()
    return (not p or p.startswith("/") or ".." in p.split("/") or "\\" in p
            or p in ("**", "*", "."))


def validate_design_turn(turn: DesignTurn, design: Design,
                         requirement_ids: Sequence[str] = ()) -> list[str]:
    """Problems with a design turn, one per line (empty when it is fine).

    Every change belongs to a described reason; ids exist; refs resolve within the turn;
    providers and users are components; served requirements exist; paths are relative and do
    not escape; no two active components own the same path pattern; diagrams are Mermaid; a
    technical decision gives its reason (with the options rejected) in at least 20 characters."""
    problems: list[str] = []
    named = [g.name for g in turn.groups]
    for dup in sorted({n for n in named if named.count(n) > 1}):
        problems.append(f"groups: {dup!r} is described twice")
    known_groups = set(named)
    doc = design.doc
    existing = {kind: {r["id"]: r for r in doc.get(_LIST[kind], [])} for kind in _LIST}
    refs: dict[str, str] = {}
    for n, ch in enumerate(turn.changes, 1):
        if ch.group not in known_groups:
            problems.append(f"design change {n} ({ch.op}): group {ch.group!r} is not described "
                            f"in `groups`")
        ref = getattr(ch, "ref", None)
        if ref:
            if ref in refs:
                problems.append(f"design change {n} ({ch.op}): ref {ref} is used twice")
            refs[ref] = _KIND_OF[ch.op]
    reqs = set(requirement_ids)
    dropping = {ch.id for ch in turn.changes if ch.op == "drop_component"}

    def comp_ok(n: int, ch: Any, value: str, what: str) -> None:  # Any: a change
        if _REF.match(value):
            if refs.get(value) != "component":
                problems.append(f"design change {n} ({ch.op}): {what} {value} is not a component "
                                f"added in this turn")
        else:
            row = existing["component"].get(value)
            if row is None or row.get("status", "active") != "active" or value in dropping:
                problems.append(f"design change {n} ({ch.op}): {what} {value} is not an active "
                                f"component")

    def exists(n: int, ch: Any, kind: str) -> dict[str, Any] | None:  # Any: JSON row
        row = existing[kind].get(ch.id)
        if row is None:
            problems.append(f"design change {n} ({ch.op}): unknown {kind.replace('_', ' ')} "
                            f"{ch.id}")
            return None
        if row.get("status", "active") not in ("active", "accepted", "open"):
            problems.append(f"design change {n} ({ch.op}): {ch.id} is {row.get('status')}")
        return row

    owned: dict[str, str] = {}
    for row in existing["component"].values():
        if row.get("status", "active") == "active" and row["id"] not in dropping:
            for p in row.get("paths", []):
                owned[p.strip("/")] = row["id"]
    for n, ch in enumerate(turn.changes, 1):
        if isinstance(ch, (AddComponent, EditComponent)):
            if isinstance(ch, EditComponent):
                exists(n, ch, "component")
            for p in ch.paths or []:
                if _bad_path(p):
                    problems.append(f"design change {n} ({ch.op}): path {p!r} must be relative, "
                                    f"inside the project and narrower than everything")
                    continue
                me = ch.ref if isinstance(ch, AddComponent) else ch.id
                other = owned.get(p.strip("/"))
                if other and other != me:
                    problems.append(f"design change {n} ({ch.op}): {p} is already owned by "
                                    f"{other}; one behaviour has one owner")
                owned[p.strip("/")] = me
            for r in ch.serves or []:
                if reqs and r not in reqs:
                    problems.append(f"design change {n} ({ch.op}): serves {r}, which is not an "
                                    f"active requirement")
        elif isinstance(ch, (AddInterface, EditInterface)):
            if isinstance(ch, EditInterface):
                exists(n, ch, "interface")
            if ch.provider is not None:
                comp_ok(n, ch, ch.provider, "provider")
            for u in ch.users or []:
                comp_ok(n, ch, u, "user")
            if ch.name is not None and not _IDENT.match(ch.name.split("(")[0].strip()):
                problems.append(f"design change {n} ({ch.op}): name {ch.name!r} must be the "
                                f"exported identifier (step, createGame, PlayerDied)")
        elif isinstance(ch, (AddFlow, EditFlow)):
            if isinstance(ch, EditFlow):
                exists(n, ch, "flow")
            if ch.diagram is not None and not ch.diagram.lstrip().startswith(DIAGRAM_START):
                problems.append(f"design change {n} ({ch.op}): diagram must be Mermaid and start "
                                f"with one of {', '.join(DIAGRAM_START[:4])}, ...")
        elif isinstance(ch, (AddTechDecision, SupersedeTechDecision)):
            if len(ch.reason.strip()) < MIN_REASON:
                problems.append(f"design change {n} ({ch.op}): reason must be at least "
                                f"{MIN_REASON} characters and name the options rejected")
            if isinstance(ch, SupersedeTechDecision):
                row = exists(n, ch, "tech_decision")
                if row is not None and row.get("status", "accepted") != "accepted":
                    problems.append(f"design change {n} ({ch.op}): {ch.id} is not accepted")
            for c in ch.components:
                comp_ok(n, ch, c, "component")
        elif isinstance(ch, AddDebt):
            for c in ch.components:
                comp_ok(n, ch, c, "component")
            for p in ch.paths:
                if _bad_path(p):
                    problems.append(f"design change {n} ({ch.op}): path {p!r} must be relative")
        elif isinstance(ch, ResolveDebt):
            row = exists(n, ch, "debt")
            if row is not None and row.get("status", "open") != "open":
                problems.append(f"design change {n} ({ch.op}): {ch.id} is already resolved")
        elif isinstance(ch, (EditDatatype, DropDatatype)):
            exists(n, ch, "datatype")
        elif isinstance(ch, DropFlow):
            exists(n, ch, "flow")
        elif isinstance(ch, DropInterface):
            exists(n, ch, "interface")
        elif isinstance(ch, DropComponent):
            row = exists(n, ch, "component")
            if row is not None:
                users = [i["id"] for i in existing["interface"].values()
                         if i.get("status", "active") == "active" and i.get("provider") == ch.id
                         and not any(o.op == "drop_interface" and o.id == i["id"]
                                     for o in turn.changes)]
                if users:
                    problems.append(f"design change {n} ({ch.op}): {ch.id} still provides "
                                    f"{', '.join(users)}; drop or move them too")
    return problems


# ── applying ────────────────────────────────────────────────────────────────
def _stamp(reason: Mapping[str, Any] | None, by: str, draft: str, now: float
           ) -> dict[str, Any]:  # Any: JSON row
    out: dict[str, Any] = {"by": by, "draft": draft, "at": now}  # Any: JSON row
    if reason:
        out["why"] = {k: reason.get(k) for k in ("name", "summary", "because", "evidence")}
    return out


def apply_design_turn(doc: dict[str, Any], turn: DesignTurn, *, by: str,  # Any: JSON doc
                      draft: str, now: float | None = None) -> dict[str, str]:
    """Apply a validated design turn to the design document in place; returns ref → new id.

    Every new row gets an `origin` and every changed row a `history` entry with the reason."""
    now = time.time() if now is None else now
    reasons = {g.name: g.model_dump() for g in turn.groups}
    created: dict[str, str] = {}
    for ch in turn.changes:               # ids first, so refs may point forward
        ref = getattr(ch, "ref", None)
        if ref:
            kind = _KIND_OF[ch.op]
            counter = _COUNTER[kind]
            nxt = doc.setdefault("next", {})
            num = int(nxt.get(counter, 1))
            nxt[counter] = num + 1
            created[ref] = f"{counter}{num}"

    def res(value: str) -> str:
        return created.get(value, value)

    def rows(kind: str) -> list[dict[str, Any]]:  # Any: JSON rows
        return doc.setdefault(_LIST[kind], [])

    def find(kind: str, ident: str) -> dict[str, Any]:  # Any: JSON row
        row = next((r for r in rows(kind) if r["id"] == ident), None)
        if row is None:
            raise DesignError(f"unknown {kind} {ident}")
        return row

    def changed(row: dict[str, Any], ch: Any) -> None:  # Any: JSON row / change
        before = {k: v for k, v in row.items() if k not in ("history", "origin")}
        row.setdefault("history", []).append(
            {**_stamp(reasons.get(ch.group), by, draft, now), "before": before})
        row["version"] = int(row.get("version", 1)) + 1

    def new(kind: str, ch: Any, fields: dict[str, Any]) -> None:  # Any: change / JSON
        row = {"id": created[ch.ref], **fields, "status": "accepted" if kind == "tech_decision"
               else "open" if kind == "debt" else "active", "version": 1,
               "origin": _stamp(reasons.get(ch.group), by, draft, now)}
        rows(kind).append(row)

    for ch in turn.changes:
        if isinstance(ch, AddComponent):
            new("component", ch, {"name": ch.name, "paths": list(ch.paths),
                                  "responsibility": ch.responsibility.strip(),
                                  "serves": list(ch.serves)})
        elif isinstance(ch, EditComponent):
            row = find("component", ch.id)
            changed(row, ch)
            for key in ("name", "paths", "responsibility", "serves"):
                value = getattr(ch, key)
                if value is not None:
                    row[key] = value
        elif isinstance(ch, (DropComponent, DropInterface, DropDatatype, DropFlow)):
            kind = {"drop_component": "component", "drop_interface": "interface",
                    "drop_datatype": "datatype", "drop_flow": "flow"}[ch.op]
            row = find(kind, ch.id)
            changed(row, ch)
            row["status"], row["dropped_because"] = "dropped", ch.reason.strip()
        elif isinstance(ch, AddInterface):
            new("interface", ch, {"name": ch.name, "provider": res(ch.provider), "kind": ch.kind,
                                  "signature": ch.signature.strip(),
                                  "description": ch.description.strip(),
                                  "users": [res(u) for u in ch.users]})
        elif isinstance(ch, EditInterface):
            row = find("interface", ch.id)
            changed(row, ch)
            for key in ("name", "kind", "signature", "description"):
                value = getattr(ch, key)
                if value is not None:
                    row[key] = value
            if ch.provider is not None:
                row["provider"] = res(ch.provider)
            if ch.users is not None:
                row["users"] = [res(u) for u in ch.users]
        elif isinstance(ch, AddDatatype):
            new("datatype", ch, {"name": ch.name, "description": ch.description.strip(),
                                 "fields": [f.model_dump() for f in ch.fields]})
        elif isinstance(ch, EditDatatype):
            row = find("datatype", ch.id)
            changed(row, ch)
            if ch.name is not None:
                row["name"] = ch.name
            if ch.description is not None:
                row["description"] = ch.description
            if ch.fields is not None:
                row["fields"] = [f.model_dump() for f in ch.fields]
        elif isinstance(ch, AddFlow):
            new("flow", ch, {"title": ch.title, "kind": ch.kind, "diagram": ch.diagram.strip()})
        elif isinstance(ch, EditFlow):
            row = find("flow", ch.id)
            changed(row, ch)
            for key in ("title", "kind", "diagram"):
                value = getattr(ch, key)
                if value is not None:
                    row[key] = value.strip() if isinstance(value, str) else value
        elif isinstance(ch, (AddTechDecision, SupersedeTechDecision)):
            fields = {"title": ch.title, "choice": ch.choice.strip(), "reason": ch.reason.strip(),
                      "binding": ch.binding, "components": [res(c) for c in ch.components]}
            if isinstance(ch, SupersedeTechDecision):
                old = find("tech_decision", ch.id)
                changed(old, ch)
                old["status"], old["superseded_by"] = "superseded", created[ch.ref]
                fields["supersedes"] = ch.id
            new("tech_decision", ch, fields)
        elif isinstance(ch, AddDebt):
            new("debt", ch, {"text": ch.text.strip(), "paths": list(ch.paths),
                             "components": [res(c) for c in ch.components]})
        elif isinstance(ch, ResolveDebt):
            row = find("debt", ch.id)
            changed(row, ch)
            row["status"], row["resolved_how"] = "resolved", ch.how.strip()
    doc["version"] = int(doc.get("version", 0)) + 1
    return created


# ── drafts and approval ─────────────────────────────────────────────────────
@dataclass
class DesignOutcome:
    """What proposing or deciding a design turn did."""

    draft_id: str
    status: Literal["applied", "open", "discarded"]
    created: dict[str, str] = field(default_factory=dict)
    version: int = 0


class DesignDrafts:
    """Design turns waiting for the Lead seat, and the applied ones (kept as a record)."""

    def __init__(self, data_root: Path, project_id: str) -> None:
        self.db = WorkDB(Path(data_root) / "projects" / project_id)

    def all(self) -> list[dict[str, Any]]:  # Any: JSON rows
        """Every design draft, oldest first."""
        return self.db.read_design_drafts()

    def open(self) -> list[dict[str, Any]]:  # Any: JSON rows
        """Drafts waiting for a decision."""
        return [d for d in self.all() if d.get("status") == "open"]

    def propose(self, turn: DesignTurn, *, by: str, auto_apply: bool,
                requirement_ids: Sequence[str] = (), run_id: str | None = None) -> DesignOutcome:
        """Store a design turn; apply it at once when `auto_apply` (no human Lead).
        DesignError (with every problem) when it is not valid."""
        with self.db.design_txn() as (doc, drafts):
            problems = validate_design_turn(turn, Design(doc), requirement_ids)
            if problems:
                raise DesignError("\n".join(problems))
            num = max([int(d["id"].split("-")[1]) for d in drafts] + [0]) + 1
            draft_id = f"design-{num}"
            row: dict[str, Any] = {"id": draft_id, "status": "open", "by": by,  # Any: JSON
                                   "run_id": run_id, "created_at": time.time(),
                                   "turn": turn.model_dump(mode="json")}
            created: dict[str, str] = {}
            if auto_apply:
                created = apply_design_turn(doc, turn, by=by, draft=draft_id)
                row.update(status="approved", approved_by=by, closed_at=time.time(),
                           created=created, version=doc["version"])
            drafts.append(row)
            return DesignOutcome(draft_id, "applied" if auto_apply else "open", created,
                                 int(doc.get("version", 0)))

    def approve(self, draft_id: str, by: str, reject_groups: Sequence[str] = (),
                requirement_ids: Sequence[str] = ()) -> DesignOutcome:
        """The Lead approves an open draft (optionally without some of its reasons)."""
        with self.db.design_txn() as (doc, drafts):
            row = next((d for d in drafts if d["id"] == draft_id), None)
            if row is None or row.get("status") != "open":
                raise DesignError(f"{draft_id} is not an open design draft")
            turn = DesignTurn.model_validate(row["turn"])
            keep = [g for g in turn.groups if g.name not in set(reject_groups)]
            turn = DesignTurn(groups=keep, changes=[c for c in turn.changes
                                                    if c.group in {g.name for g in keep}])
            problems = validate_design_turn(turn, Design(doc), requirement_ids)
            if problems:
                raise DesignError("\n".join(problems))
            created = apply_design_turn(doc, turn, by=by, draft=draft_id) if turn.changes else {}
            row.update(status="approved", approved_by=by, closed_at=time.time(),
                       created=created, version=doc.get("version", 0),
                       rejected_groups=list(reject_groups))
            return DesignOutcome(draft_id, "applied", created, int(doc.get("version", 0)))

    def discard(self, draft_id: str, by: str) -> DesignOutcome:
        """The Lead turns an open draft down."""
        with self.db.design_txn() as (_doc, drafts):
            row = next((d for d in drafts if d["id"] == draft_id), None)
            if row is None or row.get("status") != "open":
                raise DesignError(f"{draft_id} is not an open design draft")
            row.update(status="discarded", closed_at=time.time(), discarded_by=by)
            return DesignOutcome(draft_id, "discarded")


# ── drift (role contracts plan §4.2) ────────────────────────────────────────
@dataclass(frozen=True)
class Drift:
    """An exported name the design does not declare for the component that owns the file."""

    path: str
    name: str
    component: str

    def text(self) -> str:
        """One line for a card thread."""
        return f"`{self.path}` exports `{self.name}`, which {self.component} does not declare"


def drift(design: Design, exports: Mapping[str, Sequence[str]]) -> list[Drift]:
    """Exported names (path → names, as `handoff.exported_names` gives them) that the owning
    component's interfaces do not declare. Components that declare no interface yet are
    skipped: the design is still incomplete there, not drifting."""
    declared: dict[str, set[str]] = {}
    for i in design.interfaces():
        declared.setdefault(i.provider, set()).add(_bare(i.name))
    out: list[Drift] = []
    for path, names in exports.items():
        owner = design.component_of(path)
        if owner is None or owner.id not in declared:
            continue
        for name in names:
            bare = _bare(name)
            if bare and bare not in declared[owner.id]:
                out.append(Drift(path=path, name=bare, component=owner.id))
    return out


def _bare(name: str) -> str:
    """`step(input)` → `step`; `Game.step` → `step`."""
    return name.split("(")[0].strip().rsplit(".", 1)[-1]


# ── rendering ───────────────────────────────────────────────────────────────
def render_markdown(design: Design, title: str = "Design") -> str:
    """`DESIGN.md`: the design as people read it, Mermaid diagrams in fenced blocks."""
    doc = design.doc
    out = [f"# {title}", "", f"Design version {design.version}. Owned by the tech lead; changed "
           "through reasoned design drafts.", ""]
    comps = design.components()
    if comps:
        out += ["## Components", ""]
        for c in comps:
            out.append(f"### {c.id} {c.name}")
            out.append(c.responsibility)
            out.append("")
            out.append("Owns: " + ", ".join(f"`{p}`" for p in c.paths))
            if c.serves:
                out.append("Serves: " + ", ".join(c.serves))
            provided = [i for i in design.interfaces() if i.provider == c.id]
            if provided:
                out.append("")
                out.append("Provides:")
                out += [f"- **{i.id}** `{i.signature}` ({i.kind})"
                        + (f": {i.description}" if i.description else "")
                        + (f" — used by {', '.join(i.users)}" if i.users else "")
                        for i in provided]
            out.append("")
    types = [t for t in doc.get("datatypes", []) if t.get("status", "active") == "active"]
    if types:
        out += ["## Data", ""]
        for t in types:
            out.append(f"### {t['id']} {t['name']}")
            if t.get("description"):
                out.append(t["description"])
            out += [f"- `{f['name']}`: {f['type']}" + (f" — {f['meaning']}" if f.get("meaning")
                                                        else "") for f in t.get("fields", [])]
            out.append("")
    flows = [f for f in doc.get("flows", []) if f.get("status", "active") == "active"]
    if flows:
        out += ["## Flows", ""]
        for f in flows:
            out += [f"### {f['id']} {f['title']}", "", "```mermaid", f["diagram"], "```", ""]
    decisions = [d for d in doc.get("decisions", []) if d.get("status") == "accepted"]
    if decisions:
        out += ["## Technical decisions", ""]
        for d in decisions:
            out.append(f"- **{d['id']} {d['title']}**{' (binding)' if d.get('binding') else ''}: "
                       f"{d['choice']} Because {d['reason']}")
        out.append("")
    debt = design.open_debt()
    if debt:
        out += ["## Known debt", ""]
        out += [f"- **{d['id']}** {d['text']}" + (f" ({', '.join(d['paths'])})"
                                                   if d.get("paths") else "") for d in debt]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_for_brief(design: Design, *, focus: Sequence[str] = (),
                     files: Sequence[str] = ()) -> str | None:
    """The "The design" brief section. With `focus` (component or interface ids) or `files`,
    only the components they touch, their interfaces and the interfaces they use are shown;
    otherwise the whole map in one line per element. None when there is no design."""
    if design.empty:
        return None
    comps = {c.id: c for c in design.components()}
    ifaces = design.interfaces()
    wanted: set[str] = set()
    for ident in focus:
        if ident in comps:
            wanted.add(ident)
        iface = next((i for i in ifaces if i.id == ident), None)
        if iface:
            wanted.add(iface.provider)
    for f in files:
        owner = design.component_of(f)
        if owner:
            wanted.add(owner.id)
    shown = [c for c in comps.values() if not wanted or c.id in wanted]
    lines = []
    for c in shown:
        lines.append(f"- **{c.id} {c.name}** owns {', '.join(f'`{p}`' for p in c.paths)}: "
                     f"{c.responsibility}")
        for i in ifaces:
            if i.provider == c.id:
                lines.append(f"    - provides **{i.id}** `{i.signature}`"
                             + (f" (used by {', '.join(i.users)})" if i.users else ""))
    if wanted:
        used = [i for i in ifaces if set(i.users) & wanted and i.provider not in wanted]
        if used:
            lines.append("Interfaces from other components you may use (do not change them):")
            lines += [f"- **{i.id}** `{i.signature}` from {i.provider}" for i in used]
    binding = design.binding_decisions()
    if binding:
        lines.append("Binding technical decisions:")
        lines += [f"- **{d['id']}** {d['title']}: {d['choice']}" for d in binding]
    head = ("Build against this design. Export only what it declares for your component; if the "
            "work needs a new or changed interface, say so in your result instead of inventing "
            "one.")
    return head + "\n\n" + "\n".join(lines)


def design_view(design: Design) -> dict[str, Any]:  # Any: JSON
    """The API view: the whole document."""
    return {**design.doc, "empty": design.empty}


__all__ = [
    "AddComponent", "AddDatatype", "AddDebt", "AddFlow", "AddInterface", "AddTechDecision",
    "Component", "Design", "DesignChange", "DesignDrafts", "DesignError", "DesignOutcome",
    "DesignReason", "DesignTurn", "Drift", "EditComponent", "EditDatatype", "EditFlow", "EditInterface",
    "Interface", "ResolveDebt", "SupersedeTechDecision", "apply_design_turn", "design_view",
    "drift",
    "parse_design_change", "path_matches", "render_for_brief", "render_markdown",
    "validate_design_turn",
]
