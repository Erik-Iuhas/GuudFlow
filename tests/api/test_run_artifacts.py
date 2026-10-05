"""Tests for GET /api/runs/{run_id}/artifact-files and POST /visual_feedback (S33)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.signals import SignalType
from core.workspace import Workspace

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d"
                    "49444154789c6360000002000001e221bc330000000049454e44ae426082")
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<html><body><script>window.x=1</script></body></html>"


@pytest.fixture
def run_root(data_root: Path) -> Path:
    root = data_root / "runs" / "run_a"
    for rel, data in {
        "artifacts/backend/shot.png": PNG,
        "artifacts/backend/pic.svg": SVG,
        "artifacts/backend/page.html": HTML,
        "artifacts/backend/doc.pdf": b"%PDF-1.4",
        "screenshots/frontend/a.png": PNG,
        "visual/T1/diff_1.png": PNG,
        "baselines/tasks/T1.png": PNG,
        "artifacts/backend/notes.txt": b"nope",
        "workspace/secret.png": PNG,
        "meta.json": b"{}",
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


async def test_serves_png_with_nosniff(client, run_root) -> None:
    r = await client.get("/api/runs/run_a/artifact-files/artifacts/backend/shot.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in r.headers
    assert r.content == PNG


@pytest.mark.parametrize("rel", ["screenshots/frontend/a.png", "visual/T1/diff_1.png",
                                 "baselines/tasks/T1.png", "artifacts/backend/doc.pdf"])
async def test_serves_every_visual_folder(client, run_root, rel) -> None:
    r = await client.get(f"/api/runs/run_a/artifact-files/{rel}")
    assert r.status_code == 200


@pytest.mark.parametrize("rel,ctype", [("pic.svg", "image/svg+xml"),
                                       ("page.html", "text/html; charset=utf-8")])
async def test_active_content_is_sandboxed(client, run_root, rel, ctype) -> None:
    r = await client.get(f"/api/runs/run_a/artifact-files/artifacts/backend/{rel}")
    assert r.status_code == 200
    assert r.headers["content-type"] == ctype
    assert r.headers["content-security-policy"].startswith("sandbox")
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("rel", [
    "artifacts/backend/notes.txt",      # extension not allow-listed
    "workspace/secret.png",             # folder not served
    "meta.json",
    "artifacts/missing.png",
    "artifacts/backend",                # a directory
    "artifacts/../meta.json",
    "artifacts/../workspace/secret.png",
    "%2e%2e/%2e%2e/etc/passwd.png",
    "/etc/passwd",
])
async def test_refuses_everything_else(client, run_root, rel) -> None:
    r = await client.get(f"/api/runs/run_a/artifact-files/{rel}")
    assert r.status_code in (404, 405)


async def test_refuses_symlinks(client, run_root, tmp_path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    os.symlink(outside, run_root / "artifacts" / "backend" / "link.png")
    os.symlink(tmp_path, run_root / "artifacts" / "dirlink")
    r = await client.get("/api/runs/run_a/artifact-files/artifacts/backend/link.png")
    assert r.status_code == 404
    r = await client.get("/api/runs/run_a/artifact-files/artifacts/dirlink/outside.png")
    assert r.status_code == 404


async def test_unknown_or_traversing_run_id(client, run_root) -> None:
    assert (await client.get("/api/runs/nope/artifact-files/artifacts/x.png")).status_code == 404
    assert (await client.get("/api/runs/../artifact-files/artifacts/x.png")).status_code == 404


# ── visual feedback ──────────────────────────────────────────────────────────

async def test_visual_feedback_posts_signal(client, app, auth_header) -> None:
    ws = Workspace(run_id="run_v", goal="x")
    app.state.run_registry.runs["run_v"] = ws
    r = await client.post("/api/runs/run_v/visual_feedback", headers=auth_header,
                          json={"request_id": "rq1", "feedback": "[region 1,2,3,4] too wide"})
    assert r.status_code == 200
    sig = [s for s in ws.board if s.type is SignalType.USER_VISUAL_FEEDBACK][-1]
    assert json.loads(sig.detail or "{}") == {"request_id": "rq1",
                                              "feedback": "[region 1,2,3,4] too wide"}
    assert sig.from_user


async def test_visual_feedback_needs_token_and_active_run(client, app, auth_header) -> None:
    body = {"request_id": "rq1", "feedback": "x"}
    assert (await client.post("/api/runs/run_v/visual_feedback", json=body)).status_code == 401
    r = await client.post("/api/runs/gone/visual_feedback", headers=auth_header, json=body)
    assert r.status_code == 410
    r = await client.post("/api/runs/gone/visual_feedback", headers=auth_header,
                          json={"request_id": "r", "feedback": ""})
    assert r.status_code == 422


async def test_run_meta_reports_harness_and_budget(tmp_path, monkeypatch) -> None:
    """The cockpit reads harness and token_budget_usd from GET /api/runs/{id}."""
    from types import SimpleNamespace

    from api.routes.runs import _add_harness_meta

    class Store:
        async def get(self, pid: str) -> dict:
            return {"harness": "legacy", "approval_gates": {"token_budget_usd": 5}} if pid == "old" else {}

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(project_store=Store())))
    old = {"project_id": "old"}
    await _add_harness_meta(request, old)  # type: ignore[arg-type]
    assert old["harness"] == "legacy" and old["token_budget_usd"] == 5
    new = {"project_id": "new"}
    await _add_harness_meta(request, new)  # type: ignore[arg-type]
    assert new["harness"] == "opencode" and new["token_budget_usd"] is None
    assert new["review_column_cap"] == 3   # the board's WIP cap; Studio no longer hardcodes it
