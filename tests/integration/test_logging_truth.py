"""Track 1b — end-to-end: direct file reads visible in iteration_metrics.jsonl.

Verifies that the call_tool token-accounting hook (agents/base.py) plus the
existing iteration_metrics persister (core/runner.py) produce a JSONL row
whose `direct_file_read_tokens` and `tool_token_breakdown` reflect real
read-class tool calls.

This is the regression guard for `run_75df8cf97a` / `run_26d48d4328` where
both fields were stuck at 0 despite agents reading hundreds of KB of files.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from agents.base import BaseAgent
from agents.tools.base import BaseTool
from core.tool_manifest import ToolDescriptor, ToolManifest
from core.workspace import IterationMetrics, Workspace


class _ReadingAgent(BaseAgent):
    """Agent that calls fs-read three times and fs-write once in run()."""

    async def run(self, workspace: Workspace) -> str:
        await self.call_tool(workspace, "fs-read", {"path": "src/a.py"})
        await self.call_tool(workspace, "fs-read", {"path": "src/b.py"})
        await self.call_tool(workspace, "fs-read", {"path": "src/c.py"})
        await self.call_tool(workspace, "fs-write", {"path": "src/d.py", "content": "x"})
        return "done"


class _StubTool(BaseTool):
    def __init__(self, name: str, payload: Any):
        self.name = name
        self.description = f"stub {name}"
        self.input_schema = {"type": "object"}
        self._payload = payload

    async def run(self, arguments: dict[str, Any]) -> Any:
        return self._payload


def _attach_metrics_persister(workspace: Workspace, path: Path) -> None:
    """Mirror the runner's persister wiring so we don't need a full runner."""

    def _persist(m: IterationMetrics) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(m), default=str) + "\n")

    workspace._iteration_metrics_persister = _persist


async def test_direct_file_reads_show_in_metrics(tmp_path: Path):
    """Running an agent that calls fs-read produces a metrics row with
    direct_file_read_tokens > 0 and tool_token_breakdown including 'fs-read'."""
    workspace = Workspace(run_id="run-test-1b", goal="logging-truth")
    metrics_path = tmp_path / "iteration_metrics.jsonl"
    _attach_metrics_persister(workspace, metrics_path)

    agent = _ReadingAgent(role="developer")
    agent.iteration = 1

    # Wire two tools: a read-class one and a write-class one.
    file_payload = {"content": "import os\n" * 200}  # ~1600 chars → ~400 tokens
    write_ack = {"ok": True, "bytes_written": 1}
    descriptors = [
        ToolDescriptor(name="fs-read", description="read", input_schema={}, invocation="local"),
        ToolDescriptor(name="fs-write", description="write", input_schema={}, invocation="local"),
    ]
    agent._manifest = ToolManifest(descriptors=descriptors)
    agent._local_tools = {
        "fs-read": _StubTool("fs-read", file_payload),
        "fs-write": _StubTool("fs-write", write_ack),
    }

    # Drive one iteration: open metrics, run, close metrics.
    agent._current_metrics = IterationMetrics(
        run_id=workspace.run_id, role=agent.role, iteration=agent.iteration
    )
    await agent.run(workspace)
    workspace.add_iteration_metrics(agent._current_metrics)

    # Assertions — file exists and reflects real token costs.
    assert metrics_path.exists(), "iteration_metrics.jsonl was not written"
    rows = [json.loads(line) for line in metrics_path.read_text().splitlines() if line.strip()]
    assert len(rows) == 1, f"expected exactly one row, got {len(rows)}"

    row = rows[0]
    assert row["role"] == "developer"
    assert row["iteration"] == 1

    # The bug we are guarding against: this used to be 0.
    assert row["direct_file_read_tokens"] > 0, (
        "direct_file_read_tokens should be > 0 after three fs-read calls"
    )

    # Per-tool breakdown reflects every call_tool, including writes.
    assert "fs-read" in row["tool_token_breakdown"]
    assert "fs-write" in row["tool_token_breakdown"]
    assert row["tool_token_breakdown"]["fs-read"] > row["tool_token_breakdown"]["fs-write"]

    # Reconciliation: read-class breakdown matches direct_file_read_tokens.
    assert row["tool_token_breakdown"]["fs-read"] == row["direct_file_read_tokens"]

    # tools_called records every invocation in order.
    assert row["tools_called"] == ["fs-read", "fs-read", "fs-read", "fs-write"]
