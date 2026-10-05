"""End-to-end resume test (Phase G-prime.3), on the opencode harness.

This is the acceptance test for the whole G-prime phase: a run stops
mid-way, the user calls the resume endpoint, and the second pass picks up
from the persisted state. Completed agents do NOT re-run; the rest do.

Together with G-prime.0/1/2's unit tests, this proves the four
chunks compose correctly.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.harness.config_gen import ProviderBinding
from core.harness.run import HarnessSettings
from core.llm import MockLLMClient
from core.llm.registry import ModelInfo
from core.runner import AgentRunner
from core.scheduler import SchedulerStatus
from tests.harness.conftest import requires_opencode
from tests.harness.fake_llm import FakeLLM, Text, ToolCall
from worker.job_runner import JobRunner, default_runner_factory, serialise_project
from worker.queue import JOB_COMPLETE, Job

FAKE_MODEL = ModelInfo(id="fake-model", provider="fake", tier="solid", supports_images=True)


def _settings(fake: FakeLLM) -> HarnessSettings:
    return HarnessSettings(
        production=False, turn_timeout=60, sandbox="host",
        binding_factory=lambda cfg: ProviderBinding(
            "fake", FAKE_MODEL, "openai_compatible", base_url=fake.base_url, api_key="fake"))


@requires_opencode
async def test_resume_does_not_rerun_completed_agents(tmp_path: Path):
    """Acceptance test for Phase G-prime: a first pass completes the developer and
    leaves its board and artifacts on disk (no board.json: the worker never got to
    persist); resuming via the worker runs only the reviewer."""
    project_data = {
        "project_id": "proj_resume",
        "goal": "Add hello.py",
        "agents": [
            {"role": "developer", "system_prompt": "Write hello.py.", "model_tier": "solid"},
            {"role": "reviewer", "depends_on": ["developer"], "model_tier": "solid",
             "agent_class_role_type": "code_reviewer", "is_reviewer": True},
        ],
        "rbac": {},
        "max_iterations": 3,
    }
    run_id = "run_resume_e2e"
    data_root = tmp_path
    run_root = data_root / "runs" / run_id

    # ── Phase 1: a first pass in which only the developer is on the team ─────
    phase1 = serialise_project(project_data)
    phase1.agents = [c for c in phase1.agents if c.role == "developer"]
    with FakeLLM() as fake1:
        fake1.route("Your turn: developer",
                    ToolCall("write", {"filePath": "hello.py",
                                       "content": "def greet():\n    return 'hi'\n"}),
                    ToolCall("guudflow_submit_result",
                             {"status": "done", "summary": "Wrote hello.py with greet().",
                              "files_changed": ["hello.py"]}),
                    Text("Done."))
        runner_phase1 = AgentRunner(project=phase1, run_id=run_id, run_root=run_root,
                                    max_wall_seconds=300)
        runner_phase1.harness_settings = _settings(fake1)
        result = await runner_phase1.run()
    assert result.status is SchedulerStatus.COMPLETE
    assert "developer" in runner_phase1.workspace.completed_agents

    # The run ended without the worker's end-of-run persist (simulates a crash):
    # board.jsonl + artifacts.json are on disk via the persisters, board.json is not.
    assert (run_root / "board.jsonl").is_file()
    assert (run_root / "artifacts.json").is_file()
    assert not (run_root / "board.json").exists()
    persisted = (run_root / "board.jsonl").read_text(encoding="utf-8")
    assert "STARTED" in persisted
    assert "COMPLETE" in persisted

    # ── Phase 2: write meta.json so the runs API "knows" about this run ──────
    (run_root / "meta.json").write_text(json.dumps({
        "run_id": run_id, "project_id": "proj_resume", "goal": "Add hello.py",
        "status": "failed",
    }), encoding="utf-8")

    # ── Phase 3: resume via the worker ───────────────────────────────────────
    from worker.queue import InMemoryJobQueue
    queue = InMemoryJobQueue()

    async def loader(project_id: str):
        return project_data if project_id == "proj_resume" else None

    with FakeLLM() as fake2:
        # Only the reviewer is scripted: were the developer spawned again it would
        # get the exhausted-script fallback, submit no result and fail the job.
        fake2.route("Your turn: reviewer",
                    ToolCall("read", {"filePath": "hello.py"}),
                    ToolCall("guudflow_submit_result", {"verdict": "pass", "issues": []}),
                    Text("Reviewed."))

        def factory(project, rid, root, llm, **kwargs):
            runner = default_runner_factory(project, rid, root, llm, **kwargs)
            runner.harness_settings = _settings(fake2)
            return runner

        worker = JobRunner(queue=queue, project_loader=loader, data_root=data_root,
                           llm=MockLLMClient([]), runner_factory=factory)
        job = Job(job_id="job_resume_1", project_id="proj_resume", run_id=run_id,
                  payload={"resume": True})
        await queue.enqueue(job)
        dequeued = await queue.dequeue(timeout=1.0)
        assert dequeued is not None
        await worker.run_one(dequeued)
        developer_spawns = [r for r in fake2.requests if "Your turn: developer" in str(r["messages"])]

    # ── Assertions ───────────────────────────────────────────────────────────
    done = await queue.get(job.job_id)
    assert done.status == JOB_COMPLETE, done.error
    assert not developer_spawns, "the completed developer must not run again"

    # The consolidated board.json now exists (end-of-run persist).
    assert (run_root / "board.json").is_file()
    final_board = json.loads((run_root / "board.json").read_text(encoding="utf-8"))
    types = [s["type"] for s in final_board]
    # The developer's STARTED and COMPLETE from phase 1 are still on the board,
    # and the reviewer's own result from phase 2 is there too.
    assert types.count("STARTED") >= 2
    assert types.count("AGENT_RESULT") >= 2
