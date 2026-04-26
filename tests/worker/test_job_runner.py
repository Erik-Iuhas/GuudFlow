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


# ── approval_gates round-trip (Phase C.0.5) ────────────────────────────────


def test_serialise_project_baselines_approval_gates_when_missing():
    """Projects without `approval_gates` get the opinionated baseline."""
    p = serialise_project({"project_id": "p", "goal": "x"})
    assert p.approval_gates["pre_run_cost_preview"] is True
    assert p.approval_gates["heavy_tier_mid_run_dispatch"] is True
    assert p.approval_gates["iteration_breach_at"] == 2


def test_serialise_project_preserves_explicit_approval_gates():
    """User-set values pass through; baseline defaults fill any gaps."""
    p = serialise_project({
        "project_id": "p",
        "goal": "x",
        "approval_gates": {
            "pre_run_cost_preview": False,
            "token_budget_usd": 5.00,
            # heavy_tier_mid_run_dispatch + iteration_breach_at omitted
        },
    })
    assert p.approval_gates["pre_run_cost_preview"] is False
    assert p.approval_gates["token_budget_usd"] == 5.00
    # Defaults still fill missing keys.
    assert p.approval_gates["heavy_tier_mid_run_dispatch"] is True
    assert p.approval_gates["iteration_breach_at"] == 2


def test_serialise_project_handles_non_dict_approval_gates():
    """Defensive: a malformed approval_gates value falls back to baseline."""
    p = serialise_project({
        "project_id": "p", "goal": "x",
        "approval_gates": "not a dict",
    })
    assert isinstance(p.approval_gates, dict)
    assert p.approval_gates["pre_run_cost_preview"] is True


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


# ── Run initialisation (DESIGN §33, D-prime Chunk 5) ───────────────────────


def _stage_project_inputs(data_root: Path, project_id: str) -> Path:
    """Materialise data/projects/{id}/inputs/ with uploads + source_repo
    + a from_run sidecar so the initializer has work to do."""
    project_dir = data_root / "projects" / project_id
    inputs = project_dir / "inputs"
    (inputs / "uploads").mkdir(parents=True)
    (inputs / "uploads" / "spec.txt").write_text("the spec\n", encoding="utf-8")

    (inputs / "source_repo").mkdir()
    (inputs / "source_repo" / "main.py").write_text("print('seed')\n", encoding="utf-8")
    (inputs / "source_repo.meta.json").write_text(
        json.dumps({"kind": "git_url", "git_url": "https://example.com/r.git", "branch": "main"}),
        encoding="utf-8",
    )

    (inputs / "from_run_prior").mkdir()
    (inputs / "from_run_prior" / "deliverables").mkdir()
    (inputs / "from_run_prior" / "deliverables" / "out.txt").write_text("done\n", encoding="utf-8")
    (inputs / "from_run_prior.meta.json").write_text(
        json.dumps({"kind": "prior_run", "run_id": "prior", "has_feedback": False}),
        encoding="utf-8",
    )
    return project_dir


def _build_initializer_worker(tmp_path: Path) -> JobRunner:
    """A JobRunner just well-enough constructed to call _initialize_run."""
    return JobRunner(
        queue=InMemoryJobQueue(),
        project_loader=lambda _: None,
        data_root=tmp_path,
    )


def test_initialize_run_seeds_uploads(tmp_path: Path):
    _stage_project_inputs(tmp_path, "p1")
    worker = _build_initializer_worker(tmp_path)
    run_root = tmp_path / "runs" / "run_init"
    run_root.mkdir(parents=True)
    job = Job(job_id="j", project_id="p1", run_id="run_init")

    worker._initialize_run(job, run_root)

    assert (run_root / "workspace" / "inputs" / "uploads" / "spec.txt").read_text(
        encoding="utf-8"
    ) == "the spec\n"


def test_initialize_run_seeds_working_with_git(tmp_path: Path):
    _stage_project_inputs(tmp_path, "p1")
    worker = _build_initializer_worker(tmp_path)
    run_root = tmp_path / "runs" / "run_init"
    run_root.mkdir(parents=True)
    job = Job(job_id="j", project_id="p1", run_id="run_init")

    worker._initialize_run(job, run_root)

    working = run_root / "workspace" / "working"
    assert (working / "main.py").read_text(encoding="utf-8") == "print('seed')\n"
    assert (working / ".git").is_dir()


def test_initialize_run_writes_readme(tmp_path: Path):
    _stage_project_inputs(tmp_path, "p1")
    worker = _build_initializer_worker(tmp_path)
    run_root = tmp_path / "runs" / "run_init"
    run_root.mkdir(parents=True)
    job = Job(job_id="j", project_id="p1", run_id="run_init")

    worker._initialize_run(job, run_root)

    readme = (run_root / "workspace" / "inputs" / "README.md").read_text(encoding="utf-8")
    assert "uploads/spec.txt" in readme
    assert "source_repo/" in readme
    assert "from_run_prior/deliverables/" in readme


def test_initialize_run_bundles_prior_history(tmp_path: Path):
    _stage_project_inputs(tmp_path, "p1")
    # Stage a prior run with its own working/ git history on disk.
    prior_working = tmp_path / "runs" / "prior" / "workspace" / "working"
    prior_working.mkdir(parents=True)
    from git import Repo as _Repo
    repo = _Repo.init(prior_working, initial_branch="main")
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "test")
        cfg.set_value("user", "email", "test@example.com")
        cfg.set_value("commit", "gpgsign", "false")
    (prior_working / "x.txt").write_text("x", encoding="utf-8")
    repo.index.add(["x.txt"])
    repo.index.commit("init")

    worker = _build_initializer_worker(tmp_path)
    run_root = tmp_path / "runs" / "run_init"
    run_root.mkdir(parents=True)
    job = Job(job_id="j", project_id="p1", run_id="run_init")

    worker._initialize_run(job, run_root)

    bundle = run_root / "workspace" / "inputs" / "from_run_prior" / ".history.bundle"
    assert bundle.is_file()
    assert bundle.stat().st_size > 0


def test_initialize_run_greenfield_project(tmp_path: Path):
    """Project with no inputs dir at all — initialisation succeeds and
    creates an empty inputs/ + greenfield README."""
    worker = _build_initializer_worker(tmp_path)
    run_root = tmp_path / "runs" / "run_blank"
    run_root.mkdir(parents=True)
    job = Job(job_id="j", project_id="p_blank", run_id="run_blank")

    worker._initialize_run(job, run_root)

    inputs = run_root / "workspace" / "inputs"
    assert inputs.is_dir()
    readme = (inputs / "README.md").read_text(encoding="utf-8")
    assert "greenfield" in readme.lower()
    # No source_repo → no working/ either.
    assert not (run_root / "workspace" / "working").exists()


async def test_default_runner_factory_threads_docker_manager(tmp_path: Path):
    """Phase B wiring: default_runner_factory propagates the
    docker_manager kwarg into AgentRunner.docker_manager so the runner's
    tool instantiation can reach it."""
    project = serialise_project({"project_id": "p", "goal": "x"})
    docker_mgr = DockerManager(mode="dev")
    runner = default_runner_factory(
        project, "run_x", tmp_path / "runs" / "run_x",
        MockLLMClient([]),
        docker_manager=docker_mgr,
    )
    assert runner.docker_manager is docker_mgr


async def test_production_mode_wires_container_runner_for_code_exec(
    tmp_path: Path, monkeypatch
):
    """Phase B integration: with AGENTFLOW_MODE=production and a
    docker_manager threaded into AgentRunner, the runner's instantiated
    code:exec tool routes through DockerManager.exec_in. Tests the seam
    that connects JobRunner → AgentRunner._instantiate_local_tools →
    make_container_runner → DockerManager.

    Goes via the runner's tool-instantiation path directly rather than
    the full agent-loop e2e, to keep the assertion focused on the
    routing seam itself."""
    monkeypatch.setenv("AGENTFLOW_MODE", "production")

    project = serialise_project({
        "project_id": "p_prod",
        "goal": "exec something",
        "agents": [{
            "role": "developer",
            "description": "runs code",
            "declared_local_tools": ["code:exec"],
            "model_tier": "solid",
        }],
        "rbac": {"developer": {"allowed_scopes": ["code:exec"]}},
    })

    class _FakeClient:
        def ping(self): return True

    docker_mgr = DockerManager(mode="production", client=_FakeClient())
    captured: list[dict] = []

    async def spy_exec_in(run_id, language, code, timeout_s=30.0):
        captured.append({
            "run_id": run_id, "language": language,
            "code": code, "timeout_s": timeout_s,
        })
        from worker.docker_manager import ExecResult
        return ExecResult(exit_code=0, stdout="ok\n", stderr="")
    docker_mgr.exec_in = spy_exec_in  # type: ignore[method-assign]

    runner = AgentRunner(
        project=project,
        run_id="run_prod",
        run_root=tmp_path / "runs" / "run_prod",
        llm=MockLLMClient([]),
        docker_manager=docker_mgr,
    )

    tools = runner._instantiate_local_tools("developer")
    code_exec = tools["code:exec"]

    result = await code_exec.run({
        "language": "python",
        "code": "print('hello container')",
    })

    assert result["ok"] is True
    assert result["container_mode"] is True
    assert captured == [{
        "run_id": "run_prod",
        "language": "python",
        "code": "print('hello container')",
        "timeout_s": 30.0,
    }]


async def test_dev_mode_keeps_host_runner_even_with_docker_manager(
    tmp_path: Path, monkeypatch
):
    """Inverse seam: AGENTFLOW_MODE=dev keeps the hardened host runner
    even when a docker_manager is available. exec_in is never reached."""
    monkeypatch.setenv("AGENTFLOW_MODE", "dev")

    project = serialise_project({
        "project_id": "p_dev",
        "goal": "x",
        "agents": [{
            "role": "developer",
            "description": "runs code",
            "declared_local_tools": ["code:exec"],
            "model_tier": "solid",
        }],
        "rbac": {"developer": {"allowed_scopes": ["code:exec"]}},
    })
    docker_mgr = DockerManager(mode="dev")
    captured: list = []

    async def spy(*a, **kw):
        captured.append((a, kw))
        from worker.docker_manager import ExecResult
        return ExecResult(exit_code=0, stdout="", stderr="", is_stub=True)
    docker_mgr.exec_in = spy  # type: ignore[method-assign]

    runner = AgentRunner(
        project=project,
        run_id="run_dev",
        run_root=tmp_path / "runs" / "run_dev",
        llm=MockLLMClient([]),
        docker_manager=docker_mgr,
    )

    tools = runner._instantiate_local_tools("developer")
    result = await tools["code:exec"].run({
        "language": "python",
        "code": "print('hi')",
    })

    assert result["ok"] is True
    assert result.get("dev_mode") is True
    assert captured == []


async def test_production_without_docker_manager_falls_back_to_host(
    tmp_path: Path, monkeypatch
):
    """If somehow a runner ends up in production mode without a
    docker_manager (misconfiguration), it falls back to the host runner —
    which itself refuses to run via CodeExec.run()'s mode check
    (DESIGN §29 rule 61). Defence in depth: wrong wiring fails closed."""
    monkeypatch.setenv("AGENTFLOW_MODE", "production")

    project = serialise_project({
        "project_id": "p_misconfig",
        "goal": "x",
        "agents": [{
            "role": "developer",
            "description": "runs code",
            "declared_local_tools": ["code:exec"],
            "model_tier": "solid",
        }],
        "rbac": {"developer": {"allowed_scopes": ["code:exec"]}},
    })

    runner = AgentRunner(
        project=project,
        run_id="run_misconfig",
        run_root=tmp_path / "runs" / "run_misconfig",
        llm=MockLLMClient([]),
        docker_manager=None,  # the misconfiguration
    )

    tools = runner._instantiate_local_tools("developer")
    result = await tools["code:exec"].run({
        "language": "python",
        "code": "print('this should be refused')",
    })

    # Host runner refuses production-mode execution.
    assert "error" in result
    assert "production" in result["error"].lower()


async def test_run_one_initializes_inputs_for_real_run(tmp_path: Path, monkeypatch):
    """End-to-end: a project with uploads + source_repo runs through the
    worker; the run's workspace shows the seeded inputs and a working/
    with the source_repo content + a fresh git history."""
    monkeypatch.setenv("AGENTFLOW_MODE", "dev")
    _stage_project_inputs(tmp_path, "p_seeded")

    project = {
        "project_id": "p_seeded",
        "goal": "extend the existing app",
        "agents": [{
            "role": "developer", "description": "no-op",
            "declared_local_tools": ["fs:read"],
        }],
        "rbac": {},
        "max_iterations": 1,
    }
    queue = InMemoryJobQueue()
    job = Job(job_id="j_init", project_id="p_seeded", run_id="run_init_e2e")
    await queue.enqueue(job)

    async def loader(_: str):
        return project
    llm = MockLLMClient([MockTurn(text="ok done")])

    worker = JobRunner(
        queue=queue, project_loader=loader,
        data_root=tmp_path, llm=llm,
        docker_manager=DockerManager(mode="dev"),
    )
    await worker.run_one(job)

    out = await queue.get("j_init")
    assert out.status == JOB_COMPLETE

    run_root = tmp_path / "runs" / "run_init_e2e"
    inputs = run_root / "workspace" / "inputs"
    assert (inputs / "uploads" / "spec.txt").exists()
    assert (inputs / "source_repo" / "main.py").exists()
    assert (inputs / "README.md").exists()
    # working/ was seeded from source_repo
    assert (run_root / "workspace" / "working" / "main.py").exists()
