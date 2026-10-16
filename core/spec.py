"""The project spec: requirements, decisions and rules (project-flow plan §2).

The spec is stored in the project's `work.db` (`core.work_db`) as one document,

    {"version": n, "next": {"R": n, "D": n, "rule": n},
     "requirements": [...], "decisions": [...], "rules": [...]}

This module holds the dataclasses for those rows, `Spec` (a read view with lookup and a
compact prompt rendering) and the document helpers `core.plan_drafts` uses when a draft is
approved. Binding decisions own a rule (`source` = the decision id); `sync_decision_rule`
keeps that rule in step with the decision.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Kind = Literal["feature", "constraint", "quality", "non_goal"]
CheckMethod = Literal["unit", "manual", "visual", "perf"]
CHECK_METHODS: tuple[str, ...] = ("unit", "manual", "visual", "perf")
TITLE_LIMIT = 60
ParamValue = str | int | float | bool
Priority = Literal["must", "should", "could", "wont"]
CheckState = Literal["unknown", "met", "unmet"]
KINDS: tuple[str, ...] = ("feature", "constraint", "quality", "non_goal")
PRIORITIES: tuple[str, ...] = ("must", "should", "could", "wont")
REQ_ID = re.compile(r"^R[0-9]+$")
_CHECK_ID = re.compile(r"^(R[0-9]+)\.([0-9]+)$")
CHECK_REF = re.compile(r"^(R[0-9]+\.[0-9]+)(?::\s*(.*))?$", re.DOTALL)
# Older checks say how they are verified in a prefix ("Bun test: ...", "Manual: ...").
_METHOD_PREFIX = re.compile(
    r"^(?P<p>(?:manual(?: and)? )?(?:bun |unit |py)?tests?|manual(?: screenshot)?|screenshot|"
    r"visual(?: check)?|perf(?:ormance)?)\s*(?:over [^:]{1,40})?:\s*", re.IGNORECASE)


def infer_method(text: str) -> CheckMethod | None:
    """How an older, free-text check is verified, from its prefix (None if it has none)."""
    found = _METHOD_PREFIX.match(text.strip())
    if not found:
        return None
    prefix = found.group("p").casefold()
    if "test" in prefix:
        return "unit"
    if "screenshot" in prefix or "visual" in prefix:
        return "visual"
    if prefix.startswith("perf"):
        return "perf"
    return "manual"


def derive_title(text: str) -> str:
    """A short title from an older requirement's text: its first clause, at most 60 chars."""
    first = re.split(r"(?<=[a-z0-9)])[:.;]\s|\s[—–-]\s", text.strip(), maxsplit=1)[0].strip()
    if len(first) <= TITLE_LIMIT:
        return first.rstrip(".:")
    cut = first[:TITLE_LIMIT - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


class SpecError(ValueError):
    """A spec operation was refused (unknown id, bad state)."""


@dataclass
class Check:
    """One acceptance check of a requirement."""

    id: str                      # "R4.2"; stable: edits address it, numbers are never reused
    text: str                    # what must hold ("then")
    state: CheckState = "unknown"
    run_id: str | None = None    # the accepted session whose review set the state
    method: CheckMethod | None = None   # unit | manual | visual | perf
    given: str = ""              # optional setup ("given")
    test: str | None = None      # linked test, "file::name", for unit checks
    # How `state` was established (core/ledger.py): verified | claimed | failing |
    # missing_test | unmet | person; None for states set before the ledger existed.
    evidence: str | None = None

    def to_dict(self) -> dict[str, Any]:  # Any: JSON row
        """JSON form."""
        return asdict(self)

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> Check:  # Any: JSON row
        """Load a check, ignoring unknown keys. Older checks get a method from their prefix."""
        text = str(row.get("text", ""))
        method = row.get("method")
        return cls(id=str(row["id"]), text=text, state=row.get("state", "unknown"),
                   run_id=row.get("run_id"),
                   method=method if method in CHECK_METHODS else infer_method(text),
                   given=str(row.get("given") or ""), test=row.get("test") or None,
                   evidence=row.get("evidence") or None)


@dataclass
class Param:
    """A tunable value of a requirement ("charges: 2"), kept out of the prose (plan §11.2)."""

    key: str
    value: ParamValue
    unit: str = ""
    assumed: bool = False        # proposed by the PM; a human has not confirmed it
    note: str = ""

    def to_dict(self) -> dict[str, Any]:  # Any: JSON row
        """JSON form."""
        return asdict(self)

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> Param:  # Any: JSON row
        """Load a param, ignoring unknown keys."""
        return cls(key=str(row["key"]), value=row.get("value", ""), unit=str(row.get("unit") or ""),
                   assumed=bool(row.get("assumed", False)), note=str(row.get("note") or ""))

    def render(self) -> str:
        """`charges = 2` / `recharge = 10 s`."""
        return f"{self.key} = {self.value}{(' ' + self.unit) if self.unit else ''}"


@dataclass
class Requirement:
    """One requirement ("R4")."""

    id: str
    text: str                        # the statement: 1-2 sentences of behaviour
    kind: Kind
    priority: Priority
    checks: list[Check] = field(default_factory=list)
    title: str = ""                  # short name (derived from `text` for older rows)
    params: list[Param] = field(default_factory=list)
    rationale: str = ""
    assumed: bool = False            # PM assumption, not yet confirmed by a human
    experiment: bool = False
    status: Literal["active", "dropped"] = "active"
    source: str = "human"            # human | pm | session:<run_id>
    version: int = 1
    # earlier versions: {version, text, checks, priority, at, by, draft}
    history: list[dict[str, Any]] = field(default_factory=list)  # Any: JSON rows
    # QUESTION: not in plan §2; needed for "an edit to a pinned field is refused" (§3).
    pinned_fields: list[str] = field(default_factory=list)
    # Where it came from: {draft, at, by, because, quote, source} (plan §12).
    origin: dict[str, Any] | None = None  # Any: JSON row

    def to_dict(self) -> dict[str, Any]:  # Any: JSON row
        """JSON form."""
        return asdict(self)

    def __post_init__(self) -> None:
        if not self.title:
            self.title = derive_title(self.text)

    def param(self, key: str) -> Param | None:
        """One param by key."""
        return next((p for p in self.params if p.key == key), None)

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> Requirement:  # Any: JSON row
        """Load a requirement, ignoring unknown keys."""
        text = str(row.get("text", ""))
        return cls(
            id=str(row["id"]), text=text, kind=row.get("kind", "feature"),
            priority=row.get("priority", "should"),
            checks=[Check.from_dict(c) for c in row.get("checks", [])],
            title=str(row.get("title") or ""),
            params=[Param.from_dict(p) for p in row.get("params", []) if p.get("key")],
            rationale=str(row.get("rationale") or ""),
            assumed=bool(row.get("assumed", False)), experiment=bool(row.get("experiment", False)),
            status=row.get("status", "active"), source=str(row.get("source", "human")),
            version=int(row.get("version", 1)), history=list(row.get("history", [])),
            pinned_fields=list(row.get("pinned_fields", [])),
            origin=dict(row["origin"]) if isinstance(row.get("origin"), dict) else None)


@dataclass
class Decision:
    """One decision ("D3")."""

    id: str
    title: str
    choice: str
    reason: str                      # >= 20 chars
    serves: list[str] = field(default_factory=list)   # requirement ids
    binding: bool = False            # True: becomes a rule (source D3)
    status: Literal["accepted", "superseded", "dropped"] = "accepted"
    decided_by: str = "pm"           # pm | human
    supersedes: str | None = None
    version: int = 1
    history: list[dict[str, Any]] = field(default_factory=list)  # Any: JSON rows

    def to_dict(self) -> dict[str, Any]:  # Any: JSON row
        """JSON form."""
        return asdict(self)

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> Decision:  # Any: JSON row
        """Load a decision, ignoring unknown keys."""
        return cls(
            id=str(row["id"]), title=str(row.get("title", "")), choice=str(row.get("choice", "")),
            reason=str(row.get("reason", "")), serves=list(row.get("serves", [])),
            binding=bool(row.get("binding", False)), status=row.get("status", "accepted"),
            decided_by=str(row.get("decided_by", "pm")), supersedes=row.get("supersedes"),
            version=int(row.get("version", 1)), history=list(row.get("history", [])))


@dataclass
class Rule:
    """One project rule ("rule-2"). Binding rules become review checklist lines."""

    id: str
    text: str
    source: str                      # R1 | D2 | human | default (QUESTION: also "pm")
    applies_to: list[str] = field(default_factory=lambda: ["*"])  # role types, or ["*"]
    strength: Literal["binding", "preference"] = "binding"
    status: Literal["active", "dropped"] = "active"

    def to_dict(self) -> dict[str, Any]:  # Any: JSON row
        """JSON form."""
        return asdict(self)

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> Rule:  # Any: JSON row
        """Load a rule, ignoring unknown keys."""
        return cls(id=str(row["id"]), text=str(row.get("text", "")),
                   source=str(row.get("source", "human")),
                   applies_to=list(row.get("applies_to", ["*"])),
                   strength=row.get("strength", "binding"), status=row.get("status", "active"))


STACK_FIELDS = ("language", "framework", "package_manager", "test_command", "run_command",
                "source_dir", "test_dir", "notes")
STACK_LABELS = {"language": "Language", "framework": "Framework and libraries",
                "package_manager": "Packages", "test_command": "Test command",
                "run_command": "Run command", "source_dir": "Source folder",
                "test_dir": "Test folder", "notes": "Also"}


@dataclass
class Stack:
    """What the project is built with (worker context plan B1). Every field is optional; a field
    a person set is pinned, so the project manager cannot change it."""

    language: str = ""
    framework: str = ""
    package_manager: str = ""
    test_command: str = ""
    run_command: str = ""
    source_dir: str = ""
    test_dir: str = ""
    notes: str = ""
    pinned_fields: list[str] = field(default_factory=list)
    set_by: str = ""                 # human | pm

    @classmethod
    def from_dict(cls, row: dict[str, Any] | None) -> "Stack":  # Any: JSON row
        """Read the `stack` object of the spec document (missing → empty)."""
        row = row or {}
        return cls(**{k: str(row.get(k) or "") for k in STACK_FIELDS},
                   pinned_fields=[str(f) for f in row.get("pinned_fields") or []],
                   set_by=str(row.get("set_by") or ""))

    def to_dict(self) -> dict[str, Any]:  # Any: JSON row
        """JSON form."""
        return asdict(self)

    @property
    def empty(self) -> bool:
        """True when no field is set."""
        return not any(getattr(self, k) for k in STACK_FIELDS)

    def lines(self) -> list[str]:
        """`- Language: Python 3.12` lines for the fields that are set."""
        return [f"- {STACK_LABELS[k]}: {getattr(self, k)}" for k in STACK_FIELDS if getattr(self, k)]


class Spec:
    """A read view of the spec document (`WorkDB.read_spec_doc()`)."""

    def __init__(self, doc: dict[str, Any]) -> None:  # Any: JSON document
        self.version = int(doc.get("version", 0))
        self.requirements = [Requirement.from_dict(r) for r in doc.get("requirements", [])]
        self.decisions = [Decision.from_dict(r) for r in doc.get("decisions", [])]
        self.rules = [Rule.from_dict(r) for r in doc.get("rules", [])]
        self.stack = Stack.from_dict(doc.get("stack"))

    # ── lookup ──────────────────────────────────────────────────────────────
    def requirement(self, req_id: str) -> Requirement:
        """One requirement; SpecError if unknown."""
        for r in self.requirements:
            if r.id == req_id:
                return r
        raise SpecError(f"unknown requirement {req_id!r}")

    def decision(self, dec_id: str) -> Decision:
        """One decision; SpecError if unknown."""
        for d in self.decisions:
            if d.id == dec_id:
                return d
        raise SpecError(f"unknown decision {dec_id!r}")

    def rule(self, rule_id: str) -> Rule:
        """One rule; SpecError if unknown."""
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise SpecError(f"unknown rule {rule_id!r}")

    def get(self, obj_id: str) -> Requirement | Decision | Rule:
        """Any object by id (R4, D3, rule-2); use `check()` for check ids."""
        if obj_id.startswith("R"):
            return self.requirement(obj_id)
        if obj_id.startswith("D"):
            return self.decision(obj_id)
        return self.rule(obj_id)

    def check(self, check_id: str) -> Check:
        """One check by id ("R4.2"); SpecError if unknown."""
        found = _CHECK_ID.match(check_id)
        if not found:
            raise SpecError(f"bad check id {check_id!r}")
        for c in self.requirement(found.group(1)).checks:
            if c.id == check_id:
                return c
        raise SpecError(f"unknown check {check_id!r}")

    # ── rules ───────────────────────────────────────────────────────────────
    def rules_for(self, role_type: str) -> list[Rule]:
        """Active rules that apply to this role type."""
        return [r for r in self.rules if r.status == "active"
                and ("*" in r.applies_to or role_type in r.applies_to)]

    def binding_rules(self) -> list[Rule]:
        """Active binding rules (they become review checklist lines)."""
        return [r for r in self.rules if r.status == "active" and r.strength == "binding"]

    # ── rendering ───────────────────────────────────────────────────────────
    def render(self, limit: int = 30) -> str:
        """Compact markdown for prompts: requirements with checks and state, decisions with
        their reason, active rules. At most `limit` entries per section ("" when empty).

        # QUESTION: plan §2 does not say what `limit` counts; entries per section is used.
        """
        sections: list[str] = []
        if not self.stack.empty:
            sections.append("\n".join(["## Stack"] + self.stack.lines()))
        reqs = [r for r in self.requirements if r.status == "active"]
        if reqs:
            lines = ["## Requirements"]
            for r in reqs[:limit]:
                flags = "".join([", assumed" if r.assumed else "",
                                 ", experiment" if r.experiment else ""])
                lines.append(f"- **{r.id} {r.title}** [{r.kind}, {r.priority}{flags}] {r.text}")
                if r.params:
                    lines.append("    - params: " + "; ".join(
                        p.render() + (" (assumed)" if p.assumed else "") for p in r.params))
                lines += [f"    - {c.id} ({c.state}{', ' + c.method if c.method else ''}): {c.text}"
                          for c in r.checks]
            lines += _more(len(reqs), limit)
            sections.append("\n".join(lines))
        decs = [d for d in self.decisions if d.status == "accepted"]
        if decs:
            lines = ["## Decisions"]
            for d in decs[:limit]:
                tag = " (binding)" if d.binding else ""
                lines.append(f"- **{d.id}**{tag} {d.title}: {d.choice} (because {d.reason})")
            lines += _more(len(decs), limit)
            sections.append("\n".join(lines))
        rules = [r for r in self.rules if r.status == "active"]
        if rules:
            lines = ["## Rules"]
            lines += [f"- **{r.id}** ({r.strength}, {r.source}) {r.text}" for r in rules[:limit]]
            lines += _more(len(rules), limit)
            sections.append("\n".join(lines))
        return "\n\n".join(sections)


def _more(total: int, limit: int) -> list[str]:
    return [f"- (+{total - limit} more)"] if total > limit else []


# ── document helpers (used inside a `WorkDB` transaction) ───────────────────
def next_id(doc: dict[str, Any], counter: Literal["R", "D", "rule"]) -> str:  # Any: JSON doc
    """Take the next id from the spec document's counters ("R4", "D3", "rule-2")."""
    nxt = doc.setdefault("next", {})
    n = int(nxt.get(counter, 1))
    nxt[counter] = n + 1
    return f"rule-{n}" if counter == "rule" else f"{counter}{n}"


def find_row(doc: dict[str, Any], key: str, obj_id: str) -> dict[str, Any]:  # Any: JSON rows
    """The raw row `obj_id` in `doc[key]`; SpecError if unknown."""
    for row in doc.get(key, []):
        if row.get("id") == obj_id:
            return row
    raise SpecError(f"unknown {key[:-1]} {obj_id!r}")


def make_checks(req_id: str, texts: Sequence[str], existing: Sequence[Check] = (),
                reserved: int = 0) -> list[Check]:
    """Checks for `texts`: a text equal to an existing check keeps its id and state; new texts
    get the next free number (never one in `existing` or at/below `reserved`)."""
    by_text = {c.text.strip().casefold(): c for c in existing}
    top = max([reserved] + [int(c.id.rsplit(".", 1)[1]) for c in existing])
    out: list[Check] = []
    for text in texts:
        text = text.strip()
        if not text:
            continue
        kept = by_text.get(text.casefold())
        if kept is not None and all(kept.id != c.id for c in out):
            out.append(kept)
            continue
        top += 1
        out.append(Check(id=f"{req_id}.{top}", text=text))
    return out


def structured_check(check_id: str, spec: Any) -> Check:  # Any: str or a CheckSpec model
    """A new check from a draft: a plain string (method inferred from its prefix) or a
    structured `CheckSpec` (`core.plan_drafts`)."""
    if isinstance(spec, str):
        return Check(id=check_id, text=spec.strip(), method=infer_method(spec))
    return Check(id=check_id, text=spec.text.strip(), method=spec.method or infer_method(spec.text),
                 given=spec.given.strip(), test=(spec.test or "").strip() or None)


def structured_checks(req_id: str, specs: Sequence[Any]) -> list[Check]:  # Any: str/CheckSpec
    """Numbered checks for a new requirement (blank strings skipped)."""
    out: list[Check] = []
    for spec in specs:
        if not (spec if isinstance(spec, str) else spec.text).strip():
            continue
        out.append(structured_check(f"{req_id}.{len(out) + 1}", spec))
    return out


def resolve_acceptance(spec: Spec, lines: Sequence[str]) -> list[str]:
    """Item acceptance lines with check references resolved to the current check text.

    Items store `"R4.8"` (or, written before plan §11.2, `"R4.8: <text at the time>"`); both
    become `"R4.8: <current text>"`. A check that no longer exists is marked so; any other
    line is the item's own and stays as written."""
    out: list[str] = []
    for line in lines:
        found = CHECK_REF.match(line.strip())
        if not found:
            out.append(line)
            continue
        try:
            check = spec.check(found.group(1))
            req = spec.requirement(found.group(1).split(".")[0])
        except SpecError:
            out.append(f"{found.group(1)}: {found.group(2) or ''} (removed from the spec)".strip())
            continue
        out.append(f"{check.id}: {check.text}" + (" (requirement dropped)"
                                                    if req.status != "active" else ""))
    return out


def highest_check_number(row: dict[str, Any]) -> int:  # Any: JSON row
    """The highest check number ever used by a requirement (current checks and history)."""
    ids = [str(c.get("id", "")) for c in row.get("checks", [])]
    for old in row.get("history", []):
        ids += [str(c.get("id", "")) for c in old.get("checks", [])]
    return max([int(i.rsplit(".", 1)[1]) for i in ids if _CHECK_ID.match(i)] + [0])


def sync_decision_rule(doc: dict[str, Any], decision: Decision) -> Rule | None:  # Any: JSON doc
    """Keep the rule a binding decision owns in step with it.

    An accepted binding decision creates (or refreshes and reactivates) a rule whose source is
    the decision id. A decision that is not binding, or is superseded or dropped, drops it.
    Returns the rule that is active afterwards, or None.
    """
    rows = doc.setdefault("rules", [])
    row = next((r for r in rows if r.get("source") == decision.id), None)
    active = decision.binding and decision.status == "accepted"
    if not active:
        if row is not None:
            row["status"] = "dropped"
        return None
    text = f"{decision.title}: {decision.choice}"
    if row is None:
        rule = Rule(id=next_id(doc, "rule"), text=text, source=decision.id,
                    applies_to=["*"], strength="binding")
        rows.append(rule.to_dict())
        return rule
    row.update(text=text, strength="binding", status="active")
    return Rule.from_dict(row)


def drop_decision(doc: dict[str, Any], dec_id: str, *, at: float, by: str,  # Any: JSON doc
                  reason: str = "") -> Decision:
    """Mark a decision dropped (history entry, version bump) and drop the rule it owns."""
    row = find_row(doc, "decisions", dec_id)
    if row.get("status") != "accepted":
        raise SpecError(f"{dec_id} is {row.get('status')}; cannot drop it")
    row.setdefault("history", []).append(
        {"version": row.get("version", 1), "title": row.get("title"),
         "choice": row.get("choice"), "reason": row.get("reason"), "at": at, "by": by,
         "dropped": reason})
    row["version"] = int(row.get("version", 1)) + 1
    row["status"] = "dropped"
    decision = Decision.from_dict(row)
    sync_decision_rule(doc, decision)
    return decision


def set_check_states(doc: dict[str, Any], states: Mapping[str, bool], *, run_id: str,  # Any: JSON doc
                     at: float, evidence: Mapping[str, str] | None = None) -> dict[str, str]:
    """Set check states from an accepted session's review verdicts (plan §1 call 5).

    `states` maps a check id ("R4.2") to met (True) or unmet (False). Unknown ids are skipped.
    This is spec-version neutral: the spec version and the requirement's version stay as they
    are; each requirement whose checks changed gets a history entry (`check_states`, note
    "set by session"). Returns {check id: new state} for the checks that were written.
    """
    written: dict[str, str] = {}
    for row in doc.get("requirements", []):
        changed: dict[str, str] = {}
        for check in row.get("checks", []):
            if check.get("id") not in states:
                continue
            state = "met" if states[check["id"]] else "unmet"
            proof = (evidence or {}).get(check["id"])
            if check.get("state") == state and check.get("run_id") == run_id \
                    and check.get("evidence") == proof:
                continue
            check["state"], check["run_id"] = state, run_id
            if proof:
                check["evidence"] = proof
            changed[check["id"]] = state
        if changed:
            row.setdefault("history", []).append(
                {"version": row.get("version", 1), "at": at, "by": f"session:{run_id}",
                 "note": "set by session", "check_states": changed})
            written.update(changed)
    return written
