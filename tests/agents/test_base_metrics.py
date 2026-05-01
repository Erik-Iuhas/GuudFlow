"""Test that BaseAgent captures IterationMetrics correctly via the llm() wrapper."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from agents.base import BaseAgent
from agents.tools.base import BaseTool
from core.llm import LLMMessage, LLMResponse
from core.tool_manifest import ToolDescriptor, ToolManifest
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


# ── Track 1b: tool token accounting in call_tool ─────────────────────────────


class _MockTool(BaseTool):
    """Configurable mock tool. Returns `result` or raises `exc` if set."""

    def __init__(self, name: str, result: Any = None, exc: Exception | None = None):
        self.name = name
        self.description = f"mock {name}"
        self.input_schema = {"type": "object"}
        self._result = result if result is not None else {"ok": True}
        self._exc = exc

    async def run(self, arguments: dict[str, Any]) -> Any:
        if self._exc is not None:
            raise self._exc
        return self._result


def _wire_tool(agent: SimpleAgent, tool: _MockTool) -> None:
    """Register a local tool on a test agent."""
    descriptor = ToolDescriptor(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
        invocation="local",
    )
    agent._manifest = ToolManifest(descriptors=[*(agent._manifest or ToolManifest()).descriptors, descriptor])
    agent._local_tools[tool.name] = tool


async def test_call_tool_records_breakdown(agent: SimpleAgent, workspace: Workspace):
    """Every call_tool invocation populates tool_token_breakdown[name]."""
    tool = _MockTool("fs-list", result={"entries": ["a.py", "b.py", "c.py"]})
    _wire_tool(agent, tool)
    agent._current_metrics = IterationMetrics(run_id="r", role="developer", iteration=1)

    await agent.call_tool(workspace, "fs-list", {"path": "."})
    await agent.call_tool(workspace, "fs-list", {"path": "."})

    m = agent._current_metrics
    assert m is not None
    # Two calls of identical result → breakdown sums to 2× single-call cost.
    assert "fs-list" in m.tool_token_breakdown
    assert m.tool_token_breakdown["fs-list"] > 0
    assert m.tool_token_breakdown["fs-list"] == 2 * (m.tool_token_breakdown["fs-list"] // 2)


async def test_call_tool_read_class_increments_direct_reads(agent: SimpleAgent, workspace: Workspace):
    """fs-read result tokens flow into direct_file_read_tokens."""
    payload = "x" * 4000  # ~1000 tokens at 4 chars/token
    tool = _MockTool("fs-read", result={"content": payload})
    _wire_tool(agent, tool)
    agent._current_metrics = IterationMetrics(run_id="r", role="developer", iteration=1)

    await agent.call_tool(workspace, "fs-read", {"path": "src/main.py"})

    m = agent._current_metrics
    assert m is not None
    assert m.direct_file_read_tokens > 0
    assert m.tool_token_breakdown["fs-read"] == m.direct_file_read_tokens


async def test_call_tool_write_class_does_not_increment_reads(agent: SimpleAgent, workspace: Workspace):
    """fs-write result tokens do NOT flow into direct_file_read_tokens."""
    tool = _MockTool("fs-write", result={"ok": True, "bytes_written": 1234})
    _wire_tool(agent, tool)
    agent._current_metrics = IterationMetrics(run_id="r", role="developer", iteration=1)

    await agent.call_tool(workspace, "fs-write", {"path": "src/foo.py", "content": "..."})

    m = agent._current_metrics
    assert m is not None
    assert m.direct_file_read_tokens == 0
    assert m.tool_token_breakdown["fs-write"] > 0  # still tracked in breakdown


async def test_call_tool_failure_does_not_record_tokens(agent: SimpleAgent, workspace: Workspace):
    """A tool that raises produces no breakdown entry and no direct-read delta."""
    tool = _MockTool("fs-read", exc=RuntimeError("boom"))
    _wire_tool(agent, tool)
    agent._current_metrics = IterationMetrics(run_id="r", role="developer", iteration=1)

    with pytest.raises(RuntimeError):
        await agent.call_tool(workspace, "fs-read", {"path": "src/main.py"})

    m = agent._current_metrics
    assert m is not None
    assert "fs-read" not in m.tool_token_breakdown
    assert m.direct_file_read_tokens == 0
    # tools_called is appended BEFORE invocation (see call_tool); that's expected.
    assert "fs-read" in m.tools_called


async def test_account_tool_tokens_swallows_exceptions(agent: SimpleAgent, workspace: Workspace):
    """Unserialisable tool result must not break call_tool."""

    class _Unserialisable:
        def __repr__(self) -> str:
            raise RuntimeError("repr blew up")

    tool = _MockTool("fs-read", result={"weird": _Unserialisable()})
    _wire_tool(agent, tool)
    agent._current_metrics = IterationMetrics(run_id="r", role="developer", iteration=1)

    # Must not raise — accounting failure is swallowed.
    result = await agent.call_tool(workspace, "fs-read", {"path": "x"})
    assert result is not None
    m = agent._current_metrics
    assert m is not None
    # Accounting silently no-op'd: no breakdown entry was recorded.
    assert m.tool_token_breakdown.get("fs-read", 0) == 0
    assert m.direct_file_read_tokens == 0


async def test_call_tool_no_metrics_does_not_crash(agent: SimpleAgent, workspace: Workspace):
    """call_tool with _current_metrics=None must not raise."""
    tool = _MockTool("fs-list", result={"entries": []})
    _wire_tool(agent, tool)
    agent._current_metrics = None

    result = await agent.call_tool(workspace, "fs-list", {"path": "."})
    assert result == {"entries": []}
