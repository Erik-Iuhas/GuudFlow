"""Project kinds (role contracts plan §3): a template for a kind of work.

A kind names the lifecycle phases (who owns each, what it needs and produces, the gate that ends
it), the team the project manager starts from (contract ids), the start modes that fit, and the
capabilities it uses (`sandbox`, `repo`, `test_runner`, `screenshots`): nothing in the core
assumes them. GuudFlow ships a library in `data/kinds/*.yaml`.

A project stores its kind in its doc (`kind`). Without one it is treated as `software_app`, the
kind every project was before kinds existed. `project_phases` turns the project's state into the
phase strip on Studio's Work tab; `item_phase` places one backlog item on it.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from core.contracts import ARTIFACTS, load_library
from core.start_modes import START_MODES

LIBRARY_DIR = Path(__file__).resolve().parent.parent / "data" / "kinds"
DEFAULT_KIND = "software_app"
# Phase owners that are not contracts: the human Owner seat.
PEOPLE = ("owner", "lead")
# What phases may need or produce beyond contract artifacts.
PHASE_ARTIFACTS = ARTIFACTS | {"verification_ledger", "retrospective"}
USES = ("sandbox", "repo", "test_runner", "screenshots")
# The project manager (Jack) is host-side, not a team row.
HOST_CONTRACTS = ("product_manager",)

PhaseState = Literal["done", "current", "todo", "skipped"]


class KindError(ValueError):
    """A kind file is missing a field or names something unknown."""


@dataclass(frozen=True)
class KindPhase:
    """One lifecycle phase."""

    id: str
    title: str
    owner: str                     # a contract id, or "owner" / "lead" (a person)
    gate: str
    needs: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:  # Any: JSON view
        """JSON view."""
        return {"id": self.id, "title": self.title, "owner": self.owner, "gate": self.gate,
                "needs": list(self.needs), "produces": list(self.produces)}


@dataclass(frozen=True)
class Kind:
    """One project kind."""

    id: str
    title: str
    purpose: str
    phases: tuple[KindPhase, ...]
    team: tuple[str, ...]          # contract ids, repeated for two of a role
    start_modes: tuple[str, ...]
    default_start_mode: str
    uses: tuple[str, ...] = ()

    def phase(self, phase_id: str) -> KindPhase | None:
        """The phase with this id, or None."""
        return next((p for p in self.phases if p.id == phase_id), None)

    @property
    def lead_contract(self) -> str | None:
        """The contract that owns the design-like phase (tech lead, editor, docs lead)."""
        design = self.phase("design")
        return design.owner if design and design.owner not in PEOPLE else None

    def to_dict(self) -> dict[str, Any]:  # Any: JSON view
        """JSON view (API and Studio)."""
        return {"id": self.id, "title": self.title, "purpose": self.purpose,
                "phases": [p.to_dict() for p in self.phases], "team": list(self.team),
                "start_modes": list(self.start_modes),
                "default_start_mode": self.default_start_mode, "uses": list(self.uses),
                "lead_contract": self.lead_contract}


def _strs(value: object, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise KindError(f"{where} must be a list of non-empty strings")
    return tuple(v.strip() for v in value)


def parse_kind(data: Mapping[str, Any], contracts: Sequence[str]) -> Kind:  # Any: YAML
    """A kind from its YAML mapping, checked against the contract ids; KindError if invalid."""
    kid = str(data.get("id") or "").strip()
    if not kid:
        raise KindError("a kind needs an id")
    for key in ("title", "purpose"):
        if not str(data.get(key) or "").strip():
            raise KindError(f"{kid}: missing {key}")
    known = set(contracts)
    phases = []
    for raw in data.get("phases") or []:
        if not isinstance(raw, Mapping):
            raise KindError(f"{kid}.phases entries are mappings")
        pid, owner = str(raw.get("id") or "").strip(), str(raw.get("owner") or "").strip()
        if not pid or not owner or not str(raw.get("gate") or "").strip():
            raise KindError(f"{kid}.phases entries need id, owner and gate")
        if owner not in known and owner not in PEOPLE:
            raise KindError(f"{kid}.{pid}: owner {owner!r} is not a contract or a person")
        needs = _strs(raw.get("needs"), f"{kid}.{pid}.needs")
        produces = _strs(raw.get("produces"), f"{kid}.{pid}.produces")
        unknown = [a for a in needs + produces if a not in PHASE_ARTIFACTS]
        if unknown:
            raise KindError(f"{kid}.{pid}: unknown artifact(s) {', '.join(unknown)}")
        phases.append(KindPhase(id=pid, title=str(raw.get("title") or pid).strip(), owner=owner,
                                gate=str(raw["gate"]).strip(), needs=needs, produces=produces))
    if not phases:
        raise KindError(f"{kid}: needs at least one phase")
    if len({p.id for p in phases}) != len(phases):
        raise KindError(f"{kid}: phase ids repeat")
    team = _strs(data.get("team"), f"{kid}.team")
    missing = [c for c in team if c not in known]
    if not team or missing:
        raise KindError(f"{kid}.team: unknown contract(s) {', '.join(missing)}" if missing
                        else f"{kid}: needs a team")
    modes = _strs(data.get("start_modes"), f"{kid}.start_modes")
    bad = [m for m in modes if m not in START_MODES]
    if not modes or bad:
        raise KindError(f"{kid}.start_modes: unknown mode(s) {', '.join(bad)}" if bad
                        else f"{kid}: needs start modes")
    default = str(data.get("default_start_mode") or modes[0])
    if default not in modes:
        raise KindError(f"{kid}: default_start_mode {default!r} is not one of its modes")
    uses = _strs(data.get("uses"), f"{kid}.uses")
    if any(u not in USES for u in uses):
        raise KindError(f"{kid}.uses: one of {', '.join(USES)}")
    return Kind(id=kid, title=str(data["title"]).strip(), purpose=str(data["purpose"]).strip(),
                phases=tuple(phases), team=team, start_modes=modes, default_start_mode=default,
                uses=uses)


@lru_cache(maxsize=4)
def _load(directory: str) -> dict[str, Kind]:
    contracts = list(load_library())
    out: dict[str, Kind] = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping):
            raise KindError(f"{path.name}: not a mapping")
        kind = parse_kind(data, contracts)
        if kind.id != path.stem:
            raise KindError(f"{path.name}: id {kind.id!r} does not match the file name")
        out[kind.id] = kind
    return out


def load_kinds(directory: Path | None = None) -> dict[str, Kind]:
    """The kind library, by id (cached; KindError if a file is invalid)."""
    return dict(_load(str(directory or LIBRARY_DIR)))


def kind_of(doc: Mapping[str, Any] | None) -> Kind:  # Any: project doc
    """The project's kind; `software_app` when the doc names none (or an unknown one)."""
    kinds = load_kinds()
    name = (doc or {}).get("kind")
    return kinds.get(name, kinds[DEFAULT_KIND]) if isinstance(name, str) else kinds[DEFAULT_KIND]


def team_template(kind: Kind) -> list[dict[str, str]]:
    """The team the project manager starts from: one row per agent contract (Jack is host-side),
    with a role name, role type and contract. Two of a contract get `builder` and `builder_2`."""
    library = load_library()
    rows: list[dict[str, str]] = []
    seen: dict[str, int] = {}
    for cid in kind.team:
        if cid in HOST_CONTRACTS:
            continue
        seen[cid] = seen.get(cid, 0) + 1
        contract = library[cid]
        rows.append({"role": cid if seen[cid] == 1 else f"{cid}_{seen[cid]}",
                     "role_type": contract.role_type, "contract": cid,
                     "model_tier": contract.model_tier, "purpose": contract.purpose})
    return rows


def render_for_pm(kind: Kind, *, team_contracts: Sequence[str] = ()) -> str:
    """The kind paragraph of the project manager's context: phases, the team it expects and the
    roles the current team is missing."""
    lines = [f"Project kind: {kind.id} ({kind.title}). {kind.purpose}", "Phases:"]
    for p in kind.phases:
        lines.append(f"- {p.title} ({p.id}), owner {p.owner}: {p.gate}")
    expected = [c for c in kind.team if c not in HOST_CONTRACTS]
    lines.append("The team this kind starts from (contracts): " + ", ".join(expected) + ".")
    have = list(team_contracts)
    missing = []
    for cid in dict.fromkeys(expected):
        short = expected.count(cid) - have.count(cid)
        if short > 0:
            missing.append(cid if short == 1 else f"{cid} x{short}")
    if team_contracts and missing:
        lines.append("The current team lacks: " + ", ".join(missing) + ". Propose them with "
                     "`add_role` (with `contract`) if the work needs them; a person ticks it.")
    return "\n".join(lines)


# ── where the project is ────────────────────────────────────────────────────
@dataclass
class ProjectState:
    """What `project_phases` reads; the caller fills it from work.db, the repo and the jobs."""

    spec_version: int = 0
    design_empty: bool = True
    has_lead: bool = False              # the team has the design phase's owner
    running: bool = False               # a session is running
    pending: bool = False               # a session waits for review
    accepted: int = 0                   # accepted sessions
    start_mode: str = "spec_first"
    open_design_drafts: int = 0


@dataclass
class PhaseView:
    """One phase on the Work tab's strip."""

    id: str
    title: str
    owner: str
    gate: str
    state: PhaseState
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        """JSON view."""
        return {"id": self.id, "title": self.title, "owner": self.owner, "gate": self.gate,
                "state": self.state, "note": self.note}


@dataclass
class PhaseStrip:
    """The phases with their states, and the current one."""

    kind: str
    phases: list[PhaseView] = field(default_factory=list)

    @property
    def current(self) -> str | None:
        """The id of the current phase."""
        return next((p.id for p in self.phases if p.state == "current"), None)

    def to_dict(self) -> dict[str, Any]:  # Any: JSON view
        """JSON view."""
        return {"kind": self.kind, "current": self.current,
                "phases": [p.to_dict() for p in self.phases]}


# Start modes that run before the spec is approved (project-flow plan §5).
_NO_SPEC_GATE = ("prototype_first", "research_first", "maintain", "adopt")


def project_phases(kind: Kind, state: ProjectState) -> PhaseStrip:
    """Each phase's state for the Work tab. A phase is done when its artifact exists (an
    approved spec, a design), `skipped` when the start mode or the team leaves it out, and the
    current phase is the first one that is not; a running session is in build, one waiting for
    review in accept."""
    views: list[PhaseView] = []
    for p in kind.phases:
        st: PhaseState = "todo"
        note = ""
        if p.id == "discover":
            if state.spec_version > 0:
                st = "done"
            elif state.start_mode in _NO_SPEC_GATE or state.accepted:
                st, note = "skipped", f"{state.start_mode} starts without an approved spec"
        elif p.id == "design":
            if state.open_design_drafts:
                note = f"{state.open_design_drafts} design draft(s) wait for the Lead seat"
            elif not state.has_lead:
                st, note = "skipped", f"no {p.owner} on the team"
            elif not state.design_empty:
                st = "done"
            elif state.start_mode == "adopt":
                note = "the first session recovers the design from the code"
            elif state.start_mode in ("prototype_first", "maintain"):
                st, note = "skipped", f"{state.start_mode}: design follows the first session"
        elif p.id == "build":
            if state.running:
                st, note = "current", "a session is running"
            elif state.accepted:
                st = "done"
        elif p.id == "accept":
            if state.pending:
                st, note = "current", "a session waits for your review"
            elif state.accepted:
                st, note = "done", f"{state.accepted} session(s) accepted"
        elif p.id == "learn" and state.accepted:
            st = "done"
        views.append(PhaseView(p.id, p.title, p.owner, p.gate, st, note))
    if not any(v.state == "current" for v in views):
        nxt = next((v for v in views if v.state == "todo" and v.id not in ("learn",)), None)
        if nxt is None and state.accepted:
            # Everything has run at least once: the next session is build again.
            nxt = next((v for v in views if v.id == "build"), None)
        if nxt is not None:
            nxt.state = "current"
    return PhaseStrip(kind=kind.id, phases=views)


def item_phase(item: Mapping[str, Any], strip: PhaseStrip) -> str:  # Any: backlog row
    """The phase one backlog item is in: done, accept (its session waits for review), build (in
    a session), or the project's current phase for an open item when that comes before build."""
    status = item.get("status")
    if status == "done":
        return "done"
    if status == "dropped":
        return "dropped"
    if status == "in_run":
        return "accept" if item.get("pending") else "build"
    order = [p.id for p in strip.phases]
    current = strip.current
    if current in order and "build" in order and order.index(current) < order.index("build"):
        return current
    return "ready"


__all__ = ["DEFAULT_KIND", "Kind", "KindError", "KindPhase", "PhaseStrip", "PhaseView",
           "ProjectState", "item_phase", "kind_of", "load_kinds", "parse_kind",
           "project_phases", "render_for_pm", "team_template"]
