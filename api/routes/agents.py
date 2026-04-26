"""/api/agents — read-only views of the agent archetype catalog.

Phase C.0. The catalog is populated at startup by
`agents.builtin.register_builtins()`; this route exposes it as JSON
so the meta-agent (Phase D) and the studio can list available
archetypes when proposing or customising a team.

No auth required — purely informational.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agents.catalog import get_archetype, list_archetypes


router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/catalog")
async def get_catalog() -> dict:
    """List every registered archetype with its metadata."""
    return {
        "archetypes": [m.to_dict() for m in list_archetypes()],
    }


@router.get("/catalog/{name}")
async def get_catalog_entry(name: str) -> dict:
    """Look up one archetype by registry name."""
    meta = get_archetype(name)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"archetype {name!r} not found")
    return meta.to_dict()
