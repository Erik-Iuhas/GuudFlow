"""/api/agents — read-only views of the agent archetype catalog.

Phase C.0. The catalog is populated at startup by
`agents.builtin.register_builtins()`; this route exposes it as JSON
so the meta-agent (Phase D) and the studio can list available
archetypes when proposing or customising a team.

Phase F.2 — `GET /catalog/status` adds the "shipped vs described" view
across runtime catalog ∪ YAML, with per-archetype training-camp
coverage so users can see which archetypes have scenarios authored.

No auth required — purely informational.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from agents.catalog import get_archetype, list_all_archetypes, list_archetypes
from core.training.scenario import Scenario, list_scenarios


router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/catalog")
async def get_catalog() -> dict:
    """List every registered archetype with its metadata."""
    return {
        "archetypes": [m.to_dict() for m in list_archetypes()],
    }


@router.get("/catalog/status")
async def get_catalog_status(request: Request) -> dict:
    """Shipped vs described overview across runtime ∪ YAML catalog.

    For each archetype, returns:
      - `shipped` — true if a concrete class is in the runtime catalog,
        false if only described in `docs/worker-archetypes.yaml`
      - `n_scenarios` — count of training scenarios whose
        `expected_role_types` includes this archetype's name

    Phase F.2 is mostly a content phase (training-camp tournaments
    grow `docs/worker-archetypes.yaml` with validated variations and
    eventually graduate them to shipped classes). This endpoint is
    the platform-side surface so the studio + meta-agent can see
    which archetypes are battle-tested.
    """
    runtime_names = {m.name for m in list_archetypes()}
    all_archetypes = list_all_archetypes()

    scenarios = list_scenarios(_scenarios_root(request))
    scenario_role_counts: dict[str, int] = {}
    for s in scenarios:
        for role_type in s.expected_role_types:
            scenario_role_counts[role_type] = scenario_role_counts.get(role_type, 0) + 1

    rows = [
        {
            **m.to_dict(),
            "shipped": m.name in runtime_names,
            "n_scenarios": scenario_role_counts.get(m.name, 0),
        }
        for m in all_archetypes
    ]
    rows.sort(key=lambda r: (not r["shipped"], r["name"]))

    return {
        "archetypes": rows,
        "n_total": len(rows),
        "n_shipped": sum(1 for r in rows if r["shipped"]),
        "n_described_only": sum(1 for r in rows if not r["shipped"]),
        "n_scenarios": len(scenarios),
    }


def _scenarios_root(request: Request) -> Path:
    """Mirror the helper in `routes/training.py` so this endpoint can
    discover the same scenarios."""
    override = getattr(request.app.state, "training_scenarios_root", None)
    if override is not None:
        return Path(override)
    return Path(request.app.state.data_root) / "training_camp" / "scenarios"


@router.get("/catalog/{name}")
async def get_catalog_entry(name: str) -> dict:
    """Look up one archetype by registry name."""
    meta = get_archetype(name)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"archetype {name!r} not found")
    return meta.to_dict()
