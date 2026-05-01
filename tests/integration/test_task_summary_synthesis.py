"""M5.5 C1 integration test — TaskCompletionRecord synthesized on task close.

Verifies that when an agent closes a task via `closes: task:<id>` in a COMMIT,
the runner synthesizes a TaskCompletionRecord and stores it on the Task.
The TaskSummarizer uses a MockLLMClient deterministic response.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import protocol
from core.llm import LLMMessage, MockLLMClient, MockTurn
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.task_summarizer import TaskSummarizer
from core.tool_manifest import RoleRBAC
from core.workspace import Task


_SUMMARY_RESPONSE = json.dumps({
    "summary": "Implemented the login route with JWT auth.",
    "deliverables": ["Added routes/login.py", "Wrote 3 unit tests"],
    "references": ["routes/login.py", "tests/test_login.py"],
    "closed_by_commit": "abc1234",
})


def _runner(tmp_path: Path) -> AgentRunner:
    proj = ProjectConfig(
        project_id="p-summary",
        goal="build API",
        agents=[AgentConfig(role="dev", description="dev", depends_on=[])],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=[])},
    )
    summarizer_llm = MockLLMClient([MockTurn(text=_SUMMARY_RESPONSE)])
    return AgentRunner(
        project=proj,
        run_id="r-summary",
        run_root=tmp_path / "run",
        llm=MockLLMClient([]),
        task_summarizer=TaskSummarizer(llm=summarizer_llm),
    )


def _commit_block(closes: str, summary: str = "did the work") -> protocol.CompactBlock:
    return protocol.CompactBlock(kind="COMMIT", fields={"summary": summary, "closes": closes})


@pytest.mark.asyncio
async def test_completion_summary_set_on_task_close(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="T1",
        story_id="s1",
        assigned_to="dev",
        created_by="arch",
        goal="implement login route",
        acceptance_criteria=["JWT returned on success"],
    ))
    r.workspace.mutate_task("T1", "IN_PROGRESS", iteration=1)
    r._current_agent_messages = [
        LLMMessage(role="user", content="implement task T1"),
        LLMMessage(role="assistant", content="writing routes/login.py"),
    ]

    await r._handle_task_closures("dev", _commit_block("task:T1"), commit_hash="abc12345")

    task = r.workspace.tasks["T1"]
    assert task.status == "COMPLETE"
    assert task.completion_summary is not None
    assert isinstance(task.completion_summary, dict)
    assert task.completion_summary["summary"] == "Implemented the login route with JWT auth."
    assert "Added routes/login.py" in task.completion_summary["deliverables"]
    assert task.completion_summary["task_id"] == "T1"
    assert "synthesized_at" in task.completion_summary


@pytest.mark.asyncio
async def test_no_summarizer_leaves_completion_summary_none(tmp_path: Path) -> None:
    """Without a TaskSummarizer, completion_summary stays None."""
    proj = ProjectConfig(
        project_id="p-no-sum",
        goal="test",
        agents=[AgentConfig(role="dev", description="dev", depends_on=[])],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=[])},
    )
    r = AgentRunner(
        project=proj,
        run_id="r-no-sum",
        run_root=tmp_path / "run2",
        llm=MockLLMClient([]),
    )
    r.workspace.add_task(Task(
        task_id="T2", story_id="s1", assigned_to="dev",
        created_by="arch", goal="write tests",
    ))
    r.workspace.mutate_task("T2", "IN_PROGRESS", iteration=1)

    await r._handle_task_closures("dev", _commit_block("T2"), commit_hash="def67890")

    task = r.workspace.tasks["T2"]
    assert task.status == "COMPLETE"
    assert task.completion_summary is None


@pytest.mark.asyncio
async def test_summarizer_failure_doesnt_block_close(tmp_path: Path) -> None:
    """When summarizer LLM returns malformed JSON twice, close still succeeds."""
    proj = ProjectConfig(
        project_id="p-fail-sum",
        goal="test",
        agents=[AgentConfig(role="dev", description="dev", depends_on=[])],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=[])},
    )
    bad_summarizer = TaskSummarizer(llm=MockLLMClient([MockTurn(text="not json"), MockTurn(text="also bad")]))
    r = AgentRunner(
        project=proj,
        run_id="r-fail-sum",
        run_root=tmp_path / "run3",
        llm=MockLLMClient([]),
        task_summarizer=bad_summarizer,
    )
    r.workspace.add_task(Task(
        task_id="T3", story_id="s1", assigned_to="dev",
        created_by="arch", goal="deploy",
    ))
    r.workspace.mutate_task("T3", "IN_PROGRESS", iteration=1)
    r._current_agent_messages = []

    await r._handle_task_closures("dev", _commit_block("T3"), commit_hash="abc12345")

    task = r.workspace.tasks["T3"]
    # Task still closes despite summarizer failure.
    assert task.status == "COMPLETE"
    # Degraded record is still stored.
    assert task.completion_summary is not None
    assert task.completion_summary["summary"] == "(summary unavailable)"
