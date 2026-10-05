"""Tests for worker/job_runner.py — end-to-end job processing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from collections.abc import Iterator

from core.harness.config_gen import ProviderBinding
from core.harness.run import HarnessSettings
from core.llm import MockLLMClient
from core.llm.registry import ModelInfo
from tests.harness.conftest import requires_opencode
from tests.harness.fake_llm import FakeLLM, Text, ToolCall
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
                "declared_local_tools": ["fs-read", "fs-write"],
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
            "developer": {"allowed_scopes": ["fs-write"]},
            "reviewer": {"allowed_scopes": []},
        },
    }
    p = serialise_project(data)
    assert len(p.agents) == 2
    assert p.agents[0].role == "developer"
    assert p.agents[0].declared_local_tools == ["fs-read", "fs-write"]
    assert p.agents[0].max_tool_iterations == 6
    assert p.agents[1].is_reviewer is True
    assert p.rbac["developer"].allowed_scopes == ["fs-write"]


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


def test_serialise_project_carries_is_final_reviewer():
    """Phase C.4: is_final_reviewer round-trips through the JSON layer."""
    p = serialise_project({
        "project_id": "p", "goal": "x",
        "agents": [
            {"role": "dev"},
            {
                "role": "architect",
                "agent_class_role_type": "architect",
                "is_final_reviewer": True,
                "is_reviewer": True,
            },
        ],
    })
    by_role = {a.role: a for a in p.agents}
    assert by_role["dev"].is_final_reviewer is False
    assert by_role["architect"].is_final_reviewer is True
    assert by_role["architect"].is_reviewer is True


# ── JobRunner end-to-end ───────────────────────────────────────────────────


FAKE_MODEL = ModelInfo(id="fake-model", provider="fake", tier="solid", supports_images=True)


@pytest.fixture
def fake_llm() -> Iterator[FakeLLM]:
    """A started scripted LLM that opencode talks to through the harness gateway."""
    with FakeLLM() as fake:
        yield fake


def _factory(fake: FakeLLM):
    """A runner factory that builds the default runner and points it at the fake LLM."""
    def make(project, run_id, run_root, llm, **kwargs):
        runner = default_runner_factory(project, run_id, run_root, llm, **kwargs)
        runner.harness_settings = HarnessSettings(
            production=False, turn_timeout=60, sandbox="host",
            binding_factory=lambda cfg: ProviderBinding(
                "fake", FAKE_MODEL, "openai_compatible", base_url=fake.base_url, api_key="fake"))
        return runner
    return make


def _noop_dev(fake: FakeLLM) -> None:
    """The lone `developer` role finishes at once."""
    fake.route("Your turn: developer",
               ToolCall("guudflow_submit_result",
                        {"status": "done", "summary": "Nothing to change; the task is already met.",
                         "files_changed": []}),
               Text("Done."))


@requires_opencode
async def test_job_runner_runs_one_job_to_completion(tmp_path: Path, monkeypatch,
                                                     fake_llm: FakeLLM):
    """The smoking exit criterion: enqueue a job, run_one() drives it
    through queued -> running -> complete; meta.json + board.json land
    on disk."""
    monkeypatch.setenv("GUUDFLOW_MODE", "dev")

    # Project: developer writes a file, reviewer passes it.
    project = {
        "project_id": "p1",
        "goal": "write hello.py",
        "agents": [
            {"role": "developer", "description": "write a file", "model_tier": "solid"},
            {"role": "reviewer", "depends_on": ["developer"],
             "agent_class_role_type": "code_reviewer", "is_reviewer": True},
        ],
        "rbac": {},
        "max_iterations": 2,
    }

    queue = InMemoryJobQueue()
    job = Job(job_id="j1", project_id="p1", run_id="run_e2e_worker")
    await queue.enqueue(job)

    async def loader(project_id: str):
        return project if project_id == "p1" else None

    fake_llm.route("Your turn: developer",
                   ToolCall("write", {"filePath": "hello.py", "content": "print('hi')\n"}),
                   ToolCall("guudflow_submit_result",
                            {"status": "done", "summary": "Wrote hello.py printing a greeting.",
                             "files_changed": ["hello.py"]}),
                   Text("Done."))
    fake_llm.route("Your turn: reviewer",
                   ToolCall("read", {"filePath": "hello.py"}),
                   ToolCall("guudflow_submit_result", {"verdict": "pass", "issues": []}),
                   Text("Reviewed."))

    worker = JobRunner(
        queue=queue,
        project_loader=loader,
        data_root=tmp_path,
        llm=MockLLMClient([]),
        docker_manager=DockerManager(mode="dev"),
        runner_factory=_factory(fake_llm),
    )

    await worker.run_one(job)

    # Job status moved to complete.
    out = await queue.get("j1")
    assert out is not None
    assert out.status == JOB_COMPLETE, out.error
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
    assert any(s["type"] == "AGENT_RESULT" for s in board)
    assert any(s["type"] == "GIT_MERGE" for s in board)
    assert (run_dir / "workspace" / "working" / "hello.py").exists()


async def test_job_runner_fails_a_legacy_harness_project(tmp_path: Path):
    """A stored project that still asks for the retired legacy harness fails fast."""
    project = {"project_id": "p1", "goal": "x", "harness": "legacy",
               "agents": [{"role": "developer", "description": "dev"}], "rbac": {}}
    queue = InMemoryJobQueue()
    job = Job(job_id="jl", project_id="p1", run_id="run_legacy")
    await queue.enqueue(job)

    async def loader(_: str):
        return project

    worker = JobRunner(queue=queue, project_loader=loader, data_root=tmp_path,
                       llm=MockLLMClient([]))
    await worker.run_one(job)

    out = await queue.get("jl")
    assert out.status == JOB_FAILED
    assert "legacy harness was retired" in (out.error or "")


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


@requires_opencode
async def test_job_runner_registers_with_run_registry(tmp_path: Path, monkeypatch,
                                                      fake_llm: FakeLLM):
    """When a RunRegistry is provided, the worker registers the workspace
    while the run is active and unregisters at the end."""
    monkeypatch.setenv("GUUDFLOW_MODE", "dev")
    from api.websocket import RunRegistry

    project = {
        "project_id": "p1", "goal": "x",
        "agents": [{"role": "developer", "description": "no-op"}],
        "rbac": {},
        "max_iterations": 1,
    }
    _noop_dev(fake_llm)
    queue = InMemoryJobQueue()
    job = Job(job_id="j4", project_id="p1", run_id="run_reg")
    await queue.enqueue(job)

    async def loader(_: str):
        return project

    registry = RunRegistry()
    worker = JobRunner(
        queue=queue, project_loader=loader,
        data_root=tmp_path, llm=MockLLMClient([]),
        run_registry=registry, runner_factory=_factory(fake_llm),
    )

    await worker.run_one(job)

    # By the time run_one returns, the workspace is unregistered.
    assert "run_reg" not in registry.runs


@requires_opencode
async def test_run_forever_drains_then_stops(tmp_path: Path, monkeypatch, fake_llm: FakeLLM):
    """The long loop processes a job and respects stop()."""
    import asyncio
    monkeypatch.setenv("GUUDFLOW_MODE", "dev")

    project = {
        "project_id": "p1", "goal": "x",
        "agents": [{"role": "developer", "description": "dev"}],
        "rbac": {},
        "max_iterations": 1,
    }
    _noop_dev(fake_llm)
    queue = InMemoryJobQueue()
    await queue.enqueue(Job(job_id="j5", project_id="p1", run_id="run_loop"))

    async def loader(_: str):
        return project

    worker = JobRunner(
        queue=queue, project_loader=loader,
        data_root=tmp_path, llm=MockLLMClient([]),
        poll_timeout_s=0.05, runner_factory=_factory(fake_llm),
    )

    task = asyncio.create_task(worker.run_forever())
    # Give it time to drain.
    for _ in range(600):
        await asyncio.sleep(0.1)
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


@requires_opencode
async def test_run_one_initializes_inputs_for_real_run(tmp_path: Path, monkeypatch,
                                                    fake_llm: FakeLLM):
    """End-to-end: a project with uploads + source_repo runs through the
    worker; the run's workspace shows the seeded inputs and a working/
    with the source_repo content + a fresh git history."""
    monkeypatch.setenv("GUUDFLOW_MODE", "dev")
    _stage_project_inputs(tmp_path, "p_seeded")

    project = {
        "project_id": "p_seeded",
        "goal": "extend the existing app",
        "agents": [{"role": "developer", "description": "no-op"}],
        "rbac": {},
        "max_iterations": 1,
    }
    _noop_dev(fake_llm)
    queue = InMemoryJobQueue()
    job = Job(job_id="j_init", project_id="p_seeded", run_id="run_init_e2e")
    await queue.enqueue(job)

    async def loader(_: str):
        return project

    worker = JobRunner(
        queue=queue, project_loader=loader,
        data_root=tmp_path, llm=MockLLMClient([]),
        docker_manager=DockerManager(mode="dev"),
        runner_factory=_factory(fake_llm),
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
