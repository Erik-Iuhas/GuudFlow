"""Tests for /api/meta."""

from __future__ import annotations

import json

import pytest

from agents.builtin import register_builtins
from core.llm import MockLLMClient, MockTurn, ToolCall


@pytest.fixture(autouse=True)
def _ensure_builtins():
    register_builtins()


# ── Greenfield path (no project_id — LLM still drives) ─────────────────────


async def test_propose_without_project_id_invokes_llm(
    client, auth_header, app,
):
    """Greenfield Propose-view flow: user types a goal, no project
    exists yet. The LLM is invoked anyway with goal + roster +
    archetype catalog. Without this, every greenfield proposal would
    be the same static skeleton — exactly the bug surfaced in live
    testing 2026-04-26."""
    app.state.meta_llm = MockLLMClient([MockTurn(tool_calls=[ToolCall(
        id="m1", name="propose_team",
        arguments={
            "rationale": "JWT auth needs a backend_dev + reviewer.",
            "agents": [
                {
                    "role": "auth_dev",
                    "archetype": "backend_dev",
                    "depends_on": [],
                    "persona": {"action": "create_new", "name_hint": "Sarah"},
                },
                {
                    "role": "auth_reviewer",
                    "archetype": "code_reviewer",
                    "depends_on": ["auth_dev"],
                    "is_reviewer": True,
                    "persona": {"action": "create_new"},
                },
            ],
        },
    )])])

    r = await client.post(
        "/api/meta/propose",
        json={"goal": "Build a JWT auth flow"},  # no project_id
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    # LLM-driven role names (not the static "developer"/"reviewer"
    # the deterministic skeleton would produce).
    roles = {a["role"] for a in body["agents"]}
    assert "auth_dev" in roles
    assert "auth_reviewer" in roles
    # New shape from D.1's TeamProposal.to_dict() still present.
    assert "persona_hires" in body
    assert "warnings" in body
    assert "deltas" in body


async def test_propose_without_project_id_falls_back_on_llm_failure(
    client, auth_header, app,
):
    """If the LLM crashes during a greenfield proposal, return the
    deterministic skeleton + a warning — same fail-soft contract as
    the project_id path."""

    class ExplodingLLM:
        async def complete(self, **kwargs):
            raise RuntimeError("anthropic 503")

    app.state.meta_llm = ExplodingLLM()

    r = await client.post(
        "/api/meta/propose",
        json={"goal": "x"},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    roles = {a["role"] for a in body["agents"]}
    assert "developer" in roles
    assert "reviewer" in roles
    assert any("anthropic 503" in w for w in body["warnings"]) or \
        "anthropic 503" in body["rationale"]


async def test_propose_requires_goal(client, auth_header):
    r = await client.post("/api/meta/propose", json={}, headers=auth_header)
    assert r.status_code == 400


async def test_propose_requires_auth(client):
    r = await client.post("/api/meta/propose", json={"goal": "x"})
    assert r.status_code == 401


# ── LLM-driven path (project_id supplied) ─────────────────────────────────


async def test_propose_with_project_id_uses_meta_agent(
    client, auth_header, app, data_root,
):
    """When project_id is given AND the project exists, the route
    instantiates MetaAgent with the injected LLM and returns its
    proposal."""
    # Set up a project on disk + an upload so the meta-agent has
    # something to read.
    await client.post(
        "/api/projects",
        json={"project_id": "p_meta", "goal": "Build refresh tokens"},
        headers=auth_header,
    )
    project_dir = data_root / "projects" / "p_meta"
    (project_dir / "inputs" / "uploads").mkdir(parents=True, exist_ok=True)
    (project_dir / "inputs" / "uploads" / "spec.md").write_text(
        "# Spec\n\nAdd refresh tokens.\n", encoding="utf-8",
    )

    # Inject a mocked LLM that emits a propose_team tool call.
    app.state.meta_llm = MockLLMClient([MockTurn(tool_calls=[ToolCall(
        id="m1", name="propose_team",
        arguments={
            "rationale": "auth_dev + reviewer covers the spec.",
            "agents": [
                {
                    "role": "auth_dev",
                    "archetype": "backend_dev",
                    "depends_on": [],
                    "persona": {"action": "create_new", "name_hint": "Mike"},
                },
                {
                    "role": "auth_reviewer",
                    "archetype": "code_reviewer",
                    "depends_on": ["auth_dev"],
                    "is_reviewer": True,
                    "persona": {"action": "create_new"},
                },
            ],
        },
    )])])

    r = await client.post(
        "/api/meta/propose",
        json={"goal": "Add refresh tokens", "project_id": "p_meta"},
        headers=auth_header,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "auth_dev" in {a["role"] for a in body["agents"]}
    assert "auth_reviewer" in {a["role"] for a in body["agents"]}
    # Persona hires reflect Mike + a non-Mike (because used_display_names
    # in D.1 prevents same name across roles in one team).
    hires_by_role = {h["role"]: h for h in body["persona_hires"]}
    assert hires_by_role["auth_dev"]["display_name"] == "Mike"
    assert hires_by_role["auth_reviewer"]["display_name"] != "Mike"


async def test_propose_with_unknown_project_returns_404(client, auth_header):
    r = await client.post(
        "/api/meta/propose",
        json={"goal": "x", "project_id": "ghost"},
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_propose_falls_back_to_skeleton_on_llm_failure(
    client, auth_header, app,
):
    """If the LLM raises, the proposal still returns a skeleton with
    a warning rather than 500-ing."""
    await client.post(
        "/api/projects",
        json={"project_id": "p_fail", "goal": "x"},
        headers=auth_header,
    )

    class ExplodingLLM:
        async def complete(self, **kwargs):
            raise RuntimeError("anthropic 503")

    app.state.meta_llm = ExplodingLLM()

    r = await client.post(
        "/api/meta/propose",
        json={"goal": "x", "project_id": "p_fail"},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    # Skeleton roles are present.
    roles = {a["role"] for a in body["agents"]}
    assert "developer" in roles
    assert "reviewer" in roles
    # Warning surfaces the failure reason for the studio to show.
    assert any("anthropic 503" in w for w in body["warnings"]) or \
        "anthropic 503" in body["rationale"]


async def test_propose_with_prior_run_id_passes_continuation_context(
    client, auth_header, app, data_root,
):
    """Smoke test: prior_run_id flows through to MetaAgent.propose;
    the continuation prompt path is exercised via D.3 tests at the
    agent layer."""
    await client.post(
        "/api/projects",
        json={
            "project_id": "p_cont",
            "goal": "extend",
            "agents": [
                {"role": "old_dev"},
                {"role": "old_reviewer", "is_reviewer": True},
            ],
        },
        headers=auth_header,
    )
    # Stage a prior_run sidecar so meta_inputs.continuation gets
    # populated (the route reads from inputs/from_run_*.meta.json).
    project_dir = data_root / "projects" / "p_cont"
    inputs = project_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / "from_run_prev.meta.json").write_text(
        json.dumps({
            "kind": "prior_run", "run_id": "prev",
            "status": "complete", "completed_agents": ["old_dev", "old_reviewer"],
            "has_feedback": True,
        }),
        encoding="utf-8",
    )
    (inputs / "from_run_prev").mkdir(exist_ok=True)
    (inputs / "from_run_prev" / "feedback.md").write_text(
        "Token expiry was missing — please address.\n", encoding="utf-8",
    )

    # Capture LLM call args to assert continuation context flowed in.
    captured: dict = {}

    class CapturingLLM:
        async def complete(self, **kwargs):
            captured.update(kwargs)
            from core.llm import LLMResponse
            return LLMResponse(
                text="", tool_calls=[ToolCall(
                    id="c1", name="propose_team", arguments={
                        "rationale": "keep team",
                        "agents": [
                            {"role": "old_dev", "archetype": "backend_dev",
                             "depends_on": []},
                        ],
                    },
                )],
                stop_reason="tool_use", model="mock",
            )

    app.state.meta_llm = CapturingLLM()

    r = await client.post(
        "/api/meta/propose",
        json={"goal": "extend", "project_id": "p_cont", "prior_run_id": "prev"},
        headers=auth_header,
    )
    assert r.status_code == 200, r.text
    user_msg = captured["messages"][0].content
    # Continuation context renders into the prompt.
    assert "CONTINUATION" in user_msg
    assert "Token expiry was missing" in user_msg
    assert "old_dev" in user_msg  # prior team roles surfaced


# ── Harness + playbook (S11) ──────────────────────────────────────────────


async def test_propose_passes_harness_and_playbook_to_meta_agent(
    client, auth_header, app, data_root,
):
    """A project's playbook is shown to the model and an opencode harness
    named in the request gets a default planner."""
    from core.playbook import Playbook

    await client.post(
        "/api/projects",
        json={"project_id": "p_pb", "goal": "Build it"},
        headers=auth_header,
    )
    pb = Playbook.load(data_root, "p_pb")
    pb.add("brief_addenda", "builder", "Read research/notes.md first", "run_1", confidence=0.8)
    pb.save()

    llm = MockLLMClient([MockTurn(tool_calls=[ToolCall(
        id="m1", name="propose_team",
        arguments={
            "rationale": "two builders",
            "agents": [
                {"role": "api", "archetype": "backend_dev", "depends_on": []},
                {"role": "ui", "archetype": "backend_dev", "depends_on": []},
            ],
        },
    )])])
    app.state.meta_llm = llm

    r = await client.post(
        "/api/meta/propose",
        json={"goal": "Build it", "project_id": "p_pb", "harness": "opencode"},
        headers=auth_header,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [a["role"] for a in body["agents"]][0] == "planner"
    user_msg = llm.calls[0]["messages"][0].content
    assert "Project playbook (lessons from earlier runs" in user_msg
    assert "Read research/notes.md first" in user_msg


async def test_propose_reports_playbook_entries_used(client, auth_header, app, data_root):
    """`playbook_entries_used` lists the active entries rendered into the prompt."""
    from core.playbook import Playbook

    await client.post("/api/projects", json={"project_id": "p_used", "goal": "g"},
                      headers=auth_header)
    pb = Playbook.load(data_root, "p_used")
    active = pb.add("brief_addenda", "builder", "Read notes first", "run_1", confidence=0.8)
    pb.add("known_pitfalls", "builder", "emerging only", "run_1", confidence=0.5)
    pb.save()
    app.state.meta_llm = MockLLMClient([MockTurn(tool_calls=[ToolCall(
        id="m1", name="propose_team",
        arguments={"rationale": "r", "agents": [
            {"role": "api", "archetype": "backend_dev", "depends_on": []}]},
    )])])
    r = await client.post("/api/meta/propose",
                          json={"goal": "g", "project_id": "p_used"}, headers=auth_header)
    assert r.status_code == 200, r.text
    assert r.json()["playbook_entries_used"] == [active.id]


async def test_propose_without_playbook_reports_empty_list(client, auth_header, app):
    app.state.meta_llm = MockLLMClient([MockTurn(tool_calls=[ToolCall(
        id="m1", name="propose_team",
        arguments={"rationale": "r", "agents": [
            {"role": "api", "archetype": "backend_dev", "depends_on": []}]},
    )])])
    r = await client.post("/api/meta/propose", json={"goal": "g"}, headers=auth_header)
    assert r.json()["playbook_entries_used"] == []


# ── Start mode hint (project flow plan §5) ────────────────────────────────


def _team_llm():
    return MockLLMClient([MockTurn(tool_calls=[ToolCall(
        id="m1", name="propose_team",
        arguments={
            "rationale": "team",
            "agents": [{"role": "api", "archetype": "backend_dev", "depends_on": []}],
        },
    )])])


async def test_propose_start_mode_adds_team_hint(client, auth_header, app):
    llm = _team_llm()
    app.state.meta_llm = llm
    r = await client.post(
        "/api/meta/propose",
        json={"goal": "Build it", "start_mode": "test_first"},
        headers=auth_header,
    )
    assert r.status_code == 200, r.text
    user_msg = llm.calls[0]["messages"][0].content
    assert "start mode test_first: include a tester role ahead of builders" in user_msg


async def test_propose_without_start_mode_has_no_hint(client, auth_header, app):
    llm = _team_llm()
    app.state.meta_llm = llm
    r = await client.post(
        "/api/meta/propose", json={"goal": "Build it"}, headers=auth_header,
    )
    assert r.status_code == 200, r.text
    assert "start mode" not in llm.calls[0]["messages"][0].content


async def test_propose_rejects_unknown_start_mode(client, auth_header):
    r = await client.post(
        "/api/meta/propose",
        json={"goal": "Build it", "start_mode": "nonsense"},
        headers=auth_header,
    )
    assert r.status_code == 400
