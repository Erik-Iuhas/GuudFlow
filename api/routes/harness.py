"""/api/harness — capability metadata and resolution for the team approval screen (S17)."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.auth import require_bearer
from core.harness.capabilities import (
    HOST_DEV_WARNING,
    LEVELS,
    ROLE_TYPES,
    Capabilities,
    CapabilityError,
    defaults_for,
    gate_vision,
    infer_role_type,
)

router = APIRouter(prefix="/api/harness", tags=["harness"], dependencies=[Depends(require_bearer)])



def _production() -> bool:
    return os.environ.get("GUUDFLOW_ENV") == "production"


class ResolveRequest(BaseModel):
    """Body for POST /api/harness/capabilities/resolve (API boundary model)."""

    model_config = ConfigDict(protected_namespaces=())

    role: str
    role_type: str | None = None
    is_reviewer: bool = False
    model_id: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)


@router.get("/capabilities")
async def get_capabilities() -> dict:
    """Toggle levels, role types and per-role-type defaults for the switches UI."""
    return {
        "levels": {name: list(values) for name, values in LEVELS.items()},
        "role_types": list(ROLE_TYPES),
        "defaults": {rt: defaults_for(rt).to_dict() for rt in ROLE_TYPES},
        "host_dev_warning": HOST_DEV_WARNING,
        "production": _production(),
    }


@router.post("/capabilities/resolve")
async def resolve_capabilities(body: ResolveRequest) -> dict:
    """Merge overrides onto the role-type defaults and gate vision by the model."""
    if body.role_type is not None and body.role_type not in ROLE_TYPES:
        raise HTTPException(status_code=422, detail=f"unknown role type {body.role_type!r}")
    role_type = infer_role_type(body.role, is_reviewer=body.is_reviewer,
                                declared=body.role_type or "")
    try:
        caps = Capabilities.from_dict(body.capabilities, base=defaults_for(role_type))
    except (CapabilityError, TypeError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    if caps.sandbox == "host_dev" and _production():
        raise HTTPException(status_code=422, detail="sandbox 'host_dev' is refused in production")

    vision_available = True
    warnings: list[str] = []
    if body.model_id:
        from core.llm.registry import ModelRegistry
        from core import storage
        info = ModelRegistry.for_data_root(storage.data_root()).get(body.model_id)
        vision_available = bool(info and info.supports_images)
        gated = gate_vision(caps, vision_available)
        if gated.vision != caps.vision:
            warnings.append("vision turned off: model has no image input")
        caps = gated
    if caps.sandbox == "host_dev":
        warnings.append(HOST_DEV_WARNING)
    return {
        "role_type": role_type,
        "capabilities": caps.to_dict(),
        "vision_available": vision_available,
        "errors": [],
        "warnings": warnings,
    }
