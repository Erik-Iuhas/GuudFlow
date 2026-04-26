"""Tests for /api/training (Phase F.1.2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


# ── Shared fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def write_scenario(data_root: Path):
    """Materialise a scenario under data/training_camp/scenarios/."""
    def _write(
        scenario_id: str,
        *,
        goal: str = "Add a hello.py module",
        expected_role_types: list[str] | None = None,
        rubric: dict[str, Any] | None = None,
    ) -> Path:
        d = data_root / "training_camp" / "scenarios" / scenario_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "scenario.json").write_text(json.dumps({
            "scenario_id": scenario_id,
            "goal": goal,
            "project_type": "python_module",
            "expected_role_types": expected_role_types or ["developer", "reviewer"],
        }), encoding="utf-8")
        (d / "rubric.json").write_text(json.dumps(rubric or {
            "auto_checks": [
                {"name": "exists", "type": "files_present", "weight": 1.0,
                 "paths": ["hello.py"]},
            ],
        }), encoding="utf-8")
        return d
    return _write


# ── GET /scenarios ─────────────────────────────────────────────────────────


async def test_list_scenarios_returns_summary(client, write_scenario):
    write_scenario("a_v1")
    write_scenario("b_v1", goal="other goal")
    r = await client.get("/api/training/scenarios")
    assert r.status_code == 200
    items = r.json()
    assert [s["scenario_id"] for s in items] == ["a_v1", "b_v1"]
    assert items[0]["expected_role_types"] == ["developer", "reviewer"]
    assert items[0]["n_auto_checks"] == 1
    assert items[0]["total_auto_weight"] == 1.0


async def test_list_scenarios_empty_when_root_missing(client):
    r = await client.get("/api/training/scenarios")
    assert r.status_code == 200
    assert r.json() == []


# ── POST /run ──────────────────────────────────────────────────────────────


async def test_launch_training_run_returns_202(
    client, auth_header, write_persona, write_scenario,
):
    write_persona("aria-v1", name="Aria")
    write_scenario("hello_v1")

    r = await client.post(
        "/api/training/run",
        json={"persona_id": "aria-v1", "scenario_id": "hello_v1"},
        headers=auth_header,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["run_id"].startswith("run_")
    assert body["job_id"].startswith("job_")
    assert body["project_id"].startswith("train_hello_v1_aria-v1_")


async def test_launch_training_run_persists_project(
    client, auth_header, write_persona, write_scenario, app,
):
    """The project created by /run must be loadable via the project
    store — that's what the worker reads to build the runner."""
    write_persona("aria-v1")
    write_scenario("hello_v1")

    r = await client.post(
        "/api/training/run",
        json={"persona_id": "aria-v1", "scenario_id": "hello_v1"},
        headers=auth_header,
    )
    project_id = r.json()["project_id"]
    project = await app.state.project_store.get(project_id)
    assert project is not None
    assert project["mode"] == "training"
    assert project["training_scenario_id"] == "hello_v1"
    # Aria is attached to the first non-reviewer role (developer).
    dev = next(a for a in project["agents"] if a["role"] == "developer")
    assert dev["persona_id"] == "aria-v1"


async def test_launch_training_run_unknown_persona_404(
    client, auth_header, write_scenario,
):
    write_scenario("hello_v1")
    r = await client.post(
        "/api/training/run",
        json={"persona_id": "ghost", "scenario_id": "hello_v1"},
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_launch_training_run_unknown_scenario_404(
    client, auth_header, write_persona,
):
    write_persona("aria-v1")
    r = await client.post(
        "/api/training/run",
        json={"persona_id": "aria-v1", "scenario_id": "ghost"},
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_launch_training_run_missing_field_400(client, auth_header):
    r = await client.post(
        "/api/training/run",
        json={"persona_id": "aria-v1"},  # no scenario_id
        headers=auth_header,
    )
    assert r.status_code == 400


async def test_launch_training_run_requires_auth(client, write_persona, write_scenario):
    write_persona("aria-v1")
    write_scenario("hello_v1")
    r = await client.post(
        "/api/training/run",
        json={"persona_id": "aria-v1", "scenario_id": "hello_v1"},
    )
    assert r.status_code in (401, 403)


# ── POST /tournament ───────────────────────────────────────────────────────


async def test_launch_tournament_enqueues_one_run_per_persona(
    client, auth_header, write_persona, write_scenario, app,
):
    write_persona("aria-v1", name="Aria")
    write_persona("kai-v1", name="Kai")
    write_persona("nova-v1", name="Nova")
    write_scenario("hello_v1")

    r = await client.post(
        "/api/training/tournament",
        json={
            "scenario_id": "hello_v1",
            "persona_ids": ["aria-v1", "kai-v1", "nova-v1"],
        },
        headers=auth_header,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["tournament_id"].startswith("tour_")
    assert body["scenario_id"] == "hello_v1"
    assert len(body["participants"]) == 3
    persona_ids = {p["persona_id"] for p in body["participants"]}
    assert persona_ids == {"aria-v1", "kai-v1", "nova-v1"}

    # Each participant has its own project on disk.
    for p in body["participants"]:
        proj = await app.state.project_store.get(p["project_id"])
        assert proj is not None
        assert proj["training_scenario_id"] == "hello_v1"


async def test_tournament_requires_at_least_two_personas(
    client, auth_header, write_persona, write_scenario,
):
    write_persona("aria-v1")
    write_scenario("hello_v1")
    r = await client.post(
        "/api/training/tournament",
        json={"scenario_id": "hello_v1", "persona_ids": ["aria-v1"]},
        headers=auth_header,
    )
    assert r.status_code == 400


async def test_tournament_unknown_persona_404(
    client, auth_header, write_persona, write_scenario,
):
    write_persona("aria-v1")
    write_scenario("hello_v1")
    r = await client.post(
        "/api/training/tournament",
        json={"scenario_id": "hello_v1",
              "persona_ids": ["aria-v1", "ghost"]},
        headers=auth_header,
    )
    assert r.status_code == 404


# ── GET /tournaments/{id} ──────────────────────────────────────────────────


async def test_tournament_result_pending_when_runs_not_finished(
    client, auth_header, write_persona, write_scenario,
):
    write_persona("aria-v1")
    write_persona("kai-v1")
    write_scenario("hello_v1")

    r = await client.post(
        "/api/training/tournament",
        json={"scenario_id": "hello_v1",
              "persona_ids": ["aria-v1", "kai-v1"]},
        headers=auth_header,
    )
    tournament_id = r.json()["tournament_id"]

    # No runs have completed yet → board.json files don't exist.
    r2 = await client.get(f"/api/training/tournaments/{tournament_id}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["complete"] is False
    assert all(p["score"] is None for p in body["leaderboard"])


async def test_tournament_result_aggregates_rubric_scores(
    client, auth_header, write_persona, write_scenario, data_root: Path,
):
    """Once both runs have RUBRIC_SCORE on their persisted boards, the
    leaderboard sorts by score descending."""
    write_persona("aria-v1")
    write_persona("kai-v1")
    write_scenario("hello_v1")

    r = await client.post(
        "/api/training/tournament",
        json={"scenario_id": "hello_v1",
              "persona_ids": ["aria-v1", "kai-v1"]},
        headers=auth_header,
    )
    body = r.json()
    tournament_id = body["tournament_id"]
    by_persona = {p["persona_id"]: p for p in body["participants"]}

    # Simulate the worker writing board.json with RUBRIC_SCORE signals.
    _write_run_board(
        data_root, by_persona["aria-v1"]["run_id"], rubric_score=0.91,
    )
    _write_run_board(
        data_root, by_persona["kai-v1"]["run_id"], rubric_score=0.74,
    )

    r2 = await client.get(f"/api/training/tournaments/{tournament_id}")
    assert r2.status_code == 200
    result = r2.json()
    assert result["complete"] is True
    leaderboard = result["leaderboard"]
    assert [p["persona_id"] for p in leaderboard] == ["aria-v1", "kai-v1"]
    assert leaderboard[0]["score"] == 0.91
    assert leaderboard[1]["score"] == 0.74


async def test_tournament_result_unknown_id_404(client):
    r = await client.get("/api/training/tournaments/tour_doesnotexist")
    assert r.status_code == 404


# ── Helper for forging completed runs in the tournament test ──────────────


def _write_run_board(data_root: Path, run_id: str, *, rubric_score: float) -> None:
    """Write a minimal board.json with a single RUBRIC_SCORE signal."""
    run_dir = data_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "board.json").write_text(json.dumps([
        {
            "type": "RUBRIC_SCORE",
            "summary": f"score: {rubric_score}",
            "from_agent": "rubric_scorer",
            "detail": json.dumps({"score": rubric_score, "scenario_id": "hello_v1"}),
        },
    ]), encoding="utf-8")
