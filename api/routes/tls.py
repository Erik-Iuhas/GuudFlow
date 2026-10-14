"""Certificate (Studio Settings): the company CA to trust, as a backup for GUUDFLOW_CA_BUNDLE.

  GET /api/tls    both paths (.env and Studio), which one is used, the certificates it holds
  PUT /api/tls    save the Studio path ({"ca_bundle": "<path>"}, empty clears it)

The .env path wins when it points to a file; the Studio path is used when .env has none or its
file is not found. A saved CA is trusted from the next connection on. See core/tls.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import require_bearer
from core import tls

router = APIRouter(prefix="/api/tls", tags=["tls"], dependencies=[Depends(require_bearer)])


class CaBody(BaseModel):
    ca_bundle: str = ""


@router.get("")
async def get_tls() -> dict[str, object]:
    """Where the CA comes from and what it holds."""
    return tls.status()


@router.put("")
async def put_tls(body: CaBody) -> dict[str, object]:
    """Save the CA path from Studio; 400 when the file is missing or holds no certificate."""
    try:
        tls.save_app_setting(body.ca_bundle)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return tls.status()
