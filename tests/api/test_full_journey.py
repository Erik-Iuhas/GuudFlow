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

from agents.builtin.code_reviewer import CodeReviewer
from agents.registry import registry
from core.llm import MockLLMClient, MockTurn, ToolCall
from worker.docker_manager import DockerManager
from worker.job_runner import JobRunner, serialise_project
from worker.queue import JOB_COMPLETE


# ── Test-scoped fixtures ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _register_code_reviewer():
    """The reviewer agent in the journey is the CodeReviewer builtin —
    ensure it's in the registry before the test runs."""
    if not registry.has("code_reviewer"):
        registry.register("code_reviewer", CodeReviewer)


# Note: Phase G.7 auto-loads personas from `cfg.persona_id` in the
# runner itself (`AgentRunner._build_agents` reads `personas_root` and
# attaches), so this test relies on the default JobRunner factory.
# It used to ship a custom factory that wrapped `_build_agents`; that
# workaround is gone now that production does the right thing.


# ── The journey ────────────────────────────────────────────────────────────


async def test_full_user_journey(
    client, auth_header, app, data_root: Path, write_persona, monkeypatch
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
    app.state.meta_llm = MockLLMClient([MockTurn(tool_calls=[ToolCall(
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
    monkeypatch.setenv("AGENTFLOW_MODE", "dev")
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
    # Scripted LLM: developer writes hello.py → developer ends turn →
    # reviewer pulls a diff → reviewer approves. Same script runner_e2e
    # uses for a clean 2-agent run.
    llm = MockLLMClient([
        MockTurn(tool_calls=[ToolCall(
            id="dev_t1",
            name="fs-write",
            arguments={"path": "hello.py", "content": "def greet():\n    return 'hi'\n"},
        )]),
        MockTurn(text="[COMMIT]\n  summary: wrote greet()\n  files: hello.py"),
        MockTurn(tool_calls=[ToolCall(
            id="rev_t1",
            name="git-diff",
            arguments={"branch_a": "agent/developer", "branch_b": "main"},
        )]),
        MockTurn(text="[VERIFY] PASS"),
    ])

    async def loader(project_id: str):
        return await app.state.project_store.get(project_id)

    worker = JobRunner(
        queue=app.state.queue,
        project_loader=loader,
        data_root=data_root,
        llm=llm,
        docker_manager=DockerManager(mode="dev"),
        run_registry=app.state.run_registry,
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
    # STARTED — every agent posts one when its run loop begins.
    assert "STARTED" in types
    # GIT_COMMIT — runner commits at end of each agent's iteration.
    assert "GIT_COMMIT" in types
    # FILE_CLAIM/FILE_RELEASE — fs:write auto-claims before writing.
    assert "FILE_CLAIM" in types
    assert "FILE_RELEASE" in types
    # APPROVED — reviewer's terminal verdict that triggers merge to main.
    assert "APPROVED" in types

    # ── 8. Persona memory was updated end-to-end ───────────────────────
    # The memory_writer ran post-run because the runner_factory above
    # attached the persona to the developer. Reputation should reflect
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

    # 9b. Developer agent wrote hello.py via fs:write → it's in working/.
    hello_py = run_workspace / "working" / "hello.py"
    assert hello_py.exists()
    assert "def greet" in hello_py.read_text(encoding="utf-8")

    # 9c. Reviewer's APPROVED triggered a merge — main has more than the
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
