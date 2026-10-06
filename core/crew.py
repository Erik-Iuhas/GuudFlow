"""Every worker has a name: the crew of a project (a small work simulator touch).

A role's name is, in order: the name set on the team (`AgentConfig.name`), the name of the
persona playing it, or a stable pick from NAME_POOL seeded by the project and the role, so
the same person keeps the same seat run after run. Names are unique within a team. Agents
see their teammates by name in their brief and may address notes by name or role; signals
keep the role as `from_agent`, and a TEAM signal at run start tells Studio who is who.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

NAME_POOL: tuple[str, ...] = (
    "Ada", "Bea", "Cass", "Dev", "Eli", "Fern", "Gus", "Hana", "Ines", "Jules", "Kofi", "Lena",
    "Milo", "Nia", "Omar", "Pia", "Quinn", "Ravi", "Sana", "Theo", "Uma", "Vik", "Wren", "Xia",
    "Yara", "Zed", "Arlo", "Bram", "Cleo", "Dara", "Emil", "Freya", "Ivo", "Juno", "Kai", "Lior",
    "Maya", "Noor", "Otto", "Remy", "Sol", "Tove",
)


@dataclass(frozen=True)
class Member:
    """One seat on the team."""

    role: str
    name: str
    role_type: str = ""
    description: str = ""

    @property
    def label(self) -> str:
        """How others see this worker: "Maya (builder)"."""
        return label(self.name, self.role)


def label(name: str, role: str) -> str:
    """"Maya (builder)"; just the role when the name is the role itself."""
    return role if not name or name.lower() == role.lower() else f"{name} ({role})"


def _pick(project_id: str, role: str, taken: set[str]) -> str:
    """A stable name for (project, role) that nobody on the team has yet."""
    start = int(hashlib.sha256(f"{project_id}:{role}".encode()).hexdigest(), 16) % len(NAME_POOL)
    for step in range(len(NAME_POOL)):
        name = NAME_POOL[(start + step) % len(NAME_POOL)]
        if name.lower() not in taken:
            return name
    return f"{role.capitalize()}-{len(taken) + 1}"  # a team bigger than the pool


def crew_names(project_id: str, roles: Iterable[str], *,
               explicit: Mapping[str, str | None] | None = None,
               persona_names: Mapping[str, str | None] | None = None) -> dict[str, str]:
    """role -> name for a team: explicit names, then persona names, then stable picks."""
    roles = list(roles)
    names: dict[str, str] = {}
    taken: set[str] = set()
    for source in (explicit or {}, persona_names or {}):
        for role in roles:
            given = (source.get(role) or "").strip()
            if role not in names and given:
                names[role] = given
                taken.add(given.lower())
    for role in roles:
        if role not in names:
            names[role] = _pick(project_id, role, taken)
            taken.add(names[role].lower())
    return names


def name_agents(project_id: str, agents: list[dict[str, object]],
                persona_names: Mapping[str, str | None] | None = None) -> list[dict[str, object]]:
    """Fill in `name` on project agent dicts that have none (same picks as at run time)."""
    roles = [str(a.get("role") or "") for a in agents if a.get("role")]
    names = crew_names(project_id, roles,
                       explicit={str(a.get("role")): str(a.get("name") or "") for a in agents},
                       persona_names=persona_names)
    return [{**a, "name": names.get(str(a.get("role") or ""), a.get("name"))} if a.get("role")
            else a for a in agents]


def resolve_recipient(key: str, names: Mapping[str, str]) -> str | None:
    """The role a note is for, addressed by role or by name (case-insensitive)."""
    wanted = key.strip().lower()
    for role, name in names.items():
        if wanted in (role.lower(), name.lower(), label(name, role).lower()):
            return role
    return None


def team_section(me: str, members: Iterable[Member]) -> str:
    """The brief's "Your team" section: who you are and who your teammates are."""
    members = list(members)
    mine = next((m for m in members if m.role == me), None)
    lines = [f"You are **{mine.name}**, the team's {me}." if mine else f"You are the team's {me}."]
    others = [m for m in members if m.role != me]
    if others:
        lines.append("Your teammates (call them by name):")
        for m in others:
            job = f": {m.description.strip()}" if m.description.strip() else ""
            lines.append(f"- **{m.name}**, {m.role}{job}")
        lines.append("Address a note with `notes_for` keyed by a teammate's name or role "
                     f"(for example {others[0].name}).")
    return "\n".join(lines)
