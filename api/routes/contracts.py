"""Role contracts for Studio (role contracts plan §2): the library, and a project's team with
the contract each role works under."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import require_bearer
from core.contracts import ContractError, contract_id_for, load_for_project, load_library
from core.harness.capabilities import infer_role_type

router = APIRouter(prefix="/api", tags=["contracts"], dependencies=[Depends(require_bearer)])


@router.get("/contracts")
async def library() -> dict[str, Any]:  # Any: JSON
    """Every shipped contract."""
    try:
        lib = await asyncio.to_thread(load_library)
    except ContractError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None
    return {"contracts": [c.to_dict() for c in lib.values()]}


@router.get("/projects/{project_id}/contracts")
async def project_contracts(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """The contracts this project's team works under (library plus the project's overrides)."""
    doc = await request.app.state.project_store.get(project_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"no project {project_id}")
    data_root = Path(request.app.state.data_root)
    try:
        contracts = await asyncio.to_thread(load_for_project, data_root, project_id)
    except ContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    team = []
    for row in doc.get("agents") or []:
        role = str(row.get("role") or "")
        role_type = infer_role_type(role, is_reviewer=bool(row.get("is_reviewer")),
                                    declared=str(row.get("role_type") or ""))
        cid = contract_id_for(row, role_type)
        team.append({"role": role, "name": row.get("name"), "role_type": role_type,
                     "contract": cid if cid in contracts else None})
    return {"contracts": [c.to_dict() for c in contracts.values()], "team": team}
