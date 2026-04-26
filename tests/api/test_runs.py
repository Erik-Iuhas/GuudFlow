"""Tests for /api/runs."""

from __future__ import annotations

from pathlib import Path


async def test_list_runs_empty(client):
    r = await client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_runs_with_data(client, write_run):
    write_run("run_1", project_id="p1")
    write_run("run_2", project_id="p2")
    r = await client.get("/api/runs")
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_list_runs_filtered_by_project(client, write_run):
    write_run("run_1", project_id="p1")
    write_run("run_2", project_id="p2")
    r = await client.get("/api/runs?project_id=p1")
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) == 1
    assert runs[0]["project_id"] == "p1"


async def test_get_run(client, write_run):
    write_run("run_1", project_id="p1")
    r = await client.get("/api/runs/run_1")
    assert r.status_code == 200
    assert r.json()["run_id"] == "run_1"


async def test_get_unknown_run(client):
    r = await client.get("/api/runs/ghost")
    assert r.status_code == 404


async def test_get_run_board(client, write_run):
    board = [{"type": "STARTED", "summary": "x", "from_agent": "developer"}]
    write_run("run_1", board=board)
    r = await client.get("/api/runs/run_1/board")
    assert r.status_code == 200
    assert r.json() == board


async def test_download_deliverable(client, write_run, data_root: Path):
    run_dir = write_run("run_1")
    (run_dir / "workspace" / "deliverables" / "out.txt").write_text(
        "deliverable contents", encoding="utf-8",
    )
    r = await client.get("/api/runs/run_1/deliverables/out.txt")
    assert r.status_code == 200
    assert r.text == "deliverable contents"


async def test_download_deliverable_path_escape_blocked(client, write_run):
    write_run("run_1")
    r = await client.get("/api/runs/run_1/deliverables/../meta.json")
    # Either 400 (escape detected) or 404 (resolved file not found in dir).
    assert r.status_code in (400, 404)


async def test_download_missing_deliverable(client, write_run):
    write_run("run_1")
    r = await client.get("/api/runs/run_1/deliverables/nope.txt")
    assert r.status_code == 404
