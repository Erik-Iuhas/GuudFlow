"""Tests for /api/projects/{id}/inputs and the per-project inputs storage layer.

DESIGN §33 Chunks 1 & 2 — uploads + git/local-path source repo + secret
scan + list + delete + project-dir lifecycle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.storage import ProjectStore
from core import source_repo as source_repo_lib


# ── HTTP fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def project(client, auth_header):
    """Create a single project and return its id."""
    r = await client.post(
        "/api/projects",
        json={"project_id": "p1", "goal": "build x", "agents": []},
        headers=auth_header,
    )
    assert r.status_code == 201
    return "p1"


# ── Upload ─────────────────────────────────────────────────────────────────


async def test_upload_single_file(client, auth_header, data_root, project):
    r = await client.post(
        f"/api/projects/{project}/inputs/upload",
        files=[("files", ("spec.txt", b"hello world", "text/plain"))],
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"uploaded": [{"filename": "spec.txt", "size": 11}]}

    landed = data_root / "projects" / project / "inputs" / "uploads" / "spec.txt"
    assert landed.is_file()
    assert landed.read_bytes() == b"hello world"


async def test_upload_multiple_files(client, auth_header, data_root, project):
    r = await client.post(
        f"/api/projects/{project}/inputs/upload",
        files=[
            ("files", ("a.txt", b"AAA", "text/plain")),
            ("files", ("b.txt", b"BBBB", "text/plain")),
        ],
        headers=auth_header,
    )
    assert r.status_code == 200
    assert r.json() == {
        "uploaded": [
            {"filename": "a.txt", "size": 3},
            {"filename": "b.txt", "size": 4},
        ]
    }
    uploads = data_root / "projects" / project / "inputs" / "uploads"
    assert (uploads / "a.txt").read_bytes() == b"AAA"
    assert (uploads / "b.txt").read_bytes() == b"BBBB"


async def test_upload_unknown_project(client, auth_header):
    r = await client.post(
        "/api/projects/ghost/inputs/upload",
        files=[("files", ("x.txt", b"x", "text/plain"))],
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_upload_requires_auth(client, project):
    r = await client.post(
        f"/api/projects/{project}/inputs/upload",
        files=[("files", ("x.txt", b"x", "text/plain"))],
    )
    assert r.status_code == 401


async def test_upload_rejects_path_traversal(client, auth_header, data_root, project):
    r = await client.post(
        f"/api/projects/{project}/inputs/upload",
        files=[("files", ("../escape.txt", b"bad", "text/plain"))],
        headers=auth_header,
    )
    assert r.status_code == 400
    # Nothing landed anywhere outside (or inside) the uploads dir.
    project_dir = data_root / "projects" / project
    assert not (project_dir.parent / "escape.txt").exists()
    assert not (project_dir / "escape.txt").exists()
    uploads = project_dir / "inputs" / "uploads"
    assert list(uploads.iterdir()) == []


async def test_upload_rejects_backslash(client, auth_header, project):
    r = await client.post(
        f"/api/projects/{project}/inputs/upload",
        files=[("files", ("foo\\bar.txt", b"bad", "text/plain"))],
        headers=auth_header,
    )
    assert r.status_code == 400


async def test_upload_overwrites_on_collision(client, auth_header, data_root, project):
    await client.post(
        f"/api/projects/{project}/inputs/upload",
        files=[("files", ("doc.txt", b"first", "text/plain"))],
        headers=auth_header,
    )
    r = await client.post(
        f"/api/projects/{project}/inputs/upload",
        files=[("files", ("doc.txt", b"second-version", "text/plain"))],
        headers=auth_header,
    )
    assert r.status_code == 200
    landed = data_root / "projects" / project / "inputs" / "uploads" / "doc.txt"
    assert landed.read_bytes() == b"second-version"


# ── List ───────────────────────────────────────────────────────────────────


async def test_list_inputs_empty(client, project):
    r = await client.get(f"/api/projects/{project}/inputs")
    assert r.status_code == 200
    assert r.json() == {"uploads": [], "source_repo": None, "prior_runs": []}


async def test_list_inputs_after_upload(client, auth_header, project):
    await client.post(
        f"/api/projects/{project}/inputs/upload",
        files=[
            ("files", ("a.txt", b"AAA", "text/plain")),
            ("files", ("b.txt", b"BB", "text/plain")),
        ],
        headers=auth_header,
    )
    r = await client.get(f"/api/projects/{project}/inputs")
    assert r.status_code == 200
    body = r.json()
    assert body["source_repo"] is None
    assert body["prior_runs"] == []
    assert body["uploads"] == [
        {"filename": "a.txt", "size": 3},
        {"filename": "b.txt", "size": 2},
    ]


async def test_list_inputs_unknown_project(client):
    r = await client.get("/api/projects/ghost/inputs")
    assert r.status_code == 404


# ── Delete ─────────────────────────────────────────────────────────────────


async def test_delete_upload(client, auth_header, data_root, project):
    await client.post(
        f"/api/projects/{project}/inputs/upload",
        files=[("files", ("doc.txt", b"x", "text/plain"))],
        headers=auth_header,
    )
    r = await client.delete(
        f"/api/projects/{project}/inputs/uploads/doc.txt",
        headers=auth_header,
    )
    assert r.status_code == 204
    landed = data_root / "projects" / project / "inputs" / "uploads" / "doc.txt"
    assert not landed.exists()

    r = await client.get(f"/api/projects/{project}/inputs")
    assert r.json()["uploads"] == []


async def test_delete_unknown_file(client, auth_header, project):
    r = await client.delete(
        f"/api/projects/{project}/inputs/uploads/missing.txt",
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_delete_unknown_project(client, auth_header):
    r = await client.delete(
        "/api/projects/ghost/inputs/uploads/x.txt",
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_delete_requires_auth(client, auth_header, project):
    await client.post(
        f"/api/projects/{project}/inputs/upload",
        files=[("files", ("doc.txt", b"x", "text/plain"))],
        headers=auth_header,
    )
    r = await client.delete(f"/api/projects/{project}/inputs/uploads/doc.txt")
    assert r.status_code == 401


# ── ProjectStore directory lifecycle ───────────────────────────────────────


async def test_create_project_makes_inputs_dir(data_root: Path):
    store = ProjectStore(data_root)
    await store.create({"project_id": "px", "goal": "g"})
    uploads = data_root / "projects" / "px" / "inputs" / "uploads"
    assert uploads.is_dir()


async def test_delete_project_removes_inputs_dir(data_root: Path):
    store = ProjectStore(data_root)
    await store.create({"project_id": "px", "goal": "g"})
    project_dir = data_root / "projects" / "px"
    (project_dir / "inputs" / "uploads" / "spec.txt").write_text("hello", encoding="utf-8")
    assert project_dir.is_dir()

    ok = await store.delete("px")
    assert ok is True
    assert not project_dir.exists()


# ── Source repo: git URL ───────────────────────────────────────────────────


def _fake_clone_factory(payload: dict[str, str] | None = None):
    """Returns an async stand-in for clone_git_repo that creates `payload`
    files under the dest. Defaults to a single README."""
    files = payload if payload is not None else {"README.md": "# upstream\n"}

    async def _fake(url, branch, shallow, dest):
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    return _fake


async def test_git_import_happy_path(client, auth_header, data_root, project, monkeypatch):
    monkeypatch.setattr(source_repo_lib, "clone_git_repo", _fake_clone_factory())
    r = await client.post(
        f"/api/projects/{project}/inputs/git",
        json={"git_url": "https://example.com/repo.git", "branch": "main"},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["source_repo"]["kind"] == "git_url"
    assert body["source_repo"]["git_url"] == "https://example.com/repo.git"
    assert body["source_repo"]["branch"] == "main"
    assert body["warnings"] == []

    landed = data_root / "projects" / project / "inputs" / "source_repo" / "README.md"
    assert landed.read_text(encoding="utf-8") == "# upstream\n"
    meta = data_root / "projects" / project / "inputs" / "source_repo.meta.json"
    assert meta.exists()


async def test_git_import_requires_url(client, auth_header, project):
    r = await client.post(
        f"/api/projects/{project}/inputs/git",
        json={},
        headers=auth_header,
    )
    assert r.status_code == 400


async def test_git_import_requires_auth(client, project):
    r = await client.post(
        f"/api/projects/{project}/inputs/git",
        json={"git_url": "https://example.com/repo.git"},
    )
    assert r.status_code == 401


async def test_git_import_unknown_project(client, auth_header):
    r = await client.post(
        "/api/projects/ghost/inputs/git",
        json={"git_url": "https://example.com/repo.git"},
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_git_import_clone_failure(client, auth_header, project, monkeypatch):
    async def boom(*a, **kw):
        raise source_repo_lib.SourceCloneError("repository not found")
    monkeypatch.setattr(source_repo_lib, "clone_git_repo", boom)
    r = await client.post(
        f"/api/projects/{project}/inputs/git",
        json={"git_url": "https://example.com/missing.git"},
        headers=auth_header,
    )
    assert r.status_code == 400
    assert "repository not found" in r.json()["detail"]


async def test_git_import_replaces_existing(client, auth_header, data_root, project, monkeypatch):
    monkeypatch.setattr(
        source_repo_lib, "clone_git_repo", _fake_clone_factory({"a.txt": "first"})
    )
    await client.post(
        f"/api/projects/{project}/inputs/git",
        json={"git_url": "https://example.com/r.git"},
        headers=auth_header,
    )

    monkeypatch.setattr(
        source_repo_lib, "clone_git_repo", _fake_clone_factory({"b.txt": "second"})
    )
    r = await client.post(
        f"/api/projects/{project}/inputs/git",
        json={"git_url": "https://example.com/r.git"},
        headers=auth_header,
    )
    assert r.status_code == 200
    src = data_root / "projects" / project / "inputs" / "source_repo"
    assert (src / "b.txt").read_text(encoding="utf-8") == "second"
    assert not (src / "a.txt").exists()


async def test_git_import_secrets_refused(client, auth_header, data_root, project, monkeypatch):
    monkeypatch.setattr(
        source_repo_lib,
        "clone_git_repo",
        _fake_clone_factory({"README.md": "ok\n", ".env": "DB_PASS=hunter2\n"}),
    )
    r = await client.post(
        f"/api/projects/{project}/inputs/git",
        json={"git_url": "https://example.com/r.git"},
        headers=auth_header,
    )
    assert r.status_code == 409
    body = r.json()
    assert any(f["path"] == ".env" for f in body["findings"])
    src = data_root / "projects" / project / "inputs" / "source_repo"
    assert not src.exists()
    meta = data_root / "projects" / project / "inputs" / "source_repo.meta.json"
    assert not meta.exists()


async def test_git_import_secrets_force_strips(client, auth_header, data_root, project, monkeypatch):
    monkeypatch.setattr(
        source_repo_lib,
        "clone_git_repo",
        _fake_clone_factory({"README.md": "ok\n", ".env": "DB_PASS=hunter2\n"}),
    )
    r = await client.post(
        f"/api/projects/{project}/inputs/git",
        json={"git_url": "https://example.com/r.git", "force": True},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert any(w["path"] == ".env" for w in body["warnings"])
    src = data_root / "projects" / project / "inputs" / "source_repo"
    assert (src / "README.md").exists()
    assert not (src / ".env").exists()


# ── Source repo: local path ───────────────────────────────────────────────


@pytest.fixture
def local_src(tmp_path: Path) -> Path:
    src = tmp_path / "external_src"
    (src / "src").mkdir(parents=True)
    (src / "README.md").write_text("# from disk\n", encoding="utf-8")
    (src / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    return src


async def test_local_path_happy_path(client, auth_header, data_root, project, local_src):
    r = await client.post(
        f"/api/projects/{project}/inputs/local_path",
        json={"path": str(local_src)},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["source_repo"]["kind"] == "local_path"
    assert body["source_repo"]["path"] == str(local_src)
    assert body["warnings"] == []

    dest = data_root / "projects" / project / "inputs" / "source_repo"
    assert (dest / "README.md").read_text(encoding="utf-8") == "# from disk\n"
    assert (dest / "src" / "main.py").exists()


async def test_local_path_requires_path(client, auth_header, project):
    r = await client.post(
        f"/api/projects/{project}/inputs/local_path",
        json={},
        headers=auth_header,
    )
    assert r.status_code == 400


async def test_local_path_rejects_relative(client, auth_header, project):
    r = await client.post(
        f"/api/projects/{project}/inputs/local_path",
        json={"path": "./relative/dir"},
        headers=auth_header,
    )
    assert r.status_code == 400


async def test_local_path_missing_dir(client, auth_header, project, tmp_path):
    r = await client.post(
        f"/api/projects/{project}/inputs/local_path",
        json={"path": str(tmp_path / "does_not_exist")},
        headers=auth_header,
    )
    assert r.status_code == 400


async def test_local_path_requires_auth(client, project, local_src):
    r = await client.post(
        f"/api/projects/{project}/inputs/local_path",
        json={"path": str(local_src)},
    )
    assert r.status_code == 401


async def test_local_path_unknown_project(client, auth_header, local_src):
    r = await client.post(
        "/api/projects/ghost/inputs/local_path",
        json={"path": str(local_src)},
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_local_path_secrets_refused(client, auth_header, data_root, project, local_src):
    (local_src / ".env").write_text("DB=hunter2", encoding="utf-8")
    r = await client.post(
        f"/api/projects/{project}/inputs/local_path",
        json={"path": str(local_src)},
        headers=auth_header,
    )
    assert r.status_code == 409
    dest = data_root / "projects" / project / "inputs" / "source_repo"
    assert not dest.exists()


async def test_local_path_secrets_force_strips(client, auth_header, data_root, project, local_src):
    (local_src / ".env").write_text("DB=hunter2", encoding="utf-8")
    r = await client.post(
        f"/api/projects/{project}/inputs/local_path",
        json={"path": str(local_src), "force": True},
        headers=auth_header,
    )
    assert r.status_code == 200
    dest = data_root / "projects" / project / "inputs" / "source_repo"
    assert (dest / "README.md").exists()
    assert not (dest / ".env").exists()


async def test_local_path_allowlist_blocks_outside(client, auth_header, project, local_src, monkeypatch):
    # Allowlist points elsewhere — local_src is not under it.
    monkeypatch.setenv("GUUDFLOW_LOCAL_PATH_ALLOWLIST", str(local_src.parent.parent / "other"))
    r = await client.post(
        f"/api/projects/{project}/inputs/local_path",
        json={"path": str(local_src)},
        headers=auth_header,
    )
    assert r.status_code == 403


async def test_local_path_allowlist_permits_inside(client, auth_header, project, local_src, monkeypatch):
    monkeypatch.setenv("GUUDFLOW_LOCAL_PATH_ALLOWLIST", str(local_src.parent))
    r = await client.post(
        f"/api/projects/{project}/inputs/local_path",
        json={"path": str(local_src)},
        headers=auth_header,
    )
    assert r.status_code == 200


async def test_local_path_allowlist_unset_permits(client, auth_header, project, local_src, monkeypatch):
    monkeypatch.delenv("GUUDFLOW_LOCAL_PATH_ALLOWLIST", raising=False)
    r = await client.post(
        f"/api/projects/{project}/inputs/local_path",
        json={"path": str(local_src)},
        headers=auth_header,
    )
    assert r.status_code == 200


# ── List surfaces source_repo metadata ─────────────────────────────────────


async def test_list_includes_source_repo_after_local_import(
    client, auth_header, project, local_src
):
    await client.post(
        f"/api/projects/{project}/inputs/local_path",
        json={"path": str(local_src)},
        headers=auth_header,
    )
    r = await client.get(f"/api/projects/{project}/inputs")
    assert r.status_code == 200
    body = r.json()
    assert body["source_repo"]["kind"] == "local_path"
    assert body["source_repo"]["path"] == str(local_src)


async def test_list_source_repo_null_when_absent(client, project):
    r = await client.get(f"/api/projects/{project}/inputs")
    assert r.json()["source_repo"] is None


# ── Continuation: attach / detach a prior run ──────────────────────────────


@pytest.fixture
def prior_run(write_run, data_root):
    """Materialise a run with deliverables on disk and return its run_id."""
    def _make(run_id: str = "run_xyz", project_id: str = "p1", files: dict | None = None):
        run_dir = write_run(run_id, project_id=project_id)
        deliverables = run_dir / "workspace" / "deliverables"
        files = files if files is not None else {"app.py": "print('hi')\n"}
        for rel, content in files.items():
            target = deliverables / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return run_id
    return _make


async def test_attach_prior_run_happy_path(client, auth_header, data_root, project, prior_run):
    run_id = prior_run()
    r = await client.post(
        f"/api/projects/{project}/inputs/from_run/{run_id}",
        json={},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["prior_run"]["kind"] == "prior_run"
    assert body["prior_run"]["run_id"] == run_id
    assert body["prior_run"]["has_feedback"] is False

    landed = data_root / "projects" / project / "inputs" / f"from_run_{run_id}" / "deliverables" / "app.py"
    assert landed.read_text(encoding="utf-8") == "print('hi')\n"
    assert (data_root / "projects" / project / "inputs" / f"from_run_{run_id}.meta.json").exists()


async def test_attach_prior_run_with_feedback(client, auth_header, data_root, project, prior_run):
    run_id = prior_run()
    r = await client.post(
        f"/api/projects/{project}/inputs/from_run/{run_id}",
        json={"feedback": "Token expiry was not enforced. Please address."},
        headers=auth_header,
    )
    assert r.status_code == 200
    assert r.json()["prior_run"]["has_feedback"] is True
    feedback = data_root / "projects" / project / "inputs" / f"from_run_{run_id}" / "feedback.md"
    assert "Token expiry" in feedback.read_text(encoding="utf-8")


async def test_attach_prior_run_unknown_run(client, auth_header, project):
    r = await client.post(
        f"/api/projects/{project}/inputs/from_run/run_ghost",
        json={},
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_attach_prior_run_no_deliverables(client, auth_header, data_root, project, write_run):
    run_dir = write_run("run_empty")
    # write_run creates an empty deliverables dir; remove it to simulate missing.
    import shutil
    shutil.rmtree(run_dir / "workspace" / "deliverables")
    r = await client.post(
        f"/api/projects/{project}/inputs/from_run/run_empty",
        json={},
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_attach_prior_run_unknown_project(client, auth_header, prior_run):
    run_id = prior_run()
    r = await client.post(
        f"/api/projects/ghost/inputs/from_run/{run_id}",
        json={},
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_attach_prior_run_requires_auth(client, project, prior_run):
    run_id = prior_run()
    r = await client.post(
        f"/api/projects/{project}/inputs/from_run/{run_id}",
        json={},
    )
    assert r.status_code == 401


async def test_attach_prior_run_replaces_existing(client, auth_header, data_root, project, prior_run):
    run_id = prior_run(files={"v1.py": "first"})
    await client.post(
        f"/api/projects/{project}/inputs/from_run/{run_id}",
        json={"feedback": "first round"},
        headers=auth_header,
    )
    # Re-attach without feedback — feedback file should disappear too.
    r = await client.post(
        f"/api/projects/{project}/inputs/from_run/{run_id}",
        json={},
        headers=auth_header,
    )
    assert r.status_code == 200
    attached = data_root / "projects" / project / "inputs" / f"from_run_{run_id}"
    assert not (attached / "feedback.md").exists()


async def test_detach_prior_run(client, auth_header, data_root, project, prior_run):
    run_id = prior_run()
    await client.post(
        f"/api/projects/{project}/inputs/from_run/{run_id}",
        json={},
        headers=auth_header,
    )
    r = await client.delete(
        f"/api/projects/{project}/inputs/from_run/{run_id}",
        headers=auth_header,
    )
    assert r.status_code == 204
    inputs = data_root / "projects" / project / "inputs"
    assert not (inputs / f"from_run_{run_id}").exists()
    assert not (inputs / f"from_run_{run_id}.meta.json").exists()


async def test_detach_prior_run_not_attached(client, auth_header, project):
    r = await client.delete(
        f"/api/projects/{project}/inputs/from_run/run_ghost",
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_detach_prior_run_requires_auth(client, project, prior_run, auth_header):
    run_id = prior_run()
    await client.post(
        f"/api/projects/{project}/inputs/from_run/{run_id}",
        json={},
        headers=auth_header,
    )
    r = await client.delete(f"/api/projects/{project}/inputs/from_run/{run_id}")
    assert r.status_code == 401


async def test_list_includes_prior_runs(client, auth_header, project, prior_run):
    run_id_a = prior_run(run_id="run_a", files={"a.py": "1"})
    run_id_b = prior_run(run_id="run_b", files={"b.py": "2"})
    await client.post(
        f"/api/projects/{project}/inputs/from_run/{run_id_a}",
        json={"feedback": "fix a"},
        headers=auth_header,
    )
    await client.post(
        f"/api/projects/{project}/inputs/from_run/{run_id_b}",
        json={},
        headers=auth_header,
    )
    r = await client.get(f"/api/projects/{project}/inputs")
    assert r.status_code == 200
    body = r.json()
    run_ids = sorted(p["run_id"] for p in body["prior_runs"])
    assert run_ids == ["run_a", "run_b"]
    by_id = {p["run_id"]: p for p in body["prior_runs"]}
    assert by_id["run_a"]["has_feedback"] is True
    assert by_id["run_b"]["has_feedback"] is False
