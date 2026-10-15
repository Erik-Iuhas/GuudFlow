"""Tests for the Code tab's routes (plan §15)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.git_workspace import GitWorkspace
from core.harness.git_roles import RoleClones
from core.project_repo import ProjectRepo


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


async def test_project_tree_and_file(client, auth_header, data_root: Path) -> None:
    r = await client.post("/api/projects", json={"project_id": "p1", "goal": "x"},
                          headers=auth_header)
    assert r.status_code in (200, 201)
    missing = await client.get("/api/projects/p1/code/tree", headers=auth_header)
    assert missing.status_code == 404
    repo = ProjectRepo(data_root, "p1")
    base = repo.ensure(None)
    work = data_root / "tmp" / "w"
    repo.clone_into(work)
    _git(work, "config", "user.name", "t")
    _git(work, "config", "user.email", "t@x")
    (work / "app.py").write_text("print(1)\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "feat: app")
    repo.submit("run_a", work, base)
    repo.accept("run_a")

    body = (await client.get("/api/projects/p1/code/tree", headers=auth_header)).json()
    assert [e["path"] for e in body["entries"]] == ["app.py"]
    f = (await client.get("/api/projects/p1/code/file", params={"path": "app.py"},
                          headers=auth_header)).json()
    assert f["text"] == "print(1)\n"
    bad = await client.get("/api/projects/p1/code/file", params={"path": "../x"},
                           headers=auth_header)
    assert bad.status_code == 400
    assert (await client.get("/api/projects/p1/code/tree")).status_code == 401


@pytest.fixture
def role_run(data_root: Path) -> Path:
    run_root = data_root / "runs" / "run_a"
    main = GitWorkspace(run_root / "workspace" / "working")
    main.init()
    clones = RoleClones(main, run_root / "roles")
    work = clones.ensure("builder", "Sam")
    (work / "feature.py").write_text("def f():\n    return 1\n")
    return run_root


async def test_run_and_role_sources(client, auth_header, role_run) -> None:
    roles = (await client.get("/api/runs/run_a/code/roles", headers=auth_header)).json()
    assert roles == {"roles": ["builder"]}
    run = await client.get("/api/runs/run_a/code/tree", headers=auth_header)
    assert run.status_code == 200 and run.json()["source"] == "run"
    tree = (await client.get("/api/runs/run_a/code/tree", params={"role": "builder"},
                             headers=auth_header)).json()
    assert {e["path"]: e["status"] for e in tree["entries"]}["feature.py"] == "A"
    assert tree["last_edit"]["path"] == "feature.py"
    f = (await client.get("/api/runs/run_a/code/file",
                          params={"role": "builder", "path": "feature.py", "diff": 1},
                          headers=auth_header)).json()
    assert "+    return 1" in f["diff"]


@pytest.mark.parametrize("path", [
    "/api/runs/run_a/code/tree?role=nobody",
    "/api/runs/missing/code/tree",
    "/api/runs/run_a/code/tree?role=..",
    "/api/runs/..%2Frun_a/code/tree",
])
async def test_unknown_or_escaping_is_404(client, auth_header, role_run, path) -> None:
    assert (await client.get(path, headers=auth_header)).status_code == 404
