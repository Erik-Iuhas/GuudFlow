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
async def client(app):
    """These routes need the API token now (S33 follow-up): an authenticated client."""
    import httpx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                 headers={"Authorization": "Bearer test-token"}) as c:
        yield c


@pytest.fixture
async def anon(app):
    """A client without the token."""
    import httpx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

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


async def test_visual_feedback_needs_token_and_active_run(client, anon, app,
                                                          auth_header) -> None:
    body = {"request_id": "rq1", "feedback": "x"}
    assert (await anon.post("/api/runs/run_v/visual_feedback", json=body)).status_code == 401
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


async def test_files_need_the_token_or_the_run_cookie(app, anon, client, run_root) -> None:
    url = "/api/runs/run_a/artifact-files/artifacts/backend/shot.png"
    assert (await anon.get(url)).status_code == 401
    grant = await client.post("/api/runs/run_a/artifact-access")
    assert grant.status_code == 200 and grant.json()["expires_at"]
    cookie = grant.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Path=/api/runs/run_a/artifact-files" in cookie
    value = cookie.split(";")[0].split("=", 1)[1]
    ok = await anon.get(url, cookies={"gf_artifacts": value})
    assert ok.status_code == 200 and ok.content == PNG
    # The cookie is bound to its run and its signature.
    other = await anon.get("/api/runs/run_b/artifact-files/artifacts/x.png",
                           cookies={"gf_artifacts": value})
    assert other.status_code == 401
    forged = value.split(".")[0] + ".00"
    assert (await anon.get(url, cookies={"gf_artifacts": forged})).status_code == 401
    assert (await anon.post("/api/runs/run_a/artifact-access")).status_code == 401


def test_artifact_cookie_expires(monkeypatch) -> None:
    from api.auth import ARTIFACT_TTL_SECONDS, artifact_cookie_valid, issue_artifact_cookie
    monkeypatch.setenv("GUUDFLOW_API_TOKEN", "k")
    issued = issue_artifact_cookie("r", now=1000)
    assert issued is not None
    value, _ = issued
    assert artifact_cookie_valid("r", value, now=1000 + ARTIFACT_TTL_SECONDS - 1)
    assert not artifact_cookie_valid("r", value, now=1000 + ARTIFACT_TTL_SECONDS + 1)
    monkeypatch.delenv("GUUDFLOW_API_TOKEN")
    assert issue_artifact_cookie("r") is None   # dev mode: open, nothing to sign


async def test_console_route_returns_lines_and_role_logs(client, auth_header, data_root) -> None:
    from core.harness import run_console
    run_root = data_root / "runs" / "run_console1"
    (run_root / "opencode_home" / "bram").mkdir(parents=True)
    (run_root / "opencode_home" / "bram" / "opencode-serve.log").write_text("boom\n")
    run_console.note(run_root, "runner", "run started")
    r = await client.get("/api/runs/run_console1/console", headers=auth_header)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lines"][0]["text"] == "run started"
    assert body["roles"] == [{"role": "bram", "lines": ["boom"]}]
    assert (await client.get("/api/runs/nope/console", headers=auth_header)).status_code == 404


async def test_wake_overseer_needs_an_active_run(client, auth_header) -> None:
    r = await client.post("/api/runs/not_running/overseer/wake", headers=auth_header,
                          json={"text": "look"})
    assert r.status_code == 410


async def test_wake_overseer_posts_an_escalation_or_says_there_is_none(client, auth_header,
                                                                        app) -> None:
    from core.signals import Signal, SignalType
    from core.workspace import Workspace
    ws = Workspace(run_id="run_w", goal="x")
    app.state.run_registry.runs["run_w"] = ws
    r = await client.post("/api/runs/run_w/overseer/wake", headers=auth_header, json={"text": "x"})
    assert r.status_code == 409 and "no overseer" in r.json()["detail"]
    ws.post_signal(Signal(type=SignalType.TEAM, summary="team: bram, uma, overseer",
                          from_agent="runner"))
    r = await client.post("/api/runs/run_w/overseer/wake", headers=auth_header,
                          json={"text": "it is stuck"})
    assert r.status_code == 200, r.text
    last = ws.board[-1]
    assert last.type is SignalType.ESCALATION and last.from_agent == "owner"
    assert "it is stuck" in last.summary
