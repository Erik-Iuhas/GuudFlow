"""GuudFlow's MCP server: the only way an agent talks back to GuudFlow (plan §5.6).

Speaks the JSON-response flavour of MCP's streamable HTTP transport: the client
POSTs JSON-RPC messages, requests get an `application/json` reply, notifications
get `202 Accepted`. That is all opencode's remote MCP client needs.

Security model:
  - Every request carries `Authorization: Bearer <token>`. The token maps to an
    `McpIdentity` (run, role, granted tools). The caller's role comes from the
    token only, never from tool arguments.
  - `tools/list` returns only the tools granted to that identity; `tools/call`
    on anything else is refused (default-deny RBAC).
  - Tokens are revoked when the run ends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

log = logging.getLogger(__name__)

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "guudflow"


@dataclass(frozen=True)
class McpIdentity:
    """Who is calling: one role in one run, with the MCP tools it was granted."""

    run_id: str
    role: str
    tools: frozenset[str]


@dataclass
class ToolResult:
    """What a tool handler returns. Images are (base64 data, mime type) pairs."""

    text: str
    is_error: bool = False
    images: list[tuple[str, str]] = field(default_factory=list)

    def to_mcp(self) -> dict[str, Any]:
        """MCP `tools/call` result payload."""
        content: list[dict[str, Any]] = [{"type": "text", "text": self.text}]
        content += [{"type": "image", "data": data, "mimeType": mime} for data, mime in self.images]
        return {"content": content, "isError": self.is_error}


ToolHandler = Callable[[McpIdentity, dict[str, Any]], Awaitable[ToolResult]]
SchemaSource = dict[str, Any] | Callable[[McpIdentity], dict[str, Any]]
CallHook = Callable[[McpIdentity, str, dict[str, Any], ToolResult], None]


@dataclass(frozen=True)
class McpTool:
    """One tool. `input_schema` may depend on the caller (e.g. submit_result)."""

    name: str
    description: str
    input_schema: SchemaSource
    handler: ToolHandler

    def schema_for(self, identity: McpIdentity) -> dict[str, Any]:
        """The input schema this caller sees."""
        return self.input_schema(identity) if callable(self.input_schema) else self.input_schema


class McpServer:
    """Token-scoped tool registry plus its HTTP app."""

    def __init__(self) -> None:
        self._tools: dict[str, McpTool] = {}
        self._tokens: dict[str, McpIdentity] = {}
        self._hooks: list[CallHook] = []

    # ── registry ────────────────────────────────────────────────────────────
    def register(self, tool: McpTool) -> None:
        """Add a tool. Names are unique."""
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def on_call(self, hook: CallHook) -> None:
        """Observe every completed tool call (used for board audit signals)."""
        self._hooks.append(hook)

    def issue_token(self, identity: McpIdentity) -> str:
        """Mint a bearer token bound to `identity`."""
        token = secrets.token_urlsafe(32)
        self._tokens[token] = identity
        return token

    def revoke(self, token: str) -> None:
        """Invalidate one token."""
        self._tokens.pop(token, None)

    def revoke_run(self, run_id: str) -> None:
        """Invalidate every token of a run (called when the run ends)."""
        for token in [t for t, i in self._tokens.items() if i.run_id == run_id]:
            del self._tokens[token]

    def identity_for(self, token: str) -> McpIdentity | None:
        """Look up a token."""
        return self._tokens.get(token)

    def granted(self, identity: McpIdentity) -> list[McpTool]:
        """Registered tools this identity may use, in registration order."""
        return [t for name, t in self._tools.items() if name in identity.tools]

    # ── protocol ────────────────────────────────────────────────────────────
    async def handle(self, identity: McpIdentity, message: dict[str, Any]) -> dict[str, Any] | None:
        """Answer one JSON-RPC message (None for notifications)."""
        mid = message.get("id")
        method = message.get("method")
        if mid is None:
            return None
        params = message.get("params") or {}
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return _ok(mid, {"protocolVersion": version, "capabilities": {"tools": {}},
                             "serverInfo": {"name": SERVER_NAME, "version": "1"}})
        if method == "ping":
            return _ok(mid, {})
        if method == "tools/list":
            tools = [{"name": t.name, "description": t.description,
                      "inputSchema": t.schema_for(identity)} for t in self.granted(identity)]
            return _ok(mid, {"tools": tools})
        if method == "tools/call":
            return _ok(mid, (await self._call(identity, params)).to_mcp())
        return _error(mid, -32601, f"method not found: {method}")

    async def _call(self, identity: McpIdentity, params: dict[str, Any]) -> ToolResult:
        name = str(params.get("name", ""))
        args = params.get("arguments") or {}
        tool = self._tools.get(name)
        if tool is None or name not in identity.tools:
            result = ToolResult(f"tool {name!r} is not available to role {identity.role!r}",
                                is_error=True)
        elif not isinstance(args, dict):
            result = ToolResult("arguments must be a JSON object", is_error=True)
        else:
            try:
                result = await tool.handler(identity, args)
            except Exception as exc:  # a tool bug must not crash the agent's turn
                log.exception("MCP tool %s failed", name)
                result = ToolResult(f"tool {name!r} failed: {exc}", is_error=True)
        for hook in self._hooks:
            hook(identity, name, args, result)
        return result

    # ── HTTP ────────────────────────────────────────────────────────────────
    def app(self) -> Starlette:
        """ASGI app. Mount it (e.g. at /mcp) or serve it on its own."""

        async def endpoint(request: Request) -> Response:
            if request.method != "POST":
                return Response(status_code=405, headers={"Allow": "POST"})
            auth = request.headers.get("authorization", "")
            identity = self.identity_for(auth.removeprefix("Bearer ").strip()) \
                if auth.startswith("Bearer ") else None
            if identity is None:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            try:
                payload = json.loads(await request.body())
            except ValueError:
                return JSONResponse(_error(None, -32700, "parse error"), status_code=400)
            if isinstance(payload, list):
                replies = [r for r in [await self.handle(identity, m) for m in payload] if r]
                return JSONResponse(replies) if replies else Response(status_code=202)
            reply = await self.handle(identity, payload)
            return JSONResponse(reply) if reply is not None else Response(status_code=202)

        return Starlette(routes=[Route("/", endpoint, methods=["GET", "POST", "DELETE"])])


def _ok(mid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


@dataclass
class RunningHttpServer:
    """An in-process uvicorn server started by `serve_app`."""

    server: uvicorn.Server
    task: asyncio.Task[None]
    host: str
    port: int

    @property
    def url(self) -> str:
        """Base URL (no trailing slash)."""
        return f"http://{self.host}:{self.port}"

    async def stop(self) -> None:
        """Shut the server down."""
        self.server.should_exit = True
        await self.task


async def serve_app(app: Starlette, host: str = "127.0.0.1", port: int = 0) -> RunningHttpServer:
    """Serve an ASGI app in this event loop (used by the standalone runner and tests).

    In the API process the MCP app is mounted on the main FastAPI app instead.
    """
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning",
                                           lifespan="off"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()  # surface the startup error
        await asyncio.sleep(0.02)
    bound_port = server.servers[0].sockets[0].getsockname()[1]
    return RunningHttpServer(server=server, task=task, host=host, port=int(bound_port))
