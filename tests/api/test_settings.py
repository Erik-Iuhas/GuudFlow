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


def test_put_settings_mode_change_requires_restart(client):
    """Changing storage_mode flips restart_required to True."""
    client.put("/api/settings", json={"storage_mode": "global"})
    resp = client.put("/api/settings", json={"storage_mode": "in-directory"})
    assert resp.status_code == 200
    assert resp.json()["restart_required"] is True


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
