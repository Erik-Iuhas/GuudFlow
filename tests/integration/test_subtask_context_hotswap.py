"""Track 8 — subtask context hot-swap.

Closing a subtask should:
  1. Stash the message-history span produced since it started.
  2. Replace the span with a single canonical marker.
  3. Bump IterationMetrics.subtask_messages_stashed by N (count of
     blocks evicted).

Calling recall-subtask(<id>) should:
  1. Find the marker in the live history.
  2. Splice the stashed span back in at the marker's position.
  3. Bump IterationMetrics.subtask_messages_restored.

Both stash + recall are idempotent within a run — re-closing a
recalled subtask re-stashes from scratch.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from core.llm import LLMMessage, MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.tool_manifest import RoleRBAC


@dataclass
class _FakeRecord:
    """Stand-in for TaskCompletionRecord — only `summary` is required by
    `_compact_messages_after_close`. `deliverables` is optional and
    Track 8's marker formatter renders it when present."""
    summary: str = "subtask done"
    deliverables: list = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.deliverables is None:
            self.deliverables = []


def _make_runner(tmp_path: Path) -> AgentRunner:
    proj = ProjectConfig(
        project_id="p_hotswap", goal="g",
        agents=[AgentConfig(role="dev", model_tier="solid")],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=[])},
    )
    return AgentRunner(
        project=proj, run_id="r_hotswap",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )


def _seed_messages_for_subtask(runner: AgentRunner, task_id: str, n_blocks: int) -> None:
    """Simulate an agent's tool-use loop having appended N pairs of
    (assistant tool-use, user tool-result) since `task_id` started."""
    runner._current_agent_messages.append(LLMMessage(
        role="user", content=f"start working on task:{task_id}",
    ))
    for i in range(n_blocks):
        runner._current_agent_messages.append(LLMMessage(
            role="assistant", content=f"calling tool {i} for {task_id}",
        ))
        runner._current_agent_messages.append(LLMMessage(
            role="user", content=f"tool result {i} for {task_id}: {'x' * 200}",
        ))


def _attach_metrics_row(runner: AgentRunner, role: str = "dev"):
    """Give the runner's agent a metrics row so the stash/restore counters
    have somewhere to land."""
    from core.workspace import IterationMetrics
    agent = runner.agents.get(role)
    if agent is None:
        # The runner builds agents lazily in run(); for these unit-style
        # tests we instantiate a stub via the local-tool wiring path.
        # Easiest: just tack a minimal object onto runner.agents.
        class _Stub:
            _current_metrics = None
            _local_tools = None
        agent = _Stub()
        runner.agents[role] = agent
    agent._current_metrics = IterationMetrics(
        run_id=runner.run_id, role=role, iteration=0,
    )
    return agent


# ── Stash on close ────────────────────────────────────────────────────────


def test_close_subtask_stashes_message_span_and_inserts_marker(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)
    agent = _attach_metrics_row(runner)

    _seed_messages_for_subtask(runner, "s1", n_blocks=3)
    initial_len = len(runner._current_agent_messages)
    # 1 start msg + 3 pairs = 7 entries
    assert initial_len == 7

    runner._compact_messages_after_close(
        runner._current_agent_messages, "s1", "abc1234567",
        _FakeRecord(summary="implemented foo", deliverables=["foo.py"]),
    )

    # Live history shrinks: 1 start + 1 marker = 2 entries
    # (the start msg is BEFORE the first task_id mention so it survives;
    # actually the start msg DOES mention task_id so it's part of the
    # stashed span; live ends with just the marker).
    assert len(runner._current_agent_messages) == 1
    marker = runner._current_agent_messages[0]
    assert marker.role == "user"
    assert marker.content.startswith("[SUBTASK CLOSED — id=s1, sha=abc1234]")
    assert "summary: implemented foo" in marker.content
    assert "deliverables: [foo.py]" in marker.content
    assert 'recall-subtask("s1")' in marker.content

    # Stash holds the 7 evicted blocks.
    assert "s1" in runner._stashed_subtask_messages
    assert len(runner._stashed_subtask_messages["s1"]) == 7

    # Metrics: 7 stashed.
    assert agent._current_metrics.subtask_messages_stashed == 7


def test_marker_omits_deliverables_when_none(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)
    _attach_metrics_row(runner)
    _seed_messages_for_subtask(runner, "s2", n_blocks=2)

    runner._compact_messages_after_close(
        runner._current_agent_messages, "s2", "def4567890",
        _FakeRecord(summary="quick win", deliverables=[]),
    )
    marker_text = runner._current_agent_messages[-1].content
    assert "[SUBTASK CLOSED — id=s2, sha=def4567]" in marker_text
    assert "summary: quick win" in marker_text
    assert "deliverables:" not in marker_text


def test_degraded_summary_falls_back_cleanly(tmp_path: Path) -> None:
    """When the summarizer returns '(summary unavailable)', the marker
    falls back to a generic '[task X] work completed' phrase."""
    runner = _make_runner(tmp_path)
    _attach_metrics_row(runner)
    _seed_messages_for_subtask(runner, "s3", n_blocks=1)

    runner._compact_messages_after_close(
        runner._current_agent_messages, "s3", "fff0000000",
        _FakeRecord(summary="(summary unavailable)"),
    )
    marker_text = runner._current_agent_messages[-1].content
    assert "[task s3] work completed" in marker_text


# ── Recall ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recall_subtask_splices_stashed_messages_back(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)
    agent = _attach_metrics_row(runner)
    _seed_messages_for_subtask(runner, "s1", n_blocks=4)
    runner._compact_messages_after_close(
        runner._current_agent_messages, "s1", "abc",
        _FakeRecord(summary="ok"),
    )
    # Add some unrelated messages after the marker, so we can prove the
    # recall splices in-place (not at the end).
    runner._current_agent_messages.append(
        LLMMessage(role="assistant", content="moving on"),
    )
    runner._current_agent_messages.append(
        LLMMessage(role="user", content="another tool result"),
    )
    pre_recall_len = len(runner._current_agent_messages)
    stashed_count = len(runner._stashed_subtask_messages["s1"])

    from agents.tools.recall_subtask import RecallSubtask
    tool = RecallSubtask("dev", runner)
    res = await tool.run({"subtask_id": "s1"})
    assert res["ok"]
    assert res["restored_messages"] == stashed_count

    # Marker replaced: live = pre_recall_len - 1 (marker) + stashed_count
    assert len(runner._current_agent_messages) == pre_recall_len - 1 + stashed_count

    # Splice happened in-place: the trailing "moving on" + "another tool result"
    # are still at the END.
    last_two = runner._current_agent_messages[-2:]
    assert last_two[0].content == "moving on"
    assert last_two[1].content == "another tool result"

    # Stash consumed.
    assert "s1" not in runner._stashed_subtask_messages
    # Restore metric bumped.
    assert agent._current_metrics.subtask_messages_restored == stashed_count


@pytest.mark.asyncio
async def test_recall_subtask_unknown_id_returns_error(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)
    _attach_metrics_row(runner)
    from agents.tools.recall_subtask import RecallSubtask
    tool = RecallSubtask("dev", runner)
    res = await tool.run({"subtask_id": "ghost"})
    assert "error" in res
    assert "ghost" in res["error"]


@pytest.mark.asyncio
async def test_close_after_recall_re_stashes(tmp_path: Path) -> None:
    """Idempotent: closing s1 again after recall stashes the span anew."""
    runner = _make_runner(tmp_path)
    _attach_metrics_row(runner)
    _seed_messages_for_subtask(runner, "s1", n_blocks=2)
    runner._compact_messages_after_close(
        runner._current_agent_messages, "s1", "abc",
        _FakeRecord(summary="first close"),
    )

    from agents.tools.recall_subtask import RecallSubtask
    tool = RecallSubtask("dev", runner)
    await tool.run({"subtask_id": "s1"})
    # Stash empty after recall.
    assert "s1" not in runner._stashed_subtask_messages

    # Simulate fresh tool calls then close again.
    runner._current_agent_messages.append(
        LLMMessage(role="assistant", content="more s1 work"),
    )
    runner._current_agent_messages.append(
        LLMMessage(role="user", content="another result for s1"),
    )
    runner._compact_messages_after_close(
        runner._current_agent_messages, "s1", "def",
        _FakeRecord(summary="second close"),
    )
    # Stash is back, with the freshly-stashed span.
    assert "s1" in runner._stashed_subtask_messages


# ── Isolation ─────────────────────────────────────────────────────────────


def test_two_subtasks_stashed_independently(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)
    _attach_metrics_row(runner)
    _seed_messages_for_subtask(runner, "s1", n_blocks=2)
    runner._compact_messages_after_close(
        runner._current_agent_messages, "s1", "aaa",
        _FakeRecord(summary="s1 done"),
    )
    _seed_messages_for_subtask(runner, "s2", n_blocks=3)
    runner._compact_messages_after_close(
        runner._current_agent_messages, "s2", "bbb",
        _FakeRecord(summary="s2 done"),
    )

    assert "s1" in runner._stashed_subtask_messages
    assert "s2" in runner._stashed_subtask_messages
    # Independent counts — s1 had 5 blocks (1 start + 2 pairs),
    # s2 had 7 (1 start + 3 pairs).
    assert len(runner._stashed_subtask_messages["s1"]) == 5
    assert len(runner._stashed_subtask_messages["s2"]) == 7
