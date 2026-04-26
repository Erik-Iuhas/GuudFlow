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


def dedupe_role_names(roles: list[str]) -> list[str]:
    """Ensure role names are unique within a team (Phase D.0).

    The meta-agent's system prompt instructs it to use descriptive
    distinct names (e.g. `auth_dev`, `api_dev` instead of
    `developer_1`, `developer_2`), but if the LLM produces collisions
    anyway this helper appends `_2`, `_3`, ... suffixes deterministically.

    Order is preserved. Names that don't collide are returned unchanged.

    Belt-and-suspenders: the scheduler ALSO raises on duplicate roles
    (`core/scheduler.py:95`) so even if this helper is bypassed, a
    collision still surfaces — just less gracefully.
    """
    seen: set[str] = set()
    out: list[str] = []
    for name in roles:
        if name not in seen:
            seen.add(name)
            out.append(name)
            continue
        # Collision — append a numeric suffix until unique.
        n = 2
        candidate = f"{name}_{n}"
        while candidate in seen:
            n += 1
            candidate = f"{name}_{n}"
        seen.add(candidate)
        out.append(candidate)
    return out


def suggest_persona_id(role: str, existing_ids: set[str]) -> str:
    """Generate a globally-unique persona id for a new hire (Phase D.0).

    Format: `{role}-{adjective}-v1`, falling back to `{role}-v{n}` if
    every adjective collides. The meta-agent uses this when
    recommending "create new persona" so the id doesn't conflict with
    the existing roster.

    Adjective list is small + intentional: not random, not exhaustive.
    Real personas can be renamed later by the user; this just gets the
    initial id past the uniqueness check.
    """
    adjectives = (
        "aria", "nova", "atlas", "echo", "vega", "lyra", "orion",
        "ember", "sage", "kai", "iris", "rowan", "june",
    )
    for adj in adjectives:
        candidate = f"{role}-{adj}-v1"
        if candidate not in existing_ids:
            return candidate
    n = 2
    while f"{role}-v{n}" in existing_ids:
        n += 1
    return f"{role}-v{n}"


def list_all_archetypes() -> list[ArchetypeMeta]:
    """Union of the runtime catalog (`list_archetypes()`) and the
    YAML doc catalog (`docs/worker-archetypes.yaml`).

    Used by the meta-agent (Phase D) at proposal time so it can pick
    from BOTH shipped concrete classes AND described-only archetypes.
    Runtime entries win on name conflict — the shipped class is
    authoritative because it carries actual code.

    YAML entries that fail to load (file missing, malformed) don't
    crash the call; they're skipped silently and the runtime catalog
    alone is returned. Drift is loud (the parser raises) when the
    YAML is loaded directly via `core.archetype_doc.load_archetypes`,
    but the catalog endpoint stays available even with a broken doc.
    """
    runtime = list_archetypes()
    runtime_names = {m.name for m in runtime}

    try:
        from core.archetype_doc import load_archetypes
        doc_entries = load_archetypes()
    except Exception:
        doc_entries = []

    extras: list[ArchetypeMeta] = []
    for entry in doc_entries:
        if entry.name in runtime_names:
            continue  # shipped class wins
        meta_dict = entry.to_meta_dict()
        extras.append(ArchetypeMeta(
            name=meta_dict["name"],
            abstract_base=meta_dict["abstract_base"],
            description=meta_dict["description"],
            when_to_use=meta_dict["when_to_use"],
            default_model_tier=meta_dict["default_model_tier"],
            default_tools=meta_dict["default_tools"],
            default_rbac_scopes=meta_dict["default_rbac_scopes"],
        ))

    out = runtime + extras
    out.sort(key=lambda m: (m.abstract_base, m.name))
    return out
