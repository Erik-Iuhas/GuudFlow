"""Async HTTP client for the ``opencode serve`` API (opencode v1.18.34).

GuudFlow drives one opencode agent session per task through this client.
Large opencode payloads are returned as raw dicts; a few typed helpers
(:class:`ModelRef`, :class:`TurnOutcome`, part builders) cover the common cases.
The client performs no retries: the runner decides how to recover.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Literal

import httpx


class OpencodeError(Exception):
    """Raised on non-2xx responses and on transport failures."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status_code: int | None = status_code
        self.body: str = body


@dataclass(frozen=True)
class ModelRef:
    """A provider/model pair selecting the LLM for a turn."""

    provider_id: str
    model_id: str


def text_part(text: str) -> dict[str, Any]:
    """Build a text message part."""
    return {"type": "text", "text": text}


def file_part(url: str, mime: str, filename: str | None = None) -> dict[str, Any]:
    """Build a file message part (``file://`` or ``data:`` URL)."""
    part: dict[str, Any] = {"type": "file", "mime": mime, "url": url}
    if filename is not None:
        part["filename"] = filename
    return part


def output_format(schema: dict[str, Any], retry_count: int = 0) -> dict[str, Any]:
    """Build a ``json_schema`` structured-output format request."""
    return {"type": "json_schema", "schema": schema, "retryCount": retry_count}


@dataclass
class TurnOutcome:
    """Typed view over a synchronous ``POST /session/{id}/message`` response."""

    structured: Any | None  # Any: structured output shape is defined by the caller's schema
    error_name: str | None
    error_message: str | None
    cost: float
    tokens: dict[str, Any]
    finish: str | None
    raw: dict[str, Any] = field(repr=False)

    @classmethod
    def from_response(cls, resp: dict[str, Any]) -> TurnOutcome:
        """Parse a response, tolerating missing or null keys."""
        info = resp.get("info") or {}
        error = info.get("error") or {}
        data = error.get("data")
        return cls(
            structured=info.get("structured"),
            error_name=error.get("name"),
            error_message=data.get("message") if isinstance(data, dict) else None,
            cost=float(info.get("cost") or 0.0),
            tokens=info.get("tokens") or {},
            finish=info.get("finish"),
            raw=resp,
        )


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _prompt_body(
    parts: list[dict[str, Any]],
    agent: str | None,
    model: ModelRef | None,
    system: str | None,
    tools: dict[str, bool] | None,
    format: dict[str, Any] | None,
    variant: str | None = None,
) -> dict[str, Any]:
    return _drop_none(
        {
            "parts": parts,
            "agent": agent,
            "model": (
                {"providerID": model.provider_id, "modelID": model.model_id}
                if model is not None
                else None
            ),
            "system": system,
            "tools": tools,
            "format": format,
            "variant": variant,
        }
    )


class OpencodeClient:
    """Typed async client bound to one opencode server and project directory."""

    def __init__(
        self,
        base_url: str,
        directory: str,
        *,
        password: str | None = None,
        username: str = "opencode",
        timeout: float = 600.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create the client; ``transport`` lets tests inject a mock transport."""
        self._directory = directory
        auth = httpx.BasicAuth(username, password) if password else None
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            auth=auth,
            timeout=timeout,
            transport=transport,
            params={"directory": directory},
            # The server is local: an HTTP(S)_PROXY from the environment must not carry these
            # calls to a company proxy that cannot reach this machine (the turn would hang).
            trust_env=False,
        )

    async def __aenter__(self) -> OpencodeClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def _request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None
    ) -> httpx.Response:
        try:
            resp = await self._http.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise OpencodeError(f"{method} {path} failed: {exc!r}") from exc
        if not resp.is_success:
            raise OpencodeError(
                f"{method} {path} -> HTTP {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
            )
        return resp

    async def _json(self, method: str, path: str, *, body: dict[str, Any] | None = None) -> Any:  # Any: raw opencode JSON
        resp = await self._request(method, path, body=body)
        try:
            return resp.json()
        except ValueError as exc:
            raise OpencodeError(
                f"{method} {path} returned invalid JSON",
                status_code=resp.status_code,
                body=resp.text,
            ) from exc

    async def health(self) -> bool:
        """Return True if ``GET /global/health`` succeeds; never raises."""
        try:
            await self._request("GET", "/global/health")
        except OpencodeError:
            return False
        return True

    async def create_session(
        self,
        *,
        title: str | None = None,
        agent: str | None = None,
        permission: list[dict[str, str]] | None = None,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a session and return the session object (has ``id``)."""
        body = _drop_none(
            {"title": title, "agent": agent, "permission": permission, "parentID": parent_id}
        )
        result: dict[str, Any] = await self._json("POST", "/session", body=body)
        return result

    async def prompt(
        self,
        session_id: str,
        parts: list[dict[str, Any]],
        *,
        agent: str | None = None,
        model: ModelRef | None = None,
        system: str | None = None,
        tools: dict[str, bool] | None = None,
        format: dict[str, Any] | None = None,
        variant: str | None = None,
    ) -> TurnOutcome:
        """Send a prompt and wait for the turn to finish."""
        body = _prompt_body(parts, agent, model, system, tools, format, variant)
        resp: dict[str, Any] = await self._json("POST", f"/session/{session_id}/message", body=body)
        return TurnOutcome.from_response(resp)

    async def prompt_async(
        self,
        session_id: str,
        parts: list[dict[str, Any]],
        *,
        agent: str | None = None,
        model: ModelRef | None = None,
        system: str | None = None,
        tools: dict[str, bool] | None = None,
        format: dict[str, Any] | None = None,
        variant: str | None = None,
    ) -> None:
        """Send a prompt without waiting for the turn; any 2xx is success. `variant` picks one
        of the model's configured variants (an effort level) for this turn only."""
        body = _prompt_body(parts, agent, model, system, tools, format, variant)
        await self._request("POST", f"/session/{session_id}/prompt_async", body=body)

    async def messages(self, session_id: str) -> list[dict[str, Any]]:
        """Return the session's messages as ``{"info", "parts"}`` dicts."""
        result: list[dict[str, Any]] = await self._json("GET", f"/session/{session_id}/message")
        return result

    async def abort(self, session_id: str) -> bool:
        """Abort the session's running turn."""
        return bool(await self._json("POST", f"/session/{session_id}/abort"))

    async def session_status(self) -> dict[str, Any]:
        """Return a mapping of session id to status dict."""
        result: dict[str, Any] = await self._json("GET", "/session/status")
        return result

    async def session_diff(self, session_id: str) -> list[dict[str, Any]]:
        """Return the file diffs produced by the session."""
        result: list[dict[str, Any]] = await self._json("GET", f"/session/{session_id}/diff")
        return result

    async def reply_permission(
        self,
        request_id: str,
        reply: Literal["once", "always", "reject"],
        message: str | None = None,
    ) -> None:
        """Answer a pending permission request."""
        body = _drop_none({"reply": reply, "message": message})
        await self._request("POST", f"/permission/{request_id}/reply", body=body)

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield parsed events from the ``GET /event`` SSE stream (no read timeout)."""
        timeout = httpx.Timeout(self._http.timeout.connect, read=None)
        try:
            async with self._http.stream("GET", "/event", timeout=timeout) as resp:
                if not resp.is_success:
                    body = (await resp.aread()).decode(errors="replace")
                    raise OpencodeError(
                        f"GET /event -> HTTP {resp.status_code}",
                        status_code=resp.status_code,
                        body=body,
                    )
                data_lines: list[str] = []
                async for line in resp.aiter_lines():
                    if line == "":
                        if data_lines:
                            event = _parse_event("\n".join(data_lines))
                            data_lines = []
                            if event is not None:
                                yield event
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].removeprefix(" "))
                    # comment lines (":") and other SSE fields are ignored
                if data_lines:
                    event = _parse_event("\n".join(data_lines))
                    if event is not None:
                        yield event
        except httpx.HTTPError as exc:
            raise OpencodeError(f"GET /event failed: {exc!r}") from exc


def _parse_event(payload: str) -> dict[str, Any] | None:
    """Parse one SSE data payload; return None if it is not a JSON object."""
    try:
        event = json.loads(payload)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None
