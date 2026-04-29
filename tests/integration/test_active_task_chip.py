"""M4.7 — smoke test that /api/runs/{id}/desks returns the task in IN_PROGRESS
after ASSIGN_TASK + TASK_STARTED, as expected by ActiveTaskChip.

This test exercises only the Python side (workspace + serialise_task), not
the full HTTP layer. It confirms the shape that the hook depends on.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.workspace import Task
from worker.job_runner import serialise_task


def _runner(tmp_path: Path) -> AgentRunner:
    proj = ProjectConfig(project_id="p1", goal="build login", agents=[], rbac={})
    return AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=None,  # type: ignore[arg-type]
    )


def test_desks_returns_in_progress_task(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    task = Task(
        task_id="t1", story_id="s1",
        assigned_to="developer", created_by="architect",
        goal="Implement JWT auth",
        acceptance_criteria=["Token issued on POST /login"],
    )
    r.workspace.add_task(task)
    r.workspace.mutate_task("t1", "IN_PROGRESS", iteration=1)

    # Simulate what GET /api/runs/{id}/desks returns from workspace
    tasks_by_id = {tid: serialise_task(t) for tid, t in r.workspace.tasks.items()}
    desks: dict[str, list[dict]] = {}
    for role, ids in r.workspace.task_queues.items():
        desks[role] = [tasks_by_id[tid] for tid in ids if tid in tasks_by_id]

    # Chip expects: desks["developer"][0].status == "IN_PROGRESS"
    assert "developer" in desks
    assert len(desks["developer"]) == 1
    chip_task = desks["developer"][0]
    assert chip_task["status"] == "IN_PROGRESS"
    assert chip_task["goal"] == "Implement JWT auth"
    assert chip_task["acceptance_criteria"] == ["Token issued on POST /login"]


def test_desks_serialise_completed_task_excluded_from_in_progress(tmp_path: Path) -> None:
    """Completed tasks stay on the desk list but the chip skips them."""
    r = _runner(tmp_path)
    task = Task(
        task_id="t2", story_id="s1",
        assigned_to="developer", created_by="architect",
        goal="Write tests",
    )
    r.workspace.add_task(task)
    r.workspace.mutate_task("t2", "IN_PROGRESS", iteration=1)
    r.workspace.mutate_task("t2", "COMPLETE", iteration=2)

    tasks_by_id = {tid: serialise_task(t) for tid, t in r.workspace.tasks.items()}
    desks: dict[str, list[dict]] = {}
    for role, ids in r.workspace.task_queues.items():
        desks[role] = [tasks_by_id[tid] for tid in ids if tid in tasks_by_id]

    assert desks["developer"][0]["status"] == "COMPLETE"
    # The chip logic (in TS) would show "idle" for this; no IN_PROGRESS found.
