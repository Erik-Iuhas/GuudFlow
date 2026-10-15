"""Tests for GET /api/runs/{run_id}/roles/{role}/changes (live role changes)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.git_workspace import GitWorkspace
from core.harness.git_roles import RoleClones


@pytest.fixture
def role_run(data_root: Path) -> Path:
    run_root = data_root / "runs" / "run_a"
    main = GitWorkspace(run_root / "workspace" / "working")
    main.init()
    clones = RoleClones(main, run_root / "roles")
    work = clones.ensure("builder", "Sam")
    (work / "feature.py").write_text("def f():\n    return 1\n")
    return run_root


async def test_returns_working_changes(client, auth_header, role_run) -> None:
    res = await client.get("/api/runs/run_a/roles/builder/changes", headers=auth_header)
    assert res.status_code == 200
    body = res.json()
    assert body["role"] == "builder" and not body["truncated"]
    assert body["files"] == [{"path": "feature.py", "status": "untracked", "added": 2, "removed": 0}]
    assert "+++ b/feature.py" in body["diff"]
    assert body["base"] == body["head"]


async def test_needs_token(client, role_run) -> None:
    res = await client.get("/api/runs/run_a/roles/builder/changes")
    assert res.status_code == 401


@pytest.mark.parametrize("path", [
    "/api/runs/run_a/roles/nobody/changes",
    "/api/runs/missing/roles/builder/changes",
    "/api/runs/run_a/roles/..%2F..%2Fx/changes",
    "/api/runs/..%2Frun_a/roles/builder/changes",
])
async def test_unknown_or_escaping_paths_are_404(client, auth_header, role_run, path) -> None:
    res = await client.get(path, headers=auth_header)
    assert res.status_code == 404
