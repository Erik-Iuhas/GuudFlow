"""OpenAI-compatible adapter (Ollama, Groq, OpenRouter, DeepSeek, vLLM, LM Studio).

All providers that implement /chat/completions in the OpenAI shape work here.
Uses httpx directly — no openai SDK dependency.
"""
from __future__ import annotations

import json
import re
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
    TimeoutError,
)


class OpenAIClient:
    """OpenAI-compatible adapter over httpx.

    Verified targets:
      OpenAI:     base_url="https://api.openai.com/v1"      api_key=<key>
      Groq:       base_url="https://api.groq.com/openai/v1" api_key=<key>
      OpenRouter: base_url="https://openrouter.ai/api/v1"   api_key=<key>
      Ollama:     base_url="http://localhost:11434/v1"       api_key="ollama"
      LM Studio:  base_url="http://localhost:1234/v1"        api_key="lm-studio"
      vLLM:       base_url="http://host:8000/v1"             api_key=<configured>
      DeepSeek:   base_url="https://api.deepseek.com/v1"    api_key=<key>

    cache_system is a no-op — OpenAI auto-caches stable prefixes ≥1024 tokens.
    """

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        timeout: float = 60.0,
        http_client: Any = None,
        extra_headers: dict[str, str] | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client = http_client
        self._extra_headers = dict(extra_headers or {})
        self._owns_client = http_client is None

    async def complete(
        self,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        model: str = "gpt-4o-mini",
        cache_system: bool = False,  # no-op: OpenAI auto-prefix-caches
    ) -> LLMResponse:
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=self._timeout)

        oai_messages: list[dict[str, Any]] = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        for m in messages:
            oai_messages.extend(_to_openai_messages(m))

        body: dict[str, Any] = {
            "model": model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = [_to_openai_tool(t) for t in tools]

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        headers.update(self._extra_headers)

        try:
            resp = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=body,
                headers=headers,
            )
            resp.raise_for_status()
        except Exception as exc:
            raise _map_error(exc) from exc

        return _from_openai_response(resp.json())

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()


def _map_error(exc: Exception) -> LLMError:
    """Map httpx exceptions to our normalized taxonomy."""
    try:
        import httpx
    except ImportError:
        return LLMError(str(exc))

    if isinstance(exc, httpx.TimeoutException):
        return TimeoutError(str(exc))

    if isinstance(exc, httpx.ConnectError):
        return NetworkError(str(exc))

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body: dict = {}
        try:
            body = exc.response.json()
        except Exception:
            pass

        if status == 429:
            retry_after: float | None = None
            raw_ra = exc.response.headers.get("retry-after")
            try:
                retry_after = float(raw_ra) if raw_ra else None
            except (TypeError, ValueError):
                pass
            return RateLimitError(str(exc), retry_after_s=retry_after)

        if status in (401, 403):
            return AuthError(str(exc))

        if status == 400:
            code = body.get("error", {}).get("code", "")
            msg_lower = str(exc).lower()
            if code == "context_length_exceeded" or "context" in msg_lower:
                return ContextLengthError(str(exc))
            return InvalidRequestError(str(exc))

        if status >= 500:
            return ServerError(str(exc))

    if isinstance(exc, httpx.NetworkError):
        return NetworkError(str(exc))

    return LLMError(str(exc))


# ── Translation helpers ────────────────────────────────────────────────────────


def _to_openai_messages(m: LLMMessage) -> list[dict[str, Any]]:
    """Convert one LLMMessage to one or more OpenAI messages.

    Our format follows Anthropic content blocks. OpenAI's format is flatter:
    tool_use → tool_calls on the assistant message; each tool_result →
    its own role=tool message.
    """
    if isinstance(m.content, str):
        return [{"role": m.role, "content": m.content}]

    if not isinstance(m.content, list):
        return [{"role": m.role, "content": str(m.content)}]

    if m.role == "assistant":
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in m.content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", "") or "")
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.get("id", "") or "",
                    "type": "function",
                    "function": {
                        "name": block.get("name", "") or "",
                        "arguments": json.dumps(block.get("input", {}) or {}),
                    },
                })
        msg: dict[str, Any] = {"role": "assistant"}
        # OpenAI requires content to be present (string or null) when tool_calls is set
        msg["content"] = "".join(text_parts) if text_parts else None
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return [msg]

    if m.role == "user":
        out: list[dict[str, Any]] = []
        text_parts_u: list[str] = []
        for block in m.content:
            if not isinstance(block, dict):
                text_parts_u.append(str(block))
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts_u.append(block.get("text", "") or "")
            elif btype == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                out.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", "") or "",
                    "content": str(content),
                })
        if text_parts_u:
            out.insert(0, {"role": "user", "content": "".join(text_parts_u)})
        return out

    return [{"role": m.role, "content": str(m.content)}]


def _to_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Anthropic tool schema → OpenAI function tool schema."""
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
        },
    }


_OPENAI_FINISH_MAP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",  # legacy
    "length": "max_tokens",
    "content_filter": "error",
}


def _from_openai_response(resp: dict[str, Any]) -> LLMResponse:
    """OpenAI /chat/completions response → our LLMResponse."""
    choices = resp.get("choices") or []
    choice = choices[0] if choices else {}
    message = choice.get("message", {}) or {}

    text = message.get("content") or ""

    tool_calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function", {}) or {}
        raw_args = fn.get("arguments", "{}") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except (json.JSONDecodeError, TypeError):
            args = {}
        tool_calls.append(ToolCall(
            id=tc.get("id", "") or "",
            name=fn.get("name", "") or "",
            arguments=args,
        ))

    finish_reason = choice.get("finish_reason", "stop")
    stop_reason = _OPENAI_FINISH_MAP.get(finish_reason, finish_reason)

    # Defensive recovery for "text-as-tool-call" — diagnosed in
    # run_2b1c86d56f where Codestral wrote `fs-list {"path":"src/render"}`
    # as plain text content with `finish_reason="stop"` and `tool_calls=[]`,
    # silently bricking the agent (runner saw end_turn → synthesised a
    # no-op COMMIT → task falsely marked complete). Salvage by parsing
    # the text shape `<tool-name> <json-args>`. Conservative: only fires
    # when we have NO real tool_calls AND the entire stripped text matches
    # the pattern AND the JSON parses cleanly. Any of those checks failing
    # leaves the response as text, preserving today's behaviour.
    if (
        not tool_calls
        and stop_reason == "end_turn"
        and text
    ):
        salvaged = _try_parse_text_as_tool_call(text)
        if salvaged is not None:
            tool_calls = [salvaged]
            text = ""
            stop_reason = "tool_use"

    usage = resp.get("usage") or {}
    cache_read = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
    return LLMResponse(
        text=text,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        model=resp.get("model", "") or "",
        input_tokens=int(usage.get("prompt_tokens", 0) or 0),
        output_tokens=int(usage.get("completion_tokens", 0) or 0),
        cache_read_tokens=cache_read,
        raw=resp,
    )


# Matches `<identifier> <{...json}>` over the WHOLE stripped text. The
# identifier allows hyphens (fs-list, ask-peer) and underscores. No
# leading prose, no trailing prose — strict to avoid false positives on
# normal model output that happens to mention a tool name.
_TEXT_TOOL_CALL_RE = re.compile(
    r"^([A-Za-z_][\w-]*)\s*(\{.*\})\s*$",
    re.DOTALL,
)


def _try_parse_text_as_tool_call(text: str) -> ToolCall | None:
    """Recognise `fs-list {"path": "x"}` written as text instead of as a
    structured tool_call (Codestral / some Mistral failure mode). Returns
    a synthetic ToolCall on confident match, None otherwise.

    Rules (all required):
      - whole stripped text matches `<identifier> <{json}>`
      - the JSON portion parses to a dict
      - identifier is at least 2 chars (cuts down on false positives
        like `x {1}` from incidental content)

    The synthetic id starts with `salvaged_` so downstream logs make it
    obvious this was a recovery, not a model-emitted tool_use block.
    """
    stripped = text.strip()
    if not stripped:
        return None
    m = _TEXT_TOOL_CALL_RE.match(stripped)
    if m is None:
        return None
    name, raw_args = m.group(1), m.group(2)
    if len(name) < 2:
        return None
    try:
        args = json.loads(raw_args)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(args, dict):
        return None
    # Synthetic id; the runner doesn't care about the value beyond
    # tool_use/tool_result pairing, but the prefix tags it for debugging.
    return ToolCall(id=f"salvaged_{name}", name=name, arguments=args)


# ── Factories ──────────────────────────────────────────────────────────────────


def ollama(base_url: str = "http://localhost:11434/v1", api_key: str = "ollama") -> OpenAIClient:
    """Pre-configured client for a local Ollama server."""
    return OpenAIClient(base_url=base_url, api_key=api_key)


def openai_compatible(
    base_url: str,
    api_key: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> OpenAIClient:
    """Generic factory for any OpenAI-compatible inference server."""
    return OpenAIClient(base_url=base_url, api_key=api_key, extra_headers=extra_headers)
