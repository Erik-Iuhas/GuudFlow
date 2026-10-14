"""Models & providers (Studio Settings): providers, models, prices and tier choices.

  GET    /api/model-config                         effective config + key status per provider
  PUT    /api/model-config                         validate and save the install's config
  POST   /api/model-config/reset                   back to the shipped defaults
  PUT    /api/model-config/providers/{name}/key    store a key on the host
  DELETE /api/model-config/providers/{name}/key    forget a stored key
  POST   /api/model-config/providers/{name}/test   list the provider's models with the key

Keys are write-only: no response ever contains one, only where it comes from
(`env`, `stored`, `local` or `missing`). See core/model_config.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth import require_bearer
from core import model_config
from core.model_config import KeyStore, ModelConfigError

router = APIRouter(prefix="/api/model-config", tags=["model-config"],
                   dependencies=[Depends(require_bearer)])

ANTHROPIC_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
TEST_TIMEOUT = 10.0


def _root(request: Request) -> Path:
    return Path(request.app.state.data_root)


def _view(request: Request) -> dict[str, Any]:  # Any: a JSON document
    root = _root(request)
    doc = model_config.load(root)
    keys = KeyStore(root)
    providers = doc.get("providers") or {}
    return {
        "custom": model_config.is_custom(root),
        "providers": [
            {"name": name, "client": p.get("client"), "base_url": p.get("base_url") or "",
             "api_key_env": p.get("api_key_env") or "",
             "key": model_config.key_status(p, name, keys)}
            for name, p in providers.items() if isinstance(p, dict)
        ],
        "models": doc.get("models") or [],
        "tiers": {t: model_config.tier_list(v) for t, v in (doc.get("tiers") or {}).items()},
    }


def _provider(request: Request, name: str) -> dict[str, Any]:  # Any: a JSON document
    providers = model_config.load(_root(request)).get("providers") or {}
    p = providers.get(name)
    if not isinstance(p, dict):
        raise HTTPException(status_code=404, detail=f"unknown provider {name!r}")
    return p


@router.get("")
async def get_config(request: Request) -> dict[str, Any]:
    """The effective configuration, with each provider's key status (never the key)."""
    return _view(request)


class ConfigBody(BaseModel):
    providers: dict[str, dict[str, Any]]
    models: list[dict[str, Any]]
    tiers: dict[str, list[str] | str] = Field(default_factory=dict)


@router.put("")
async def put_config(request: Request, body: ConfigBody) -> dict[str, Any]:
    """Save the install's configuration (it replaces the shipped defaults as a whole)."""
    try:
        model_config.save(_root(request), body.model_dump())
    except ModelConfigError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _view(request)


@router.post("/reset")
async def reset_config(request: Request) -> dict[str, Any]:
    """Drop the install's configuration; stored keys are kept."""
    model_config.reset(_root(request))
    return _view(request)


class KeyBody(BaseModel):
    key: str = Field(min_length=1, max_length=512)


@router.put("/providers/{name}/key")
async def set_key(request: Request, name: str, body: KeyBody) -> dict[str, Any]:
    """Store a provider key on the host (file mode 0600). The response never echoes it."""
    _provider(request, name)
    try:
        KeyStore(_root(request)).set(name, body.key)
    except ModelConfigError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _view(request)


@router.delete("/providers/{name}/key")
async def clear_key(request: Request, name: str) -> dict[str, Any]:
    """Forget a stored key (an environment variable, if set, still applies)."""
    _provider(request, name)
    KeyStore(_root(request)).clear(name)
    return _view(request)


@router.post("/providers/{name}/test")
async def test_provider(request: Request, name: str) -> dict[str, Any]:
    """Call the provider's model list with the host-side key; report what it says."""
    p = _provider(request, name)
    key = model_config.api_key(p, name, KeyStore(_root(request)))
    client = p.get("client")
    base = (p.get("base_url") or (ANTHROPIC_URL if client == "anthropic" else "")).rstrip("/")
    if not key and not model_config.is_local(base):
        return {"ok": False, "detail": "no key: set one here or in the environment variable",
                "models": []}
    headers: dict[str, str] = {}
    if client == "anthropic":
        headers = {"anthropic-version": ANTHROPIC_VERSION}
        if key:
            headers["x-api-key"] = key
    elif key:
        headers = {"authorization": f"Bearer {key}"}
    try:
        from core.tls import httpx_verify
        async with httpx.AsyncClient(timeout=TEST_TIMEOUT, verify=httpx_verify()) as http:
            r = await http.get(f"{base}/models", headers=headers)
    except httpx.HTTPError as e:
        return {"ok": False, "detail": f"could not reach {base}: {type(e).__name__}", "models": []}
    if r.status_code != 200:
        return {"ok": False, "detail": f"{base}/models answered {r.status_code}", "models": []}
    try:
        data = r.json().get("data") or []
    except ValueError:
        data = []
    ids = sorted({str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")})
    return {"ok": True, "detail": f"{len(ids)} model(s) available", "models": ids[:200]}
