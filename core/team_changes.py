"""Apply approved team ops (add_role / edit_role / remove_role) to a project's `agents`.

The ops come from `ApproveResult.team_changes` (plan §10.2). The team lives in the project doc
(`data/projects.json`), not in work.db, so the approve route applies them here. The new team
applies from the next session; a running session never changes.
"""

from __future__ import annotations

from typing import Any

from core.harness.capabilities import infer_role_type

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
