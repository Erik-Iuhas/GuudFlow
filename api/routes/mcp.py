"""Third-party MCP servers (Studio Settings → MCP servers), docs/MCP_PLAN.md.

  GET    /api/mcp/servers                 every server, with its key status and cached tools
  POST   /api/mcp/servers                 add a server
  PUT    /api/mcp/servers/{name}          replace a server's definition (cached tools are kept)
  DELETE /api/mcp/servers/{name}          remove it and its stored secret
  PUT    /api/mcp/servers/{name}/key      store its secret on the host (file mode 0600)
  DELETE /api/mcp/servers/{name}/key      forget the stored secret
  POST   /api/mcp/servers/{name}/test     connect, `tools/list`, refresh the cached tools
  POST   /api/mcp/servers/import          import a standard `mcp.json` or an opencode `mcp` block

Secrets are write-only: no response contains one, only where it comes from
(`env`, `stored`, `missing` or `none`). See core/mcp_registry.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth import require_bearer
from core.harness.mcp_proxy import McpProxy, McpProxyError
from core.mcp_registry import McpRegistry, McpRegistryError, McpServerSpec

router = APIRouter(prefix="/api/mcp/servers", tags=["mcp"],
                   dependencies=[Depends(require_bearer)])


def _registry(request: Request) -> McpRegistry:
    return McpRegistry(Path(request.app.state.data_root))


def _server(reg: McpRegistry, name: str) -> McpServerSpec:
    spec = reg.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown MCP server {name!r}")
    return spec


class ServerBody(BaseModel):
    """A server definition. Cached tools are never accepted from the client."""

    name: str | None = None
    transport: str
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: list[str] = Field(default_factory=list)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    credential: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    default_grants: dict[str, list[str]] = Field(default_factory=dict)


@router.get("")
async def list_servers(request: Request) -> dict[str, Any]:
    """Every registered server (no secrets)."""
    reg = _registry(request)
    return {"servers": [reg.view(s) for s in reg.list()]}


@router.post("", status_code=201)
async def add_server(request: Request, body: ServerBody) -> dict[str, Any]:
    """Add a server."""
    reg = _registry(request)
    try:
        spec = reg.put(body.model_dump(), create=True)
    except McpRegistryError as e:
        raise HTTPException(status_code=409 if "already exists" in str(e) else 422,
                            detail=str(e)) from e
    return reg.view(spec)


@router.post("/import")
async def import_servers(request: Request, body: dict[str, Any],
                         replace: bool = False) -> dict[str, Any]:
    """Import `{"mcpServers": {...}}` (standard mcp.json) or an opencode `mcp` block."""
    reg = _registry(request)
    try:
        result = reg.import_servers(body, replace=replace)
    except McpRegistryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {**result, "servers": [reg.view(s) for s in reg.list()]}


@router.put("/{name}")
async def replace_server(request: Request, name: str, body: ServerBody) -> dict[str, Any]:
    """Replace a server's definition. A masked (`***`) header value keeps what is stored."""
    reg = _registry(request)
    try:
        spec = reg.put({**body.model_dump(), "name": name}, create=False)
    except McpRegistryError as e:
        raise HTTPException(status_code=404 if "unknown server" in str(e) else 422,
                            detail=str(e)) from e
    return reg.view(spec)


@router.delete("/{name}")
async def delete_server(request: Request, name: str) -> dict[str, Any]:
    """Remove a server and its stored secret."""
    if not _registry(request).delete(name):
        raise HTTPException(status_code=404, detail=f"unknown MCP server {name!r}")
    return {"deleted": name}


class KeyBody(BaseModel):
    key: str = Field(min_length=1, max_length=4096)


@router.put("/{name}/key")
async def set_key(request: Request, name: str, body: KeyBody) -> dict[str, Any]:
    """Store the server's secret on the host. The response never echoes it."""
    reg = _registry(request)
    _server(reg, name)
    try:
        reg.keys.set(name, body.key)
    except McpRegistryError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return reg.view(_server(reg, name))


@router.delete("/{name}/key")
async def clear_key(request: Request, name: str) -> dict[str, Any]:
    """Forget the stored secret (an environment variable, if set, still applies)."""
    reg = _registry(request)
    _server(reg, name)
    reg.keys.clear(name)
    return reg.view(_server(reg, name))


@router.post("/{name}/test")
async def test_server(request: Request, name: str) -> dict[str, Any]:
    """Connect with the host-side credential, list the tools and refresh the cached list."""
    reg = _registry(request)
    _server(reg, name)
    proxy = McpProxy(reg, http_transport=getattr(request.app.state, "mcp_http_transport", None))
    try:
        tools = await proxy.probe(name)
    except McpProxyError as e:
        return {"ok": False, "error": proxy.scrub(name, str(e)),
                "server": reg.view(_server(reg, name))}
    finally:
        await proxy.aclose()
    spec = reg.set_tools(name, [t.to_dict() for t in tools])
    return {"ok": True, "tool_count": len(tools), "server": reg.view(spec)}
