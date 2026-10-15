"""Tests for /api/invoke."""

from __future__ import annotations


async def test_invoke_unknown_project_404(client, auth_header):
    r = await client.post("/api/invoke/ghost", json={}, headers=auth_header)
    assert r.status_code == 404


async def test_invoke_returns_202_with_run_id(client, auth_header):
    """DESIGN §10: invoke returns 202 immediately with run_id.

    Phase C.0.5: with the default `pre_run_cost_preview` gate on, the
    second POST (with confirm: true) is the one that returns 202.
    """
    await client.post("/api/projects", json={
        "project_id": "p1", "goal": "x",
    }, headers=auth_header)
    r = await client.post(
        "/api/invoke/p1",
        json={"context": "test", "confirm": True},
        headers=auth_header,
    )
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] is True
    assert body["run_id"].startswith("run_")
    assert body["job_id"].startswith("job_")
    assert body["project_id"] == "p1"


async def test_invoke_returns_cost_preview_when_gate_on(client, auth_header):
    """Phase C.0.5: with `pre_run_cost_preview` on (the baseline), the
    first POST returns 200 with `requires_approval: true` + a cost
    estimate. The run is NOT enqueued on this leg."""
    await client.post(
        "/api/projects",
        json={"project_id": "p_preview", "goal": "x"},
        headers=auth_header,
    )
    r = await client.post(
        "/api/invoke/p_preview",
        json={},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is False
    assert body["requires_approval"] is True
    assert body["gate"] == "pre_run_cost_preview"
    assert "estimated_cost" in body
    assert "total_usd" in body["estimated_cost"]


async def test_invoke_skips_preview_when_gate_disabled(client, auth_header):
    """Project explicitly disables pre_run_cost_preview → first POST
    enqueues immediately."""
    await client.post(
        "/api/projects",
        json={
            "project_id": "p_no_gate",
            "goal": "x",
            "approval_gates": {
                "pre_run_cost_preview": False,
            },
        },
        headers=auth_header,
    )
    r = await client.post(
        "/api/invoke/p_no_gate",
        json={},
        headers=auth_header,
    )
    assert r.status_code == 202
    assert r.json()["accepted"] is True


async def test_invoke_asks_first_when_a_test_runner_is_missing(client, auth_header,
                                                                monkeypatch):
    """Run findings F3: a runner the project names but agents cannot run is reported before
    the run, even with the cost preview off; `confirm: true` starts anyway."""
    from core.harness import runtime_check

    seen: list[str] = []

    def missing(data_root, project_id, check_command, sandbox=None):
        seen.append(check_command)
        return [runtime_check.MissingRuntime(program="bun", why="spec rule rule-1: Use bun test",
                                             where="this machine")]

    monkeypatch.setattr(runtime_check, "missing_runtimes", missing)
    await client.post("/api/projects", json={
        "project_id": "p_rt", "goal": "x",
        "approval_gates": {"pre_run_cost_preview": False, "check_command": "bun test"},
    }, headers=auth_header)
    r = await client.post("/api/invoke/p_rt", json={}, headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is False and body["gate"] == "missing_runtime"
    assert body["missing_runtimes"] == [{"program": "bun", "why": "spec rule rule-1: Use bun test",
                                         "where": "this machine", "checked": True}]
    assert "bun (this machine)" in body["message"]
    assert seen == ["bun test"]
    r = await client.post("/api/invoke/p_rt", json={"confirm": True}, headers=auth_header)
    assert r.status_code == 202
    assert seen == ["bun test"]


def test_the_gate_message_tells_missing_from_unchecked() -> None:
    from api.routes.invoke import _missing_message

    text = _missing_message([
        {"program": "bun", "where": "this machine", "checked": True},
        {"program": "pytest", "where": "the sandbox image", "checked": False}])
    assert "not installed where agents run: bun (this machine)." in text
    assert "Not checked (Docker unreachable or the image not built yet): pytest" in text
    assert "not installed" not in _missing_message(
        [{"program": "pytest", "where": "the sandbox image", "checked": False}])


async def test_cost_preview_lists_missing_runners_too(client, auth_header, monkeypatch):
    from core.harness import runtime_check

    monkeypatch.setattr(runtime_check, "missing_runtimes", lambda *a, **k: [
        runtime_check.MissingRuntime(program="pytest", why="the check command `pytest`",
                                     where="the sandbox image")])
    await client.post("/api/projects", json={"project_id": "p_rt2", "goal": "x"},
                      headers=auth_header)
    body = (await client.post("/api/invoke/p_rt2", json={}, headers=auth_header)).json()
    assert body["gate"] == "pre_run_cost_preview"
    assert [m["program"] for m in body["missing_runtimes"]] == ["pytest"]


async def test_invoke_requires_auth(client):
    r = await client.post("/api/invoke/p1", json={})
    assert r.status_code == 401


async def test_invoke_enqueues_job(client, auth_header, app):
    await client.post("/api/projects", json={"project_id": "p1", "goal": "x"},
                      headers=auth_header)
    r = await client.post(
        "/api/invoke/p1",
        json={"confirm": True},
        headers=auth_header,
    )
    body = r.json()

    job = await app.state.queue.get(body["job_id"])
    assert job is not None
    assert job.status == "queued"
    assert job.project_id == "p1"


async def test_job_status_endpoint(client, auth_header):
    await client.post("/api/projects", json={"project_id": "p1", "goal": "x"},
                      headers=auth_header)
    r = await client.post(
        "/api/invoke/p1",
        json={"confirm": True},
        headers=auth_header,
    )
    job_id = r.json()["job_id"]

    r = await client.get(f"/api/invoke/{job_id}/status", headers=auth_header)
    assert r.status_code == 200
    assert r.json()["status"] == "queued"


async def test_job_status_unknown(client, auth_header):
    r = await client.get("/api/invoke/job_ghost/status", headers=auth_header)
    assert r.status_code == 404


async def test_mcp_manifest_lists_projects(client, auth_header):
    await client.post("/api/projects", json={
        "project_id": "p1", "goal": "build a thing",
    }, headers=auth_header)
    r = await client.get("/api/invoke/mcp/manifest")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 1
    assert len(body["tools"]) == 1
    tool = body["tools"][0]
    assert tool["name"] == "guudflow:p1"
    assert "build a thing" in tool["description"]
    assert tool["input_schema"]["type"] == "object"
