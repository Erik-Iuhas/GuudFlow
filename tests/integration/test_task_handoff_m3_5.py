"""M3.5 — task handoff integration tests.

Exercises the runner's `_handle_task_closures` and
`_maybe_auto_start_task` paths without spinning up a full LLM-backed
run. Each test plants tasks via `Workspace.add_task`, builds a
synthetic CompactBlock for the COMMIT, and verifies the resulting
state transitions on the workspace.

Per chunks.md: chunks 4–5 land the basic case; the full integration
matrix (resume, user_comment routing, depth caps via the API) lives
in Chunks 6–8.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core import protocol
from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.signals import SignalType
from core.workspace import Task, TaskRef


def _runner(tmp_path: Path) -> AgentRunner:
    proj = ProjectConfig(project_id="p1", goal="g", agents=[], rbac={})
    return AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )


def _commit_block(closes: str | None = None) -> protocol.CompactBlock:
    fields: dict[str, str] = {"summary": "did the work"}
    if closes:
        fields["closes"] = closes
    return protocol.CompactBlock(kind="COMMIT", fields=fields)


# ── closes: bookkeeping ─────────────────────────────────────────────────────


def test_closes_marks_task_complete_and_emits_signal(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="developer",
        created_by="architect", goal="ship issuer",
    ))
    r.workspace.mutate_task("t1", "IN_PROGRESS", iteration=1)
    # Drain prior signals.
    starting_count = sum(1 for s in r.workspace.board if s.type == SignalType.TASK_COMPLETED)

    r._handle_task_closures(
        "developer", _commit_block(closes="task:t1"), commit_hash="abc1234",
    )

    task = r.workspace.tasks["t1"]
    assert task.status == "COMPLETE"
    assert task.closed_by_commit == "abc1234"

    completed = [s for s in r.workspace.board if s.type == SignalType.TASK_COMPLETED]
    assert len(completed) == starting_count + 1


def test_closes_accepts_bare_id_as_well_as_prefixed(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="dev", goal="x",
    ))
    r.workspace.mutate_task("t1", "IN_PROGRESS")
    r._handle_task_closures("dev", _commit_block(closes="t1"), commit_hash="hash")
    assert r.workspace.tasks["t1"].status == "COMPLETE"


def test_closing_other_roles_task_emits_warning_does_not_block(tmp_path: Path) -> None:
    """§4.3 — runner-side check is bookkeeping, not gating. The COMMIT
    is not blocked; the warning surfaces alongside it for the reviewer."""
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="developer", goal="x",
    ))
    r.workspace.mutate_task("t1", "IN_PROGRESS")

    r._handle_task_closures(
        "wrong_role", _commit_block(closes="task:t1"), commit_hash="h",
    )
    # Task untouched.
    assert r.workspace.tasks["t1"].status == "IN_PROGRESS"
    # Warning posted.
    warnings = [s for s in r.workspace.board if s.type == SignalType.TASK_OUT_OF_SCOPE]
    assert len(warnings) == 1
    assert "wrong_role" in warnings[0].summary
    assert "developer" in warnings[0].summary


def test_closing_unknown_task_emits_warning(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r._handle_task_closures(
        "dev", _commit_block(closes="task:does_not_exist"), commit_hash="h",
    )
    warnings = [s for s in r.workspace.board if s.type == SignalType.TASK_OUT_OF_SCOPE]
    assert len(warnings) == 1
    assert "unknown" in warnings[0].summary


def test_closing_already_complete_emits_warning(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="dev", goal="x",
    ))
    r.workspace.mutate_task("t1", "IN_PROGRESS")
    r.workspace.mutate_task("t1", "COMPLETE", closed_by_commit="prior")

    r._handle_task_closures("dev", _commit_block(closes="task:t1"), commit_hash="new")
    warnings = [s for s in r.workspace.board if s.type == SignalType.TASK_OUT_OF_SCOPE]
    assert len(warnings) == 1
    assert "COMPLETE" in warnings[0].detail


def test_commit_without_closes_no_op(tmp_path: Path) -> None:
    """Regression guard: M1 COMMIT flow unchanged when closes: absent."""
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="dev", goal="x",
    ))
    r.workspace.mutate_task("t1", "IN_PROGRESS")
    snapshot = list(r.workspace.board)

    r._handle_task_closures("dev", _commit_block(closes=None), commit_hash="h")

    assert r.workspace.tasks["t1"].status == "IN_PROGRESS"
    assert r.workspace.board == snapshot


def test_handle_task_closures_ignores_non_commit_blocks(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="dev", goal="x",
    ))
    verify_block = protocol.CompactBlock(
        kind="VERIFY", fields={"result": "PASS"},
    )
    r._handle_task_closures("dev", verify_block, commit_hash="h")
    assert r.workspace.tasks["t1"].status == "PENDING"


# ── subtask cascade ────────────────────────────────────────────────────────


def test_closing_last_child_auto_closes_parent(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="parent", story_id="s", assigned_to="lead", goal="auth",
    ))
    r.workspace.add_task(Task(
        task_id="c1", story_id="s", assigned_to="dev",
        parent_task_id="parent", goal="issuer",
    ))
    r.workspace.add_task(Task(
        task_id="c2", story_id="s", assigned_to="dev",
        parent_task_id="parent", goal="verifier",
    ))
    r.workspace.mutate_task("parent", "IN_PROGRESS")
    r.workspace.mutate_task("c1", "IN_PROGRESS")
    r.workspace.mutate_task("c2", "IN_PROGRESS")

    # Close the first child — parent should NOT cascade yet.
    r._handle_task_closures("dev", _commit_block(closes="task:c1"), commit_hash="h1")
    assert r.workspace.tasks["c1"].status == "COMPLETE"
    assert r.workspace.tasks["parent"].status == "IN_PROGRESS"

    # Close the last child — parent cascades.
    r._handle_task_closures("dev", _commit_block(closes="task:c2"), commit_hash="h2")
    assert r.workspace.tasks["c2"].status == "COMPLETE"
    parent = r.workspace.tasks["parent"]
    assert parent.status == "COMPLETE"
    assert parent.closed_by_commit == "h2"


def test_parent_does_not_cascade_when_child_blocked(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="parent", story_id="s", assigned_to="lead", goal="p",
    ))
    r.workspace.add_task(Task(
        task_id="c1", story_id="s", assigned_to="dev",
        parent_task_id="parent", goal="c1",
    ))
    r.workspace.add_task(Task(
        task_id="c2", story_id="s", assigned_to="dev",
        parent_task_id="parent", goal="c2",
    ))
    r.workspace.mutate_task("parent", "IN_PROGRESS")
    r.workspace.mutate_task("c1", "IN_PROGRESS")
    r.workspace.mutate_task("c2", "BLOCKED", blocked_on=["external"])

    r._handle_task_closures("dev", _commit_block(closes="task:c1"), commit_hash="h")
    assert r.workspace.tasks["parent"].status == "IN_PROGRESS"


# ── TASK_STARTED auto-emit ──────────────────────────────────────────────────


def test_auto_start_picks_fifo_pending(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(task_id="t1", story_id="s", assigned_to="dev", goal="a"))
    r.workspace.add_task(Task(task_id="t2", story_id="s", assigned_to="dev", goal="b"))

    r._maybe_auto_start_task("dev", tool_arguments={"path": "x"})

    assert r.workspace.tasks["t1"].status == "IN_PROGRESS"
    assert r.workspace.tasks["t2"].status == "PENDING"
    started = [s for s in r.workspace.board if s.type == SignalType.TASK_STARTED]
    assert len(started) == 1


def test_auto_start_honours_explicit_task_id_in_args(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(task_id="t1", story_id="s", assigned_to="dev", goal="a"))
    r.workspace.add_task(Task(task_id="t2", story_id="s", assigned_to="dev", goal="b"))

    r._maybe_auto_start_task("dev", tool_arguments={"task_id": "task:t2"})
    assert r.workspace.tasks["t1"].status == "PENDING"
    assert r.workspace.tasks["t2"].status == "IN_PROGRESS"


def test_auto_start_no_op_when_already_in_progress(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(task_id="t1", story_id="s", assigned_to="dev", goal="a"))
    r.workspace.mutate_task("t1", "IN_PROGRESS")
    started_before = sum(1 for s in r.workspace.board if s.type == SignalType.TASK_STARTED)

    r._maybe_auto_start_task("dev")
    assert sum(1 for s in r.workspace.board if s.type == SignalType.TASK_STARTED) == started_before


def test_auto_start_no_op_when_desk_empty(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r._maybe_auto_start_task("dev")  # should not raise


def test_auto_start_ignores_unknown_task_id_falls_back_to_fifo(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(task_id="t1", story_id="s", assigned_to="dev", goal="a"))
    r._maybe_auto_start_task("dev", tool_arguments={"task_id": "task:bogus"})
    # Falls back to FIFO pickup rather than emitting noise.
    assert r.workspace.tasks["t1"].status == "IN_PROGRESS"


# ── End-to-end mini flow: assign → start → close → cascade ──────────────────


def test_end_to_end_assign_start_close_cascade(tmp_path: Path) -> None:
    """Architect assigns parent + 2 children to developer; developer
    closes both children via COMMIT closes:; parent auto-cascades."""
    r = _runner(tmp_path)
    ws = r.workspace

    # Architect plants the work.
    ws.add_task(Task(
        task_id="parent", story_id="s", assigned_to="developer",
        created_by="architect", goal="auth",
        references=[TaskRef(kind="file", value="docs/auth.md", note="design")],
    ))
    ws.add_task(Task(
        task_id="c1", story_id="s", assigned_to="developer",
        created_by="architect", parent_task_id="parent", goal="issuer",
        acceptance_criteria=["rs256"],
    ))
    ws.add_task(Task(
        task_id="c2", story_id="s", assigned_to="developer",
        created_by="architect", parent_task_id="parent", goal="verifier",
        acceptance_criteria=["validates"],
    ))

    # Developer's first tool call kicks off auto-start.
    r._maybe_auto_start_task("developer", tool_arguments={"path": "src/issuer.py"})
    assert ws.tasks["parent"].status == "IN_PROGRESS"

    # Developer closes c1.
    r._handle_task_closures(
        "developer", _commit_block(closes="task:c1"), commit_hash="hash1",
    )
    assert ws.tasks["c1"].status == "COMPLETE"
    assert ws.tasks["parent"].status == "IN_PROGRESS"

    # Developer closes c2 — parent cascades.
    r._handle_task_closures(
        "developer", _commit_block(closes="task:c2"), commit_hash="hash2",
    )
    assert ws.tasks["c2"].status == "COMPLETE"
    assert ws.tasks["parent"].status == "COMPLETE"
    assert ws.tasks["parent"].closed_by_commit == "hash2"
