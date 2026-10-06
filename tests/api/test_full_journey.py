"""Project-wide end-to-end smoke test.

Drives the **entire** user-facing flow via the public HTTP API:

    POST /api/meta/propose
        ↓
    POST /api/projects                (with persona_id assigned)
        ↓
    POST /api/projects/{id}/inputs/upload
        ↓
    POST /api/invoke/{id}             → 202 + run_id + job_id
        ↓
    worker.run_one(job)               (drains the queue with mocked LLM)
        ↓
    GET /api/invoke/{job_id}/status   → complete
    GET /api/runs/{run_id}            → status complete
    GET /api/runs/{run_id}/board      → expected signal types
    GET /api/personas/{persona_id}    → reputation updated

Plus filesystem assertions that prove the run initializer threaded the
project's uploads into the run's workspace and the developer agent
actually wrote a file the reviewer merged into main.

Why this test exists
--------------------
The other end-to-end tests (test_runner_e2e, test_job_runner) cover
runner / scheduler / agents thoroughly but bypass the HTTP layer. The
HTTP tests (test_invoke, test_d_prime_flow) cover the API surface but
stop before agents actually run. Nothing covers the seam from "user
clicks Run" all the way to "user reads the persona's updated
reputation" — drift in any join in that chain currently slips through.
This single test is that seam check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from collections.abc import Iterator

from core.harness.config_gen import ProviderBinding
from core.harness.run import HarnessSettings
from core.llm import MockLLMClient, MockTurn
from core.llm import ToolCall as MetaToolCall
from core.llm.registry import ModelInfo
from tests.harness.conftest import requires_opencode
from tests.harness.fake_llm import FakeLLM, Text, ToolCall
from worker.docker_manager import DockerManager
from worker.job_runner import JobRunner, default_runner_factory, serialise_project
from worker.queue import JOB_COMPLETE

FAKE_MODEL = ModelInfo(id="fake-model", provider="fake", tier="solid", supports_images=True)


@pytest.fixture
def fake_llm() -> Iterator[FakeLLM]:
    """A started scripted LLM that opencode talks to through the harness gateway."""
    with FakeLLM() as fake:
        yield fake


# Note: the runner loads personas from `cfg.persona_id` itself (personas_root),
# so this test uses the default JobRunner factory and only points the harness at
# the fake LLM.


# ── The journey ────────────────────────────────────────────────────────────


@requires_opencode
async def test_full_user_journey(
    client, auth_header, app, data_root: Path, write_persona, monkeypatch, fake_llm: FakeLLM
):
    """One linear test of the whole machine. Each step's assertions are
    inline so a failure points directly at the joint that broke."""
    PROJECT_ID = "p_journey"
    PERSONA_ID = "aria-dev-v1"

    # ── 0. Pre-stage a persona on disk ─────────────────────────────────
    write_persona(PERSONA_ID, name="Aria", specialty="developer")

    # ── 1. Meta-agent proposes a team ──────────────────────────────────
    # Post-fix (2026-04-26): /api/meta/propose always invokes the LLM,
    # so the journey test injects a mock that emits a developer +
    # reviewer team. Earlier the route returned a static skeleton when
    # no project_id was provided; that path is gone.
    app.state.meta_llm = MockLLMClient([MockTurn(tool_calls=[MetaToolCall(
        id="meta_journey",
        name="propose_team",
        arguments={
            "rationale": "Tiny module — developer + reviewer is enough.",
            "agents": [
                {
                    "role": "developer",
                    "archetype": "backend_dev",
                    "depends_on": [],
                    "persona": {"action": "create_new", "name_hint": "Aria"},
                },
                {
                    "role": "reviewer",
                    "archetype": "code_reviewer",
                    "depends_on": ["developer"],
                    "is_reviewer": True,
                    "persona": {"action": "create_new"},
                },
            ],
        },
    )])])

    r = await client.post(
        "/api/meta/propose",
        json={"goal": "Add a hello.py module"},
        headers=auth_header,
    )
    assert r.status_code == 200, r.text
    proposal = r.json()
    assert "rationale" in proposal
    proposal_roles = {a["role"] for a in proposal["agents"]}
    assert proposal_roles == {"developer", "reviewer"}

    # ── 2. User customises the proposal — hire a persona for developer
    #      — and creates the project ────────────────────────────────────
    agents = []
    for a in proposal["agents"]:
        if a["role"] == "developer":
            agents.append({**a, "persona_id": PERSONA_ID, "model_tier": "solid"})
        else:
            agents.append({**a, "max_tool_iterations": 5})

    r = await client.post(
        "/api/projects",
        json={
            "project_id": PROJECT_ID,
            "goal": "Add a hello.py module",
            "agents": agents,
            "rbac": proposal["rbac"],
            "max_iterations": 2,
            "pr_mode": "auto",
        },
        headers=auth_header,
    )
    assert r.status_code == 201, r.text
    # Side effect of create: the per-project inputs dir is materialised.
    assert (data_root / "projects" / PROJECT_ID / "inputs" / "uploads").is_dir()

    # ── 3. User uploads a spec to the project's inputs ─────────────────
    spec_body = b"# hello.py\nWrite a greet() function that returns 'hi'.\n"
    r = await client.post(
        f"/api/projects/{PROJECT_ID}/inputs/upload",
        files=[("files", ("spec.md", spec_body, "text/markdown"))],
        headers=auth_header,
    )
    assert r.status_code == 200, r.text

    # ── 4. User invokes a run ──────────────────────────────────────────
    monkeypatch.setenv("GUUDFLOW_MODE", "dev")
    # Phase C.0.5: pre_run_cost_preview is on by default — pass
    # confirm=true to skip the cost-preview leg in this e2e test.
    r = await client.post(
        f"/api/invoke/{PROJECT_ID}",
        json={"confirm": True},
        headers=auth_header,
    )
    assert r.status_code == 202, r.text
    invoke_body = r.json()
    run_id = invoke_body["run_id"]
    job_id = invoke_body["job_id"]

    # The job is queued.
    r = await client.get(f"/api/invoke/{job_id}/status", headers=auth_header)
    assert r.status_code == 200
    assert r.json()["status"] == "queued"

    # ── 5. Worker drains the queue ─────────────────────────────────────
    # Scripted LLM through real opencode: the developer writes hello.py and
    # submits its result; the reviewer reads it and passes.
    fake_llm.route("Your turn: developer",
                   ToolCall("write", {"filePath": "hello.py",
                                      "content": "def greet():\n    return 'hi'\n"}),
                   ToolCall("guudflow_submit_result",
                            {"status": "done", "summary": "Wrote greet() in hello.py.",
                             "files_changed": ["hello.py"]}),
                   Text("Done."))
    fake_llm.route("Your turn: reviewer",
                   ToolCall("read", {"filePath": "hello.py"}),
                   ToolCall("guudflow_submit_result", {"verdict": "pass", "issues": []}),
                   Text("Reviewed."))

    async def loader(project_id: str):
        return await app.state.project_store.get(project_id)

    def runner_factory(project, rid, root, llm, **kwargs):
        runner = default_runner_factory(project, rid, root, llm, **kwargs)
        runner.harness_settings = HarnessSettings(
            production=False, turn_timeout=60, sandbox="host",
            binding_factory=lambda cfg: ProviderBinding(
                "fake", FAKE_MODEL, "openai_compatible", base_url=fake_llm.base_url,
                api_key="fake"))
        return runner

    worker = JobRunner(
        queue=app.state.queue,
        project_loader=loader,
        data_root=data_root,
        llm=MockLLMClient([]),
        docker_manager=DockerManager(mode="dev"),
        run_registry=app.state.run_registry,
        runner_factory=runner_factory,
    )

    job = await app.state.queue.get(job_id)
    assert job is not None
    await worker.run_one(job)

    # ── 6. Job + run report complete via the API ───────────────────────
    r = await client.get(f"/api/invoke/{job_id}/status", headers=auth_header)
    assert r.status_code == 200
    assert r.json()["status"] == JOB_COMPLETE

    r = await client.get(f"/api/runs/{run_id}")
    assert r.status_code == 200, r.text
    run_meta = r.json()
    assert run_meta["status"] == "complete"
    assert set(run_meta["completed_agents"]) == {"developer", "reviewer"}

    # ── 7. Board carries every expected signal type ────────────────────
    r = await client.get(f"/api/runs/{run_id}/board")
    assert r.status_code == 200, r.text
    board = r.json()
    types = {s["type"] for s in board}
    # STARTED — every role posts one when its turn begins.
    assert "STARTED" in types
    # AGENT_RESULT — each role's validated structured result.
    assert "AGENT_RESULT" in types
    # GIT_MERGE — the runner merged the developer's branch into main.
    assert "GIT_MERGE" in types
    assert "COMPLETE" in types

    # ── 8. Persona memory was updated end-to-end ───────────────────────
    # The memory_writer ran post-run because the runner loaded the developer's
    # persona from its persona_id. Reputation should reflect
    # one successful run; episodes.jsonl should have one entry.
    r = await client.get(f"/api/personas/{PERSONA_ID}")
    assert r.status_code == 200, r.text
    persona_summary = r.json()
    assert persona_summary["n_episodes"] == 1
    assert persona_summary["reputation"]["production"]["total_runs"] == 1

    # ── 9. Filesystem proves the data flowed through every layer ───────
    run_workspace = data_root / "runs" / run_id / "workspace"

    # 9a. Run initializer (D-prime Chunk 5) seeded the project's upload
    #     into the run's workspace/inputs/.
    assert (run_workspace / "inputs" / "uploads" / "spec.md").read_bytes() == spec_body
    assert (run_workspace / "inputs" / "README.md").is_file()

    # 9b. The developer wrote hello.py in its clone and the runner merged it → it's in working/.
    hello_py = run_workspace / "working" / "hello.py"
    assert hello_py.exists()
    assert "def greet" in hello_py.read_text(encoding="utf-8")

    # 9c. The runner merged the developer's branch — main has more than the
    #     initial seed commit and at least one merge commit message.
    from git import Repo
    repo = Repo(run_workspace / "working")
    main_log = list(repo.iter_commits("main"))
    assert len(main_log) >= 2  # initial commit + at least one merge
    assert any("merge" in c.message.lower() for c in main_log)

    # 9d. Persona file on disk has an episode and an updated reputation.
    persona_dir = data_root / "personas" / PERSONA_ID
    episodes = (persona_dir / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([e for e in episodes if e.strip()]) == 1
    rep = json.loads((persona_dir / "reputation.json").read_text(encoding="utf-8"))
    assert rep["production"]["total_runs"] == 1


# ── Sanity sub-test: serialise_project round-trips the persona_id ──────────


async def test_serialise_project_preserves_persona_id():
    """If the journey ever fails because cfg.persona_id was dropped on
    the way from JSON storage → ProjectConfig, this much smaller test
    will fail first and tell you exactly where."""
    p = serialise_project({
        "project_id": "p",
        "goal": "x",
        "agents": [{"role": "developer", "persona_id": "aria-v1"}],
    })
    assert p.agents[0].persona_id == "aria-v1"
