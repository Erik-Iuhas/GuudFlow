"""GET /api/models — returns the model registry for studio dropdowns."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

_REGISTRY_PATH = Path("data/models.json")


@router.get("/api/models")
async def list_models() -> dict:
    """Return all models and providers from data/models.json.

    Used by the studio cost estimator and model picker dropdowns.
    The registry is read fresh on each call so live edits to models.json
    are reflected without a server restart.
    """
    from core.llm.registry import ModelRegistry
    reg = ModelRegistry(_REGISTRY_PATH)
    return {
        "models": [
            {
                "id": m.id,
                "provider": m.provider,
                "tier": m.tier,
                "supports_tools": m.supports_tools,
                "max_context_tokens": m.max_context_tokens,
                "max_output_tokens": m.max_output_tokens,
                "cost_per_1k_input": m.cost_per_1k_input,
                "cost_per_1k_output": m.cost_per_1k_output,
                "cache_read_per_1k": m.cache_read_per_1k,
                "deprecated": m.deprecated,
                "replacement": m.replacement,
            }
            for m in reg.list_models()
        ]
    }
