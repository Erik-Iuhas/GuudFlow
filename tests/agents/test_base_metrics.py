"""Test that BaseAgent captures IterationMetrics correctly via the llm() wrapper."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from agents.base import BaseAgent
from core.llm import LLMMessage, LLMResponse
from core.tool_manifest import ToolManifest
from core.workspace import IterationMetrics, Workspace


# ── Minimal mock LLM ────────────────────────────────────────────────────────


@dataclass
class MockLLM:
    """Returns a canned response with fixed token counts."""

    response: LLMResponse = field(
        default_factory=lambda: LLMResponse(
            text="done",
            model="claude-sonnet-4-6",
            input_tokens=120,
            output_tokens=40,
            cache_read_tokens=30,
            cache_creation_tokens=0,
        )
    )
    call_count: int = 0

    async def complete(self, **_kwargs: Any) -> LLMResponse:
        self.call_count += 1
        return self.response


# ── Concrete test agent ──────────────────────────────────────────────────────


class SimpleAgent(BaseAgent):
    """Agent that makes exactly one LLM call in run()."""

    async def run(self, workspace: Workspace) -> str:
        await self.llm([LLMMessage(role="user", content="go")])
        return "done"


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.fixture
def workspace() -> Workspace:
    return Workspace(run_id="run-test", goal="x")


@pytest.fixture
def agent() -> SimpleAgent:
    a = SimpleAgent(role="developer")
    a._manifest = ToolManifest(descriptors=[])
    a._llm = MockLLM()  # type: ignore[assignment]
    a._model = "claude-sonnet-4-6"
    a.iteration = 1
    return a


async def test_llm_accumulates_tokens_into_current_metrics(agent: SimpleAgent, workspace: Workspace):
    """llm() accumulates token counts into _current_metrics."""
    agent._current_metrics = IterationMetrics(
        run_id="run-test", role="developer", iteration=1
    )
    await agent.llm([LLMMessage(role="user", content="hello")])

    m = agent._current_metrics
    assert m is not None
    assert m.input_tokens == 120
    assert m.output_tokens == 40
    assert m.cache_read_tokens == 30
    assert m.llm_calls == 1
    assert m.model == "claude-sonnet-4-6"


async def test_llm_accumulates_across_multiple_calls(agent: SimpleAgent, workspace: Workspace):
    """Token sums accumulate across multiple llm() calls in one iteration."""
    agent._current_metrics = IterationMetrics(
        run_id="run-test", role="developer", iteration=1
    )
    await agent.llm([LLMMessage(role="user", content="a")])
    await agent.llm([LLMMessage(role="user", content="b")])

    m = agent._current_metrics
    assert m is not None
    assert m.input_tokens == 240
    assert m.output_tokens == 80
    assert m.llm_calls == 2


async def test_current_metrics_none_does_not_crash_llm(agent: SimpleAgent, workspace: Workspace):
    """llm() is safe when _current_metrics is None (e.g. called outside run)."""
    agent._current_metrics = None
    resp = await agent.llm([LLMMessage(role="user", content="x")])
    assert resp.text == "done"


async def test_llm_no_op_when_no_metrics_set(agent: SimpleAgent):
    """Ensure llm() without _current_metrics still returns the LLM response."""
    agent._current_metrics = None
    resp = await agent.llm([LLMMessage(role="user", content="x")])
    assert resp.model == "claude-sonnet-4-6"
