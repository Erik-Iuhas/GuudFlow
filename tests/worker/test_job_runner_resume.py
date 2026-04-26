"""End-to-end resume test (Phase G-prime.3).

This is the acceptance test for the whole G-prime phase: a run fails
mid-way (one agent raises), the user calls the resume endpoint, and
the second pass picks up from the persisted state — completed agents
do NOT re-run, the failed agent retries cleanly.

Together with G-prime.0/1/2's unit tests, this proves the four
chunks compose correctly.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.builtin.code_reviewer import CodeReviewer
from agents.builtin.memory_writer import MemoryWriter
from agents.persona import Persona
from agents.registry import registry
from core.llm import MockLLMClient, MockTurn, ToolCall
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.scheduler import AgentSpec, Scheduler, SchedulerStatus
from core.signals import SignalType
from core.tool_manifest import RoleRBAC
from worker.job_runner import JobRunner, serialise_project
from worker.queue import JOB_COMPLETE, Job


def _persona(tmp_path: Path, persona_id: str) -> tuple[Persona, Path]:
    persona_dir = tmp_path / "personas" / persona_id
    persona_dir.mkdir(parents=True, exist_ok=True)
    (persona_dir / "profile.json").write_text(
        json.dumps({
            "persona_id": persona_id,
            "name": persona_id,
            "specialty": "test",
            "character": "x",
            "stake_framing": "x",
        }),
        encoding="utf-8",
    )
    persona = Persona.load(persona_id, tmp_path / "personas")
    persona._dir = persona_dir
    return persona, persona_dir


async def test_resume_does_not_rerun_completed_agents(tmp_path: Path):
    """Acceptance test for Phase G-prime: simulate a crashed run by
    posting signals to disk via the workspace persister, then resume
    via the worker and verify only the un-completed agent re-runs."""
    if not registry.has("code_reviewer"):
        registry.register("code_reviewer", CodeReviewer)

    # ── Phase 1: simulate a partial run ────────────────────────────────
    # The "crashed" run has agent A complete, agent B never started.
    # We synthesise this by running the runner once, tearing down before
    # B's turn.

    project_data = {
        "project_id": "proj_resume",
        "goal": "Add hello.py",
        "agents": [
            {
                "role": "developer",
                "system_prompt": "Write hello.py via fs:write.",
                "declared_local_tools": ["fs:read", "fs:list", "fs:write"],
                "model_tier": "solid",
            },
            {
                "role": "reviewer",
                "depends_on": ["developer"],
                "declared_local_tools": ["fs:read", "fs:list",
                                          "git:diff", "git:log"],
                "model_tier": "solid",
                "agent_class_role_type": "code_reviewer",
                "is_reviewer": True,
                "max_tool_iterations": 5,
            },
        ],
        "rbac": {
            "developer": {"allowed_scopes": ["fs:write"]},
            "reviewer": {"allowed_scopes": []},
        },
        "max_iterations": 3,
    }

    run_id = "run_resume_e2e"
    data_root = tmp_path
    run_root = data_root / "runs" / run_id

    # First pass: developer succeeds, then we abort before reviewer fires.
    # Easiest way: run scheduler with only the developer spec.
    project = serialise_project(project_data)
    dev_persona, dev_dir = _persona(tmp_path, "dev-v1")

    runner_phase1 = AgentRunner(
        project=project,
        run_id=run_id,
        run_root=run_root,
        llm=MockLLMClient([
            MockTurn(tool_calls=[ToolCall(
                id="t1", name="fs:write",
                arguments={"path": "hello.py",
                           "content": "def greet():\n    return 'hi'\n"},
            )]),
            MockTurn(text="Wrote hello.py"),
        ]),
        memory_writer=MemoryWriter(),
    )
    runner_phase1._build_agents()
    runner_phase1.agents["developer"].persona = dev_persona

    dev_cfg = next(c for c in project.agents if c.role == "developer")
    dev_spec = AgentSpec(
        role="developer",
        runner=runner_phase1._make_scheduler_runner(
            agent=runner_phase1.agents["developer"], cfg=dev_cfg,
        ),
        depends_on=[],
    )
    sched = Scheduler([dev_spec], runner_phase1.workspace, scope="pipeline")
    result = await sched.run()
    assert result.status is SchedulerStatus.COMPLETE
    assert "developer" in runner_phase1.workspace.completed_agents

    # Phase 1 ended without _persist_run being called (simulates crash).
    # board.jsonl + artifacts.json are on disk via the persisters, but
    # board.json is NOT.
    assert (run_root / "board.jsonl").is_file()
    assert (run_root / "artifacts.json").is_file()
    assert not (run_root / "board.json").exists()

    # The persister captured developer's progress.
    persisted = (run_root / "board.jsonl").read_text(encoding="utf-8")
    assert "STARTED" in persisted
    assert "COMPLETE" in persisted

    # ── Phase 2: write meta.json so the runs API "knows" about this run
    (run_root / "meta.json").write_text(json.dumps({
        "run_id": run_id,
        "project_id": "proj_resume",
        "goal": "Add hello.py",
        "status": "failed",
    }), encoding="utf-8")

    # ── Phase 3: resume via the worker ─────────────────────────────────
    # The worker dequeues a resume job, restores state, drives the
    # remaining work (just the reviewer this time).

    from worker.queue import InMemoryJobQueue
    queue = InMemoryJobQueue()

    async def loader(project_id: str):
        if project_id == "proj_resume":
            return project_data
        return None

    # The phase-2 LLM only needs the reviewer's turns — the developer
    # is already complete and won't be invoked. Empty queue would
    # error if the developer were retried.
    phase2_llm = MockLLMClient([
        MockTurn(tool_calls=[ToolCall(
            id="t1", name="git:diff",
            arguments={"branch_a": "agent/developer", "branch_b": "main"},
        )]),
        MockTurn(text="APPROVED"),
    ])

    from worker.docker_manager import DockerManager
    worker = JobRunner(
        queue=queue,
        project_loader=loader,
        data_root=data_root,
        llm=phase2_llm,
        docker_manager=DockerManager(mode="dev"),
    )

    job = Job(
        job_id="job_resume_1",
        project_id="proj_resume",
        run_id=run_id,
        payload={"resume": True},
    )
    await queue.enqueue(job)
    dequeued = await queue.dequeue(timeout=1.0)
    assert dequeued is not None
    await worker.run_one(dequeued)

    # ── Assertions ─────────────────────────────────────────────────────
    final_status = (await queue.get(job.job_id)).status
    assert final_status == JOB_COMPLETE

    # The reviewer ran exactly once — its 2 LLM turns consumed.
    # Critically the developer did NOT re-run; if it had, the LLM queue
    # would have run out (only 2 turns queued) and raised, which would
    # have flipped final_status to JOB_FAILED.

    # The consolidated board.json now exists (end-of-run persist).
    assert (run_root / "board.json").is_file()
    final_board = json.loads((run_root / "board.json").read_text(encoding="utf-8"))
    types = [s["type"] for s in final_board]
    # Developer's STARTED + COMPLETE from phase 1 are still on the board.
    assert types.count("STARTED") >= 1
    # Reviewer's APPROVED from phase 2 is there.
    assert "APPROVED" in types
