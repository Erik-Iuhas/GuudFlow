"""Apply approved team ops (add_role / edit_role / remove_role) to a project's `agents`.

The ops come from `ApproveResult.team_changes` (plan §10.2). The team lives in the project doc
(`data/projects.json`), not in work.db, so the approve route applies them here. The new team
applies from the next session; a running session never changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.harness.capabilities import infer_role_type

# Role types that build from cards: with a session planner on the team they must come after it
# (directly or through another role), or the task board never gives them work.
_CARD_BUILDERS = frozenset({"builder", "frontend_builder", "tester"})

DEFAULT_REVIEW_ROUNDS = 2
_EDITABLE = ("role_type", "description", "model_tier", "depends_on", "review_of",
             "max_review_rounds", "name", "capabilities", "contract")


class TeamChangeError(ValueError):
    """A team op does not fit the project's current team."""


def apply_team_changes(agents: list[dict[str, Any]],  # Any: agent rows
                       changes: list[dict[str, Any]],  # Any: dumped team Changes
                       ) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:  # Any: agent rows
    """The new `agents` list and `{added, edited, removed}` role names.

    An added role gets the defaults of the team approval screen: capabilities left empty (the
    role type's defaults apply), `is_reviewer` for a reviewer, two review rounds. Removing a role
    drops it from the others' `depends_on` and `review_of`. Raises TeamChangeError, changing
    nothing, when an op names a role that is missing (or already there).
    """
    team = [dict(a) for a in agents]
    summary: dict[str, list[str]] = {"added": [], "edited": [], "removed": []}
    for ch in changes:
        op, role = ch.get("op"), str(ch.get("role", ""))
        row = next((a for a in team if a.get("role") == role), None)
        if op == "add_role":
            if row is not None:
                raise TeamChangeError(f"role {role!r} already exists")
            role_type = str(ch["role_type"])
            team.append({
                "role": role, "description": ch.get("description", ""),
                "depends_on": list(ch.get("depends_on") or []),
                "model_tier": ch.get("model_tier", "solid"), "role_type": role_type,
                "is_reviewer": role_type == "reviewer",
                "review_of": list(ch.get("review_of") or []),
                "max_review_rounds": ch.get("max_review_rounds") or DEFAULT_REVIEW_ROUNDS,
                "capabilities": dict(ch.get("capabilities") or {}),
                **({"name": ch["name"]} if ch.get("name") else {}),
                **({"contract": ch["contract"]} if ch.get("contract") else {})})
            summary["added"].append(role)
        elif op == "edit_role":
            if row is None:
                raise TeamChangeError(f"role {role!r} is not on the team")
            for key in _EDITABLE:
                if key in ch:
                    row[key] = ch[key]
            if "role_type" in ch:
                row["is_reviewer"] = infer_role_type(role, declared=str(ch["role_type"])) \
                    == "reviewer"
            if role not in summary["edited"]:
                summary["edited"].append(role)
        elif op in ("set_kind", "add_contract_rule", "add_skill"):
            continue                     # not the team: see kind_change, contract_rules, skills
        elif op == "remove_role":
            if row is None:
                raise TeamChangeError(f"role {role!r} is not on the team")
            team.remove(row)
            for other in team:
                for key in ("depends_on", "review_of"):
                    if role in (other.get(key) or []):
                        other[key] = [r for r in other[key] if r != role]
            summary["removed"].append(role)
        else:
            raise TeamChangeError(f"unknown team op {op!r}")
    return team, summary


def kind_change(changes: list[dict[str, Any]]) -> dict[str, str]:  # Any: dumped Changes
    """The project doc fields a ticked `set_kind` sets (`kind`, maybe `start_mode`); empty when
    the draft has none. The last one wins."""
    out: dict[str, str] = {}
    for ch in changes:
        if ch.get("op") == "set_kind":
            out = {"kind": str(ch["kind"])}
            if ch.get("start_mode"):
                out["start_mode"] = str(ch["start_mode"])
    return out


def contract_rules(changes: list[dict[str, Any]]) -> list[tuple[str, str]]:  # Any: Changes
    """(contract, rule) for each ticked `add_contract_rule`, in order."""
    return [(str(ch["contract"]), str(ch["rule"])) for ch in changes
            if ch.get("op") == "add_contract_rule"]


def write_skills(data_root: Path, project_id: str,
                 changes: list[dict[str, Any]]) -> list[str]:  # Any: dumped Changes
    """Write each ticked `add_skill` as `data/projects/{id}/skills/<name>/SKILL.md` (worker
    context plan R5); returns the names written. Workers get them from their next spawn."""
    import re
    written: list[str] = []
    root = Path(data_root) / "projects" / project_id / "skills"
    for ch in changes:
        if ch.get("op") != "add_skill":
            continue
        name = str(ch["name"])
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,39}", name):
            raise TeamChangeError(f"bad skill name {name!r}")
        description = " ".join(str(ch["description"]).split())
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n"
            f"{str(ch['body']).strip()}\n", encoding="utf-8")
        written.append(name)
    return written


def missing_roles(agents: list[dict[str, Any]]) -> list[str]:  # Any: agent rows
    """The `team_problems` lines about roles named but not on the team (always a mistake)."""
    return [p for p in team_problems(agents) if p.endswith(_NOT_ON_TEAM)]


_NOT_ON_TEAM = "which is not on the team"


def team_problems(agents: list[dict[str, Any]],  # Any: agent rows
                  contracts: Any = None) -> list[str]:  # Any: Mapping[str, Contract]
    """What would stop a team from working, one line each (empty when it is fine):

    - a `depends_on` / `review_of` naming a role that is not on the team;
    - more than one role whose contract owns the session plan (e.g. a tech lead added next to
      the old planner): only one plans, the other idles;
    - a builder that does not come after the session planner, so it would get no cards.

    `contracts` is the contract library (default: the shipped one)."""
    from core.contracts import contract_id_for, load_library
    lib = contracts if contracts is not None else load_library()
    rows = [a for a in agents if isinstance(a, dict) and a.get("role")]
    roles = {str(a["role"]) for a in rows}
    problems: list[str] = []
    for a in rows:
        for key in ("depends_on", "review_of"):
            missing = [r for r in (a.get(key) or []) if r not in roles]
            if missing:
                problems.append(f"{a['role']}: {key} names {', '.join(missing)}, "
                                + _NOT_ON_TEAM)
    types = {str(a["role"]): infer_role_type(str(a["role"]), is_reviewer=bool(a.get("is_reviewer")),
                                             declared=str(a.get("role_type") or ""))
             for a in rows}
    planners = [str(a["role"]) for a in rows
                if (cid := contract_id_for(a, types[str(a["role"])])) in lib
                and lib[cid].may_change("session_plan")]
    if len(planners) > 1:
        problems.append(f"{' and '.join(planners)} both plan sessions; keep one (change the "
                        f"old planner into the new role instead of adding a second)")
        return problems
    if not planners:
        return problems
    planner = planners[0]
    if types[planner] not in ("planner", "architect"):
        problems.append(f"{planner} plans the sessions but its role_type is {types[planner]}; "
                        f"keep it architect (tech lead) or planner so it writes the cards")
    deps = {str(a["role"]): set(a.get("depends_on") or []) for a in rows}

    def after_planner(role: str, seen: frozenset[str] = frozenset()) -> bool:
        return any(d == planner or (d not in seen and after_planner(d, seen | {role}))
                   for d in deps.get(role, ()))

    for role, role_type in types.items():
        if role != planner and role_type in _CARD_BUILDERS and not after_planner(role):
            problems.append(f"{role} does not come after the planner {planner}, so it would get "
                            f"no cards; add {planner} to its depends_on")
    return problems
