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
    """Check the provider the way agents use it: the model list, then (OpenAI-compatible) one
    streamed chat call with a tool, as an agent turn makes. Reports what fails: the key, the
    certificate, a rejected parameter, no tool calling, or a proxy that holds back streaming
    (which makes agent turns look stalled)."""
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
    from core.tls import describe_error, httpx_verify
    ids: list[str] = []
    listing = ""
    try:
        async with httpx.AsyncClient(timeout=TEST_TIMEOUT, verify=httpx_verify()) as http:
            r = await http.get(f"{base}/models", headers=headers)
        if r.status_code == 200:
            try:
                data = r.json().get("data") or []
            except ValueError:
                data = []
            ids = sorted({str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")})
            listing = f"{len(ids)} model(s) available"
        else:
            listing = f"{base}/models answered {r.status_code}"
    except httpx.HTTPError as e:
        return {"ok": False, "detail": f"could not reach {base}: {describe_error(e)}", "models": []}
    if client != "openai_compatible":
        return {"ok": r.status_code == 200, "detail": listing, "models": ids[:200]}

    models = [m for m in model_config.load(_root(request)).get("models") or []
              if isinstance(m, dict) and m.get("provider") == name]
    if not models:
        return {"ok": r.status_code == 200, "detail": f"{listing}; add a model for this provider "
                "to test a chat call", "models": ids[:200]}
    ok, detail = await _agent_call(base, headers, models[0], httpx_verify())
    return {"ok": ok, "detail": f"{listing}; {models[0]['id']}: {detail}", "models": ids[:200]}


CHAT_TEST_TIMEOUT = 90.0


async def _agent_call(base: str, headers: dict[str, str], model: dict[str, Any],  # Any: JSON
                      verify: Any,  # Any: ssl context or bool
                      transport: httpx.AsyncBaseTransport | None = None) -> tuple[bool, str]:
    """One streamed chat call with a tool, shaped like an agent turn."""
    import json as _json
    import time
    body: dict[str, Any] = {  # Any: a JSON request
        "model": model["id"],
        "messages": [{"role": "user", "content": "Call the ping tool with ok set to true."}],
        "tools": [{"type": "function", "function": {
            "name": "ping", "description": "Answer the check.",
            "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}},
                           "required": ["ok"]}}}],
        "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 256,
    }
    level = model.get("default_effort")
    if level:  # agents send the model's effort, so check the endpoint takes it
        body["reasoning_effort"] = (model.get("effort_values") or {}).get(level, level)
    start = time.monotonic()
    first: float | None = None
    chunks = 0
    tool = False
    try:
        async with httpx.AsyncClient(timeout=CHAT_TEST_TIMEOUT, verify=verify,
                                     transport=transport) as http:
            async with http.stream("POST", f"{base}/chat/completions", headers=headers,
                                   json=body) as resp:
                if resp.status_code != 200:
                    text = (await resp.aread()).decode("utf-8", "replace")[:300]
                    return False, f"chat call answered {resp.status_code}: {text}"
                async for line in resp.aiter_lines():
                    if not line.startswith("data:") or line.strip() == "data: [DONE]":
                        continue
                    chunks += 1
                    if first is None:
                        first = time.monotonic() - start
                    try:
                        for c in _json.loads(line[5:]).get("choices") or []:
                            if (c.get("delta") or {}).get("tool_calls"):
                                tool = True
                    except ValueError:
                        pass
    except httpx.HTTPError as e:
        from core.tls import describe_error
        return False, f"chat call failed: {describe_error(e)}"
    total = time.monotonic() - start
    if chunks == 0:
        return False, ("chat call returned no streamed data: the endpoint (or a proxy) does not "
                       "stream; agents need server-sent events")
    if not tool:
        return False, ("the model answered without calling the tool: agents finish by calling "
                       "a tool, so this model or server needs tool calling (vLLM: "
                       "--enable-auto-tool-choice --tool-call-parser ...)")
    if chunks > 1 and first is not None and total > 3 and first > 0.9 * total:
        return False, (f"the whole answer arrived at once after {total:.0f}s: something between "
                       "here and the endpoint (often a company proxy) holds back streaming, so "
                       "agent turns look stalled")
    return True, f"streamed chat with a tool call works (first data after {first or 0:.1f}s)"
