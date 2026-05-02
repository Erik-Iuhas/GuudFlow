"""Track 4 — /api/settings endpoints."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from core import storage


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with a tmp settings file pointed at by both core.storage."""
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", settings_path)
    storage.reset_storage_config_for_tests()
    monkeypatch.delenv("GUUDFLOW_STORAGE_MODE", raising=False)
    monkeypatch.delenv("GUUDFLOW_STORAGE_ROOT", raising=False)
    monkeypatch.delenv("GUUDFLOW_DATA_DIR", raising=False)

    app = create_app()
    with TestClient(app) as c:
        yield c


# ── GET /api/settings ──────────────────────────────────────────────────────


def test_get_settings_returns_defaults_when_no_settings_file(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["effective"]["storage_mode"] == "global"
    assert data["persisted"] == {}
    assert data["env_overrides_active"] is False


def test_get_settings_reflects_env_override(client, monkeypatch):
    monkeypatch.setenv("GUUDFLOW_STORAGE_MODE", "in-directory")
    storage.reset_storage_config_for_tests()
    resp = client.get("/api/settings")
    data = resp.json()
    assert data["effective"]["storage_mode"] == "in-directory"
    assert data["env_overrides_active"] is True


# ── PUT /api/settings ──────────────────────────────────────────────────────


def test_put_settings_persists_storage_mode(client):
    resp = client.put("/api/settings", json={"storage_mode": "global"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["persisted"]["storage_mode"] == "global"
    assert data["restart_required"] is False  # was already global (defaults)


def test_put_settings_mode_change_hot_reloads_when_idle(client, tmp_path):
    """Track 4.4 — with no in-flight jobs, switching storage mode rebuilds
    the read stores in place; restart_required stays False, hot_reloaded
    is True. Pre-Track-4.4 behaviour was always-restart."""
    client.put("/api/settings", json={"storage_mode": "global"})
    resp = client.put("/api/settings", json={
        "storage_mode": "in-directory",
        "storage_root": str(tmp_path),
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["hot_reloaded"] is True
    assert body["restart_required"] is False


def test_put_settings_rejects_bad_mode(client):
    resp = client.put("/api/settings", json={"storage_mode": "neither"})
    assert resp.status_code == 400
    assert "must be" in resp.json()["detail"]


def test_put_settings_validates_storage_root_when_in_directory(client, tmp_path):
    """Setting storage_root in in-directory mode validates the path."""
    resp = client.put("/api/settings", json={
        "storage_mode": "in-directory",
        "storage_root": "/path/that/definitely/does/not/exist/anywhere",
    })
    assert resp.status_code == 400
    assert "does not exist" in resp.json()["detail"]


def test_put_settings_accepts_valid_storage_root(client, tmp_path):
    resp = client.put("/api/settings", json={
        "storage_mode": "in-directory",
        "storage_root": str(tmp_path),
    })
    assert resp.status_code == 200
    assert resp.json()["persisted"]["storage_root"] == str(tmp_path)


def test_put_settings_skips_root_validation_in_global_mode(client):
    """Saving a placeholder root in global mode is allowed (user can set
    it before switching modes)."""
    resp = client.put("/api/settings", json={
        "storage_mode": "global",
        "storage_root": "/will/exist/later",
    })
    assert resp.status_code == 200


# ── Track 4.4 — hot-reload of storage paths ────────────────────────────────


def test_put_settings_hot_swaps_data_root_on_app_state(client, tmp_path):
    """After PUT, app.state.data_root reflects the new storage_root and the
    project_store reads from the new location."""
    new_base = tmp_path / "new_repo"
    new_base.mkdir()
    resp = client.put("/api/settings", json={
        "storage_mode": "in-directory",
        "storage_root": str(new_base),
    })
    assert resp.status_code == 200
    assert resp.json()["hot_reloaded"] is True

    state = client.app.state  # type: ignore[attr-defined]
    assert Path(state.data_root) == (new_base / ".flow").resolve()
    # Project store is rebuilt; its root must point at the new location.
    assert Path(state.project_store.root) == (new_base / ".flow").resolve()
    # Run store rooted under <new>/.flow/runs.
    assert Path(state.run_store.runs_root) == (new_base / ".flow" / "runs").resolve()


def test_put_settings_refuses_hot_swap_when_job_in_flight(
    client, tmp_path, monkeypatch,
):
    """If a job is queued or running, hot-swap is refused; restart_required
    flips True so the user knows to restart once the job finishes."""
    from worker.queue import Job, JOB_RUNNING

    class _StubQueue:
        async def list(self, limit=50):
            return [Job(
                job_id="j1", run_id="r1", project_id="p1",
                status=JOB_RUNNING,
            )]

    client.app.state.queue = _StubQueue()  # type: ignore[attr-defined]

    new_base = tmp_path / "new_repo"
    new_base.mkdir()
    resp = client.put("/api/settings", json={
        "storage_mode": "in-directory",
        "storage_root": str(new_base),
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["hot_reloaded"] is False
    assert body["restart_required"] is True


def test_put_settings_repeat_same_value_still_hot_reloads(client):
    """PUT with the same storage_mode is idempotent and still hot-reloads
    (cheap rebuild). restart_required stays False either way."""
    resp = client.put("/api/settings", json={"storage_mode": "global"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["hot_reloaded"] is True
    assert body["restart_required"] is False


# ── POST /api/settings/validate-storage-root ───────────────────────────────


def test_validate_storage_root_valid_path(client, tmp_path):
    resp = client.post("/api/settings/validate-storage-root", json={"path": str(tmp_path)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["exists"] is True
    assert data["writable"] is True


def test_validate_storage_root_missing_path(client):
    resp = client.post("/api/settings/validate-storage-root", json={"path": "/does/not/exist"})
    data = resp.json()
    assert data["valid"] is False
    assert data["exists"] is False


def test_validate_storage_root_file_not_dir(client, tmp_path):
    f = tmp_path / "afile.txt"
    f.write_text("not a dir")
    resp = client.post("/api/settings/validate-storage-root", json={"path": str(f)})
    data = resp.json()
    assert data["valid"] is False
    assert data["exists"] is True
    assert "not a directory" in data["reason"]
