"""GET /api/tools — built-in tool registry for the studio tools dropdown."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import require_bearer
from core.runner import LOCAL_TOOL_CLASSES
from core.tool_manifest import BASELINE_LOCAL_SCOPES

router = APIRouter(prefix="/api/tools", tags=["tools"])


def _build_catalog() -> list[dict]:
    """Build the tool catalog once from LOCAL_TOOL_CLASSES.

    For each tool, computes:
    - scope: str (== tool name, per DESIGN §31)
    - baseline: bool (in BASELINE_LOCAL_SCOPES)
    - granted_by_archetypes: list[str] (archetypes whose default_rbac_scopes include this scope)
    """
    from agents.catalog import list_archetypes

    archetypes = list_archetypes()
    # Build a map: scope -> list of archetype names that grant it
    scope_to_archetypes: dict[str, list[str]] = {}
    for meta in archetypes:
        for scope in meta.default_rbac_scopes:
            scope_to_archetypes.setdefault(scope, []).append(meta.name)

    catalog = []
    for cls in LOCAL_TOOL_CLASSES:
        scope = cls.name
        entry: dict = {
            "name": cls.name,
            "scope": scope,
            "description": cls.description,
            "input_schema": getattr(cls, "input_schema", {}) or {},
            "baseline": scope in BASELINE_LOCAL_SCOPES,
        }
        granted_by = scope_to_archetypes.get(scope)
        if granted_by:
            entry["granted_by_archetypes"] = sorted(granted_by)
        catalog.append(entry)
    return catalog


@router.get("")
async def list_tools(_: None = Depends(require_bearer)) -> dict:
    """Return the full built-in tool catalog with RBAC metadata."""
    return {"tools": _build_catalog()}
