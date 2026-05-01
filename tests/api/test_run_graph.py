"""M5.C4 — GET /api/runs/{run_id}/graph tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ── helpers ───────────────────────────────────────────────────────────────


def _make_project(data_root: Path, project_id: str, agents: list[dict], rbac: dict) -> None:
    """Write a project to projects.json."""
    projects_file = data_root / "projects.json"
    data: dict = {}
    if projects_file.exists():
        data = json.loads(projects_file.read_text(encoding="utf-8"))
    data[project_id] = {
        "project_id": project_id,
        "goal": "test goal",
        "agents": agents,
        "rbac": rbac,
    }
    projects_file.write_text(json.dumps(data), encoding="utf-8")


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def five_agent_run(data_root, write_run):
    """A 5-agent project with dev, reviewer, tech_lead, architect, spec_writer."""
    project_id = "proj-graph"
    agents = [
        {
            "role": "spec_writer",
            "depends_on": [],
            "agent_class_role_type": None,
            "model_tier": "solid",
            "is_final_reviewer": False,
            "persona_id": None,
        },
        {
            "role": "dev",
            "depends_on": ["spec_writer"],
            "agent_class_role_type": None,
            "model_tier": "solid",
            "is_final_reviewer": False,
            "persona_id": None,
        },
        {
            "role": "reviewer",
            "depends_on": ["dev"],
            "agent_class_role_type": "code_reviewer",
            "model_tier": "solid",
            "is_final_reviewer": False,
            "persona_id": None,
        },
        {
            "role": "tech_lead",
            "depends_on": [],
            "agent_class_role_type": "tech_lead",
            "model_tier": "heavy",
            "is_final_reviewer": False,
            "persona_id": None,
        },
        {
            "role": "architect",
            "depends_on": ["dev", "reviewer"],
            "agent_class_role_type": "architect",
            "model_tier": "heavy",
            "is_final_reviewer": True,
            "persona_id": None,
        },
    ]
    rbac = {
        "spec_writer": {"allowed_scopes": []},
        "dev": {"allowed_scopes": []},
        "reviewer": {"allowed_scopes": []},
        "tech_lead": {"allowed_scopes": ["assign-task", "web-search"]},
        "architect": {"allowed_scopes": ["assign-task"]},
    }
    _make_project(data_root, project_id, agents, rbac)
    run_dir = write_run("run-graph-1", project_id=project_id)
    return "run-graph-1"


# ── basic structure ────────────────────────────────────────────────────────


async def test_graph_returns_nodes_and_edges(client, five_agent_run, auth_header):
    r = await client.get(f"/api/runs/{five_agent_run}/graph", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert "nodes" in body
    assert "edges" in body


async def test_graph_node_count(client, five_agent_run, auth_header):
    r = await client.get(f"/api/runs/{five_agent_run}/graph", headers=auth_header)
    assert len(r.json()["nodes"]) == 5


async def test_graph_node_roles(client, five_agent_run, auth_header):
    r = await client.get(f"/api/runs/{five_agent_run}/graph", headers=auth_header)
    roles = {n["role"] for n in r.json()["nodes"]}
    assert roles == {"spec_writer", "dev", "reviewer", "tech_lead", "architect"}


async def test_graph_node_fields(client, five_agent_run, auth_header):
    r = await client.get(f"/api/runs/{five_agent_run}/graph", headers=auth_header)
    node = next(n for n in r.json()["nodes"] if n["role"] == "architect")
    assert node["model_tier"] == "heavy"
    assert node["is_final_reviewer"] is True
    assert "archetype" in node
    assert "persona_name" in node
    assert "persona_id" in node


# ── depends_on edges ──────────────────────────────────────────────────────


async def test_graph_depends_on_edges_present(client, five_agent_run, auth_header):
    r = await client.get(f"/api/runs/{five_agent_run}/graph", headers=auth_header)
    edges = r.json()["edges"]
    dep_edges = [(e["from_role"], e["to_role"]) for e in edges if e["kind"] == "depends_on"]
    assert ("spec_writer", "dev") in dep_edges
    assert ("dev", "reviewer") in dep_edges
    assert ("dev", "architect") in dep_edges
    assert ("reviewer", "architect") in dep_edges


# ── can_assign_to edges ────────────────────────────────────────────────────


async def test_graph_can_assign_to_edges(client, five_agent_run, auth_header):
    r = await client.get(f"/api/runs/{five_agent_run}/graph", headers=auth_header)
    edges = r.json()["edges"]
    assign_edges = {(e["from_role"], e["to_role"]) for e in edges if e["kind"] == "can_assign_to"}
    # tech_lead has assign-task — should have edges to all other roles
    other_roles = {"spec_writer", "dev", "reviewer", "architect"}
    for target in other_roles:
        assert ("tech_lead", target) in assign_edges
    # architect has assign-task — should have edges to all other roles
    for target in {"spec_writer", "dev", "reviewer", "tech_lead"}:
        assert ("architect", target) in assign_edges


# ── escalates_to edges ────────────────────────────────────────────────────


async def test_graph_escalates_to_tech_lead(client, five_agent_run, auth_header):
    r = await client.get(f"/api/runs/{five_agent_run}/graph", headers=auth_header)
    edges = r.json()["edges"]
    esc_targets = {e["to_role"] for e in edges if e["kind"] == "escalates_to"}
    assert esc_targets == {"tech_lead"}

    esc_sources = {e["from_role"] for e in edges if e["kind"] == "escalates_to"}
    assert "tech_lead" not in esc_sources
    assert "dev" in esc_sources


# ── edge quality ──────────────────────────────────────────────────────────


async def test_graph_no_self_edges(client, five_agent_run, auth_header):
    r = await client.get(f"/api/runs/{five_agent_run}/graph", headers=auth_header)
    for e in r.json()["edges"]:
        assert e["from_role"] != e["to_role"]


async def test_graph_no_duplicate_edges(client, five_agent_run, auth_header):
    r = await client.get(f"/api/runs/{five_agent_run}/graph", headers=auth_header)
    seen = set()
    for e in r.json()["edges"]:
        key = (e["from_role"], e["to_role"], e["kind"])
        assert key not in seen, f"duplicate edge: {key}"
        seen.add(key)


# ── no tech_lead variant ──────────────────────────────────────────────────


async def test_graph_no_tech_lead_no_escalates_to(client, data_root, write_run, auth_header):
    """Without tech_lead on the team, escalates_to edges are absent."""
    agents = [
        {"role": "dev", "depends_on": [], "agent_class_role_type": None,
         "model_tier": "solid", "is_final_reviewer": False, "persona_id": None},
        {"role": "reviewer", "depends_on": ["dev"], "agent_class_role_type": "code_reviewer",
         "model_tier": "solid", "is_final_reviewer": False, "persona_id": None},
    ]
    rbac = {
        "dev": {"allowed_scopes": []},
        "reviewer": {"allowed_scopes": []},
    }
    _make_project(data_root, "proj-no-tl", agents, rbac)
    write_run("run-no-tl", project_id="proj-no-tl")

    r = await client.get("/api/runs/run-no-tl/graph", headers=auth_header)
    assert r.status_code == 200
    esc_edges = [e for e in r.json()["edges"] if e["kind"] == "escalates_to"]
    assert esc_edges == []


# ── explicit hierarchy — manages overrides RBAC-derived can_assign_to ────────


async def test_graph_explicit_manages_overrides_rbac(data_root, write_run, client, auth_header):
    """When manages=[...] is set, only those edges are emitted — not RBAC-derived ones."""
    agents = [
        {"role": "lead", "depends_on": [], "agent_class_role_type": None,
         "model_tier": "solid", "is_final_reviewer": False, "persona_id": None,
         "manages": ["dev_a"]},
        {"role": "dev_a", "depends_on": [], "agent_class_role_type": None,
         "model_tier": "solid", "is_final_reviewer": False, "persona_id": None},
        {"role": "dev_b", "depends_on": [], "agent_class_role_type": None,
         "model_tier": "solid", "is_final_reviewer": False, "persona_id": None},
    ]
    # lead has assign-task in RBAC, but explicit manages=[dev_a] should win
    rbac = {
        "lead": {"allowed_scopes": ["assign-task"]},
        "dev_a": {"allowed_scopes": []},
        "dev_b": {"allowed_scopes": []},
    }
    _make_project(data_root, "proj-manages", agents, rbac)
    write_run("run-manages", project_id="proj-manages")

    r = await client.get("/api/runs/run-manages/graph", headers=auth_header)
    assert r.status_code == 200
    edges = r.json()["edges"]
    assign_edges = [(e["from_role"], e["to_role"]) for e in edges if e["kind"] == "can_assign_to"]
    assert ("lead", "dev_a") in assign_edges
    # dev_b is NOT in manages, so no edge even though RBAC has assign-task
    assert ("lead", "dev_b") not in assign_edges


async def test_graph_empty_manages_falls_back_to_rbac(data_root, write_run, client, auth_header):
    """Empty manages=[] falls back to RBAC-derived can_assign_to."""
    agents = [
        {"role": "lead", "depends_on": [], "agent_class_role_type": None,
         "model_tier": "solid", "is_final_reviewer": False, "persona_id": None,
         "manages": []},
        {"role": "dev_a", "depends_on": [], "agent_class_role_type": None,
         "model_tier": "solid", "is_final_reviewer": False, "persona_id": None},
    ]
    rbac = {
        "lead": {"allowed_scopes": ["assign-task"]},
        "dev_a": {"allowed_scopes": []},
    }
    _make_project(data_root, "proj-manages-fallback", agents, rbac)
    write_run("run-manages-fallback", project_id="proj-manages-fallback")

    r = await client.get("/api/runs/run-manages-fallback/graph", headers=auth_header)
    edges = r.json()["edges"]
    assign_edges = [(e["from_role"], e["to_role"]) for e in edges if e["kind"] == "can_assign_to"]
    assert ("lead", "dev_a") in assign_edges


# ── explicit escalates_to overrides tech_lead default ─────────────────────


async def test_graph_explicit_escalates_to_overrides_default(data_root, write_run, client, auth_header):
    """When escalates_to=[...] is set on an agent, it overrides the tech_lead default."""
    agents = [
        {"role": "dev", "depends_on": [], "agent_class_role_type": None,
         "model_tier": "solid", "is_final_reviewer": False, "persona_id": None,
         "escalates_to": ["architect"]},
        {"role": "architect", "depends_on": [], "agent_class_role_type": None,
         "model_tier": "heavy", "is_final_reviewer": True, "persona_id": None},
        {"role": "tech_lead", "depends_on": [], "agent_class_role_type": "tech_lead",
         "model_tier": "heavy", "is_final_reviewer": False, "persona_id": None},
    ]
    rbac = {
        "dev": {"allowed_scopes": []},
        "architect": {"allowed_scopes": []},
        "tech_lead": {"allowed_scopes": []},
    }
    _make_project(data_root, "proj-esc-explicit", agents, rbac)
    write_run("run-esc-explicit", project_id="proj-esc-explicit")

    r = await client.get("/api/runs/run-esc-explicit/graph", headers=auth_header)
    edges = r.json()["edges"]
    esc_edges = [(e["from_role"], e["to_role"]) for e in edges if e["kind"] == "escalates_to"]
    # dev escalates to architect (explicit), not tech_lead
    assert ("dev", "architect") in esc_edges
    assert ("dev", "tech_lead") not in esc_edges
    # architect and tech_lead have no explicit escalates_to, so they use legacy default
    assert ("architect", "tech_lead") in esc_edges


async def test_graph_empty_escalates_to_falls_back_to_tech_lead(data_root, write_run, client, auth_header):
    """Empty escalates_to=[] falls back to the tech_lead default."""
    agents = [
        {"role": "dev", "depends_on": [], "agent_class_role_type": None,
         "model_tier": "solid", "is_final_reviewer": False, "persona_id": None,
         "escalates_to": []},
        {"role": "tech_lead", "depends_on": [], "agent_class_role_type": "tech_lead",
         "model_tier": "heavy", "is_final_reviewer": False, "persona_id": None},
    ]
    rbac = {"dev": {"allowed_scopes": []}, "tech_lead": {"allowed_scopes": []}}
    _make_project(data_root, "proj-esc-fallback", agents, rbac)
    write_run("run-esc-fallback", project_id="proj-esc-fallback")

    r = await client.get("/api/runs/run-esc-fallback/graph", headers=auth_header)
    edges = r.json()["edges"]
    esc_edges = [(e["from_role"], e["to_role"]) for e in edges if e["kind"] == "escalates_to"]
    assert ("dev", "tech_lead") in esc_edges


# ── error cases ───────────────────────────────────────────────────────────


async def test_graph_missing_run_404(client, auth_header):
    r = await client.get("/api/runs/nonexistent-run/graph", headers=auth_header)
    assert r.status_code == 404


async def test_graph_requires_auth(client, five_agent_run):
    r = await client.get(f"/api/runs/{five_agent_run}/graph")
    assert r.status_code in (401, 403)
