"""Unit tests for _compact_messages_after_close heuristic (M5.5 C2).

Tests the heuristic directly by constructing a fake AgentRunner and calling
the method with controlled message lists.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.llm import LLMMessage, MockLLMClient, MockTurn
from core.runner import AgentRunner, AgentConfig, ProjectConfig
from core.task_summarizer import TaskCompletionRecord
from core.tool_manifest import RoleRBAC


def _runner(tmp_path: Path) -> AgentRunner:
    project = ProjectConfig(
        project_id="p-compact",
        goal="test compaction",
        agents=[AgentConfig(role="dev", description="dev", depends_on=[])],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=[])},
    )
    r = AgentRunner(
        project=project,
        run_id="r-compact",
        run_root=tmp_path,
        llm=MockLLMClient([]),
    )
    return r


def _record(summary: str = "Did the work.") -> TaskCompletionRecord:
    return TaskCompletionRecord(
        task_id="T1",
        summary=summary,
        deliverables=["Added foo.py"],
        references=["foo.py"],
        closed_by_commit="abc12345",
    )


def _msgs(*texts: str) -> list[LLMMessage]:
    """Build a message list alternating user/assistant."""
    result = []
    for i, t in enumerate(texts):
        role = "user" if i % 2 == 0 else "assistant"
        result.append(LLMMessage(role=role, content=t))
    return result


def test_compaction_with_prior_marker(tmp_path: Path) -> None:
    """Compaction starts from the message AFTER the most recent prior marker."""
    messages = _msgs(
        "[runner] Task T0 closed at abc1234. Summary: previous task done",
        "start working on T1 now",
        "using tool fs-write",
        "result: file written",
        "using tool fs-edit",
    )
    runner = _runner(tmp_path)
    runner._compact_messages_after_close(messages, "T1", "def56789", _record())

    # Should have: original prior-marker message + new marker for T1
    assert len(messages) == 2
    assert "[runner] Task T0 closed" in messages[0].content
    assert "[runner] Task T1 closed at def5678" in messages[1].content


def test_compaction_no_prior_marker_uses_task_id(tmp_path: Path) -> None:
    """No prior marker: walk forward to first mention of task_id."""
    messages = _msgs(
        "some setup context",
        "starting task T1 now",
        "tool call 1",
        "result 1",
        "tool call 2",
    )
    runner = _runner(tmp_path)
    runner._compact_messages_after_close(messages, "T1", "abc12345", _record())

    # The message at index 1 mentions T1; everything from there onward is replaced.
    assert len(messages) == 2
    assert messages[0].content == "some setup context"
    assert "[runner] Task T1 closed at abc1234" in messages[1].content


def test_compaction_fallback_last_two(tmp_path: Path) -> None:
    """When task_id never mentioned and no prior marker, fall back to last 2."""
    messages = _msgs("msg A", "msg B", "msg C", "msg D", "msg E")
    runner = _runner(tmp_path)
    # task_id "TXYZ" never appears in any message
    runner._compact_messages_after_close(messages, "TXYZ", "abc12345", _record("Did stuff."))

    # Last 2 replaced; first 3 preserved
    assert len(messages) == 4
    assert messages[0].content == "msg A"
    assert messages[1].content == "msg B"
    assert messages[2].content == "msg C"
    assert "[runner] Task TXYZ closed" in messages[3].content


def test_compaction_empty_messages(tmp_path: Path) -> None:
    """Empty message list is a no-op."""
    messages: list[LLMMessage] = []
    runner = _runner(tmp_path)
    runner._compact_messages_after_close(messages, "T1", "abc12345", _record())
    assert messages == []


def test_compaction_degraded_record(tmp_path: Path) -> None:
    """Degraded record uses goal fallback text."""
    messages = _msgs("work for T1", "more work")
    runner = _runner(tmp_path)
    degraded = TaskCompletionRecord(
        task_id="T1",
        summary="(summary unavailable)",
        deliverables=[],
        references=[],
        closed_by_commit="abc12345",
    )
    runner._compact_messages_after_close(messages, "T1", "abc12345", degraded)
    assert "[runner] Task T1 closed" in messages[-1].content
