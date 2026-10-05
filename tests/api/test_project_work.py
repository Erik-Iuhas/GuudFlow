"""Tests for /api/projects/{id}/backlog and /runs-review (plan §13)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from core.backlog import Backlog, CardOutcome
from core.project_repo import ProjectRepo


async def _project(client, auth_header, pid: str = "p1") -> None:
    r = await client.post("/api/projects", json={"project_id": pid, "goal": "x"},
                          headers=auth_header)
    assert r.status_code in (200, 201)


def _commit(work: Path, name: str, text: str) -> None:
    (work / name).write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", f"feat: {name}"], cwd=work, check=True)


async def test_backlog_crud(client, auth_header) -> None:
    await _project(client, auth_header)
    r = await client.post("/api/projects/p1/backlog", headers=auth_header,
                          json={"title": "Add login", "acceptance": ["POST /login works"],
                                "priority": 3})
    assert r.status_code == 201 and r.json()["id"] == "B1"
    await client.post("/api/projects/p1/backlog", headers=auth_header,
                      json={"title": "Write docs", "kind": "docs"})
    items = (await client.get("/api/projects/p1/backlog", headers=auth_header)).json()["items"]
    assert [i["id"] for i in items] == ["B1", "B2"]
    r = await client.patch("/api/projects/p1/backlog/B2", headers=auth_header,
                           json={"priority": 9, "title": "Write the docs"})
    assert r.json()["priority"] == 9 and r.json()["title"] == "Write the docs"
    r = await client.patch("/api/projects/p1/backlog/B1", headers=auth_header,
                           json={"status": "dropped"})
    assert r.json()["status"] == "dropped"
    r = await client.patch("/api/projects/p1/backlog/B1", headers=auth_header,
                           json={"status": "open"})
    assert r.json()["status"] == "open"
    bad = await client.post("/api/projects/p1/backlog", headers=auth_header,
                            json={"title": "x", "kind": "art"})
    assert bad.status_code == 400
    assert (await client.patch("/api/projects/p1/backlog/B9", headers=auth_header,
                               json={"priority": 1})).status_code == 404
    assert (await client.get("/api/projects/ghost/backlog", headers=auth_header)).status_code == 404


async def test_routes_need_the_token(client, auth_header) -> None:
    await _project(client, auth_header)
    assert (await client.get("/api/projects/p1/backlog")).status_code == 401
    assert (await client.get("/api/projects/p1/runs-review")).status_code == 401


async def test_review_accept_and_reject(client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header)
    backlog = Backlog(data_root, "p1")
    item = backlog.add("Add login", ["works"])
    repo = ProjectRepo(data_root, "p1")
    base = repo.ensure(None)
    for run_id, name in (("run_a", "a.py"), ("run_b", "b.py")):
        work = data_root / "tmp" / run_id
        repo.clone_into(work)
        _commit(work, name, "x\n")
        repo.submit(run_id, work, base)
    backlog.take_for_run("run_a", ids=[item.id])
    backlog.record_outcomes("run_a", [CardOutcome("Login", "done", item.id)])

    listing = (await client.get("/api/projects/p1/runs-review", headers=auth_header)).json()
    assert listing["main"] == base and {r["run_id"] for r in listing["runs"]} == {"run_a", "run_b"}
    diff = (await client.get("/api/projects/p1/runs-review/run_a/changes",
                             headers=auth_header)).json()["diff"]
    assert "a.py" in diff
    r = await client.post("/api/projects/p1/runs-review/run_a/accept", headers=auth_header,
                          json={"note": "good"})
    assert r.status_code == 200 and r.json()["run"]["state"] == "accepted"
    assert r.json()["items"][0]["status"] == "done"
    r = await client.post("/api/projects/p1/runs-review/run_b/reject", headers=auth_header)
    assert r.json()["run"]["state"] == "rejected"
    again = await client.post("/api/projects/p1/runs-review/run_a/accept", headers=auth_header)
    assert again.status_code == 400
    missing = await client.post("/api/projects/p1/runs-review/run_zz/accept", headers=auth_header)
    assert missing.status_code == 404


async def test_conflicting_accept_is_409_with_files(client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header)
    repo = ProjectRepo(data_root, "p1")
    base = repo.ensure(None)
    for run_id, text in (("run_a", "a\n"), ("run_b", "b\n")):
        work = data_root / "tmp" / run_id
        repo.clone_into(work)
        _commit(work, "same.py", text)
        repo.submit(run_id, work, base)
    assert (await client.post("/api/projects/p1/runs-review/run_a/accept",
                              headers=auth_header)).status_code == 200
    r = await client.post("/api/projects/p1/runs-review/run_b/accept", headers=auth_header)
    assert r.status_code == 409 and r.json()["files"] == ["same.py"]


async def test_freeing_an_item_whose_run_is_gone(client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header)
    backlog = Backlog(data_root, "p1")
    item = backlog.add("Add login", ["works"])
    backlog.take_for_run("run_crashed", ids=[item.id])     # run died before submitting
    r = await client.patch(f"/api/projects/p1/backlog/{item.id}", headers=auth_header,
                           json={"status": "open"})
    assert r.status_code == 200 and r.json()["status"] == "open"


async def test_invoke_validates_backlog_and_continue_run(client, auth_header,
                                                         data_root: Path) -> None:
    await _project(client, auth_header)
    Backlog(data_root, "p1").add("Add login", ["works"])
    ok = await client.post("/api/invoke/p1", headers=auth_header,
                           json={"confirm": True, "backlog_items": ["B1"]})
    assert ok.status_code == 202
    for bad in ({"backlog_items": ["B7"]}, {"backlog_items": "B1"},
                {"continue_run": "run_nope"}):
        r = await client.post("/api/invoke/p1", headers=auth_header, json={"confirm": True, **bad})
        assert r.status_code == 400, bad
    top = await client.post("/api/invoke/p1", headers=auth_header,
                            json={"confirm": True, "backlog_items": "top", "allow_parallel": True})
    assert top.status_code == 202


async def test_one_active_run_per_project(client, auth_header) -> None:
    await _project(client, auth_header)
    first = await client.post("/api/invoke/p1", headers=auth_header, json={"confirm": True})
    assert first.status_code == 202
    second = await client.post("/api/invoke/p1", headers=auth_header, json={"confirm": True})
    assert second.status_code == 409 and first.json()["run_id"] in second.json()["detail"]
    parallel = await client.post("/api/invoke/p1", headers=auth_header,
                                 json={"confirm": True, "allow_parallel": True})
    assert parallel.status_code == 202
