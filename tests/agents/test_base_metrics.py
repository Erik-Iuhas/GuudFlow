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


# ── Track 3d.2: IterationMetrics field existence ─────────────────────────────


def test_direct_file_read_tokens_field_exists():
    """IterationMetrics must have direct_file_read_tokens with default 0."""
    m = IterationMetrics(run_id="r", role="dev", iteration=1)
    assert hasattr(m, "direct_file_read_tokens")
    assert m.direct_file_read_tokens == 0


# ── Track 3d.2: ContextSnapshot aggregation ──────────────────────────────────


def test_context_tokens_propagate_to_iteration_metrics():
    """ContextSnapshot records written to workspace.context_log are summed into
    IterationMetrics by the runner finalize block.

    This test directly drives the aggregation loop (mirrors what runner does)
    to verify the field assignments are correct and the sums are accurate.
    """
    from core.workspace import ContextSnapshot

    ws = Workspace(run_id="r-ctx", goal="test context aggregation")

    snap = ContextSnapshot(
        run_id="r-ctx",
        role="developer",
        iteration=1,
        task="write auth module",
        used_tokens=800,
        repo_map_tokens=200,
        git_diff_tokens=150,
        relevant_files=[("src/auth.py", 300), ("src/utils.py", 150)],
    )
    ws.record_context_snapshot(snap)

    m = IterationMetrics(run_id="r-ctx", role="developer", iteration=1)

    # Replicate the runner finalize aggregation loop (core/runner.py).
    for s in ws.context_log:
        if s.role == m.role and s.iteration == m.iteration:
            m.repo_map_tokens += s.repo_map_tokens
            m.git_diff_tokens += s.git_diff_tokens
            m.relevant_files_tokens += sum(t for _, t in s.relevant_files)
            m.context_tokens_total += s.used_tokens

    assert m.context_tokens_total == 800
    assert m.repo_map_tokens == 200
    assert m.git_diff_tokens == 150
    assert m.relevant_files_tokens == 450  # 300 + 150
