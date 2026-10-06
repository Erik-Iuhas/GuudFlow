"""Host-side client for third-party MCP servers (docs/MCP_PLAN.md).

Sandboxes have no internet and never hold credentials, so a third-party server is reached
from the host: a stdio subprocess or a streamable-HTTP endpoint. GuudFlow's own MCP server
exposes its tools to the roles that were granted them (`tools()` below); the role comes from
the bearer token, never from tool arguments.

This is the minimal MCP client the proxy needs: `initialize`, `tools/list` (with paging) and
`tools/call`, over newline-delimited JSON on stdio or JSON / SSE replies over HTTP.

  - One shared connection per server, started lazily; a dead connection is dropped and the
    next call reconnects.
  - Secrets are resolved from the registry and injected here, on the host: into the
    subprocess environment or into request headers. They are never returned, logged, put in a
    signal, or left in tool output (any occurrence in a result is replaced).
  - Results are text-joined and wrapped as untrusted content with a per-call nonce.
  - Every request has a timeout; `aclose()` stops every subprocess and HTTP client.
  - Each proxied call posts one `MCP_CALL` board signal: role, server, tool, duration, ok.
    Arguments are never included.

Tools never raise: failures come back as `ToolResult(..., is_error=True)`.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import os
import re
import time
from collections.abc import Iterable
from typing import Any

import httpx

from core.harness.brief import wrap_untrusted
from core.harness.mcp_server import McpIdentity, McpTool, ToolResult
from core.harness.tool_context import RunToolContext
from core.mcp_registry import McpRegistry, McpServerSpec, McpToolInfo, namespaced, split_namespaced
from core.signals import SignalType

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "guudflow-host", "version": "1"}
DEFAULT_TIMEOUT = 30.0
MAX_OUTPUT_CHARS = 50_000
MAX_LIST_PAGES = 20
STDIO_LINE_LIMIT = 16 * 1024 * 1024
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class McpProxyError(Exception):
    """A connection or protocol failure talking to a third-party server."""


# ── connections ─────────────────────────────────────────────────────────────
class _Connection:
    """One live connection to a server. Subclasses implement the transport."""

    def __init__(self, name: str, timeout: float) -> None:
        self.name = name
        self.timeout = timeout
        self.closed = False
        self._ids = itertools.count(1)

    async def _send_request(self, message: dict[str, Any]) -> dict[str, Any]:  # Any: JSON-RPC
        raise NotImplementedError

    async def _send_notification(self, message: dict[str, Any]) -> None:  # Any: JSON-RPC
        raise NotImplementedError

    async def close(self) -> None:
        """Release the transport."""
        self.closed = True

    async def initialize(self) -> None:
        """MCP handshake."""
        await self.request("initialize", {"protocolVersion": PROTOCOL_VERSION,
                                          "capabilities": {}, "clientInfo": CLIENT_INFO})
        await self._send_notification({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def request(self, method: str, params: dict[str, Any] | None = None  # Any: JSON-RPC
                      ) -> dict[str, Any]:  # Any: JSON-RPC result
        """Send one request and return its `result`; raises McpProxyError on an error reply."""
        message = {"jsonrpc": "2.0", "id": next(self._ids), "method": method,
                   "params": params or {}}
        try:
            reply = await asyncio.wait_for(self._send_request(message), self.timeout)
        except asyncio.TimeoutError as exc:
            raise McpProxyError(f"{self.name}: {method} timed out after {self.timeout:g}s") from exc
        if "error" in reply:
            err = reply["error"] if isinstance(reply["error"], dict) else {}
            raise McpProxyError(f"{self.name}: {method} failed: {err.get('message', 'error')}")
        result = reply.get("result")
        return result if isinstance(result, dict) else {}


class _StdioConnection(_Connection):
    """A subprocess speaking newline-delimited JSON-RPC on stdin/stdout."""

    def __init__(self, name: str, timeout: float, command: str, args: list[str],
                 env: dict[str, str]) -> None:
        super().__init__(name, timeout)
        self._argv = [command, *args]
        self._env = env
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}  # Any: JSON-RPC
        self._reader: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Spawn the subprocess and run the handshake."""
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=self._env, limit=STDIO_LINE_LIMIT)
        except OSError as exc:
            raise McpProxyError(f"{self.name}: cannot start {self._argv[0]!r}: {exc.strerror}") from exc
        self._reader = asyncio.create_task(self._read_loop())
        try:
            await self.initialize()
        except BaseException:
            await self.close()
            raise

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue  # stray non-JSON output on stdout
                if not isinstance(msg, dict):
                    continue
                if "method" in msg and "id" in msg:  # a server → client request
                    reply: dict[str, Any] = {"jsonrpc": "2.0", "id": msg["id"]}  # Any: JSON-RPC
                    if msg["method"] == "ping":
                        reply["result"] = {}
                    else:
                        reply["error"] = {"code": -32601, "message": "method not supported"}
                    await self._write(reply)
                elif "id" in msg and msg["id"] in self._pending:
                    fut = self._pending.pop(msg["id"])
                    if not fut.done():
                        fut.set_result(msg)
        except (asyncio.CancelledError, ConnectionError):
            pass
        finally:
            self.closed = True
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(McpProxyError(f"{self.name}: server exited"))
            self._pending.clear()

    async def _write(self, message: dict[str, Any]) -> None:  # Any: JSON-RPC
        assert self._proc is not None and self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(message).encode() + b"\n")
            await self._proc.stdin.drain()
        except (ConnectionError, RuntimeError) as exc:
            self.closed = True
            raise McpProxyError(f"{self.name}: server exited") from exc

    async def _send_request(self, message: dict[str, Any]) -> dict[str, Any]:  # Any: JSON-RPC
        if self.closed:
            raise McpProxyError(f"{self.name}: server exited")
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[message["id"]] = fut
        try:
            await self._write(message)
            return await fut
        finally:
            self._pending.pop(message["id"], None)

    async def _send_notification(self, message: dict[str, Any]) -> None:  # Any: JSON-RPC
        await self._write(message)

    async def close(self) -> None:
        await super().close()
        proc, self._proc = self._proc, None
        if proc is not None:
            with contextlib.suppress(Exception):
                if proc.stdin is not None:
                    proc.stdin.close()
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 3)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader


class _HttpConnection(_Connection):
    """Streamable HTTP: POST JSON-RPC, read an `application/json` or SSE reply."""

    def __init__(self, name: str, timeout: float, url: str, headers: dict[str, str],
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(name, timeout)
        self._url = url
        self._headers = {"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json", **headers}
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport,
                                         follow_redirects=False)
        self._session_id: str | None = None

    async def start(self) -> None:
        """Run the handshake."""
        try:
            await self.initialize()
        except BaseException:
            await self.close()
            raise

    def _headers_now(self) -> dict[str, str]:
        out = dict(self._headers)
        out["MCP-Protocol-Version"] = PROTOCOL_VERSION
        if self._session_id:
            out["Mcp-Session-Id"] = self._session_id
        return out

    async def _post(self, message: dict[str, Any]) -> httpx.Response:  # Any: JSON-RPC
        try:
            return await self._client.send(
                self._client.build_request("POST", self._url, json=message,
                                           headers=self._headers_now()), stream=True)
        except httpx.HTTPError as exc:
            # str(exc) can embed the URL; the URL may carry a token, so name only the type.
            raise McpProxyError(f"{self.name}: connection failed ({type(exc).__name__})") from exc

    async def _send_request(self, message: dict[str, Any]) -> dict[str, Any]:  # Any: JSON-RPC
        resp = await self._post(message)
        try:
            if sid := resp.headers.get("mcp-session-id"):
                self._session_id = sid
            if resp.status_code >= 400:
                raise McpProxyError(f"{self.name}: HTTP {resp.status_code}")
            ctype = resp.headers.get("content-type", "")
            if ctype.startswith("text/event-stream"):
                return await _read_sse(resp, message["id"], self.name)
            body = await resp.aread()
            try:
                doc = json.loads(body)
            except ValueError as exc:
                raise McpProxyError(f"{self.name}: reply is not JSON") from exc
            for item in doc if isinstance(doc, list) else [doc]:
                if isinstance(item, dict) and item.get("id") == message["id"]:
                    return item
            raise McpProxyError(f"{self.name}: no reply to request {message['id']}")
        finally:
            await resp.aclose()

    async def _send_notification(self, message: dict[str, Any]) -> None:  # Any: JSON-RPC
        resp = await self._post(message)
        await resp.aclose()

    async def close(self) -> None:
        await super().close()
        if self._session_id:
            with contextlib.suppress(Exception):  # best-effort session end
                await self._client.delete(self._url, headers=self._headers_now())
        await self._client.aclose()


async def _read_sse(resp: httpx.Response, want_id: int, name: str) -> dict[str, Any]:  # Any: JSON-RPC
    data: list[str] = []
    async for line in resp.aiter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            payload, data = "\n".join(data), []
            with contextlib.suppress(ValueError):
                msg = json.loads(payload)
                if isinstance(msg, dict) and msg.get("id") == want_id:
                    return msg
    if data:
        with contextlib.suppress(ValueError):
            msg = json.loads("\n".join(data))
            if isinstance(msg, dict) and msg.get("id") == want_id:
                return msg
    raise McpProxyError(f"{name}: stream ended without a reply")


# ── the proxy ───────────────────────────────────────────────────────────────
class McpProxy:
    """Shared, lazily started connections to the registry's servers."""

    def __init__(self, registry: McpRegistry, *, timeout: float = DEFAULT_TIMEOUT,
                 max_output_chars: int = MAX_OUTPUT_CHARS,
                 http_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.registry = registry
        self.timeout = timeout
        self.max_output_chars = max_output_chars
        self._http_transport = http_transport  # tests
        self._conns: dict[str, _Connection] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # connection management
    def _spec(self, server: str) -> McpServerSpec:
        spec = self.registry.get(server)
        if spec is None:
            raise McpProxyError(f"unknown MCP server {server!r}")
        if not spec.enabled:
            raise McpProxyError(f"MCP server {server!r} is disabled")
        return spec

    async def _open(self, spec: McpServerSpec) -> _Connection:
        secret = self.registry.secret(spec)
        if spec.credential and not secret:
            raise McpProxyError(f"{spec.name}: its credential is not set on the host")
        if spec.transport == "stdio":
            env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR") if k in os.environ}
            env.update({k: os.environ[k] for k in spec.env if k in os.environ})
            if secret and spec.credential.get("env"):
                env[spec.credential["env"]] = secret
            stdio = _StdioConnection(spec.name, self.timeout, spec.command, spec.args, env)
            await stdio.start()
            return stdio
        headers = {k: _expand(v, secret) for k, v in spec.headers.items()}
        if secret and not any("${credential}" in v for v in spec.headers.values()) \
                and not any(k.lower() == "authorization" for k in headers):
            headers["Authorization"] = f"Bearer {secret}"
        conn = _HttpConnection(spec.name, self.timeout, spec.url, headers, self._http_transport)
        await conn.start()
        return conn

    async def _conn(self, server: str) -> _Connection:
        lock = self._locks.setdefault(server, asyncio.Lock())
        async with lock:
            conn = self._conns.get(server)
            if conn is not None and not conn.closed:
                return conn
            if conn is not None:
                await conn.close()
            conn = await self._open(self._spec(server))
            self._conns[server] = conn
            return conn

    async def _drop(self, server: str, conn: _Connection) -> None:
        if self._conns.get(server) is conn:
            del self._conns[server]
        await conn.close()

    async def invalidate(self, server: str) -> None:
        """Close a server's connection (after its configuration changed)."""
        conn = self._conns.pop(server, None)
        if conn is not None:
            await conn.close()

    async def aclose(self) -> None:
        """Stop every subprocess and HTTP client."""
        conns, self._conns = list(self._conns.values()), {}
        for conn in conns:
            with contextlib.suppress(Exception):
                await conn.close()

    # protocol
    async def list_tools(self, server: str) -> list[McpToolInfo]:
        """The server's tools, over the shared connection."""
        conn = await self._conn(server)
        try:
            return await _list_tools(conn)
        except McpProxyError:
            if conn.closed:
                await self._drop(server, conn)
            raise

    async def probe(self, server: str) -> list[McpToolInfo]:
        """Connect fresh with the current configuration, list the tools and disconnect.

        Used by the Studio "test" button, so a changed credential is picked up at once.
        """
        await self.invalidate(server)
        spec = self._spec(server)
        conn = await self._open(spec)
        try:
            return await _list_tools(conn)
        finally:
            await conn.close()

    async def call(self, server: str, tool: str, args: dict[str, Any], *,  # Any: tool arguments
                   role: str) -> ToolResult:
        """Call `tool` on `server` on behalf of `role`; the text result is wrapped as untrusted.

        Granting is the caller's job (the MCP server checks the role's token). Never raises.
        """
        try:
            conn = await self._conn(server)
            try:
                result = await conn.request("tools/call", {"name": tool, "arguments": args})
            except McpProxyError:
                if conn.closed:
                    await self._drop(server, conn)
                raise
        except McpProxyError as exc:
            log.warning("MCP call %s.%s for %s failed: %s", server, tool, role, exc)
            return ToolResult(self.scrub(server, str(exc)), is_error=True)
        text = self.scrub(server, _join_content(result.get("content")))
        if len(text) > self.max_output_chars:
            text = text[:self.max_output_chars] + f"\n[truncated at {self.max_output_chars} chars]"
        return ToolResult(wrap_untrusted(f"mcp:{server}.{tool}", text or "(no output)"),
                          is_error=bool(result.get("isError")))

    def scrub(self, server: str, text: str) -> str:
        """Replace the server's secret if it shows up in output (a server echoing its token)."""
        spec = self.registry.get(server)
        secret = self.registry.secret(spec) if spec is not None else None
        return text.replace(secret, "[redacted]") if secret and len(secret) >= 6 else text


async def _list_tools(conn: _Connection) -> list[McpToolInfo]:
    tools: list[McpToolInfo] = []
    cursor: str | None = None
    for _ in range(MAX_LIST_PAGES):
        result = await conn.request("tools/list", {"cursor": cursor} if cursor else {})
        for raw in result.get("tools") or []:
            if isinstance(raw, dict) and isinstance(raw.get("name"), str) and raw["name"]:
                schema = raw.get("inputSchema")
                tools.append(McpToolInfo(
                    name=raw["name"], description=str(raw.get("description") or ""),
                    input_schema=schema if isinstance(schema, dict) else {"type": "object"}))
        cursor = result.get("nextCursor") if isinstance(result.get("nextCursor"), str) else None
        if not cursor:
            break
    return tools


def _join_content(content: object) -> str:
    parts: list[str] = []
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text", "")))
        elif kind == "resource" and isinstance(item.get("resource"), dict):
            res = item["resource"]
            parts.append(str(res.get("text") or f"[resource {res.get('uri', '')} not shown]"))
        else:
            parts.append(f"[{kind or 'unknown'} content not shown]")
    return "\n".join(parts)


def _expand(value: str, secret: str | None) -> str:
    def sub(m: re.Match[str]) -> str:
        if m.group(1) == "credential":
            return secret or ""
        return os.environ.get(m.group(1), "")
    return _PLACEHOLDER.sub(sub, value)


# ── exposure on GuudFlow's MCP server ───────────────────────────────────────
def tools(ctx: RunToolContext, proxy: McpProxy, names: Iterable[str]) -> list[McpTool]:
    """One `McpTool` per namespaced third-party tool in `names` (those some role is granted).

    The MCP server still filters per caller (`McpIdentity.tools`), so a role without the
    grant neither sees nor can call a tool; the handler checks again as defence in depth.
    Schemas come from the registry's cached `tools/list`.
    """
    cached: dict[str, McpToolInfo] = {}
    for spec in proxy.registry.list():
        for info in spec.tools:
            cached[namespaced(spec.name, info.name)] = info
    out: list[McpTool] = []
    for name in sorted(set(names)):
        parsed = split_namespaced(name)
        info = cached.get(name)
        if parsed is None or info is None:
            continue
        server, tool = parsed
        out.append(McpTool(
            name=name,
            description=(f"[{server}] {info.description}".strip()),
            input_schema=info.input_schema or {"type": "object"},
            handler=_make_handler(ctx, proxy, name, server, tool)))
    return out


def _make_handler(ctx: RunToolContext, proxy: McpProxy, name: str, server: str, tool: str):  # type: ignore[no-untyped-def]
    async def handler(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:  # Any: tool args
        started = time.monotonic()
        if name not in identity.tools:
            return ToolResult(f"tool {name!r} is not available to role {identity.role!r}",
                              is_error=True)
        result = await proxy.call(server, tool, args, role=identity.role)
        duration_ms = int((time.monotonic() - started) * 1000)
        try:
            ctx.post(SignalType.MCP_CALL, identity.role,
                     f"{identity.role}: {server}.{tool}" + ("" if not result.is_error else " (failed)"),
                     {"role": identity.role, "server": server, "tool": tool,
                      "duration_ms": duration_ms, "ok": not result.is_error})
        except Exception:  # noqa: BLE001 - auditing must not turn a result into a crash
            log.exception("could not post MCP_CALL signal")
        return result
    return handler
