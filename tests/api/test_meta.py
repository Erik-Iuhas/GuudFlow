"""Tests for /api/meta."""

from __future__ import annotations

import json

import pytest

from agents.builtin import register_builtins
from core.llm import MockLLMClient, MockTurn, ToolCall


@pytest.fixture(autouse=True)
def _ensure_builtins():
    register_builtins()


# ── Back-compat skeleton path (no project_id) ──────────────────────────────


async def test_propose_returns_skeleton(client, auth_header):
    r = await client.post(
        "/api/meta/propose",
        json={"goal": "Build a JWT auth flow"},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert "rationale" in body
    assert len(body["agents"]) >= 1
    # Includes a developer + reviewer baseline.
    roles = {a["role"] for a in body["agents"]}
    assert "developer" in roles
    assert "reviewer" in roles
    assert "rbac" in body
    # New shape from D.1's TeamProposal.to_dict().
    assert "persona_hires" in body
    assert "warnings" in body
    assert "deltas" in body


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
