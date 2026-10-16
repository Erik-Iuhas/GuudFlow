"""Drafts of spec and backlog changes, and the plan conversation (project-flow plan §3).

Every spec change goes through a draft. The project manager proposes a `PlanTurn` (a reply, up
to 20 typed `Change`s and questions); `validate_turn` checks it; `PlanDrafts.merge_pm_turn`
puts the changes into the project's one open draft; a person ticks or rejects each change;
`PlanDrafts.approve` applies the ticked ones in a single `work.db` transaction (spec, drafts and
backlog together). A human's direct edit is `apply_direct`: a one-change draft approved on the
spot, which also records the edited fields as pinned so the PM cannot overwrite them.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from core.backlog import BacklogError, _check_deps, add_to_doc, update_in_doc
from core.harness.capabilities import ROLE_TYPES, Capabilities, CapabilityError
from core.spec import (
    CHECK_REF, REQ_ID, STACK_FIELDS, Check, Decision, Param, Requirement, Rule, Spec, SpecError,
    derive_title,
    find_row, highest_check_number, make_checks, next_id, structured_check, structured_checks,
    sync_decision_rule)
from core.work_db import WorkDB, WorkTxn

MAX_CHANGES = 20
MIN_REASON = 20
_REF = re.compile(r"^new:[0-9]+$")
# A tunable-looking number in a statement: digits, not part of an id like R4 or a version.
# A number that names something ("level 1", "phase 2") is not a tunable value.
_NAMED_NUMBER = re.compile(r"\b(?:level|levels|wave|waves|stage|stages|phase|phases|round|rounds|"
                           r"step|version|player|players|session|sessions|slot|slots)\s+[0-9]+\b",
                           re.IGNORECASE)
_NUMBER = re.compile(r"(?<![A-Za-z0-9.])[0-9]+(?:\.[0-9]+)?(?![0-9])")
_BACKLOG_ID = re.compile(r"^B[0-9]+$")
_DECISION_ID = re.compile(r"^D[0-9]+$")
_DECISION_OPS = ("add_decision", "supersede_decision")
TEAM_OPS = ("add_role", "edit_role", "remove_role", "set_kind",  # change the project doc,
            "add_contract_rule")                                # not the spec
_ROLE_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_BUILDER_TYPES = ("builder", "frontend_builder")

ReqKind = Literal["feature", "constraint", "quality", "non_goal"]
ReqPriority = Literal["must", "should", "could", "wont"]
ItemKind = Literal["code", "test", "docs", "research", "visual"]
ItemSize = Literal["S", "M", "L"]
# How hard agents should think on an item (core/llm/registry.py EFFORT_LEVELS).
ItemEffort = Literal["low", "medium", "high", "xhigh", "max"]
ModelTier = Literal["heavy", "solid", "fast"]


class PlanDraftError(ValueError):
    """A draft operation was refused (wrong state, unticked changes, a change that failed)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Op(_Model):
    """A change. `group` labels a theme ("Default dash") so a person can tick it as one."""

    group: str | None = Field(default=None, max_length=40)


ParamValue = str | int | float | bool


class CheckSpec(_Model):
    """A structured check (plan §11.2): what must hold, how it is verified, the linked test."""

    text: str = Field(min_length=3)                 # "then": what must hold
    method: Literal["unit", "manual", "visual", "perf"] | None = None
    given: str = ""
    test: str | None = None                         # "file::name" for unit checks


class ParamSpec(_Model):
    """A tunable value of a requirement."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    value: ParamValue
    unit: str = ""
    assumed: bool = False
    note: str = ""


class AddRequirement(_Op):
    """Add a requirement; it needs at least one check unless it is a non-goal."""

    op: Literal["add_requirement"]
    ref: str = Field(pattern=_REF.pattern)
    title: str = Field(default="", max_length=60)
    text: str = Field(min_length=3)                  # the statement
    kind: ReqKind
    priority: ReqPriority
    params: list[ParamSpec] = Field(default_factory=list)
    checks: list[str | CheckSpec] = Field(default_factory=list)
    rationale: str = ""
    assumed: bool = False
    experiment: bool = False


class EditRequirement(_Op):
    """Change kind, priority or flags of a requirement. `text` and `checks` (the full new list)
    remain for human edits; the PM uses the field-level ops below instead."""

    op: Literal["edit_requirement"]
    id: str = Field(pattern=REQ_ID.pattern)
    text: str | None = None
    priority: ReqPriority | None = None
    kind: ReqKind | None = None
    checks: list[str] | None = None
    assumed: bool | None = None
    experiment: bool | None = None


class DropRequirement(_Op):
    """Drop a requirement."""

    op: Literal["drop_requirement"]
    id: str = Field(pattern=REQ_ID.pattern)
    reason: str = Field(min_length=3)


class SetTitle(_Op):
    """Rename a requirement."""

    op: Literal["set_title"]
    id: str = Field(pattern=REQ_ID.pattern)
    title: str = Field(min_length=3, max_length=60)


class SetStatement(_Op):
    """Replace a requirement's statement (its behaviour, without tunable numbers)."""

    op: Literal["set_statement"]
    id: str = Field(pattern=REQ_ID.pattern)
    text: str = Field(min_length=3)


class SetParam(_Op):
    """Add or change one tunable value of a requirement."""

    op: Literal["set_param"]
    id: str = Field(pattern=REQ_ID.pattern)
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    value: ParamValue
    unit: str | None = None
    assumed: bool | None = None
    note: str | None = None


class RemoveParam(_Op):
    """Remove a tunable value from a requirement."""

    op: Literal["remove_param"]
    id: str = Field(pattern=REQ_ID.pattern)
    key: str


class AddCheck(_Op):
    """Add a check to a requirement; it gets the next unused number."""

    op: Literal["add_check"]
    id: str = Field(pattern=REQ_ID.pattern)
    check: CheckSpec


class EditCheck(_Op):
    """Change one check by its id ("R4.8"); the id and its state history stay."""

    op: Literal["edit_check"]
    id: str = Field(pattern=r"^R[0-9]+\.[0-9]+$")
    text: str | None = Field(default=None, min_length=3)
    method: Literal["unit", "manual", "visual", "perf"] | None = None
    given: str | None = None
    test: str | None = None


class RemoveCheck(_Op):
    """Remove one check by id; its number is never reused."""

    op: Literal["remove_check"]
    id: str = Field(pattern=r"^R[0-9]+\.[0-9]+$")


FIELD_OPS = ("set_title", "set_statement", "set_param", "remove_param", "add_check",
             "edit_check", "remove_check")


_CHECK_OF = re.compile(r"^(R[0-9]+)\.[0-9]+$")


def _to_requirements(serves: list[str]) -> list[str]:
    """`serves` names requirements; a check id (`R2.4`) means its requirement (`R2`)."""
    out = [m.group(1) if (m := _CHECK_OF.match(s.strip())) else s.strip() for s in serves]
    return list(dict.fromkeys(out))


Serves = Annotated[list[str], AfterValidator(_to_requirements)]


class AddDecision(_Op):
    """Record a decision; the reason is at least 20 characters."""

    op: Literal["add_decision"]
    ref: str = Field(pattern=_REF.pattern)
    title: str = Field(min_length=3)
    choice: str = Field(min_length=1)
    reason: str
    serves: Serves = Field(default_factory=list)
    binding: bool = False


class SupersedeDecision(_Op):
    """Replace decision `id` with a new one."""

    op: Literal["supersede_decision"]
    id: str = Field(pattern=_DECISION_ID.pattern)
    ref: str = Field(pattern=_REF.pattern)
    title: str = Field(min_length=3)
    choice: str = Field(min_length=1)
    reason: str
    serves: Serves = Field(default_factory=list)
    binding: bool = False


class AddItem(_Op):
    """Add a backlog item; `serves` copies those requirements' checks into its acceptance."""

    op: Literal["add_item"]
    ref: str = Field(pattern=_REF.pattern)
    title: str = Field(min_length=3)
    acceptance: list[str] = Field(default_factory=list)
    serves: Serves = Field(default_factory=list)
    kind: ItemKind = "code"
    size: ItemSize = "M"
    effort: ItemEffort | None = None
    depends_on: list[str] = Field(default_factory=list)   # B ids or refs


class EditItem(_Op):
    """Edit editable fields of a backlog item."""

    op: Literal["edit_item"]
    id: str = Field(pattern=_BACKLOG_ID.pattern)
    title: str | None = None
    acceptance: list[str] | None = None
    kind: ItemKind | None = None
    size: ItemSize | None = None
    effort: ItemEffort | None = None
    priority: int | None = None
    notes: str | None = None
    files_hint: list[str] | None = None
    depends_on: list[str] | None = None
    serves: Serves | None = None


class DropItem(_Op):
    """Drop an open backlog item that is no longer needed (it stays visible as dropped)."""

    op: Literal["drop_item"]
    id: str = Field(pattern=_BACKLOG_ID.pattern)
    reason: str = Field(min_length=3)


class AddRule(_Op):
    """Add a project rule (QUESTION: `ref` is optional and not in plan §3)."""

    op: Literal["add_rule"]
    text: str = Field(min_length=3)
    applies_to: list[str] = Field(default_factory=lambda: ["*"])
    strength: Literal["binding", "preference"] = "binding"
    ref: str | None = Field(default=None, pattern=_REF.pattern)


class DropRule(_Op):
    """Drop a rule that no decision owns."""

    op: Literal["drop_rule"]
    id: str = Field(pattern=r"^rule-[0-9]+$")
    reason: str = Field(min_length=3)


class AddRole(_Op):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    """Add a role to the project's team (plan §10.2); a human always ticks it."""

    op: Literal["add_role"]
    ref: str | None = Field(default=None, pattern=_REF.pattern)
    role: str
    role_type: str
    description: str = Field(min_length=3)
    model_tier: ModelTier
    depends_on: list[str] = Field(default_factory=list)
    review_of: list[str] = Field(default_factory=list)
    max_review_rounds: int | None = Field(default=None, ge=1, le=6)
    name: str | None = None
    capabilities: dict[str, str] | None = None
    contract: str | None = None          # role contract id (core/contracts.py)
    reason: str


class EditRole(_Op):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    """Change fields of an existing role (the role name itself cannot change)."""

    op: Literal["edit_role"]
    role: str
    role_type: str | None = None
    description: str | None = None
    model_tier: ModelTier | None = None
    depends_on: list[str] | None = None
    review_of: list[str] | None = None
    max_review_rounds: int | None = Field(default=None, ge=1, le=6)
    name: str | None = None
    capabilities: dict[str, str] | None = None
    contract: str | None = None
    reason: str


class RemoveRole(_Op):
    """Remove a role from the team."""

    op: Literal["remove_role"]
    role: str
    reason: str


class AddContractRule(_Op):
    """Add a rule to one of the project's role contracts (role contracts plan §6), proposed from
    contract measures with the sessions that support it; a human always ticks it."""

    op: Literal["add_contract_rule"]
    contract: str
    rule: str = Field(min_length=10, max_length=400)
    evidence_runs: list[str] = Field(default_factory=list, max_length=10)
    reason: str


class SetStack(_Op):
    """Set what the project is built with (worker context plan B1). Only the fields given
    change; a field a person set is pinned. Workers see the stack in every turn."""

    op: Literal["set_stack"]
    language: str | None = None
    framework: str | None = None
    package_manager: str | None = None
    test_command: str | None = None
    run_command: str | None = None
    source_dir: str | None = None
    test_dir: str | None = None
    notes: str | None = None
    reason: str


class AddSkill(_Op):
    """Add a project skill (worker context plan R5): a short playbook workers load when its
    work comes up, e.g. how this game's levels are built. A human always ticks it."""

    op: Literal["add_skill"]
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    description: str = Field(min_length=10, max_length=200)
    body: str = Field(min_length=20, max_length=4000)
    reason: str


class SetKind(_Op):
    """Set the project's kind (role contracts plan §3), and optionally its start mode; a human
    always ticks it and it applies from the next session."""

    op: Literal["set_kind"]
    kind: str
    start_mode: str | None = None
    reason: str


Change = Annotated[
    AddRequirement | EditRequirement | DropRequirement | SetTitle | SetStatement | SetParam
    | RemoveParam | AddCheck | EditCheck | RemoveCheck | AddDecision | SupersedeDecision
    | AddItem | EditItem | DropItem | AddRule | DropRule | AddRole | EditRole | RemoveRole
    | SetKind | AddContractRule | SetStack | AddSkill,
    Field(discriminator="op")]
_CHANGE_ADAPTER: TypeAdapter[Any] = TypeAdapter(Change)  # Any: the union members above
_REF_KIND = {"add_requirement": "requirement", "add_decision": "decision",
             "supersede_decision": "decision", "add_item": "item", "add_rule": "rule",
             "add_role": "role"}


class ChangeGroup(_Model):
    """Why a set of changes is proposed (plan §12): one reason, in plain words.

    Every change names its group in `group`. A person reviews a draft reason by reason: the
    quote or finding that asked for it, what changes for them, and the changes themselves.
    """

    name: str = Field(min_length=1, max_length=40)       # what the changes' `group` says
    summary: str = Field(min_length=10, max_length=400)  # what changes, for a person
    because: str = Field(min_length=10, max_length=400)  # the reason
    source: Literal["person", "session", "pm"] = "pm"    # who asked for it
    quote: str | None = Field(default=None, max_length=300)  # the exact words that asked


class PlanTurn(_Model):
    """The project manager's tool payload for one turn."""

    # The reasons come first, then the changes, so the model says why before what; the
    # prose reply comes last.
    groups: list[ChangeGroup] = Field(default_factory=list, max_length=MAX_CHANGES)
    changes: list[Change] = Field(default_factory=list, max_length=MAX_CHANGES)
    reply: str
    questions: list[str] = Field(default_factory=list)


def parse_change(row: dict[str, Any]) -> Any:  # Any: one member of the `Change` union
    """A stored change dict → its model."""
    return _CHANGE_ADAPTER.validate_python(row)


def _set_fields(change: Any, skip: tuple[str, ...] = ("op", "id", "group")) -> set[str]:  # Any: Change
    """Fields an edit sets (not None)."""
    return {k for k, v in change.model_dump().items() if k not in skip and v is not None}


def pin_keys(change: Any) -> set[str]:  # Any: Change
    """The pinnable fields a field-level op touches ("title", "text", "param:charges",
    "check:R4.2"); a human's direct edit pins them and the PM may not change them."""
    op = change.op
    if op == "set_title":
        return {"title"}
    if op == "set_statement":
        return {"text"}
    if op in ("set_param", "remove_param"):
        return {f"param:{change.key}"}
    if op in ("edit_check", "remove_check"):
        return {f"check:{change.id}"}
    if op == "add_check":
        return set()
    return _set_fields(change)


def _check_req_id(check_id: str) -> str:
    return check_id.split(".", 1)[0]


# ── validation ──────────────────────────────────────────────────────────────
def _plain(text: str) -> str:
    """Casefolded words only, for matching a quote against what was said."""
    return " ".join(re.findall(r"[\w.%$-]+", text.casefold()))


def validate_reasons(turn: PlanTurn, sources: Sequence[str] | None = None,
                     known: Collection[str] = ()) -> list[str]:
    """Every change belongs to a described reason; quotes are words that were really said.

    `sources` are the texts a quote may come from (the people's messages and the session
    report); None skips the quote check. `known` are reasons the open draft already describes.
    """
    problems: list[str] = []
    names = [g.name for g in turn.groups]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        problems.append(f"groups: {dup!r} is described twice")
    known = set(names) | set(known)
    for n, ch in enumerate(turn.changes, 1):
        if not ch.group:
            problems.append(f"change {n} ({ch.op}): set `group` to the name of the reason it "
                            f"belongs to (describe each reason in `groups`)")
        elif ch.group not in known:
            problems.append(f"change {n} ({ch.op}): group {ch.group!r} is not described in "
                            f"`groups`")
    if sources is not None:
        said = [_plain(t) for t in sources]
        for g in turn.groups:
            if g.quote and g.source != "pm" and not any(_plain(g.quote) in t for t in said):
                problems.append(f"groups: the quote of {g.name!r} is not in the conversation "
                                f"or the session report; quote exact words or leave it out")
    return problems


def validate_turn(turn: PlanTurn, spec: Spec, backlog_doc: dict[str, Any],  # Any: JSON doc
                  team: list[dict[str, Any]] | None = None,  # Any: agent rows
                  sources: Sequence[str] | None = None,
                  known_groups: Collection[str] = (),
                  draft_refs: Mapping[str, str] | None = None) -> list[str]:
    """Problems with a PM turn, one line each (empty when it is fine).

    `draft_refs` are the refs the open draft already defines (ref → kind): a change may serve
    or depend on any of them, not only on refs added in this turn.

    Checks that every change carries a described reason (`validate_reasons`), ids exist, refs
    resolve within the turn, dependencies have no cycle, a decision's reason is long enough,
    a requirement has a check (unless a non-goal) and no edit touches a field a human pinned.
    `team` is the project's current `agents` (empty when unknown); team ops are checked
    against it (see `_validate_team`).
    """
    problems: list[str] = validate_reasons(turn, sources, known_groups)
    refs: dict[str, str] = dict(draft_refs or {})
    own: set[str] = set()
    for n, ch in enumerate(turn.changes, 1):
        ref = getattr(ch, "ref", None)
        if ref:
            if ref in own:
                problems.append(f"change {n} ({ch.op}): ref {ref} is used twice")
            own.add(ref)
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
                                    f"requirement added in this draft")
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
                                    f"added in this draft")
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

    def statement_ok(n: int, ch: Any, text: str, has_params: bool) -> None:  # Any: Change
        nums = sorted(set(_NUMBER.findall(_NAMED_NUMBER.sub(" ", text))))
        if has_params and nums:
            problems.append(f"change {n} ({ch.op}): the statement has numbers "
                            f"({', '.join(nums)}); put tunable numbers in params")

    for n, ch in enumerate(turn.changes, 1):
        if isinstance(ch, AddRequirement):
            statement_ok(n, ch, ch.text, bool(ch.params))
            if ch.kind != "non_goal" and not [
                    c for c in ch.checks if (c if isinstance(c, str) else c.text).strip()]:
                problems.append(f"change {n} ({ch.op}): needs at least one check "
                                f"(only a non_goal may have none)")
        elif isinstance(ch, EditRequirement):
            fields = _set_fields(ch)
            if fields & {"text", "checks"}:
                problems.append(f"change {n} ({ch.op}): change the statement with set_statement "
                                f"and checks with add_check / edit_check / remove_check, one "
                                f"at a time; edit_requirement is for kind, priority and flags")
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
        elif ch.op in FIELD_OPS:
            req_id = _check_req_id(ch.id) if ch.op in ("edit_check", "remove_check") else ch.id
            try:
                req = spec.requirement(req_id)
            except SpecError:
                problems.append(f"change {n} ({ch.op}): unknown requirement {req_id}")
                continue
            if req.status != "active":
                problems.append(f"change {n} ({ch.op}): {req_id} is dropped")
                continue
            # A human's older whole-field edit pinned "checks" or "params" as a whole.
            broad = ({"checks"} if "check" in ch.op else set()) | (
                {"params"} if "param" in ch.op else set())
            pinned(n, ch, req.pinned_fields, pin_keys(ch) | broad)
            if ch.op == "set_statement":
                statement_ok(n, ch, ch.text, bool(req.params) or any(
                    o.op == "set_param" and o.id == req_id for o in turn.changes))
            if ch.op in ("edit_check", "remove_check") and not any(
                    c.id == ch.id for c in req.checks):
                problems.append(f"change {n} ({ch.op}): unknown check {ch.id}")
            elif ch.op == "edit_check" and not _set_fields(ch):
                problems.append(f"change {n} ({ch.op}): sets no field")
            elif ch.op == "remove_check" and req.kind != "non_goal" and len(req.checks) <= 1 \
                    and not any(o.op == "add_check" and o.id == req_id for o in turn.changes):
                problems.append(f"change {n} ({ch.op}): {req_id} would have no check left")
            elif ch.op == "remove_param" and req.param(ch.key) is None:
                problems.append(f"change {n} ({ch.op}): {req_id} has no param {ch.key!r}")
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
        elif isinstance(ch, DropItem):
            row = items.get(ch.id)
            if row is None:
                problems.append(f"change {n} ({ch.op}): unknown item {ch.id}")
            elif row.get("status") != "open":
                problems.append(f"change {n} ({ch.op}): {ch.id} is {row.get('status')}; "
                                f"only an open item can be dropped")
            else:
                needed = sorted(i for i, r in items.items() if r.get("status") == "open"
                                and ch.id in (r.get("depends_on") or [])
                                and not any(isinstance(o, DropItem) and o.id == i
                                            for o in turn.changes))
                if needed:
                    problems.append(f"change {n} ({ch.op}): open items {', '.join(needed)} "
                                    f"depend on {ch.id}; drop or edit them too")
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
    problems.extend(_validate_team(turn, team or []))
    problems.extend(_validate_rule_targets(turn, team or []))
    for n, ch in enumerate(turn.changes, 1):
        if isinstance(ch, SetStack):
            fields = {k for k in STACK_FIELDS if getattr(ch, k) is not None}
            if not fields:
                problems.append(f"change {n} ({ch.op}): sets no field")
            hit = sorted(fields & set(spec.stack.pinned_fields))
            if hit:
                problems.append(f"change {n} ({ch.op}): the owner set the stack's "
                                f"{', '.join(hit)}; do not change it")
    problems.extend(_validate_kind(turn))
    for n, ch in enumerate(turn.changes, 1):
        if isinstance(ch, AddContractRule):
            problems += _contract_problems(f"change {n} (add_contract_rule)", ch.contract)
    return problems


def _validate_kind(turn: PlanTurn) -> list[str]:
    """A `set_kind` names a known kind and a start mode that kind allows; at most one."""
    from core.kinds import load_kinds
    ops = [(n, ch) for n, ch in enumerate(turn.changes, 1) if isinstance(ch, SetKind)]
    problems: list[str] = []
    if len(ops) > 1:
        problems.append("set_kind: at most one per turn")
    kinds = load_kinds() if ops else {}
    for n, ch in ops:
        where = f"change {n} (set_kind)"
        if len(ch.reason.strip()) < MIN_REASON:
            problems.append(f"{where}: reason must be at least {MIN_REASON} characters and "
                            f"say why")
        kind = kinds.get(ch.kind)
        if kind is None:
            problems.append(f"{where}: kind {ch.kind!r} is not one of {', '.join(sorted(kinds))}")
        elif ch.start_mode is not None and ch.start_mode not in kind.start_modes:
            problems.append(f"{where}: start_mode {ch.start_mode!r} is not one of "
                            f"{', '.join(kind.start_modes)} for {ch.kind}")
    return problems


def rule_targets(applies_to: Sequence[str], team: Sequence[Mapping[str, Any]]  # Any: agent rows
                 ) -> tuple[list[str], list[str]]:
    """(role types a rule's `applies_to` means, entries that name nothing). A role *name* on the
    team is read as that role's type (worker context plan B5)."""
    from core.harness.capabilities import ROLE_TYPES
    by_name = {str(a.get("role")): str(a.get("role_type") or "") for a in team}
    known = set(ROLE_TYPES) | {t for t in by_name.values() if t}
    out: list[str] = []
    bad: list[str] = []
    for entry in applies_to:
        key = entry.strip()
        if key == "*" or key in known:
            out.append(key)
        elif by_name.get(key):
            out.append(by_name[key])
        else:
            bad.append(entry)
    return list(dict.fromkeys(out)), bad


def _validate_rule_targets(turn: PlanTurn, team: list[dict[str, Any]]) -> list[str]:  # Any: rows
    from core.harness.capabilities import ROLE_TYPES
    problems: list[str] = []
    for n, ch in enumerate(turn.changes, 1):
        if isinstance(ch, AddRule):
            _, bad = rule_targets(ch.applies_to, team)
            if bad:
                problems.append(
                    f"change {n} ({ch.op}): applies_to {', '.join(map(repr, bad))} is not a "
                    f"role type; use '*' or role types such as {', '.join(ROLE_TYPES[:6])}")
    return problems


def _validate_team(turn: PlanTurn, team: list[dict[str, Any]]) -> list[str]:  # Any: agent rows
    """Problems with the turn's team ops, judged against the team they would produce."""
    numbered = [(n, ch) for n, ch in enumerate(turn.changes, 1)
                if isinstance(ch, (AddRole, EditRole, RemoveRole))]
    if not numbered:
        return []
    problems_removed: list[str] = []
    builders = {"builder", "frontend_builder", "tester"}
    for n, ch in numbered:
        if isinstance(ch, RemoveRole):
            row = next((a for a in team if a.get("role") == ch.role), None)
            if row is not None and str(row.get("role_type")) in ("planner", "architect") \
                    and any(str(a.get("role_type")) in builders for a in team
                            if a.get("role") != ch.role) \
                    and not any(isinstance(c, (AddRole, EditRole)) and
                                getattr(c, "role_type", None) in ("planner", "architect")
                                for _, c in numbered):
                problems_removed.append(
                    f"change {n} ({ch.op}): {ch.role} writes the session's cards; the builders "
                    f"would get none. Keep it, or add another tech lead in the same draft")
    problems: list[str] = list(problems_removed)
    final: dict[str, dict[str, Any]] = {  # Any: role_type str + two str lists
        str(a["role"]): {"role_type": str(a.get("role_type") or ""),
                         "depends_on": list(a.get("depends_on") or []),
                         "review_of": list(a.get("review_of") or []),
                         "contract": a.get("contract") or None,
                         "is_reviewer": bool(a.get("is_reviewer"))}
        for a in team if a.get("role")}
    existing = set(final)
    added: set[str] = set()
    removed: set[str] = set()
    for n, ch in numbered:
        where = f"change {n} ({ch.op})"
        if len(ch.reason.strip()) < MIN_REASON:
            problems.append(f"{where}: reason must be at least {MIN_REASON} characters and "
                            f"say why")
        if isinstance(ch, AddRole):
            if not _ROLE_SLUG.match(ch.role):
                problems.append(f"{where}: role {ch.role!r} must be a lowercase slug "
                                f"(letters, digits, _ or -)")
            elif ch.role in existing or ch.role in added:
                problems.append(f"{where}: role {ch.role} already exists")
            else:
                added.add(ch.role)
                final[ch.role] = {"role_type": ch.role_type, "depends_on": list(ch.depends_on),
                                  "review_of": list(ch.review_of), "contract": ch.contract,
                                  "is_reviewer": ch.role_type == "reviewer"}
            if ch.role_type not in ROLE_TYPES:
                problems.append(f"{where}: role_type {ch.role_type!r} is not one of "
                                f"{', '.join(ROLE_TYPES)}")
            problems += _contract_problems(where, ch.contract)
        elif isinstance(ch, EditRole):
            if ch.role not in existing and ch.role not in added:
                problems.append(f"{where}: unknown role {ch.role}")
                continue
            if not _set_fields(ch, ("op", "role", "reason", "group")):
                problems.append(f"{where}: sets no field")
            if ch.role_type is not None and ch.role_type not in ROLE_TYPES:
                problems.append(f"{where}: role_type {ch.role_type!r} is not one of "
                                f"{', '.join(ROLE_TYPES)}")
            problems += _contract_problems(where, ch.contract)
            if ch.role in final:
                for key in ("role_type", "depends_on", "review_of", "contract"):
                    value = getattr(ch, key)
                    if value is not None:
                        final[ch.role][key] = list(value) if isinstance(value, list) else value
                if ch.role_type is not None:
                    final[ch.role]["is_reviewer"] = ch.role_type == "reviewer"
        else:
            if ch.role not in existing and ch.role not in added:
                problems.append(f"{where}: unknown role {ch.role}")
                continue
            removed.add(ch.role)
            final.pop(ch.role, None)
        caps = getattr(ch, "capabilities", None)
        if caps:
            try:
                Capabilities.from_dict(dict(caps))
            except (CapabilityError, TypeError) as e:
                problems.append(f"{where}: capabilities: {e}")
    for n, ch in numbered:
        if isinstance(ch, RemoveRole) or ch.role not in final:
            continue
        for key in ("depends_on", "review_of"):
            for ref in getattr(ch, key) or []:
                if ref == ch.role:
                    problems.append(f"change {n} ({ch.op}): {key} lists the role itself")
                elif ref not in final:
                    why = "is removed in this turn" if ref in removed else "is not a role"
                    problems.append(f"change {n} ({ch.op}): {key} {ref} {why} (use an "
                                    f"existing role or one added in this turn)")
    for role, row in sorted(final.items()):
        for other in sorted(removed):
            if other in row["depends_on"] or other in row["review_of"]:
                problems.append(f"remove_role {other}: {role} still depends on or reviews it; "
                                f"edit {role} in the same turn")
    if _has_cycle({r: [d for d in row["depends_on"] if d in final]
                   for r, row in final.items()}):
        problems.append("the team's depends_on would form a cycle")
    if not any(row["role_type"] in _BUILDER_TYPES for row in final.values()):
        problems.append("the team must keep at least one builder or frontend_builder role")
    # The team the changes produce must be able to work: no second session planner, every
    # builder after the planner (core/team_changes.team_problems). Only problems the turn
    # introduces count; to bring in a tech lead, change the existing planner with edit_role.
    from core.team_changes import team_problems
    before = set(team_problems([{"role": r, **row} for r, row in _team_rows(team).items()]))
    for line in team_problems([{"role": r, **row} for r, row in final.items()]):
        if line not in before:
            problems.append(f"the team after these changes: {line}")
    return problems


def _team_rows(team: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:  # Any: agent rows
    """The fields `team_problems` reads, by role."""
    return {str(a["role"]): {"role_type": str(a.get("role_type") or ""),
                             "depends_on": list(a.get("depends_on") or []),
                             "review_of": list(a.get("review_of") or []),
                             "contract": a.get("contract") or None,
                             "is_reviewer": bool(a.get("is_reviewer"))}
            for a in team if a.get("role")}


def _contract_problems(where: str, contract: str | None) -> list[str]:
    """A named contract must exist in the library."""
    if contract is None:
        return []
    from core.contracts import load_library
    known = load_library()
    if contract not in known:
        return [f"{where}: contract {contract!r} is not one of {', '.join(sorted(known))}"]
    return []


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    """True when the dependency graph has a cycle."""
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        state[node] = 1
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1 or (nxt not in state and visit(nxt)):
                return True
        state[node] = 2
        return False

    return any(n not in state and visit(n) for n in graph)


# ── draft records ───────────────────────────────────────────────────────────
@dataclass
class Problem:
    """One problem with a PM turn: the change it is about, a stable code and how bad it is.

    `block` means approving the change would break the spec or the backlog, so it is left out
    of the draft; `warn` is a style rule, so the change goes in with the warning on it and a
    person decides (docs/PM_SELF_REPAIR_PLAN.md §4).
    """

    message: str
    code: str
    severity: Literal["block", "warn"]
    change: int | None = None   # 1-based index into the turn's changes; None: the turn itself
    target: tuple[str, ...] | None = None  # what the change acts on (`change_target`)
    change_json: str = ""       # the left-out change, shown back to the model for repair
    change_data: dict[str, Any] | None = None  # Any: JSON; the left-out change itself
    group_data: dict[str, Any] | None = None   # Any: JSON; the reason it was proposed under


# validate_turn words each problem for the model; this table gives each wording its code.
# First match wins; anything unmatched blocks as "invalid".
_PROBLEM_CODES: tuple[tuple[re.Pattern[str], str, Literal["block", "warn"]], ...] = (
    (re.compile(r"the statement has numbers"), "numbers_in_statement", "warn"),
    (re.compile(r"reason must be at least"), "reason_too_short", "warn"),
    (re.compile(r"the quote of .* is not in the conversation"), "quote_not_said", "warn"),
    (re.compile(r"is described twice"), "group_described_twice", "warn"),
    (re.compile(r"unknown requirement"), "unknown_requirement", "block"),
    (re.compile(r"unknown item"), "unknown_item", "block"),
    (re.compile(r"not an? (requirement|item) added in this draft|not an item added"),
     "ref_not_in_draft", "block"),
    (re.compile(r"human-set"), "pinned_field", "block"),
    (re.compile(r"cycle"), "dependency_cycle", "block"),
    (re.compile(r"needs at least one check|would have no check left"), "missing_check", "block"),
    (re.compile(r"`group`|group .* is not described"), "missing_group", "block"),
)
_CHANGE_N = re.compile(r"^change ([0-9]+) \(")


def classify_problem(message: str) -> Problem:
    """The code, severity and change index of one `validate_turn` line."""
    m = _CHANGE_N.match(message)
    index = int(m.group(1)) if m else None
    for pattern, code, severity in _PROBLEM_CODES:
        if pattern.search(message):
            return Problem(message, code, severity, index)
    return Problem(message, "invalid", "block", index)


_BAD_REF = (re.compile(r"unknown (?:requirement|item) (\S+)$"),
            re.compile(r"serves (\S+), which is not"),
            re.compile(r"depends_on (\S+) is not"))
_PINNED = re.compile(r"has human-set (.+); do not change it")
_CHANGE_META = frozenset({"op", "id", "ref", "group", "reason"})


def suggest_fix(problem: Problem, change: Mapping[str, Any]  # Any: JSON change
                ) -> tuple[dict[str, Any], str] | None:  # Any: JSON change
    """A computed fix for a left-out change, and its label for a person; None when there is
    no fix that keeps the change's meaning (docs/PM_SELF_REPAIR_PLAN.md §4, J5).

    An unknown or not-yet-added id in `serves` / `depends_on` is dropped from the list, and a
    field a person pinned is dropped from an edit that also sets other fields. The fixed
    change must still parse; whether it fits the draft is checked when it is accepted.
    """
    fixed: dict[str, Any] = json.loads(json.dumps(dict(change)))  # Any: JSON change
    label = ""
    if problem.code in ("unknown_requirement", "unknown_item", "ref_not_in_draft"):
        bad = next((m.group(1) for rx in _BAD_REF if (m := rx.search(problem.message))), None)
        for key, words in (("serves", "stop serving"), ("depends_on", "drop the dependency on")):
            values = fixed.get(key)
            if bad and isinstance(values, list) and bad in values:
                fixed[key] = [v for v in values if v != bad]
                label = f"{words} {bad}"
                break
    elif problem.code == "pinned_field":
        m = _PINNED.search(problem.message)
        hit = [f.strip() for f in m.group(1).split(",")] if m else []
        dropped = [f for f in hit if f in fixed and f not in _CHANGE_META]
        if dropped and any(k not in _CHANGE_META and fixed.get(k) is not None
                           for k in fixed if k not in dropped):
            for f in dropped:
                fixed.pop(f)
            label = "leave " + ", ".join(dropped) + " as the person set it"
    if not label:
        return None
    try:
        parse_change(fixed)
    except ValidationError:
        return None
    return fixed, label


def draft_refs(draft: Draft | None) -> dict[str, str]:
    """The refs an open draft defines (ref → kind), leaving out changes a person rejected."""
    if draft is None:
        return {}
    return {str(c.change["ref"]): _REF_KIND[c.op] for c in draft.changes
            if c.change.get("ref") and c.accepted is not False and c.op in _REF_KIND}


def resolve_predicted_ids(turn: PlanTurn, spec_doc: dict[str, Any],  # Any: JSON doc
                          backlog_doc: dict[str, Any],  # Any: JSON doc
                          draft: Draft | None) -> list[str]:
    """Replace ids the model predicted for not-yet-approved additions with their refs.

    Approval numbers new requirements and items in draft order from the spec's and backlog's
    counters, so `R14` with the counter at 14 can only mean the first requirement the draft
    adds. Only `serves` and `depends_on` are rewritten, and only when the id does not exist
    and exactly one addition sits at that position. Returns one note per replacement.
    """
    existing = draft.changes if draft is not None else []
    pending = [c.change for c in existing if c.accepted is not False] \
        + [c.model_dump(mode="json") for c in turn.changes]
    adds = {"R": [str(c["ref"]) for c in pending if c.get("op") == "add_requirement"],
            "B": [str(c["ref"]) for c in pending if c.get("op") == "add_item"]}
    known = {"R": {str(r.get("id")) for r in spec_doc.get("requirements", [])},
             "B": {str(r.get("id")) for r in backlog_doc.get("items", [])}}
    start = {"R": int((spec_doc.get("next") or {}).get("R", 1)),
             "B": int(backlog_doc.get("next", 1))}
    notes: list[str] = []

    def fix(value: str) -> str:
        m = re.fullmatch(r"([RB])([0-9]+)", value)
        if not m or value in known[m.group(1)]:
            return value
        kind, k = m.group(1), int(m.group(2)) - start[m.group(1)]
        if 0 <= k < len(adds[kind]):
            notes.append(f"{value} is not approved yet; read as {adds[kind][k]}")
            return adds[kind][k]
        return value

    for ch in turn.changes:
        for key in ("serves", "depends_on"):
            values = getattr(ch, key, None)
            if values:
                setattr(ch, key, list(dict.fromkeys(fix(v) for v in values)))
    return notes


def split_turn(turn: PlanTurn, problems: Sequence[Problem]
               ) -> tuple[PlanTurn, dict[int, list[str]], list[Problem]]:
    """Keep what can go into the draft: `(kept turn, warnings by kept index, left out)`.

    A change with a blocking problem is left out, and so is any change that serves or depends
    on a ref only a left-out change defined (repeated until nothing more drops). A blocking
    problem with no change number is about the team, so every role op is left out. Warnings stay
    with their change. Indexes in the result are 0-based positions in the kept turn.
    """
    dropped = {p.change for p in problems if p.severity == "block" and p.change}
    if any(p.severity == "block" and not p.change for p in problems):
        # Problems with no change number are about the team the role ops would produce.
        dropped |= {n for n, ch in enumerate(turn.changes, 1)
                    if ch.op in ("add_role", "edit_role", "remove_role")}
    while True:
        gone = {getattr(turn.changes[n - 1], "ref", None) for n in dropped} - {None}
        more = {n for n, ch in enumerate(turn.changes, 1) if n not in dropped and any(
            v in gone for key in ("serves", "depends_on") for v in (getattr(ch, key, None) or []))}
        if not more:
            break
        dropped |= more
    left_out = [p for p in problems if p.severity == "block"]
    for p in left_out:
        if not p.change:  # point a team-wide problem at the role op it is about, if one is named
            m = re.match(r"(add_role|edit_role|remove_role) ([a-z0-9_]+):", p.message)
            p.change = next((n for n, ch in enumerate(turn.changes, 1) if m and ch.op == m.group(1)
                             and getattr(ch, "role", None) == m.group(2)), None)
    for n in sorted(dropped - {p.change for p in left_out}):
        ch = turn.changes[n - 1]
        left_out.append(Problem(f"change {n} ({ch.op}): left out because a change it needs "
                                f"was left out", "needs_left_out", "block", n))
    kept = [ch for n, ch in enumerate(turn.changes, 1) if n not in dropped]
    position = {n: i for i, n in enumerate(
        (n for n in range(1, len(turn.changes) + 1) if n not in dropped))}
    warnings: dict[int, list[str]] = {}
    for p in problems:
        if p.severity == "warn" and p.change in position:
            warnings.setdefault(position[p.change], []).append(p.message)
    used = {ch.group for ch in kept}
    groups = [g for g in turn.groups if g.name in used]
    return (turn.model_copy(update={"changes": kept, "groups": groups}), warnings,
            left_out)


@dataclass
class DraftChange:
    """One change in a draft."""

    cid: str                       # "c3", unique within the draft
    change: dict[str, Any]         # Any: a dumped `Change`
    accepted: bool | None = None   # None: undecided, True: ticked, False: rejected
    by: str = "pm"                 # who proposed it: pm | human
    touched: bool = False          # a human set `accepted` (the PM may not replace it)
    warnings: list[str] = field(default_factory=list)  # style problems a person should see

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
    # The reasons (dumped ChangeGroup) by name; each change's `group` names one.
    groups: dict[str, dict[str, Any]] = field(default_factory=dict)  # Any: JSON rows

    def why(self, change: DraftChange) -> dict[str, Any] | None:  # Any: JSON row
        """The reason a change belongs to, if it has one."""
        name = change.change.get("group")
        return self.groups.get(name) if isinstance(name, str) else None

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
    # Ticked team ops (dumped add_role/edit_role/remove_role), in order. work.db does not hold
    # the team: the caller applies them to the project doc (api/routes/plan.py).
    team_changes: list[dict[str, Any]] = field(default_factory=list)  # Any: dumped Changes


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


def _target(change: dict[str, Any]) -> tuple[str, ...] | None:  # Any: Change row
    """What a draft change acts on, so a revised proposal replaces the earlier one.

    The same op on the same id (and param key or check id), or for an addition the same title (else
    text). None when the change has neither.
    """
    op = str(change.get("op", ""))
    if change.get("id"):
        check = change.get("check")
        extra = check.get("text") if isinstance(check, dict) else change.get("key")
        return (op, str(change["id"]), str(extra or ""))
    title = (change.get("title") or change.get("name") or change.get("role")
             or change.get("text"))
    if title:
        return (op, str(title).strip().lower())
    return None


change_target = _target


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

    def merge_pm_turn(self, draft: Draft | str, turn: PlanTurn, lead_held: bool,
                      warnings: Mapping[int, list[str]] | None = None) -> Draft:
        """Put a PM turn's changes into the draft.

        Earlier changes stay: the PM builds on the open draft and only sends what is new or
        revised. An incoming change replaces the PM's untouched change with the same target
        (`_target`: the same op on the same id, or the same added title); changes a human
        ticked, rejected or wrote are never replaced. With `lead_held` False the PM's
        decision changes start ticked; otherwise every change starts undecided. Team ops
        (add_role, edit_role, remove_role) always start undecided: only a human ticks them
        (hard rule 24). `warnings` (by 0-based position in the turn) go onto their changes.

        # QUESTION: plan §3 lets the PM "explicitly revise" a human-decided change; there is no
        # syntax for that yet, so a touched change is always kept.
        """
        draft_id = draft if isinstance(draft, str) else draft.id
        with self.db.drafts_doc() as doc:
            row = _draft_row(doc, draft_id)
            d = Draft.from_dict(row)
            if d.status != "open":
                raise PlanDraftError(f"{draft_id} is {d.status}, not open")
            incoming = [c.model_dump(mode="json") for c in turn.changes]
            revised = {t for c in incoming if (t := _target(c)) is not None}
            kept = [c for c in d.changes
                    if c.touched or c.by != "pm" or _target(c.change) not in revised]
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
            for i, c in enumerate(incoming):
                ticked = True if (c["op"] in _DECISION_OPS and not lead_held) else None
                kept.append(DraftChange(cid=f"c{d.next_cid}", change=c, accepted=ticked,
                                        by="pm", warnings=list((warnings or {}).get(i, []))))
                d.next_cid += 1
            d.changes = kept
            d.groups.update({g.name: g.model_dump() for g in turn.groups})
            used = {c.change.get("group") for c in d.changes}
            d.groups = {k: v for k, v in d.groups.items() if k in used}
            row.clear()
            row.update(d.to_dict())
            return d

    def add_team_change(self, change: dict[str, Any], by: str) -> DraftChange:  # Any: Change row
        """Add one team op (e.g. from the overseer) to the open draft, undecided.

        Opens a draft if none is open. PlanDraftError when `change` is not a valid
        add_role/edit_role/remove_role.
        """
        try:
            parsed = parse_change(change)
        except ValueError as e:
            raise PlanDraftError(f"not a team change: {e}") from e
        if not isinstance(parsed, (AddRole, EditRole, RemoveRole, AddContractRule)):
            raise PlanDraftError(f"{parsed.op} is not a team change")
        self.open_draft()
        with self.db.drafts_doc() as doc:
            row = next(r for r in doc["drafts"] if r.get("status") == "open")
            d = Draft.from_dict(row)
            dumped = parsed.model_dump(mode="json")
            name = dumped.get("group") or f"Team change from the {by}"[:40]
            dumped["group"] = name
            d.groups.setdefault(name, {
                "name": name, "summary": f"The {by} suggests a change to the team.",
                "because": str(dumped.get("reason") or "no reason given"), "source": "session",
                "quote": None})
            dc = DraftChange(cid=f"c{d.next_cid}", change=dumped, accepted=None, by=by)
            d.next_cid += 1
            d.changes.append(dc)
            row.clear()
            row.update(d.to_dict())
            return dc

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

    def apply_direct(self, change: Any, by: str,  # Any: a `Change` member
                     why: str | None = None) -> ApproveResult:
        """A human's direct edit: a one-change draft approved on the spot.

        The fields the human edited are recorded as pinned on the object; `why` (the person's
        own words) is kept as the change's reason.
        """
        with self.db.transaction() as txn:
            row = self._new_draft(txn)
            d = Draft.from_dict(row)
            dumped = change.model_dump(mode="json")
            dumped["group"] = "Your edit"
            target = getattr(change, "id", None) or getattr(change, "title", None) or change.op
            d.groups = {"Your edit": {
                "name": "Your edit", "summary": f"Edited {target} by hand.",
                "because": (why or "").strip() or "Edited by hand.", "source": "person",
                "quote": (why or "").strip() or None}}
            d.changes = [DraftChange(cid="c1", change=dumped,
                                     accepted=True, by="human", touched=True)]
            d.next_cid = 2
            row.clear()
            row.update(d.to_dict())
            return self._approve(txn, row, by, pin=True)

    # ── plan conversation ───────────────────────────────────────────────────
    def add_message(self, author: str, text: str, draft_id: str | None = None,
                    mode: str = "", cost_usd: float | None = None,
                    tokens: int | None = None, status: str = "",
                    problems: list[str] | None = None,
                    parked: list[dict[str, Any]] | None = None  # Any: JSON rows
                    ) -> dict[str, Any]:  # Any: JSON row
        """Append a message to the plan conversation (a PM turn's status, open problems and
        the changes they kept out)."""
        return self.db.add_plan_message(author, text, draft_id, mode, cost_usd, tokens,
                                        status, problems, parked)

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
        applier = _Applier(txn, d.id, by, pin, now, d.groups)
        created = applier.apply(chosen)
        txn.spec["version"] = int(txn.spec.get("version", 0)) + 1
        d.status, d.closed_at, d.approved_by = "approved", now, by
        d.version, d.created = int(txn.spec["version"]), created
        row.clear()
        row.update(d.to_dict())
        return ApproveResult(version=d.version, applied=[c.cid for c in chosen], created=created,
                             team_changes=applier.team_changes)


class _Applier:
    """Applies a draft's ticked changes to the documents of one transaction."""

    def __init__(self, txn: WorkTxn, draft_id: str, by: str, pin: bool, now: float,
                 groups: dict[str, dict[str, Any]] | None = None) -> None:  # Any: JSON rows
        self.txn, self.draft_id, self.by, self.pin, self.now = txn, draft_id, by, pin, now
        self.groups = groups or {}
        self.ids: dict[str, str] = {}
        self.current: DraftChange | None = None
        self.created: dict[str, str] = {}
        self.team_changes: list[dict[str, Any]] = []  # Any: dumped team Changes
        # The project's agents, so a rule naming a role is stored with its type (plan B5).
        self.team: list[dict[str, Any]] = []  # Any: agent rows

    def apply(self, chosen: list[DraftChange]) -> dict[str, str]:
        """Apply in phases (requirements, their fields and checks, decisions, rules, new items,
        item edits, item dependencies) so refs may point forward; any failure raises
        PlanDraftError."""
        parsed = [(dc, parse_change(dc.change)) for dc in chosen]
        self.team_changes = [ch.model_dump(mode="json", exclude_none=True, exclude={"group"})
                             for _, ch in parsed
                             if isinstance(ch, (AddRole, EditRole, RemoveRole, SetKind,
                                                AddContractRule, AddSkill))]
        phases: list[tuple[type, ...]] = [
            (AddRequirement, EditRequirement, DropRequirement),
            (SetTitle, SetStatement, SetParam, RemoveParam, AddCheck, EditCheck),
            (RemoveCheck,),                  # after add_check, so a swap keeps one check
            (AddDecision, SupersedeDecision), (AddRule, DropRule, SetStack), (AddItem,),
            (EditItem,), (DropItem,)]
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

    def _why(self) -> dict[str, Any]:  # Any: JSON row
        """The current change's reason, as stored on history rows (empty when it has none)."""
        name = self.current.change.get("group") if self.current else None
        g = self.groups.get(name) if isinstance(name, str) else None
        if g is None:
            return {}
        return {"why": {k: g.get(k) for k in ("name", "summary", "because", "source", "quote")}}

    def _item_history(self, item_id: str, change: str) -> None:
        """Note on an item what a draft changed and why."""
        row = next((r for r in self.txn.backlog.get("items", []) if r.get("id") == item_id),
                   None)
        if row is not None:
            row.setdefault("history", []).append({"draft": self.draft_id, "at": self.now,
                                                  "by": self.by, "change": change,
                                                  **self._why()})

    # ── one change ──────────────────────────────────────────────────────────
    def _one(self, dc: DraftChange, ch: Any) -> None:  # Any: a Change member
        spec = self.txn.spec
        if isinstance(ch, AddRequirement):
            rid = self.ids[ch.ref]
            if ch.kind != "non_goal" and not [c for c in ch.checks
                                              if (c if isinstance(c, str) else c.text).strip()]:
                raise SpecError("a requirement needs at least one check")
            req = Requirement(id=rid, text=ch.text.strip(), kind=ch.kind, priority=ch.priority,
                              checks=structured_checks(rid, ch.checks),
                              title=ch.title.strip() or derive_title(ch.text),
                              params=[Param(**p.model_dump()) for p in ch.params],
                              rationale=ch.rationale.strip(), assumed=ch.assumed,
                              experiment=ch.experiment, source=self._who(dc),
                              origin={"draft": self.draft_id, "at": self.now, "by": self.by,
                                      **self._why()})
            spec.setdefault("requirements", []).append(req.to_dict())
        elif isinstance(ch, EditRequirement):
            self._edit_requirement(ch)
        elif ch.op in FIELD_OPS:
            self._field_op(ch)
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
        elif isinstance(ch, SetStack):
            row = spec.setdefault("stack", {})
            who = self._who(dc)
            pins = list(row.get("pinned_fields") or [])
            for k in STACK_FIELDS:
                value = getattr(ch, k)
                if value is None:
                    continue
                if k in pins and who != "human":
                    raise SpecError(f"the owner set the stack's {k}")
                row[k] = value.strip()
                if who == "human" and k not in pins:
                    pins.append(k)
            row["pinned_fields"], row["set_by"] = pins, who
        elif isinstance(ch, AddRule):
            targets, _ = rule_targets(ch.applies_to, self.team)
            rule = Rule(id=self.ids[ch.ref] if ch.ref else next_id(spec, "rule"),
                        text=ch.text.strip(), source=self._who(dc),
                        applies_to=targets or ["*"], strength=ch.strength)
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
                      if k not in ("op", "id", "depends_on", "group") and v is not None}
            if "serves" in fields:
                fields["serves"] = self._serves(fields["serves"])
            if fields:
                update_in_doc(self.txn.backlog, ch.id, pin=self.pin, **fields)
            if fields or ch.depends_on is not None:
                self._item_history(ch.id, "edited: " + ", ".join(
                    sorted(set(fields) | ({"depends_on"} if ch.depends_on is not None
                                          else set()))))
        elif isinstance(ch, DropItem):
            row = next((r for r in self.txn.backlog.get("items", []) if r.get("id") == ch.id),
                       None)
            if row is None:
                raise BacklogError(f"unknown item {ch.id}")
            if row.get("status") != "open":
                raise BacklogError(f"{ch.id} is {row.get('status')}; only an open item can "
                                   f"be dropped")
            row["status"], row["updated_at"] = "dropped", self.now
            row.setdefault("history", []).append(
                {"decision": f"dropped by {self.by}: {ch.reason.strip()}", "at": self.now,
                 "draft": self.draft_id, "change": "dropped", **self._why()})

    def _field_op(self, ch: Any) -> None:  # Any: a field-level Change member
        """Apply one field-level requirement op (plan §11.2)."""
        req_id = _check_req_id(ch.id) if ch.op in ("edit_check", "remove_check") else ch.id
        row = find_row(self.txn.spec, "requirements", req_id)
        if row.get("status") != "active":
            raise SpecError(f"{req_id} is dropped")
        current = Requirement.from_dict(row)
        self._push_history(row)
        row["title"] = row.get("title") or current.title     # older rows get their title stored
        if ch.op == "set_title":
            row["title"] = ch.title.strip()
        elif ch.op == "set_statement":
            _follow_title(row, ch.text)
            row["text"] = ch.text.strip()
        elif ch.op == "set_param":
            params = [p.to_dict() for p in current.params]
            old = next((p for p in params if p["key"] == ch.key), None)
            new = dict(old or {"key": ch.key, "unit": "", "assumed": False, "note": ""})
            new["value"] = ch.value
            for name in ("unit", "assumed", "note"):
                if getattr(ch, name) is not None:
                    new[name] = getattr(ch, name)
            if self.pin:
                new["assumed"] = False       # a human set it: confirmed
            row["params"] = [new if p is old else p for p in params] if old else params + [new]
        elif ch.op == "remove_param":
            row["params"] = [p.to_dict() for p in current.params if p.key != ch.key]
        elif ch.op == "add_check":
            number = highest_check_number(row) + 1
            row["checks"] = [c.to_dict() for c in current.checks] + [
                structured_check(f"{req_id}.{number}", ch.check).to_dict()]
        elif ch.op == "edit_check":
            checks = [c.to_dict() for c in current.checks]
            target = next((c for c in checks if c["id"] == ch.id), None)
            if target is None:
                raise SpecError(f"unknown check {ch.id}")
            for name in ("text", "method", "given", "test"):
                value = getattr(ch, name)
                if value is not None:
                    target[name] = value.strip() if isinstance(value, str) else value
            row["checks"] = checks
        elif ch.op == "remove_check":
            if not any(c.id == ch.id for c in current.checks):
                raise SpecError(f"unknown check {ch.id}")
            row["checks"] = [c.to_dict() for c in current.checks if c.id != ch.id]
        if row.get("kind") != "non_goal" and not row.get("checks"):
            raise SpecError("a requirement needs at least one check")
        if self.pin:
            row["pinned_fields"] = sorted(set(row.get("pinned_fields") or []) | pin_keys(ch))

    def _push_history(self, row: dict[str, Any], **extra: Any) -> None:  # Any: JSON row
        entry = {"version": row.get("version", 1), "at": self.now, "by": self.by,
                 "draft": self.draft_id, **self._why()}
        for key in ("title", "text", "params", "checks", "priority", "kind", "assumed"):
            if key in row:
                entry[key] = ([dict(c) for c in row[key]] if key in ("checks", "params")
                              else row[key])
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
        if "text" in fields:
            _follow_title(row, ch.text)
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
            # References, not copies (plan §11.2): readers resolve "R4.8" to the current text.
            for c in find_row(self.txn.spec, "requirements", rid).get("checks", []):
                line = str(c["id"])
                if line.casefold() not in seen:
                    seen.add(line.casefold())
                    lines.append(line)
        for own in ch.acceptance:
            cited = CHECK_REF.match(own.strip())
            line = cited.group(1) if cited else own.strip()   # a cited check is a reference
            if line and line.casefold() not in seen:
                seen.add(line.casefold())
                lines.append(line)
        item = add_to_doc(self.txn.backlog, ch.title, lines, kind=ch.kind, size=ch.size,
                          source=self._who(dc), serves=serves, effort=ch.effort or "")
        self.ids[ch.ref] = item.id
        self.created[ch.ref] = item.id
        self._item_history(item.id, "created")


def _follow_title(row: dict[str, Any], new_text: str) -> None:  # Any: JSON row
    """A title nobody set (derived from the text) follows the text when it changes."""
    if not row.get("title") or row["title"] == derive_title(str(row.get("text", ""))):
        row["title"] = derive_title(new_text)


def _where(dc: DraftChange | None) -> str:
    return f"change {dc.cid} ({dc.op})" if dc else "draft"
