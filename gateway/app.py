"""LLM gateway: a reverse proxy that keeps real provider keys on the host.

Sandboxes get a per-(run, role) *virtual key* and a base URL of
`http://<gateway>/<upstream-name>`. The gateway:

  - authenticates the virtual key (unknown or revoked -> 401),
  - enforces the key's model allow-list (403) and spend caps (402),
  - swaps in the real provider key and streams the response back unchanged,
  - parses token usage out of the response as it streams and records the cost.

Real keys never enter a container, and the virtual key never reaches a provider.
Provider wire formats handled: Anthropic Messages (`x-api-key`, SSE with
`message_start` / `message_delta`) and OpenAI-compatible chat completions
(`Authorization: Bearer`, SSE with a final `usage` chunk).
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

log = logging.getLogger(__name__)

UpstreamKind = Literal["anthropic", "openai_compatible"]

# Headers never copied between client and upstream (RFC 9110 hop-by-hop, plus the
# ones httpx recomputes). Auth headers are dropped on the way up and replaced.
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection",
    "te", "trailer", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
})
_REQUEST_DROP = _HOP_BY_HOP | {"accept-encoding", "authorization", "x-api-key", "api-key"}
# The body is re-emitted decoded, so these no longer describe it.
_RESPONSE_DROP = _HOP_BY_HOP | {"content-encoding"}

# Only model-inference endpoints may be reached with the host's real key (O8 review):
# without this, a sandbox could call any provider endpoint (files, batches, admin…).
ALLOWED_ENDPOINTS: dict[str, frozenset[tuple[str, str]]] = {
    "anthropic": frozenset({("POST", "messages"), ("POST", "messages/count_tokens")}),
    "openai_compatible": frozenset({("POST", "chat/completions")}),
}
_MAX_JSON_PARSE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class Upstream:
    """A real provider the gateway forwards to. `name` is the first URL path segment."""

    name: str
    kind: UpstreamKind
    base_url: str
    api_key: str


@dataclass(frozen=True)
class Price:
    """USD per 1k tokens."""

    input_per_1k: float
    output_per_1k: float
    cache_read_per_1k: float = 0.0
    # Cache writes (Anthropic bills them at a premium); None means 1.25x the input price.
    cache_write_per_1k: float | None = None


@dataclass
class VirtualKey:
    """A sandbox's credential: scoped to one role of one run."""

    key: str
    run_id: str
    role: str
    allowed_models: frozenset[str]
    budget_usd: float | None
    spent_usd: float = 0.0


@dataclass
class UsageRecord:
    """One upstream request. `input_tokens` excludes cache reads; those are priced separately."""

    ts: str
    run_id: str
    role: str
    upstream: str
    model: str
    status: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    images: int
    cost_usd: float


@dataclass
class _Usage:
    """Token counts accumulated while a response streams past."""

    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class _Call:
    """What is known about one request while it is in flight."""

    key: VirtualKey
    upstream: Upstream
    model: str
    images: int


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class _UsageParser:
    """Incrementally extracts token usage from a response body.

    SSE bodies are parsed line by line as chunks arrive; plain JSON bodies are
    buffered (bounded) and parsed once at the end. Parse failures are ignored:
    accounting must never break the proxied response.
    """

    def __init__(self, kind: UpstreamKind, sse: bool) -> None:
        self.kind = kind
        self.sse = sse
        self.usage = _Usage()
        self._buf = b""
        self._size = 0

    def feed(self, chunk: bytes) -> None:
        """Consume the next body chunk."""
        if self.sse:
            self._buf += chunk
            *lines, self._buf = self._buf.split(b"\n")
            for line in lines:
                self._sse_line(line)
        elif self._size <= _MAX_JSON_PARSE_BYTES:
            self._buf += chunk
            self._size += len(chunk)

    def finish(self) -> _Usage:
        """Flush any trailing data and return the totals."""
        if self.sse:
            if self._buf:
                self._sse_line(self._buf)
        elif self._size <= _MAX_JSON_PARSE_BYTES:
            self._obj(self._load(self._buf))
        self._buf = b""
        return self.usage

    @staticmethod
    def _load(raw: bytes) -> object:
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def _sse_line(self, line: bytes) -> None:
        line = line.strip()
        if not line.startswith(b"data:"):
            return
        data = line[5:].strip()
        if data and data != b"[DONE]":
            self._obj(self._load(data))

    def _obj(self, obj: object) -> None:
        if not isinstance(obj, dict):
            return
        if self.kind == "anthropic":
            message = obj.get("message")
            if obj.get("type") == "message_start" and isinstance(message, dict):
                usage = message.get("usage")
            else:
                usage = obj.get("usage")
            if isinstance(usage, dict):
                self._anthropic(usage)
        elif isinstance(obj.get("usage"), dict):
            self._openai(obj["usage"])

    def _anthropic(self, usage: dict[str, Any]) -> None:
        for src, attr in (("input_tokens", "input_tokens"),
                          ("cache_creation_input_tokens", "cache_creation_tokens"),
                          ("cache_read_input_tokens", "cache_read_tokens"),
                          ("output_tokens", "output_tokens")):
            value = _int(usage.get(src))
            if value is not None:
                setattr(self.usage, attr, value)  # later events carry the running totals

    def _openai(self, usage: dict[str, Any]) -> None:
        prompt = _int(usage.get("prompt_tokens"))
        if prompt is None:
            prompt = _int(usage.get("input_tokens")) or 0
        completion = _int(usage.get("completion_tokens"))
        if completion is None:
            completion = _int(usage.get("output_tokens")) or 0
        details = usage.get("prompt_tokens_details")
        cached = (_int(details.get("cached_tokens")) if isinstance(details, dict) else None) or 0
        # OpenAI's prompt_tokens includes cached tokens; keep them out of the input count.
        self.usage = _Usage(input_tokens=max(prompt - cached, 0), cache_read_tokens=cached,
                            output_tokens=completion)


def _count_images(body: dict[str, Any]) -> int:
    """Image parts in the request's messages (Anthropic `image` blocks, OpenAI `image_url` parts)."""
    def walk(node: object) -> int:
        if isinstance(node, dict):
            own = 1 if node.get("type") in ("image", "image_url") else 0
            return own + sum(walk(v) for v in node.values())
        if isinstance(node, list):
            return sum(walk(v) for v in node)
        return 0
    return walk(body.get("messages"))


def _error(status: int, kind: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"type": kind, "message": message}}, status_code=status)


def _extract_key(request: Request) -> str | None:
    key = request.headers.get("x-api-key")
    if key:
        return key
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return (token.strip() or None) if scheme.lower() == "bearer" else None


class Gateway:
    """Reverse proxy with virtual keys, model allow-lists, spend caps and usage logging."""

    def __init__(self, upstreams: dict[str, Upstream], prices: Callable[[str], Price | None], *,
                 cost_log: Path | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 run_budget_usd: dict[str, float] | None = None) -> None:
        self._upstreams = dict(upstreams)
        self._prices = prices
        self._cost_log = cost_log
        self._client = httpx.AsyncClient(transport=transport, follow_redirects=False,
                                         timeout=httpx.Timeout(600.0, connect=15.0))
        self._keys: dict[str, VirtualKey] = {}
        self._run_budgets: dict[str, float] = dict(run_budget_usd or {})
        self._records: dict[str, list[UsageRecord]] = {}

    # ── key management ──────────────────────────────────────────────────────
    def issue_key(self, run_id: str, role: str, allowed_models: Iterable[str],
                  budget_usd: float | None = None) -> str:
        """Mint a virtual key for one role of one run. Empty `allowed_models` allows nothing."""
        key = "gf-vk-" + secrets.token_urlsafe(32)
        self._keys[key] = VirtualKey(key=key, run_id=run_id, role=role,
                                     allowed_models=frozenset(allowed_models),
                                     budget_usd=budget_usd)
        return key

    def set_run_budget(self, run_id: str, budget_usd: float | None) -> None:
        """Cap the total spend of a run across all its roles (None removes the cap)."""
        if budget_usd is None:
            self._run_budgets.pop(run_id, None)
        else:
            self._run_budgets[run_id] = budget_usd

    def revoke_run(self, run_id: str) -> None:
        """Invalidate every key of a run. Recorded usage is kept."""
        for key in [k for k, v in self._keys.items() if v.run_id == run_id]:
            del self._keys[key]

    # ── accounting ──────────────────────────────────────────────────────────
    def spend(self, run_id: str, role: str | None = None) -> float:
        """USD spent by a run, optionally restricted to one role."""
        return sum(r.cost_usd for r in self._records.get(run_id, [])
                   if role is None or r.role == role)

    def usage(self, run_id: str) -> list[UsageRecord]:
        """Every upstream request of a run, in order."""
        return list(self._records.get(run_id, []))

    def _record(self, call: _Call, status: int, usage: _Usage) -> None:
        input_tokens = usage.input_tokens + usage.cache_creation_tokens
        price = self._prices(call.model)
        cost = 0.0
        if price is not None:
            write = (price.cache_write_per_1k if price.cache_write_per_1k is not None
                     else price.input_per_1k * 1.25)
            cost = (usage.input_tokens * price.input_per_1k
                    + usage.cache_creation_tokens * write
                    + usage.output_tokens * price.output_per_1k
                    + usage.cache_read_tokens * price.cache_read_per_1k) / 1000.0
        record = UsageRecord(
            ts=datetime.now(timezone.utc).isoformat(), run_id=call.key.run_id,
            role=call.key.role, upstream=call.upstream.name, model=call.model, status=status,
            input_tokens=input_tokens, output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens, images=call.images, cost_usd=cost)
        call.key.spent_usd += cost
        self._records.setdefault(record.run_id, []).append(record)
        if self._cost_log is not None:
            try:
                self._cost_log.parent.mkdir(parents=True, exist_ok=True)
                with self._cost_log.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(record)) + "\n")
            except OSError:
                log.exception("gateway: could not append to cost log %s", self._cost_log)

    # ── HTTP ────────────────────────────────────────────────────────────────
    def app(self) -> Starlette:
        """The ASGI app: `/{upstream}/{path}` for POST and GET."""
        return Starlette(routes=[Route("/{upstream}/{path:path}", self._handle,
                                       methods=["POST", "GET"])])

    async def aclose(self) -> None:
        """Release the upstream HTTP client."""
        await self._client.aclose()

    async def _handle(self, request: Request) -> Response:
        token = _extract_key(request)
        key = self._keys.get(token) if token else None
        if key is None:
            return _error(401, "authentication_error", "invalid or revoked gateway key")
        name = request.path_params["upstream"]
        upstream = self._upstreams.get(name)
        if upstream is None:
            return _error(404, "not_found_error", f"unknown upstream {name!r}")

        endpoint = (request.method.upper(), request.path_params["path"].strip("/"))
        if endpoint not in ALLOWED_ENDPOINTS[upstream.kind]:
            return _error(403, "permission_error",
                          f"endpoint {endpoint[0]} /{endpoint[1]} is not allowed through the gateway")

        raw = await request.body()
        body: dict[str, Any] | None = None
        if request.method == "POST" and raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            body = parsed if isinstance(parsed, dict) else None
        if body is None or not isinstance(body.get("model"), str):
            return _error(400, "invalid_request_error", "a JSON body with a model is required")
        model = str(body["model"])
        if True:
            if model not in key.allowed_models:
                return _error(403, "permission_error",
                              f"model {model!r} is not allowed for role {key.role!r}")

        exceeded = self._budget_exceeded(key)
        if exceeded:
            return _error(402, "budget_exceeded", exceeded)

        if body is not None and upstream.kind == "openai_compatible" and body.get("stream"):
            options = body.get("stream_options")
            if not isinstance(options, dict) or "include_usage" not in options:
                # Without this the final usage chunk is never sent and cost would read zero.
                body["stream_options"] = {**(options if isinstance(options, dict) else {}),
                                          "include_usage": True}
                raw = json.dumps(body).encode()

        call = _Call(key=key, upstream=upstream, model=model,
                     images=_count_images(body) if body is not None else 0)
        return await self._forward(request, call, raw)

    def _budget_exceeded(self, key: VirtualKey) -> str:
        """Why this key may not make another request ('' if it may)."""
        if key.budget_usd is not None and key.spent_usd >= key.budget_usd:
            return (f"role {key.role!r} spent ${key.spent_usd:.4f} of its "
                    f"${key.budget_usd:.4f} budget")
        run_budget = self._run_budgets.get(key.run_id)
        if run_budget is not None:
            total = self.spend(key.run_id)
            if total >= run_budget:
                return f"run {key.run_id!r} spent ${total:.4f} of its ${run_budget:.4f} budget"
        return ""

    async def _forward(self, request: Request, call: _Call, raw: bytes) -> Response:
        upstream = call.upstream
        headers = [(k, v) for k, v in request.headers.items() if k.lower() not in _REQUEST_DROP]
        if upstream.kind == "anthropic":
            headers.append(("x-api-key", upstream.api_key))
        else:
            headers.append(("authorization", f"Bearer {upstream.api_key}"))
        url = httpx.URL(f"{upstream.base_url.rstrip('/')}/{request.path_params['path']}")
        url = url.copy_with(query=request.url.query.encode() or None)
        upstream_request = self._client.build_request(request.method, url, headers=headers,
                                                      content=raw or None)
        try:
            response = await self._client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            log.warning("gateway: upstream %s unreachable: %s", upstream.name, exc)
            self._record(call, 502, _Usage())
            return _error(502, "bad_gateway",
                          f"upstream {upstream.name!r} unreachable: {type(exc).__name__}")

        sse = "text/event-stream" in response.headers.get("content-type", "")
        parser = _UsageParser(upstream.kind, sse)
        out_headers = {k: v for k, v in response.headers.items() if k.lower() not in _RESPONSE_DROP}

        async def stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_bytes():
                    parser.feed(chunk)
                    yield chunk
            finally:
                # Runs on completion, upstream failure and client disconnect alike.
                self._record(call, response.status_code, parser.finish())
                await response.aclose()

        return StreamingResponse(stream(), status_code=response.status_code, headers=out_headers)
