"""Anthropic Claude client with normalized error taxonomy and prompt caching."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.llm.client import LLMMessage, LLMResponse, ToolCall
from core.llm.errors import (
    AuthError,
    ContextLengthError,
    InvalidRequestError,
    LLMError,
    NetworkError,
    RateLimitError,
    ServerError,
)


class AnthropicClient:
    """Wrapper over anthropic.AsyncAnthropic with normalized errors.

    Lazily imports the SDK so this module is importable without
    ANTHROPIC_API_KEY in the environment.

    Pass cache_system=True to complete() to add Anthropic cache_control
    to the system prompt — keeps the stable system layer cached across
    turns for agents that call repeatedly with the same persona prompt.
    """

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key
        self._client: Any = None

    async def complete(
        self,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8192,
        model: str = "claude-sonnet-4-6",
        cache_system: bool = False,
        effort: str | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        if self._client is None:
            import httpx
            from anthropic import AsyncAnthropic
            # anthropic 0.39 builds its default httpx client with `proxies=`, which
            # httpx 0.28 removed; an explicit client avoids that (proxy settings
            # still come from the environment through httpx's trust_env).
            http_client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))
            self._client = (
                AsyncAnthropic(api_key=self._api_key, http_client=http_client)
                if self._api_key
                else AsyncAnthropic(http_client=http_client)
            )

        # System prompt: plain string or cache-enabled block list
        if cache_system and system:
            system_param: Any = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        else:
            system_param = system

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_param,
            "messages": [_msg_to_api(m) for m in messages],
        }
        if tools:
            kwargs["tools"] = tools
        if effort:
            # The pinned SDK predates `output_config`; extra_body sends it as is.
            kwargs["extra_body"] = {"output_config": {"effort": effort}}

        if on_text is None:
            try:
                resp = await self._client.messages.create(**kwargs)
            except Exception as exc:
                raise _map_error(exc) from exc
            return _api_to_response(resp)
        # Streaming: hand the tool-call arguments written so far to `on_text`, then build
        # the same response from the final message.
        written = ""
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if getattr(event, "type", "") == "input_json":
                        written += getattr(event, "partial_json", "") or ""
                        on_text(written)
                resp = await stream.get_final_message()
        except Exception as exc:
            raise _map_error(exc) from exc
        return _api_to_response(resp)

    async def complete_streaming(
        self,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8192,
        model: str = "claude-sonnet-4-6",
        cache_system: bool = False,
        effort: str | None = None,
        *,
        on_text: Callable[[str], None],
    ) -> LLMResponse:
        """`complete`, streamed: `on_text` gets the tool-call arguments written so far."""
        return await self.complete(system, messages, tools, max_tokens, model, cache_system,
                                   effort, on_text=on_text)


def _map_error(exc: Exception) -> LLMError:
    """Map anthropic SDK exceptions to our normalized taxonomy."""
    try:
        import anthropic
    except ImportError:
        return LLMError(str(exc))

    if isinstance(exc, anthropic.RateLimitError):
        retry_after: float | None = None
        resp = getattr(exc, "response", None)
        if resp is not None:
            raw = getattr(resp, "headers", {}).get("retry-after")
            try:
                retry_after = float(raw) if raw is not None else None
            except (TypeError, ValueError):
                pass
        return RateLimitError(str(exc), retry_after_s=retry_after)

    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return AuthError(str(exc))

    if isinstance(exc, anthropic.BadRequestError):
        msg = str(exc).lower()
        if "context" in msg and any(w in msg for w in ("length", "window", "exceed", "too long")):
            return ContextLengthError(str(exc))
        return InvalidRequestError(str(exc))

    if isinstance(exc, anthropic.InternalServerError):
        return ServerError(str(exc))

    if isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", 0)
        if status == 429:
            return RateLimitError(str(exc))
        if status in (401, 403):
            return AuthError(str(exc))
        if status >= 500:
            return ServerError(str(exc))
        if status == 400:
            return InvalidRequestError(str(exc))

    if isinstance(exc, anthropic.APIConnectionError):
        return NetworkError(str(exc))

    return LLMError(str(exc))


def _msg_to_api(m: LLMMessage) -> dict[str, Any]:
    return {"role": m.role, "content": m.content}


def _api_to_response(resp: Any) -> LLMResponse:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in getattr(resp, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_parts.append(getattr(block, "text", "") or "")
        elif block_type == "tool_use":
            tool_calls.append(ToolCall(
                id=getattr(block, "id", "") or "",
                name=getattr(block, "name", "") or "",
                arguments=dict(getattr(block, "input", {}) or {}),
            ))

    usage = getattr(resp, "usage", None)
    return LLMResponse(
        text="".join(text_parts),
        tool_calls=tool_calls,
        stop_reason=getattr(resp, "stop_reason", "end_turn") or "end_turn",
        model=getattr(resp, "model", "") or "",
        input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
        output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) if usage else 0,
        cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) if usage else 0,
        raw=resp,
    )
