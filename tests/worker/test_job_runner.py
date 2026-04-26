"""Tests for worker/job_runner.py — end-to-end job processing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.builtin.code_reviewer import CodeReviewer
from agents.builtin.memory_writer import MemoryWriter
from agents.persona import Persona
from agents.registry import registry as _registry
from core.llm import LLMMessage, MockLLMClient, MockTurn, ToolCall
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from worker.docker_manager import DockerManager
from worker.job_runner import (
    JobRunner,
    default_runner_factory,
    serialise_project,
)
from worker.queue import (
    JOB_COMPLETE,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    InMemoryJobQueue,
    Job,
)


# ── serialise_project ───────────────────────────────────────────────────────


def test_serialise_project_minimal():
    p = serialise_project({"project_id": "p1", "goal": "x"})
    assert p.project_id == "p1"
    assert p.goal == "x"
    assert p.agents == []
    assert p.max_iterations == 5  # default


def test_serialise_project_full():
    data = {
        "project_id": "p1",
        "goal": "build x",
        "max_iterations": 7,
        "agents": [
            {
                "role": "developer",
                "description": "writes code",
                "depends_on": [],
                "system_prompt": "be precise",
                "declared_local_tools": ["fs:read", "fs:write"],
                "model_tier": "solid",
                "max_tool_iterations": 6,
            },
            {
                "role": "reviewer",
                "depends_on": ["developer"],
                "agent_class_role_type": "code_reviewer",
                "is_reviewer": True,
            },
        ],
        "rbac": {
            "developer": {"allowed_scopes": ["fs:write"]},
            "reviewer": {"allowed_scopes": []},
        },
    }
    p = serialise_project(data)
    assert len(p.agents) == 2
    assert p.agents[0].role == "developer"
    assert p.agents[0].declared_local_tools == ["fs:read", "fs:write"]
    assert p.agents[0].max_tool_iterations == 6
    assert p.agents[1].is_reviewer is True
    assert p.rbac["developer"].allowed_scopes == ["fs:write"]


def test_serialise_project_handles_missing_keys():
    p = serialise_project({})
    assert p.project_id == ""
    assert p.agents == []
    assert p.rbac == {}


# ── JobRunner end-to-end ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _register_code_reviewer():
    """E2E run uses CodeReviewer; ensure it's registered."""
    if not _registry.has("code_reviewer"):
        _registry.register("code_reviewer", CodeReviewer)


async def test_job_runner_runs_one_job_to_completion(tmp_path: Path, monkeypatch):
    """The smoking exit criterion: enqueue a job, run_one() drives it
    through queued → running → complete; meta.json + board.json land
    on disk."""
    monkeypatch.setenv("AGENTFLOW_MODE", "dev")

    # Project: developer writes a file, reviewer approves.
    project = {
        "project_id": "p1",
        "goal": "write hello.py",
        "agents": [
            {
                "role": "developer",
                "description": "write a file",
                "declared_local_tools": ["fs:read", "fs:write"],
                "model_tier": "solid",
            },
            {
                "role": "reviewer",
                "depends_on": ["developer"],
                "declared_local_tools": ["fs:read", "git:diff"],
                "agent_class_role_type": "code_reviewer",
                "is_reviewer": True,
                "max_tool_iterations": 4,
            },
        ],
        "rbac": {
            "developer": {"allowed_scopes": ["fs:write"]},
            "reviewer": {"allowed_scopes": []},
        },
        "max_iterations": 2,
    }

    queue = InMemoryJobQueue()
    job = Job(job_id="j1", project_id="p1", run_id="run_e2e_worker")
    await queue.enqueue(job)

    async def loader(project_id: str):
        return project if project_id == "p1" else None

    # Scripted LLM. Developer writes a file then ends. Reviewer diffs
    # then approves.
    llm = MockLLMClient([
        MockTurn(tool_calls=[ToolCall(
            id="dev_t1", name="fs:write",
            arguments={"path": "hello.py", "content": "print('hi')"},
        )]),
        MockTurn(text="Wrote hello.py"),
        MockTurn(tool_calls=[ToolCall(
            id="rev_t1", name="git:diff",
            arguments={"branch_a": "agent/developer", "branch_b": "main"},
        )]),
        MockTurn(text="APPROVED"),
    ])

    worker = JobRunner(
        queue=queue,
        project_loader=loader,
        data_root=tmp_path,
        llm=llm,
        docker_manager=DockerManager(mode="dev"),
    )

    await worker.run_one(job)

    # Job status moved to complete.
    out = await queue.get("j1")
    assert out is not None
    assert out.status == JOB_COMPLETE
    assert out.started_at is not None
    assert out.finished_at is not None
    assert out.error is None

    # Persisted artifacts.
    run_dir = tmp_path / "runs" / "run_e2e_worker"
    assert (run_dir / "meta.json").exists()
    assert (run_dir / "board.json").exists()

    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["run_id"] == "run_e2e_worker"
    assert meta["project_id"] == "p1"
    assert "developer" in meta["completed_agents"]
    assert "reviewer" in meta["completed_agents"]

    board = json.loads((run_dir / "board.json").read_text(encoding="utf-8"))
    assert any(s["type"] == "APPROVED" for s in board)
    assert any(s["type"] == "GIT_COMMIT" for s in board)


async def test_job_runner_unknown_project_fails_job(tmp_path: Path):
    queue = InMemoryJobQueue()
    job = Job(job_id="j2", project_id="ghost", run_id="run_x")
    await queue.enqueue(job)

    async def loader(_: str):
        return None  # not found

    worker = JobRunner(
        queue=queue, project_loader=loader,
        data_root=tmp_path, llm=MockLLMClient([]),
    )
    await worker.run_one(job)

    out = await queue.get("j2")
    assert out.status == JOB_FAILED
    assert "not found" in (out.error or "")


async def test_job_runner_loader_exception_fails_job(tmp_path: Path):
    queue = InMemoryJobQueue()
    job = Job(job_id="j3", project_id="p1", run_id="run_x")
    await queue.enqueue(job)

    async def loader(_: str):
        raise RuntimeError("storage down")

    worker = JobRunner(
        queue=queue, project_loader=loader,
        data_root=tmp_path, llm=MockLLMClient([]),
    )
    await worker.run_one(job)

    out = await queue.get("j3")
    assert out.status == JOB_FAILED
    assert "storage down" in (out.error or "")


async def test_job_runner_registers_with_run_registry(tmp_path: Path, monkeypatch):
    """When a RunRegistry is provided, the worker registers the workspace
    while the run is active and unregisters at the end."""
    monkeypatch.setenv("AGENTFLOW_MODE", "dev")
    from api.websocket import RunRegistry

    project = {
        "project_id": "p1", "goal": "x",
        "agents": [{
            "role": "developer", "description": "no-op",
            "declared_local_tools": ["fs:read"],
        }],
        "rbac": {},
        "max_iterations": 1,
    }
    queue = InMemoryJobQueue()
    job = Job(job_id="j4", project_id="p1", run_id="run_reg")
    await queue.enqueue(job)

    async def loader(_: str):
        return project

    llm = MockLLMClient([MockTurn(text="ok done")])

    registry = RunRegistry()
    worker = JobRunner(
        queue=queue, project_loader=loader,
        data_root=tmp_path, llm=llm,
        run_registry=registry,
    )

    await worker.run_one(job)

    # By the time run_one returns, the workspace is unregistered.
    assert "run_reg" not in registry.runs


async def test_run_forever_drains_then_stops(tmp_path: Path, monkeypatch):
    """The long loop processes a job and respects stop()."""
    import asyncio
    monkeypatch.setenv("AGENTFLOW_MODE", "dev")

    project = {
        "project_id": "p1", "goal": "x",
        "agents": [{
            "role": "developer", "description": "dev",
            "declared_local_tools": ["fs:read"],
        }],
        "rbac": {},
        "max_iterations": 1,
    }
    queue = InMemoryJobQueue()
    await queue.enqueue(Job(job_id="j5", project_id="p1", run_id="run_loop"))

    async def loader(_: str):
        return project

    llm = MockLLMClient([MockTurn(text="done")])
    worker = JobRunner(
        queue=queue, project_loader=loader,
        data_root=tmp_path, llm=llm,
        poll_timeout_s=0.05,
    )

    task = asyncio.create_task(worker.run_forever())
    # Give it time to drain.
    for _ in range(50):
        await asyncio.sleep(0.05)
        out = await queue.get("j5")
        if out and out.status == JOB_COMPLETE:
            break
    assert out.status == JOB_COMPLETE
    worker.stop()
    await asyncio.wait_for(task, timeout=2.0)
