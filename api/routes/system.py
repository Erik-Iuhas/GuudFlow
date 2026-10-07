"""Host health for Studio: disk space and a prune of sandbox leftovers (FIELD_NOTES F2)."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request

from api.auth import require_bearer
from core.disk import disk_report, prune

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_bearer)])


def _root(request: Request) -> Path:
    return Path(request.app.state.data_root)


@router.get("/disk")
async def get_disk(request: Request) -> dict[str, Any]:  # Any: JSON
    """Free space where GuudFlow keeps its data, and whether it is low."""
    report = disk_report(_root(request))
    return {**asdict(report), "low": report.low}


@router.post("/prune")
async def post_prune(request: Request) -> dict[str, Any]:  # Any: JSON
    """Remove Docker build cache, dangling images and stopped sandbox containers."""
    result = await asyncio.to_thread(prune, _root(request))
    report = disk_report(_root(request))
    return {**asdict(result), "disk": {**asdict(report), "low": report.low}}
