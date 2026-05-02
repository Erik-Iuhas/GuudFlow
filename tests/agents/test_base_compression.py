"""Track 3c — call_tool integrates compression and accounts savings into metrics."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from agents.base import BaseAgent
from agents.tools.base import BaseTool
from core.tool_manifest import ToolDescriptor, ToolManifest
from core.workspace import IterationMetrics, Workspace


class _StubTool(BaseTool):
    """Tool that returns a configurable result."""

    def __init__(self, name: str, result: Any):
        self.name = name
        self.description = f"stub {name}"
        self.input_schema = {"type": "object"}
        self._result = result

    async def run(self, arguments: dict[str, Any]) -> Any:
        return self._result


class _RaisingTool(BaseTool):
    def __init__(self, name: str, exc: Exception):
        self.name = name
        self.description = f"raising {name}"
        self.input_schema = {"type": "object"}
        self._exc = exc

    async def run(self, arguments: dict[str, Any]) -> Any:
        raise self._exc


class _Agent(BaseAgent):
    async def run(self, workspace: Workspace) -> str:  # pragma: no cover — unused
        return "ok"


def _wire(agent: _Agent, tool: BaseTool) -> None:
    descriptor = ToolDescriptor(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
        invocation="local",
    )
    agent._manifest = ToolManifest(descriptors=[*(agent._manifest or ToolManifest()).descriptors, descriptor])
    agent._local_tools[tool.name] = tool


@pytest.fixture
def workspace() -> Workspace:
    return Workspace(run_id="r-3c", goal="x")


@pytest.fixture
def agent() -> _Agent:
    a = _Agent(role="dev")
    a._manifest = ToolManifest(descriptors=[])
    a.iteration = 1
    return a


# ── Compression actually fires + metrics record ─────────────────────────────


async def test_call_tool_compresses_git_log(agent: _Agent, workspace: Workspace):
    """git-log result with Signed-off-by trailers gets compressed; metrics record savings."""
    raw = {
        "ok": True,
        "commits": [
            {"hash": f"h{i}", "message": f"feat: thing {i}\n\nSigned-off-by: Alice"}
            for i in range(5)
        ],
    }
    _wire(agent, _StubTool("git-log", raw))
    agent._current_metrics = IterationMetrics(run_id="r-3c", role="dev", iteration=1)

    result = await agent.call_tool(workspace, "git-log", {})

    # Trailers stripped from every commit message.
    for c in result["commits"]:
        assert "Signed-off-by" not in c["message"]
    m = agent._current_metrics
    assert m.tool_output_tokens_raw > 0
    assert m.tool_output_tokens_compressed > 0
    assert m.tokens_saved_by_compression > 0
    assert m.tool_output_tokens_compressed < m.tool_output_tokens_raw


async def test_call_tool_compresses_git_changed_files(agent: _Agent, workspace: Workspace):
    """git-changed-files with > 5 entries gets directory-grouped."""
    files = [
        "src/api/a.py", "src/api/b.py", "src/api/c.py",
        "src/db/d.py", "src/db/e.py",
        "tests/g.py", "tests/h.py",
    ]
    raw = {"ok": True, "files": files, "count": len(files)}
    _wire(agent, _StubTool("git-changed-files", raw))
    agent._current_metrics = IterationMetrics(run_id="r-3c", role="dev", iteration=1)

    result = await agent.call_tool(workspace, "git-changed-files", {})

    assert "summary" in result
    assert "files" not in result  # bulk list dropped
    assert result["files_full_count"] == len(files)


# ── Bypass ─────────────────────────────────────────────────────────────────


async def test_call_tool_compress_false_returns_raw(agent: _Agent, workspace: Workspace):
    """compress=False bypasses compression entirely — raw output, zero savings."""
    raw = {
        "ok": True,
        "commits": [
            {"hash": f"h{i}", "message": f"feat: thing {i}\n\nSigned-off-by: Alice"}
            for i in range(5)
        ],
    }
    _wire(agent, _StubTool("git-log", raw))
    agent._current_metrics = IterationMetrics(run_id="r-3c", role="dev", iteration=1)

    result = await agent.call_tool(workspace, "git-log", {}, compress=False)

    # Trailers preserved when bypassing.
    for c in result["commits"]:
        assert "Signed-off-by" in c["message"]
    m = agent._current_metrics
    # No compression metrics recorded when bypassed.
    assert m.tool_output_tokens_raw == 0
    assert m.tokens_saved_by_compression == 0


# ── Tool that raises — no compression, exception propagates ─────────────────


async def test_call_tool_exception_does_not_run_compression(agent: _Agent, workspace: Workspace):
    """A tool that raises must propagate; compression never runs on a failure."""
    _wire(agent, _RaisingTool("git-log", RuntimeError("git-log failed")))
    agent._current_metrics = IterationMetrics(run_id="r-3c", role="dev", iteration=1)

    with pytest.raises(RuntimeError):
        await agent.call_tool(workspace, "git-log", {})

    m = agent._current_metrics
    assert m.tool_output_tokens_raw == 0
    assert m.tokens_saved_by_compression == 0


# ── Unregistered tool → identity, no savings, no crash ──────────────────────


async def test_call_tool_unregistered_compressor_records_identity(
    agent: _Agent, workspace: Workspace
):
    """Tool without a registered compressor: result returned as-is, savings 0,
    raw and compressed token counts equal."""
    raw = {"ok": True, "value": "x" * 200}
    _wire(agent, _StubTool("custom-tool", raw))
    agent._current_metrics = IterationMetrics(run_id="r-3c", role="dev", iteration=1)

    result = await agent.call_tool(workspace, "custom-tool", {})

    assert result == raw
    m = agent._current_metrics
    assert m.tool_output_tokens_raw == m.tool_output_tokens_compressed
    assert m.tokens_saved_by_compression == 0


# ── Track 1b interaction: tool_token_breakdown reflects compressed bytes ──


async def test_tool_token_breakdown_uses_compressed_tokens(agent: _Agent, workspace: Workspace):
    """tool_token_breakdown should reflect what the LLM actually sees (post-
    compression). Otherwise the metric is reporting bytes that never went
    to the model."""
    raw = {
        "ok": True,
        "commits": [
            {"hash": f"h{i}", "message": f"feat: thing {i}\n\n" + "Signed-off-by: Alice\n" * 3}
            for i in range(8)
        ],
    }
    _wire(agent, _StubTool("git-log", raw))
    agent._current_metrics = IterationMetrics(run_id="r-3c", role="dev", iteration=1)

    await agent.call_tool(workspace, "git-log", {})

    m = agent._current_metrics
    breakdown = m.tool_token_breakdown["git-log"]
    # Should match the compressed cost, not the raw cost.
    assert breakdown == m.tool_output_tokens_compressed
    assert breakdown < m.tool_output_tokens_raw


# ── Metrics-None safety ─────────────────────────────────────────────────────


async def test_compression_metrics_no_op_when_metrics_none(agent: _Agent, workspace: Workspace):
    """call_tool must not crash when _current_metrics is None."""
    _wire(agent, _StubTool("git-log", {"ok": True, "commits": [{"hash": "x", "message": "feat"}]}))
    agent._current_metrics = None

    result = await agent.call_tool(workspace, "git-log", {})
    assert result["ok"] is True
