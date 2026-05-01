"""Task persistence + resume — M3.5 Chunk 7.

The task persister appends each Task mutation to `tasks.jsonl`.
`restore_workspace` replays that file (preferred) or falls back to
rebuilding from the board signal trail. Tests here exercise both
paths and the drift case where tasks.jsonl is missing entries that
board.jsonl contains.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.llm import MockLLMClient
from core.run_loader import (
    _rebuild_tasks_from_board,
    load_persisted_tasks,
    restore_workspace,
)
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.signals import Signal, SignalType
from core.workspace import Task, TaskRef
from worker.job_runner import serialise_task


def _runner(tmp_path: Path) -> AgentRunner:
    proj = ProjectConfig(project_id="p1", goal="g", agents=[], rbac={})
    return AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )


# ── Persister ───────────────────────────────────────────────────────────────


def test_task_persister_appends_to_tasks_jsonl(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="dev", goal="alpha",
    ))
    r.workspace.mutate_task("t1", "IN_PROGRESS", iteration=1)
    r.workspace.mutate_task("t1", "COMPLETE", closed_by_commit="abc")

    path = tmp_path / "run" / "tasks.jsonl"
    assert path.is_file()
    lines = path.read_text(encoding="utf-8").splitlines()
    # 3 mutations: assign + start + complete
    assert len(lines) == 3
    statuses = [json.loads(ln)["status"] for ln in lines]
    assert statuses == ["PENDING", "IN_PROGRESS", "COMPLETE"]
    assert json.loads(lines[-1])["closed_by_commit"] == "abc"


def test_task_persister_handles_references_and_subtasks(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="parent", story_id="s", assigned_to="lead", goal="auth",
    ))
    r.workspace.add_task(Task(
        task_id="child", story_id="s", assigned_to="dev",
        parent_task_id="parent", goal="issuer",
        references=[TaskRef(kind="file", value="docs/auth.md", note="design")],
    ))
    path = tmp_path / "run" / "tasks.jsonl"
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    child_row = rows[-1]
    assert child_row["parent_task_id"] == "parent"
    assert child_row["references"][0]["kind"] == "file"
    assert child_row["references"][0]["note"] == "design"


# ── Loader ──────────────────────────────────────────────────────────────────


def test_load_persisted_tasks_reads_jsonl(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(task_id="t1", story_id="s", assigned_to="dev", goal="a"))
    r.workspace.add_task(Task(task_id="t2", story_id="s", assigned_to="dev", goal="b"))
    r.workspace.mutate_task("t1", "IN_PROGRESS")

    tasks, queues = load_persisted_tasks(tmp_path / "run")
    assert set(tasks) == {"t1", "t2"}
    assert tasks["t1"].status == "IN_PROGRESS"
    assert tasks["t2"].status == "PENDING"
    assert queues["dev"] == ["t1", "t2"]


def test_load_persisted_tasks_missing_file_returns_empty(tmp_path: Path) -> None:
    tasks, queues = load_persisted_tasks(tmp_path / "noexist")
    assert tasks == {}
    assert queues == {}


def test_load_persisted_tasks_skips_malformed_lines(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    path = run_root / "tasks.jsonl"
    valid = json.dumps(serialise_task(Task(
        task_id="t1", story_id="s", assigned_to="dev", goal="a",
    )))
    path.write_text(f"not-json\n{valid}\n{{not-a-task}}\n", encoding="utf-8")

    tasks, queues = load_persisted_tasks(run_root)
    assert list(tasks) == ["t1"]


# ── Resume integration ─────────────────────────────────────────────────────


def test_restore_workspace_rebuilds_tasks_and_queues(tmp_path: Path) -> None:
    """End-to-end: a runner persists tasks, then restore_workspace
    rehydrates an equivalent workspace."""
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="dev",
        created_by="architect",
        goal="ship issuer",
        acceptance_criteria=["rs256"],
    ))
    r.workspace.add_task(Task(
        task_id="t2", story_id="s", assigned_to="dev", goal="x",
    ))
    r.workspace.add_task(Task(
        task_id="t3", story_id="s", assigned_to="dev", goal="y",
    ))
    r.workspace.mutate_task("t1", "IN_PROGRESS", iteration=1)

    proj = ProjectConfig(project_id="p1", goal="g", agents=[], rbac={})
    restored = restore_workspace(tmp_path / "run", proj)
    assert set(restored.tasks) == {"t1", "t2", "t3"}
    assert restored.tasks["t1"].status == "IN_PROGRESS"
    assert restored.tasks["t1"].acceptance_criteria == ["rs256"]
    assert restored.task_queues["dev"] == ["t1", "t2", "t3"]


def test_restore_workspace_with_no_tasks_jsonl_falls_back_to_board(tmp_path: Path) -> None:
    """Drift case from chunks.md §7 tests: tasks.jsonl missing but
    board.jsonl contains TASK_* signals — loader rebuilds from the
    signal trail. Imperfect (no acceptance criteria, no refs) but
    state and identity survive."""
    run_root = tmp_path / "run"
    run_root.mkdir()
    # Synthesize a board.jsonl with TASK_ASSIGNED + TASK_STARTED.
    from worker.job_runner import serialise_signal
    board_path = run_root / "board.jsonl"
    sig_assigned = Signal(
        type=SignalType.TASK_ASSIGNED,
        summary="task:t1 → dev: ship issuer",
        from_agent="architect",
        detail="t1",
        iteration=0,
    )
    sig_started = Signal(
        type=SignalType.TASK_STARTED,
        summary="task:t1 → IN_PROGRESS",
        from_agent="dev",
        detail="t1",
        iteration=1,
    )
    with open(board_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(serialise_signal(sig_assigned), default=str) + "\n")
        f.write(json.dumps(serialise_signal(sig_started), default=str) + "\n")

    proj = ProjectConfig(project_id="p1", goal="g", agents=[], rbac={})
    restored = restore_workspace(run_root, proj)
    assert "t1" in restored.tasks
    assert restored.tasks["t1"].status == "IN_PROGRESS"
    assert restored.tasks["t1"].assigned_to == "dev"


def test_rebuild_tasks_from_board_handles_complete_with_commit() -> None:
    """COMPLETE signals carry detail='{id};commit={hash}'. Rebuild
    must extract the commit hash so resumed runs see closed_by_commit."""
    sig_assigned = Signal(
        type=SignalType.TASK_ASSIGNED,
        summary="task:t1 → dev: x",
        from_agent="planner",
        detail="t1",
    )
    sig_complete = Signal(
        type=SignalType.TASK_COMPLETED,
        summary="task:t1 → COMPLETE",
        from_agent="dev",
        detail="t1;commit=abc123",
        iteration=2,
    )
    tasks, _ = _rebuild_tasks_from_board([sig_assigned, sig_complete])
    assert tasks["t1"].status == "COMPLETE"
    assert tasks["t1"].closed_by_commit == "abc123"
    assert tasks["t1"].completed_at_iter == 2


@pytest.mark.asyncio
async def test_subtask_cascade_survives_resume(tmp_path: Path) -> None:
    """Parent task closes after resume when remaining child closes —
    the runner rebuilds desk state, then a fresh _handle_task_closures
    on the last child cascades the parent. Per chunks.md §7 tests."""
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="parent", story_id="s", assigned_to="dev", goal="auth",
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
    r.workspace.mutate_task("c1", "COMPLETE", closed_by_commit="h1")
    r.workspace.mutate_task("c2", "IN_PROGRESS")

    # Crash + resume.
    proj = ProjectConfig(project_id="p1", goal="g", agents=[], rbac={})
    restored = restore_workspace(tmp_path / "run", proj)
    r2 = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
        restored_workspace=restored,
    )
    assert r2.workspace.tasks["c1"].status == "COMPLETE"
    assert r2.workspace.tasks["c2"].status == "IN_PROGRESS"
    assert r2.workspace.tasks["parent"].status == "IN_PROGRESS"

    # Close c2 — parent cascades.
    from core import protocol
    block = protocol.CompactBlock(
        kind="COMMIT", fields={"summary": "verifier done", "closes": "task:c2"},
    )
    await r2._handle_task_closures("dev", block, commit_hash="h2")
    assert r2.workspace.tasks["c2"].status == "COMPLETE"
    assert r2.workspace.tasks["parent"].status == "COMPLETE"


def test_restore_with_no_persistence_at_all(tmp_path: Path) -> None:
    """Empty run_root → empty workspace. Regression guard against
    M1 runs that never wrote any task state."""
    run_root = tmp_path / "run"
    run_root.mkdir()
    proj = ProjectConfig(project_id="p1", goal="g", agents=[], rbac={})
    restored = restore_workspace(run_root, proj)
    assert restored.tasks == {}
    assert restored.task_queues == {}
