"""LLM abstraction — what BaseAgent and the runner call.

DESIGN §29 rule 63: all LLM traffic goes through the runner on the host
(never inside the sandbox container). This module defines the contract.

Adapters shipped:
  - `AnthropicClient` — primary; uses the `anthropic` SDK.
  - `OpenAIClient` — OpenAI-compatible /chat/completions over httpx.
    Works with: OpenAI, Ollama (point at http://localhost:11434/v1),
    LM Studio, vLLM, OpenRouter, llamafile, or any other OpenAI-shaped
    inference server. Crucial for the §35 smaller-model story.
  - `MockLLMClient` — script-driven; tests only.

Internal message format follows Anthropic's content-block convention
(text + tool_use + tool_result blocks). Adapters that target other
providers translate at the boundary; the runner stays provider-agnostic.

The protocol is minimal. The agent's tool-use loop sits OUTSIDE this
module — it calls `complete()` repeatedly, dispatches tool calls via
`BaseAgent.call_tool`, and feeds tool results back as messages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol


@dataclass
class LLMMessage:
    """One turn in a conversation. Content can be a plain string OR a
    list of content blocks (used when feeding tool_result back)."""

    role: str  # "user" | "assistant"
    content: Any


@dataclass
class ToolCall:
    """A tool invocation requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """What `complete()` returns."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"  # "end_turn" | "tool_use" | "max_tokens" | "error"
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    raw: Any = None


class LLMClient(Protocol):
    """Minimal LLM contract. Real and mock implementations both satisfy this."""

    async def complete(
        self,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        model: str = "claude-sonnet-4-6",
    ) -> LLMResponse:
        ...


# ── Mock client for tests ──────────────────────────────────────────────────


@dataclass
class MockTurn:
    """One scripted response. Either text-only OR tool-call only — for
    multi-turn tool-use, queue several MockTurns."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None  # auto-derived if None
    model: str = "mock"


class MockLLMClient:
    """Scripted LLM client for tests.

    Pass a list of `MockTurn`s in order; each call to `complete()` pops
    the next turn. Raises if the script is exhausted, so tests stay
    honest about how many calls they expect.
    """

    def __init__(self, turns: Iterable[MockTurn] | None = None):
        self._queue: list[MockTurn] = list(turns or [])
        self.calls: list[dict[str, Any]] = []

    def queue(self, *turns: MockTurn) -> None:
        self._queue.extend(turns)

    async def complete(
        self,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        model: str = "claude-sonnet-4-6",
    ) -> LLMResponse:
        self.calls.append({
            "system": system,
            "messages": messages,
            "tools": tools,
            "max_tokens": max_tokens,
            "model": model,
        })
        if not self._queue:
            raise AssertionError(
                "MockLLMClient script exhausted — test made more LLM calls than queued"
            )
        turn = self._queue.pop(0)
        stop_reason = turn.stop_reason or (
            "tool_use" if turn.tool_calls else "end_turn"
        )
        return LLMResponse(
            text=turn.text,
            tool_calls=list(turn.tool_calls),
            stop_reason=stop_reason,
            model=turn.model,
        )


# ── Anthropic adapter (Stage 6 wires real API; here a thin wrapper) ────────


class AnthropicClient:
    """Wrapper over `anthropic.AsyncAnthropic` for production use.

    Constructed lazily — the SDK isn't imported at module load so this
    file is testable without an `ANTHROPIC_API_KEY` environment variable.
    """

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key
        self._client = None

    async def complete(
        self,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        model: str = "claude-sonnet-4-6",
    ) -> LLMResponse:
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(api_key=self._api_key) if self._api_key else AsyncAnthropic()

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [_msg_to_api(m) for m in messages],
        }
        if tools:
            kwargs["tools"] = tools

        resp = await self._client.messages.create(**kwargs)
        return _api_to_response(resp)


def _msg_to_api(m: LLMMessage) -> dict[str, Any]:
    """Convert our LLMMessage to the Anthropic API shape."""
    return {"role": m.role, "content": m.content}


def _api_to_response(resp: Any) -> LLMResponse:
    """Convert an Anthropic API response to our LLMResponse."""
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
        raw=resp,
    )


# ── OpenAI-compatible adapter ──────────────────────────────────────────────


class OpenAIClient:
    """OpenAI-compatible adapter over httpx.

    Works against any server that implements `/chat/completions` in the
    OpenAI shape. Verified targets:

      OpenAI:    base_url="https://api.openai.com/v1"   api_key=<your key>
      Ollama:    base_url="http://localhost:11434/v1"   api_key="ollama"
      LM Studio: base_url="http://localhost:1234/v1"    api_key="lm-studio"
      vLLM:      base_url="http://host:8000/v1"         api_key=<configured>
      OpenRouter: base_url="https://openrouter.ai/api/v1" api_key=<your key>

    The adapter translates between our internal Anthropic-shaped message
    format (text/tool_use/tool_result content blocks) and OpenAI's
    flatter shape (assistant.tool_calls + role=tool messages). The
    runner's tool-use loop doesn't change.

    Note on tool support: most modern Ollama models (qwen2.5, llama3.1
    8B+, mistral-small) support OpenAI-style function calling. Some
    smaller models do not — for those, drop the `tools` parameter and
    fall back to prompt-based tool calling (not yet implemented; would
    be a separate adapter).
    """

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        timeout: float = 60.0,
        http_client: Any = None,  # httpx.AsyncClient — typed lazily for testability
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

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        headers.update(self._extra_headers)

        resp = await self._client.post(
            f"{self._base_url}/chat/completions",
            json=body,
            headers=headers,
        )
        resp.raise_for_status()
        return _from_openai_response(resp.json())

    async def aclose(self) -> None:
        """Close the underlying http client if we own it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()


def _to_openai_messages(m: LLMMessage) -> list[dict[str, Any]]:
    """Convert one of our LLMMessages to one or more OpenAI messages.

    Our format follows Anthropic's content blocks. OpenAI's format is
    flatter: tool_use becomes `tool_calls` on the assistant message, and
    each tool_result becomes its own `role=tool` message.
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
        # OpenAI requires `content` to be present (string or null) when
        # `tool_calls` is set.
        msg["content"] = "".join(text_parts) if text_parts else None
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return [msg]

    if m.role == "user":
        out: list[dict[str, Any]] = []
        text_parts = []
        for block in m.content:
            if not isinstance(block, dict):
                text_parts.append(str(block))
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", "") or "")
            elif btype == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    # Nested blocks (rare in practice). Flatten to text.
                    content = " ".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                out.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", "") or "",
                    "content": str(content),
                })
        if text_parts:
            out.insert(0, {"role": "user", "content": "".join(text_parts)})
        return out

    return [{"role": m.role, "content": str(m.content)}]


def _to_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Anthropic tool schema → OpenAI tool schema.

    Anthropic: {name, description, input_schema}
    OpenAI:    {type: "function", function: {name, description, parameters}}
    """
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
        },
    }


# OpenAI finish_reason → our stop_reason vocabulary.
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

    usage = resp.get("usage") or {}
    return LLMResponse(
        text=text,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        model=resp.get("model", "") or "",
        input_tokens=int(usage.get("prompt_tokens", 0) or 0),
        output_tokens=int(usage.get("completion_tokens", 0) or 0),
        raw=resp,
    )


# ── Convenience factories ──────────────────────────────────────────────────


def ollama(base_url: str = "http://localhost:11434/v1", api_key: str = "ollama") -> OpenAIClient:
    """Pre-configured OpenAIClient pointing at a local Ollama server.

    Usage:
        client = ollama()
        # Then pass to AgentRunner(llm=client).
        # When constructing AgentConfig, set model_tier per usual; the
        # runner maps tier to a model name. For Ollama you typically want
        # to override TIER_TO_MODEL with your local model names
        # (e.g. "qwen2.5:7b", "llama3.1:8b").
    """
    return OpenAIClient(base_url=base_url, api_key=api_key)


def openai_compatible(
    base_url: str,
    api_key: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> OpenAIClient:
    """Generic factory for any OpenAI-compatible server.

    LM Studio:  openai_compatible("http://localhost:1234/v1", "lm-studio")
    vLLM:       openai_compatible("http://host:8000/v1", "<your-key>")
    OpenRouter: openai_compatible("https://openrouter.ai/api/v1", "<key>",
                                  extra_headers={"HTTP-Referer": "..."})
    """
    return OpenAIClient(base_url=base_url, api_key=api_key, extra_headers=extra_headers)
