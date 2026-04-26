"""Agent archetype catalog (Phase C.0).

Reads the metadata declared on every `AgentArchetype` subclass
(`agents/abstract.py`) and exposes it as a flat list of
`ArchetypeMeta`. Consumed by:

- The meta-agent (Phase D) when proposing a team — it picks
  archetypes by `archetype_kind` + `archetype_when_to_use`
- The studio's catalog view (later) — same list, rendered for
  humans browsing the available archetypes
- `GET /api/agents/catalog` (this chunk) — the read-only HTTP surface

Builtins NOT in the catalog
---------------------------
`MemoryWriter`, `VisualChecker`, `TestRunner`, `ITAgent` — these are
synthesis / side-task agents whose `run()` overrides the generic
loop. They could be added later if they fit a new archetype kind, but
forcing them into the current taxonomy would be premature.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Type

from agents.abstract import AgentArchetype
from agents.base import BaseAgent
from agents.registry import registry


@dataclass(frozen=True)
class ArchetypeMeta:
    """One row in the catalog. The meta-agent reads `name` to populate
    `AgentConfig.agent_class_role_type`, and the rest to populate the
    other AgentConfig fields with sensible defaults."""

    name: str                         # registry role_type
    abstract_base: str                # "reviewer" | "escalation_handler" | "specialist"
    description: str
    when_to_use: str
    default_model_tier: str           # "heavy" | "solid" | "fast"
    default_tools: list[str]
    default_rbac_scopes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _archetype_meta_from_class(name: str, cls: Type[BaseAgent]) -> ArchetypeMeta | None:
    """Extract metadata from an `AgentArchetype` subclass. Returns
    `None` if `cls` isn't an archetype (e.g. ITAgent, MemoryWriter
    extend BaseAgent directly and are intentionally excluded from
    the catalog for now)."""
    if not issubclass(cls, AgentArchetype):
        return None
    if not cls.archetype_kind:
        # Intermediate base (Reviewer / EscalationHandler / Specialist
        # itself) — only concrete subclasses with a populated kind via
        # the intermediate base, NOT the bases themselves, end up here.
        # Skip if somehow registered.
        return None
    return ArchetypeMeta(
        name=name,
        abstract_base=cls.archetype_kind,
        description=cls.archetype_description or (cls.__doc__ or "").strip(),
        when_to_use=cls.archetype_when_to_use or "",
        default_model_tier=cls.default_model_tier,
        default_tools=list(cls.default_tools),
        default_rbac_scopes=list(cls.default_rbac_scopes),
    )


def list_archetypes() -> list[ArchetypeMeta]:
    """Return metadata for every registered class that's an archetype.
    Sorted by `(abstract_base, name)` for deterministic output."""
    out: list[ArchetypeMeta] = []
    for name in registry.all_role_types():
        cls = registry.get(name)
        meta = _archetype_meta_from_class(name, cls)
        if meta is not None:
            out.append(meta)
    out.sort(key=lambda m: (m.abstract_base, m.name))
    return out


def get_archetype(name: str) -> ArchetypeMeta | None:
    """Look up one archetype by registry name; returns None if the
    name isn't registered or isn't an archetype."""
    if not registry.has(name):
        return None
    cls = registry.get(name)
    return _archetype_meta_from_class(name, cls)
