"""Role contracts (role contracts plan §2): what a role owns, reads, produces and hands off,
when its work is done, who checks it, and the rules it works by.

A contract is data, not prose. GuudFlow ships a library in `data/contracts/*.yaml`; a project
may override a contract in `data/projects/{id}/contracts/{contract_id}.yaml` (fields given there
replace the shipped ones; `rules` are appended, so a project adds rules without losing the
library's). A team row names its contract in `contract`; without one, the role type picks the
default (`DEFAULT_FOR_ROLE_TYPE`).

What a contract changes:
- `rules`, `purpose`, `owns`, `hands_off`, `done_when` and `may_not` become the "Your role
  contract" section of every brief of the role (`render_for_brief`);
- `owns` decides which artifacts a role may propose changes to (`may_change`);
- `checked_by` names the contract that verifies the role's output;
- `measures` are scored by the retrospective, never self-reported (plan §6).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

LIBRARY_DIR = Path(__file__).resolve().parent.parent / "data" / "contracts"

# The artifacts a contract may own (plan §4). `code` is the role's cards' code.
ARTIFACTS = frozenset({
    "spec", "backlog", "design", "session_plan", "knowledge_base", "code", "tests",
    "review_verdicts", "research_notes", "interventions", "outline", "sections", "sources",
    "claims", "information_architecture", "samples",
})

# A role type's contract when the team row names none.
DEFAULT_FOR_ROLE_TYPE: dict[str, str] = {
    "planner": "planner", "architect": "architect", "builder": "builder",
    "frontend_builder": "builder", "tester": "tester", "reviewer": "reviewer",
    "researcher": "researcher", "overseer": "overseer", "it_agent": "it_agent",
}


class ContractError(ValueError):
    """A contract file is missing a field, names an unknown artifact, or does not parse."""


@dataclass(frozen=True)
class Handoff:
    """One thing a role hands to another (`to` is a contract id)."""

    to: str
    what: str


@dataclass(frozen=True)
class Measure:
    """A score the retrospective computes for this contract."""

    key: str
    description: str


@dataclass(frozen=True)
class Contract:
    """One role contract."""

    id: str
    title: str
    purpose: str
    role_type: str
    version: int = 1
    model_tier: str = "solid"
    owns: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    hands_off: tuple[Handoff, ...] = ()
    done_when: tuple[str, ...] = ()
    checked_by: str = ""
    escalates_when: tuple[str, ...] = ()
    may_not: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    measures: tuple[Measure, ...] = ()
    kinds: tuple[str, ...] = ()        # project kinds it is meant for (empty: any)
    source: str = "library"            # library | project

    def may_change(self, artifact: str) -> bool:
        """May this role propose changes to `artifact`?"""
        return artifact in self.owns

    def to_dict(self) -> dict[str, Any]:  # Any: JSON view
        """JSON view (API and Studio)."""
        return {
            "id": self.id, "title": self.title, "purpose": self.purpose,
            "role_type": self.role_type, "version": self.version, "model_tier": self.model_tier,
            "owns": list(self.owns), "reads": list(self.reads), "produces": list(self.produces),
            "hands_off": [{"to": h.to, "what": h.what} for h in self.hands_off],
            "done_when": list(self.done_when), "checked_by": self.checked_by,
            "escalates_when": list(self.escalates_when), "may_not": list(self.may_not),
            "rules": list(self.rules),
            "measures": [{"key": m.key, "description": m.description} for m in self.measures],
            "kinds": list(self.kinds), "source": self.source,
        }


def _strs(value: object, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ContractError(f"{where} must be a list of non-empty strings")
    return tuple(v.strip() for v in value)


def parse_contract(data: Mapping[str, Any], *, source: str = "library") -> Contract:  # Any: YAML
    """A contract from its YAML mapping; ContractError when it is not valid."""
    cid = str(data.get("id") or "").strip()
    if not cid:
        raise ContractError("a contract needs an id")
    for key in ("title", "purpose", "role_type"):
        if not str(data.get(key) or "").strip():
            raise ContractError(f"{cid}: missing {key}")
    owns = _strs(data.get("owns"), f"{cid}.owns")
    unknown = [a for a in owns + _strs(data.get("produces"), f"{cid}.produces")
               if a not in ARTIFACTS]
    if unknown:
        raise ContractError(f"{cid}: unknown artifact(s) {', '.join(unknown)}")
    hands = []
    for h in data.get("hands_off") or []:
        if not isinstance(h, Mapping) or not h.get("to") or not h.get("what"):
            raise ContractError(f"{cid}.hands_off entries need `to` and `what`")
        hands.append(Handoff(to=str(h["to"]), what=str(h["what"]).strip()))
    measures = []
    for m in data.get("measures") or []:
        if isinstance(m, Mapping) and len(m) == 1:
            (key, desc), = m.items()
            measures.append(Measure(key=str(key), description=str(desc)))
        else:
            raise ContractError(f"{cid}.measures entries are `key: description`")
    return Contract(
        id=cid, title=str(data["title"]).strip(), purpose=str(data["purpose"]).strip(),
        role_type=str(data["role_type"]).strip(), version=int(data.get("version") or 1),
        model_tier=str(data.get("model_tier") or "solid"), owns=owns,
        reads=_strs(data.get("reads"), f"{cid}.reads"),
        produces=_strs(data.get("produces"), f"{cid}.produces"), hands_off=tuple(hands),
        done_when=_strs(data.get("done_when"), f"{cid}.done_when"),
        checked_by=str(data.get("checked_by") or ""),
        escalates_when=_strs(data.get("escalates_when"), f"{cid}.escalates_when"),
        may_not=_strs(data.get("may_not"), f"{cid}.may_not"),
        rules=_strs(data.get("rules"), f"{cid}.rules"), measures=tuple(measures),
        kinds=_strs(data.get("kinds"), f"{cid}.kinds"), source=source)


def _read_yaml(path: Path) -> dict[str, Any]:  # Any: YAML
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ContractError(f"{path.name}: {e}") from e
    if not isinstance(data, dict):
        raise ContractError(f"{path.name}: not a mapping")
    return data


def load_library(directory: Path = LIBRARY_DIR) -> dict[str, Contract]:
    """Every shipped contract by id."""
    out: dict[str, Contract] = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        c = parse_contract(_read_yaml(path))
        out[c.id] = c
    return out


def _merge(base: Contract, override: Mapping[str, Any]) -> Contract:  # Any: YAML
    """A project's override: given fields replace the library's; `rules` are appended."""
    merged = base.to_dict()
    merged["measures"] = [{m["key"]: m["description"]} for m in merged["measures"]]
    for key, value in override.items():
        if key != "rules":
            merged[key] = value
    merged["rules"] = list(base.rules) + [r for r in override.get("rules") or []
                                          if r not in base.rules]
    return parse_contract(merged, source="project")


def project_dir(data_root: Path, project_id: str) -> Path:
    """Where a project keeps its contract overrides."""
    return Path(data_root) / "projects" / project_id / "contracts"


def load_for_project(data_root: Path | None, project_id: str | None,
                     library: Mapping[str, Contract] | None = None) -> dict[str, Contract]:
    """The library with the project's overrides applied (and project-only contracts added)."""
    contracts = dict(library if library is not None else load_library())
    if data_root is None or not project_id:
        return contracts
    for path in sorted(project_dir(data_root, project_id).glob("*.yaml")):
        data = _read_yaml(path)
        cid = str(data.get("id") or path.stem)
        data["id"] = cid
        contracts[cid] = (_merge(contracts[cid], data) if cid in contracts
                          else parse_contract(data, source="project"))
    return contracts


def contract_id_for(row: Mapping[str, Any] | Any, role_type: str) -> str | None:  # Any: agent row
    """The contract a team row names, else the role type's default."""
    named = (row.get("contract") if isinstance(row, Mapping)
             else getattr(row, "contract", None))
    if isinstance(named, str) and named.strip():
        return named.strip()
    return DEFAULT_FOR_ROLE_TYPE.get(role_type)


def contracts_for_team(rows: Iterable[tuple[str, str, str | None]],
                       contracts: Mapping[str, Contract]) -> dict[str, Contract]:
    """role → contract for `(role, role_type, named contract)` rows; unknown ids are skipped."""
    out: dict[str, Contract] = {}
    for role, role_type, named in rows:
        cid = named or DEFAULT_FOR_ROLE_TYPE.get(role_type)
        if cid and cid in contracts:
            out[role] = contracts[cid]
    return out


def team_owner(rows: Iterable[Mapping[str, Any]], artifact: str,  # Any: agent rows
               contracts: Mapping[str, Contract] | None = None) -> str | None:
    """The role on a team (project `agents` rows) whose contract owns `artifact`, if any."""
    from core.harness.capabilities import infer_role_type
    lib = contracts if contracts is not None else load_library()
    for row in rows:
        role = str(row.get("role") or "")
        role_type = infer_role_type(role, is_reviewer=bool(row.get("is_reviewer")),
                                    declared=str(row.get("role_type") or ""))
        cid = contract_id_for(row, role_type)
        if cid in lib and lib[cid].may_change(artifact):
            return role
    return None


def render_for_brief(contract: Contract, roles_by_contract: Mapping[str, list[str]] | None = None,
                     names: Mapping[str, str] | None = None) -> str:
    """The "Your role contract" brief section. `roles_by_contract` turns handoff targets
    (contract ids) into this team's roles; `names` turns roles into worker names."""
    names = names or {}

    def who(cid: str) -> str:
        roles = (roles_by_contract or {}).get(cid) or []
        return ", ".join(names.get(r, r) for r in roles) or cid.replace("_", " ")

    lines = [f"You are the team's **{contract.title}**: {contract.purpose}"]
    if contract.owns:
        lines.append("You own: " + ", ".join(a.replace("_", " ") for a in contract.owns)
                     + ". Nobody else changes these; propose changes to anything else through "
                       "its owner.")
    if contract.hands_off:
        lines.append("You hand off:\n" + "\n".join(f"- to {who(h.to)}: {h.what}"
                                                   for h in contract.hands_off))
    if contract.done_when:
        lines.append("Your work is done when:\n" + "\n".join(f"- {d}" for d in contract.done_when))
    if contract.checked_by:
        lines.append(f"Your work is checked by {who(contract.checked_by)}.")
    if contract.escalates_when:
        lines.append("Stop and say so (status `blocked`, or a note) when:\n"
                     + "\n".join(f"- {e}" for e in contract.escalates_when))
    if contract.may_not:
        lines.append("You may not:\n" + "\n".join(f"- {m}" for m in contract.may_not))
    if contract.rules:
        lines.append("Rules you work by:\n" + "\n".join(f"- {r}" for r in contract.rules))
    return "\n\n".join(lines)


def with_rules(contract: Contract, rules: Iterable[str]) -> Contract:
    """A copy with extra rules (project rules proposed through drafts, plan §6)."""
    extra = [r for r in rules if r not in contract.rules]
    return replace(contract, rules=contract.rules + tuple(extra)) if extra else contract


__all__ = [
    "ARTIFACTS", "Contract", "ContractError", "DEFAULT_FOR_ROLE_TYPE", "Handoff", "LIBRARY_DIR",
    "Measure", "contract_id_for", "contracts_for_team", "load_for_project", "load_library",
    "team_owner",
    "parse_contract", "project_dir", "render_for_brief", "with_rules",
]
