"""Tests for /api/projects/{id}/inputs and the per-project inputs storage layer.

DESIGN §33 Chunk 1 — uploads + list + delete + project-dir lifecycle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.storage import ProjectStore


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
