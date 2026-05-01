"""M5.C3 — Persona desk CRUD API tests."""

from __future__ import annotations

import pytest


# ── POST creates a file ───────────────────────────────────────────────────


async def test_post_desk_file_creates_file(client, write_persona, data_root, auth_header):
    write_persona("aria-v1")
    r = await client.post(
        "/api/personas/aria-v1/desk",
        json={"filename": "schema.md", "content": "# Schema\nDo the thing.", "triggers": ["schema"]},
        headers=auth_header,
    )
    assert r.status_code == 201
    assert r.json()["filename"] == "schema.md"

    disk = data_root / "personas" / "aria-v1" / "desk" / "schema.md"
    assert disk.exists()
    content = disk.read_text(encoding="utf-8")
    assert "triggers:" in content
    assert '"schema"' in content
    assert "# Schema" in content


async def test_post_desk_file_missing_persona(client, auth_header):
    r = await client.post(
        "/api/personas/ghost-v1/desk",
        json={"filename": "schema.md", "content": "body"},
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_post_desk_file_conflict(client, write_persona, auth_header):
    write_persona("aria-v1")
    payload = {"filename": "schema.md", "content": "body"}
    await client.post("/api/personas/aria-v1/desk", json=payload, headers=auth_header)
    r = await client.post("/api/personas/aria-v1/desk", json=payload, headers=auth_header)
    assert r.status_code == 409
    assert "DELETE first" in r.json()["detail"]


async def test_post_desk_file_invalid_filename(client, write_persona, auth_header):
    write_persona("aria-v1")
    r = await client.post(
        "/api/personas/aria-v1/desk",
        json={"filename": "../etc/passwd.md", "content": "evil"},
        headers=auth_header,
    )
    assert r.status_code == 422


async def test_post_desk_file_invalid_filename_no_md(client, write_persona, auth_header):
    write_persona("aria-v1")
    r = await client.post(
        "/api/personas/aria-v1/desk",
        json={"filename": "noextension", "content": "body"},
        headers=auth_header,
    )
    assert r.status_code == 422


async def test_post_desk_file_too_large(client, write_persona, auth_header):
    write_persona("aria-v1")
    big = "x" * 200_000  # 200 KB
    r = await client.post(
        "/api/personas/aria-v1/desk",
        json={"filename": "big.md", "content": big},
        headers=auth_header,
    )
    assert r.status_code == 413


async def test_post_desk_file_requires_auth(client, write_persona):
    write_persona("aria-v1")
    r = await client.post(
        "/api/personas/aria-v1/desk",
        json={"filename": "schema.md", "content": "body"},
    )
    assert r.status_code in (401, 403)


# ── GET lists files ───────────────────────────────────────────────────────


async def test_get_desk_empty(client, write_persona, auth_header):
    write_persona("aria-v1")
    r = await client.get("/api/personas/aria-v1/desk", headers=auth_header)
    assert r.status_code == 200
    assert r.json() == {"files": []}


async def test_get_desk_lists_posted_file(client, write_persona, auth_header):
    write_persona("aria-v1")
    await client.post(
        "/api/personas/aria-v1/desk",
        json={"filename": "schema.md", "content": "# Doc", "triggers": ["schema"]},
        headers=auth_header,
    )
    r = await client.get("/api/personas/aria-v1/desk", headers=auth_header)
    assert r.status_code == 200
    files = r.json()["files"]
    assert len(files) == 1
    f = files[0]
    assert f["filename"] == "schema.md"
    assert f["triggers"] == ["schema"]
    assert f["body_excerpt"].startswith("# Doc")
    assert f["bytes"] > 0


async def test_get_desk_sorted_by_filename(client, write_persona, auth_header):
    write_persona("aria-v1")
    for name in ["z-file.md", "a-file.md", "m-file.md"]:
        await client.post(
            "/api/personas/aria-v1/desk",
            json={"filename": name, "content": "body", "triggers": ["x"]},
            headers=auth_header,
        )
    r = await client.get("/api/personas/aria-v1/desk", headers=auth_header)
    filenames = [f["filename"] for f in r.json()["files"]]
    assert filenames == sorted(filenames)


async def test_get_desk_missing_persona(client, auth_header):
    r = await client.get("/api/personas/ghost-v1/desk", headers=auth_header)
    assert r.status_code == 404


# ── DELETE removes a file ─────────────────────────────────────────────────


async def test_delete_desk_file(client, write_persona, data_root, auth_header):
    write_persona("aria-v1")
    await client.post(
        "/api/personas/aria-v1/desk",
        json={"filename": "schema.md", "content": "body", "triggers": ["x"]},
        headers=auth_header,
    )
    r = await client.delete("/api/personas/aria-v1/desk/schema.md", headers=auth_header)
    assert r.status_code == 204

    disk = data_root / "personas" / "aria-v1" / "desk" / "schema.md"
    assert not disk.exists()


async def test_delete_desk_file_not_found(client, write_persona, auth_header):
    write_persona("aria-v1")
    r = await client.delete("/api/personas/aria-v1/desk/nonexistent.md", headers=auth_header)
    assert r.status_code == 404


async def test_delete_desk_file_invalid_filename(client, write_persona, auth_header):
    write_persona("aria-v1")
    r = await client.delete("/api/personas/aria-v1/desk/../passwd.md", headers=auth_header)
    assert r.status_code in (404, 422)


async def test_delete_then_list_empty(client, write_persona, auth_header):
    write_persona("aria-v1")
    await client.post(
        "/api/personas/aria-v1/desk",
        json={"filename": "schema.md", "content": "body", "triggers": ["x"]},
        headers=auth_header,
    )
    await client.delete("/api/personas/aria-v1/desk/schema.md", headers=auth_header)
    r = await client.get("/api/personas/aria-v1/desk", headers=auth_header)
    assert r.json() == {"files": []}


async def test_delete_twice_second_is_404(client, write_persona, auth_header):
    write_persona("aria-v1")
    await client.post(
        "/api/personas/aria-v1/desk",
        json={"filename": "schema.md", "content": "body", "triggers": ["x"]},
        headers=auth_header,
    )
    await client.delete("/api/personas/aria-v1/desk/schema.md", headers=auth_header)
    r = await client.delete("/api/personas/aria-v1/desk/schema.md", headers=auth_header)
    assert r.status_code == 404


async def test_get_desk_requires_auth(client, write_persona):
    write_persona("aria-v1")
    r = await client.get("/api/personas/aria-v1/desk")
    assert r.status_code in (401, 403)
