"""LLM client Protocol, shared dataclasses, and the scripted test mock."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol


@dataclass
class LLMMessage:
    """One turn in a conversation. Content can be a plain string OR a
    list of Anthropic-style content blocks (used when feeding tool_result back)."""

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
    """Normalised response from any LLM client."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"  # "end_turn" | "tool_use" | "max_tokens" | "error"
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    raw: Any = None


class LLMClient(Protocol):
    """Minimal LLM contract. All real and mock implementations satisfy this."""

    async def complete(
        self,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8192,
        model: str = "claude-sonnet-4-6",
        cache_system: bool = False,
    ) -> LLMResponse: ...


# ── Mock client ────────────────────────────────────────────────────────────────


@dataclass
class MockTurn:
    """One scripted response. Either text-only OR tool-call only."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None  # auto-derived if None
    model: str = "mock"


class MockLLMClient:
    """Scripted LLM client for tests.

    Pass a list of MockTurns; each complete() call pops the next one.
    Raises AssertionError if the script is exhausted, so tests stay honest
    about how many LLM calls they expect.
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
        max_tokens: int = 8192,
        model: str = "claude-sonnet-4-6",
        cache_system: bool = False,
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
