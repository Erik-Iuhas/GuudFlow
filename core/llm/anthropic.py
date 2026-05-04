"""Anthropic Claude client with normalized error taxonomy and prompt caching."""
from __future__ import annotations

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
    cache_ttl accepts "5m" (default) or "1h" — use "1h" for persona
    prompts that stay stable across an entire agent loop.
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
        cache_ttl: str = "5m",
    ) -> LLMResponse:
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = (
                AsyncAnthropic(api_key=self._api_key)
                if self._api_key
                else AsyncAnthropic()
            )

        # System prompt: plain string or cache-enabled block list
        if cache_system and system:
            cache_control: dict[str, Any] = {"type": "ephemeral"}
            if cache_ttl != "5m":
                cache_control["ttl"] = cache_ttl
            system_param: Any = [
                {"type": "text", "text": system, "cache_control": cache_control}
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

        try:
            resp = await self._client.messages.create(**kwargs)
        except Exception as exc:
            raise _map_error(exc) from exc

        return _api_to_response(resp)


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
        request_id=getattr(resp, "_request_id", "") or "",
        raw=resp,
    )
