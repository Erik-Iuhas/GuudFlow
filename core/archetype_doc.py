"""Archetype YAML loader (Phase D.0).

Reads `docs/worker-archetypes.yaml` into structured records the
meta-agent's proposal flow can consume. Same `ArchetypeMeta` shape
as the runtime catalog (`agents/catalog.py`) so the two can be
unioned without further reshaping.

The YAML is the canonical source for archetype design data; the
human-readable `docs/worker-archetypes.md` is a derivative view.
The meta-agent's prompt is built from the YAML (compact tokens) —
the markdown is for humans browsing the catalog.

Why YAML, not the markdown:
  - Compact: no header noise, no prose paragraphs the meta-agent
    doesn't need at picking time
  - Structured: explicit fields, no fragile "look for the next H2"
    parsing
  - Same record shape as the registry-derived catalog, so
    `list_all_archetypes()` can merge them with a single dedupe pass
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import yaml


# Resolve to repo-root/docs/worker-archetypes.yaml unless overridden.
# Tests pass an explicit path; production code uses the default.
_DEFAULT_YAML_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "worker-archetypes.yaml"
)


@dataclass(frozen=True)
class ArchetypeDocEntry:
    """One archetype as defined in `docs/worker-archetypes.yaml`.

    Mirrors `agents.catalog.ArchetypeMeta` but adds the YAML-only
    fields (`shipped`, `responsibility`, `wake_on_signal_type`).

    Use `to_meta_dict()` to project down to the same dict shape the
    catalog endpoint emits — the meta-agent's prompt-building code
    works against either source.
    """

    name: str
    abstract_base: str               # "reviewer" | "escalation_handler" | "specialist"
    shipped: bool
    default_model_tier: str          # "heavy" | "solid" | "fast"
    default_tools: list[str]
    default_rbac_scopes: list[str]
    responsibility: str
    when_to_use: str
    wake_on_signal_type: str | None = None  # escalation_handler only

    def to_meta_dict(self) -> dict[str, Any]:
        """Project to the same shape the runtime catalog endpoint
        returns — the meta-agent's prompt builder consumes either."""
        return {
            "name": self.name,
            "abstract_base": self.abstract_base,
            "default_model_tier": self.default_model_tier,
            "default_tools": list(self.default_tools),
            "default_rbac_scopes": list(self.default_rbac_scopes),
            # Description / when_to_use map onto the catalog's same
            # field names so the prompt template doesn't branch.
            "description": self.responsibility.strip().split("\n")[0],
            "when_to_use": self.when_to_use.strip(),
        }


class ArchetypeDocError(ValueError):
    """Raised on malformed YAML (missing required field, wrong type)."""


_VALID_BASES = {"reviewer", "escalation_handler", "specialist"}
_VALID_TIERS = {"heavy", "solid", "fast"}
_REQUIRED_FIELDS = (
    "name",
    "abstract_base",
    "shipped",
    "default_model_tier",
    "default_tools",
    "default_rbac_scopes",
    "responsibility",
    "when_to_use",
)


def load_archetypes(path: Path | str | None = None) -> list[ArchetypeDocEntry]:
    """Read the archetype YAML and return structured entries.

    Returns an empty list if the file doesn't exist (e.g., in a
    minimal install). Raises `ArchetypeDocError` on malformed entries
    so configuration drift surfaces loudly.
    """
    yaml_path = Path(path) if path is not None else _DEFAULT_YAML_PATH
    if not yaml_path.exists():
        return []

    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ArchetypeDocError(f"top-level YAML must be a mapping, got {type(raw).__name__}")
    rows = raw.get("archetypes") or []
    if not isinstance(rows, list):
        raise ArchetypeDocError("`archetypes` must be a list")

    out: list[ArchetypeDocEntry] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ArchetypeDocError(f"archetypes[{i}] must be a mapping")
        for field in _REQUIRED_FIELDS:
            if field not in row:
                raise ArchetypeDocError(
                    f"archetypes[{i}] ({row.get('name', '<unnamed>')}) "
                    f"missing required field: {field}"
                )

        if row["abstract_base"] not in _VALID_BASES:
            raise ArchetypeDocError(
                f"archetypes[{i}] ({row['name']}): abstract_base must be one of "
                f"{sorted(_VALID_BASES)}, got {row['abstract_base']!r}"
            )
        if row["default_model_tier"] not in _VALID_TIERS:
            raise ArchetypeDocError(
                f"archetypes[{i}] ({row['name']}): default_model_tier must be one of "
                f"{sorted(_VALID_TIERS)}, got {row['default_model_tier']!r}"
            )
        if not isinstance(row["default_tools"], list):
            raise ArchetypeDocError(
                f"archetypes[{i}] ({row['name']}): default_tools must be a list"
            )
        if not isinstance(row["default_rbac_scopes"], list):
            raise ArchetypeDocError(
                f"archetypes[{i}] ({row['name']}): default_rbac_scopes must be a list"
            )

        out.append(ArchetypeDocEntry(
            name=str(row["name"]),
            abstract_base=str(row["abstract_base"]),
            shipped=bool(row["shipped"]),
            default_model_tier=str(row["default_model_tier"]),
            default_tools=[str(t) for t in row["default_tools"]],
            default_rbac_scopes=[str(s) for s in row["default_rbac_scopes"]],
            responsibility=str(row["responsibility"]),
            when_to_use=str(row["when_to_use"]),
            wake_on_signal_type=(
                str(row["wake_on_signal_type"])
                if row.get("wake_on_signal_type")
                else None
            ),
        ))
    return out


def get_doc_archetype(name: str, path: Path | str | None = None) -> ArchetypeDocEntry | None:
    """Look up one archetype from the YAML by name."""
    for entry in load_archetypes(path):
        if entry.name == name:
            return entry
    return None
