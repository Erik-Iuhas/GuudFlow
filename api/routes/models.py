"""GET /api/models — returns the model registry for studio dropdowns."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/models")
async def list_models() -> dict:
    """Return the install's models (Settings → Models & providers, else data/models.json).

    Used by the studio cost estimator and model picker dropdowns.
    The registry is read fresh on each call so live edits to models.json
    are reflected without a server restart.
    """
    from core import storage
    from core.llm.registry import ModelRegistry
    reg = ModelRegistry.for_data_root(storage.data_root())
    return {
        "tiers": reg.tiers(),
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
                "cache_write_per_1k": m.cache_write_price,
                "supports_images": m.supports_images,
                "deprecated": m.deprecated,
                "replacement": m.replacement,
            }
            for m in reg.list_models()
        ]
    }
