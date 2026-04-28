"""Task dataclass + per-agent desk plumbing — M3.5 Chunk 1.

DESIGN §38.12. Tasks are intra-role work items; planners post them to
agent desks and they close at COMMIT time. This file exercises the
dataclass roundtrip and the workspace helpers (add_task, mutate_task,
desk_for, child_tasks) — protocol wire format and tool surface land
in later chunks.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from core.signals import SignalType
from core.workspace import Task, TaskRef, Workspace


def _ws() -> Workspace:
    return Workspace(run_id="r1", goal="test")


def test_task_dataclass_roundtrip() -> None:
    task = Task(
        task_id="abc123",
        story_id="story_x",
        assigned_to="developer",
        created_by="architect",
        goal="implement issuer",
        acceptance_criteria=["rs256 signing", "key rotation"],
        out_of_scope=["oauth2 social"],
        references=[TaskRef(kind="file", value="docs/auth.md", note="design source")],
        created_at_iter=1,
    )
    d = asdict(task)
    assert d["task_id"] == "abc123"
    assert d["assigned_to"] == "developer"
    assert d["acceptance_criteria"] == ["rs256 signing", "key rotation"]
    assert d["references"][0]["kind"] == "file"
    assert d["status"] == "PENDING"


def test_add_task_inserts_dict_queue_and_posts_signal() -> None:
    ws = _ws()
    task = Task(
        task_id="t1",
        story_id="s1",
        assigned_to="developer",
        created_by="architect",
        goal="ship the issuer",
    )
    ws.add_task(task)

    assert ws.tasks["t1"] is task
    assert ws.task_queues["developer"] == ["t1"]

    posted = [s for s in ws.board if s.type == SignalType.TASK_ASSIGNED]
    assert len(posted) == 1
    assert posted[0].from_agent == "architect"
    assert posted[0].detail == "t1"


def test_add_task_generates_id_when_blank() -> None:
    ws = _ws()
    task = Task(task_id="", story_id="s1", assigned_to="developer", goal="x")
    ws.add_task(task)
    assert task.task_id  # populated
    assert task.task_id in ws.tasks


def test_add_task_rejects_missing_assignee() -> None:
    ws = _ws()
    task = Task(task_id="t1", story_id="s1", assigned_to="", goal="x")
    with pytest.raises(ValueError, match="assigned_to"):
        ws.add_task(task)


def test_add_task_rejects_duplicate_id() -> None:
    ws = _ws()
    ws.add_task(Task(task_id="t1", story_id="s1", assigned_to="dev", goal="a"))
    with pytest.raises(ValueError, match="already exists"):
        ws.add_task(Task(task_id="t1", story_id="s1", assigned_to="dev", goal="b"))


def test_subtask_depth_cap_two_levels() -> None:
    ws = _ws()
    ws.add_task(Task(task_id="parent", story_id="s1", assigned_to="planner", goal="p"))
    ws.add_task(Task(
        task_id="child", story_id="s1", assigned_to="dev",
        parent_task_id="parent", goal="c",
    ))
    with pytest.raises(ValueError, match="depth cap"):
        ws.add_task(Task(
            task_id="grandchild", story_id="s1", assigned_to="dev",
            parent_task_id="child", goal="gc",
        ))


def test_mutate_task_posts_status_signal() -> None:
    ws = _ws()
    ws.add_task(Task(task_id="t1", story_id="s1", assigned_to="dev", goal="x"))

    ws.mutate_task("t1", "IN_PROGRESS", iteration=2)
    assert ws.tasks["t1"].status == "IN_PROGRESS"
    assert ws.tasks["t1"].started_at_iter == 2

    started = [s for s in ws.board if s.type == SignalType.TASK_STARTED]
    assert len(started) == 1

    ws.mutate_task("t1", "COMPLETE", iteration=3, closed_by_commit="abc1234")
    assert ws.tasks["t1"].status == "COMPLETE"
    assert ws.tasks["t1"].closed_by_commit == "abc1234"
    assert ws.tasks["t1"].completed_at_iter == 3

    completed = [s for s in ws.board if s.type == SignalType.TASK_COMPLETED]
    assert len(completed) == 1
    assert "commit=abc1234" in (completed[0].detail or "")


def test_mutate_task_rejects_unknown_id() -> None:
    ws = _ws()
    with pytest.raises(KeyError):
        ws.mutate_task("nope", "IN_PROGRESS")


def test_mutate_task_rejects_pending_transition() -> None:
    ws = _ws()
    ws.add_task(Task(task_id="t1", story_id="s1", assigned_to="dev", goal="x"))
    with pytest.raises(ValueError, match="PENDING"):
        ws.mutate_task("t1", "PENDING")


def test_mutate_task_rejects_unknown_kwarg() -> None:
    ws = _ws()
    ws.add_task(Task(task_id="t1", story_id="s1", assigned_to="dev", goal="x"))
    with pytest.raises(ValueError, match="no attribute"):
        ws.mutate_task("t1", "IN_PROGRESS", not_a_field="oops")


def test_desk_for_filters_completed_by_default() -> None:
    ws = _ws()
    ws.add_task(Task(task_id="t1", story_id="s1", assigned_to="dev", goal="a"))
    ws.add_task(Task(task_id="t2", story_id="s1", assigned_to="dev", goal="b"))
    ws.add_task(Task(task_id="t3", story_id="s1", assigned_to="dev", goal="c"))
    ws.mutate_task("t2", "COMPLETE", closed_by_commit="abc")

    desk = ws.desk_for("dev")
    assert [t.task_id for t in desk] == ["t1", "t3"]

    desk_full = ws.desk_for("dev", include_complete=True)
    assert [t.task_id for t in desk_full] == ["t1", "t2", "t3"]


def test_desk_for_preserves_fifo_insertion() -> None:
    ws = _ws()
    for i, goal in enumerate(["first", "second", "third"]):
        ws.add_task(Task(
            task_id=f"t{i}", story_id="s1", assigned_to="dev", goal=goal,
        ))
    desk = ws.desk_for("dev")
    assert [t.goal for t in desk] == ["first", "second", "third"]


def test_child_tasks_returns_immediate_children() -> None:
    ws = _ws()
    ws.add_task(Task(task_id="parent", story_id="s1", assigned_to="planner", goal="p"))
    ws.add_task(Task(
        task_id="c1", story_id="s1", assigned_to="dev",
        parent_task_id="parent", goal="c1",
    ))
    ws.add_task(Task(
        task_id="c2", story_id="s1", assigned_to="dev",
        parent_task_id="parent", goal="c2",
    ))
    ws.add_task(Task(task_id="other", story_id="s1", assigned_to="dev", goal="solo"))

    children = ws.child_tasks("parent")
    assert [t.task_id for t in children] == ["c1", "c2"]
    assert ws.child_tasks("other") == []


def test_task_persister_fires_on_add_and_mutate() -> None:
    ws = _ws()
    seen: list[tuple[str, str]] = []

    def record(task: Task) -> None:
        seen.append((task.task_id, task.status))

    ws.set_task_persister(record)
    ws.add_task(Task(task_id="t1", story_id="s1", assigned_to="dev", goal="x"))
    ws.mutate_task("t1", "IN_PROGRESS")
    ws.mutate_task("t1", "COMPLETE", closed_by_commit="ab")

    assert seen == [("t1", "PENDING"), ("t1", "IN_PROGRESS"), ("t1", "COMPLETE")]


def test_persister_exception_does_not_break_workspace() -> None:
    ws = _ws()
    ws.set_task_persister(lambda _t: (_ for _ in ()).throw(OSError("disk full")))
    # Should not raise — same swallow contract as the board persister.
    ws.add_task(Task(task_id="t1", story_id="s1", assigned_to="dev", goal="x"))
    assert "t1" in ws.tasks


def test_empty_workspace_no_task_state_does_not_break() -> None:
    """Regression guard — workspaces that never use tasks behave as
    they did pre-M3.5. `tasks` and `task_queues` are empty dicts."""
    ws = _ws()
    assert ws.tasks == {}
    assert ws.task_queues == {}
    assert ws.desk_for("anyone") == []
    assert ws.child_tasks("nobody") == []
