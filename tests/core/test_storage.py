"""Track 4 — core/storage.py: mode toggle, env var precedence, path resolution."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core import storage


@pytest.fixture(autouse=True)
def _reset_storage_cache():
    """Each test starts with a clean cache so env-var changes take effect."""
    storage.reset_storage_config_for_tests()
    yield
    storage.reset_storage_config_for_tests()


# ── Default behaviour (global mode) ────────────────────────────────────────


def test_default_mode_is_global(monkeypatch):
    """No env vars, no settings file → global mode at data/."""
    monkeypatch.delenv("GUUDFLOW_STORAGE_MODE", raising=False)
    monkeypatch.delenv("GUUDFLOW_STORAGE_ROOT", raising=False)
    monkeypatch.delenv("GUUDFLOW_DATA_ROOT", raising=False)
    monkeypatch.delenv("GUUDFLOW_PERSONAS_ROOT", raising=False)
    # Point user-settings path at a non-existent file so settings file is empty.
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))

    cfg = storage.get_storage_config()
    assert cfg.mode == "global"
    assert cfg.storage_root == Path("data")
    assert cfg.personas_root == Path("data/personas")


def test_global_mode_paths_under_data(monkeypatch):
    monkeypatch.delenv("GUUDFLOW_STORAGE_MODE", raising=False)
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))
    storage.reset_storage_config_for_tests()

    assert storage.data_root() == Path("data")
    assert storage.projects_root() == Path("data/projects")
    assert storage.project_dir("foo") == Path("data/projects/foo")
    assert storage.runs_root() == Path("data/runs")
    assert storage.run_dir("run_xyz") == Path("data/runs/run_xyz")


# ── In-directory mode ──────────────────────────────────────────────────────


def test_in_directory_mode_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GUUDFLOW_STORAGE_MODE", "in-directory")
    monkeypatch.setenv("GUUDFLOW_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))

    cfg = storage.get_storage_config()
    assert cfg.mode == "in-directory"
    assert cfg.storage_root == tmp_path / ".flow"
    assert cfg.storage_base == tmp_path
    # Default working-tree mode is isolated.
    assert cfg.working_tree_mode == "isolated"


# ── Track 4.3 — working_tree_mode ──────────────────────────────────────────


def test_working_tree_mode_default_is_isolated(monkeypatch):
    monkeypatch.delenv("GUUDFLOW_STORAGE_MODE", raising=False)
    monkeypatch.delenv("GUUDFLOW_WORKING_TREE_MODE", raising=False)
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))
    cfg = storage.get_storage_config()
    assert cfg.working_tree_mode == "isolated"


def test_working_tree_mode_in_place_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GUUDFLOW_STORAGE_MODE", "in-directory")
    monkeypatch.setenv("GUUDFLOW_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("GUUDFLOW_WORKING_TREE_MODE", "in-place")
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))
    cfg = storage.get_storage_config()
    assert cfg.working_tree_mode == "in-place"
    assert cfg.storage_base == tmp_path
    assert storage.working_tree_mode() == "in-place"


def test_working_tree_mode_forced_isolated_in_global_mode(monkeypatch):
    """Global mode has no chosen base directory, so in-place is meaningless
    and must be forced back to isolated."""
    monkeypatch.delenv("GUUDFLOW_STORAGE_MODE", raising=False)
    monkeypatch.setenv("GUUDFLOW_WORKING_TREE_MODE", "in-place")
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))
    cfg = storage.get_storage_config()
    assert cfg.mode == "global"
    assert cfg.working_tree_mode == "isolated"


def test_working_tree_mode_invalid_value_raises(monkeypatch):
    monkeypatch.setenv("GUUDFLOW_WORKING_TREE_MODE", "weird")
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))
    with pytest.raises(ValueError, match="working_tree_mode"):
        storage.get_storage_config()


def test_in_directory_mode_defaults_storage_root_to_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv("GUUDFLOW_STORAGE_MODE", "in-directory")
    monkeypatch.delenv("GUUDFLOW_STORAGE_ROOT", raising=False)
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))
    monkeypatch.chdir(tmp_path)

    cfg = storage.get_storage_config()
    assert cfg.storage_root == tmp_path / ".flow"


def test_in_directory_paths_under_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("GUUDFLOW_STORAGE_MODE", "in-directory")
    monkeypatch.setenv("GUUDFLOW_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))
    storage.reset_storage_config_for_tests()

    assert storage.data_root() == tmp_path / ".flow"
    assert storage.projects_root() == tmp_path / ".flow" / "projects"
    assert storage.project_dir("myproj") == tmp_path / ".flow" / "projects" / "myproj"
    assert storage.runs_root() == tmp_path / ".flow" / "runs"
    assert storage.run_dir("r1") == tmp_path / ".flow" / "runs" / "r1"


# ── Personas always global ──────────────────────────────────────────────────


def test_personas_root_stays_global_in_in_directory_mode(monkeypatch, tmp_path):
    """Personas cross directories — they must NOT move when storage_mode changes."""
    monkeypatch.setenv("GUUDFLOW_STORAGE_MODE", "in-directory")
    monkeypatch.setenv("GUUDFLOW_STORAGE_ROOT", str(tmp_path))
    monkeypatch.delenv("GUUDFLOW_PERSONAS_ROOT", raising=False)
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))
    storage.reset_storage_config_for_tests()

    cfg = storage.get_storage_config()
    # Personas root should NOT be under tmp_path/.flow/.
    assert cfg.personas_root == Path("data/personas")
    assert tmp_path not in cfg.personas_root.parents


def test_personas_root_overridable_via_env(monkeypatch):
    monkeypatch.setenv("GUUDFLOW_PERSONAS_ROOT", "/custom/personas")
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))
    storage.reset_storage_config_for_tests()

    cfg = storage.get_storage_config()
    assert cfg.personas_root == Path("/custom/personas")


# ── Validation & errors ───────────────────────────────────────────────────


def test_invalid_mode_raises(monkeypatch):
    monkeypatch.setenv("GUUDFLOW_STORAGE_MODE", "neither-mode-nor-the-other")
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))
    storage.reset_storage_config_for_tests()

    with pytest.raises(ValueError, match="storage mode must be"):
        storage.get_storage_config()


# ── Settings file precedence ───────────────────────────────────────────────


def test_settings_file_used_when_no_env(monkeypatch, tmp_path):
    """User settings file is consulted when env vars aren't set."""
    monkeypatch.delenv("GUUDFLOW_STORAGE_MODE", raising=False)
    monkeypatch.delenv("GUUDFLOW_STORAGE_ROOT", raising=False)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "storage_mode": "in-directory",
        "storage_root": str(tmp_path / "myrepo"),
    }))
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", settings_file)
    storage.reset_storage_config_for_tests()

    cfg = storage.get_storage_config()
    assert cfg.mode == "in-directory"
    assert cfg.storage_root == tmp_path / "myrepo" / ".flow"


def test_env_var_beats_settings_file(monkeypatch, tmp_path):
    """Env var precedence is highest — settings file is overridden."""
    monkeypatch.setenv("GUUDFLOW_STORAGE_MODE", "global")
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({
        "storage_mode": "in-directory",
        "storage_root": str(tmp_path),
    }))
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", settings_file)
    storage.reset_storage_config_for_tests()

    cfg = storage.get_storage_config()
    assert cfg.mode == "global"


def test_corrupted_settings_file_falls_back_to_defaults(monkeypatch, tmp_path):
    """A malformed settings.json must not prevent startup."""
    monkeypatch.delenv("GUUDFLOW_STORAGE_MODE", raising=False)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("not-valid-json{{{")
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", settings_file)
    storage.reset_storage_config_for_tests()

    # Should not raise — just falls back to defaults.
    cfg = storage.get_storage_config()
    assert cfg.mode == "global"


# ── Caching ────────────────────────────────────────────────────────────────


def test_config_is_cached(monkeypatch):
    """get_storage_config() returns the same instance on subsequent calls."""
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))
    cfg1 = storage.get_storage_config()
    cfg2 = storage.get_storage_config()
    assert cfg1 is cfg2


def test_reset_clears_cache(monkeypatch, tmp_path):
    """reset_storage_config_for_tests() rebuilds on next call."""
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", Path("/nonexistent/settings.json"))
    monkeypatch.delenv("GUUDFLOW_STORAGE_MODE", raising=False)
    cfg1 = storage.get_storage_config()
    assert cfg1.mode == "global"

    monkeypatch.setenv("GUUDFLOW_STORAGE_MODE", "in-directory")
    monkeypatch.setenv("GUUDFLOW_STORAGE_ROOT", str(tmp_path))
    storage.reset_storage_config_for_tests()
    cfg2 = storage.get_storage_config()
    assert cfg2.mode == "in-directory"
    assert cfg1 is not cfg2


# ── user_settings_path ─────────────────────────────────────────────────────


def test_user_settings_path_is_in_home():
    p = storage.user_settings_path()
    assert p.name == "settings.json"
    assert p.parent.name == ".guudflow"
    assert p.parent.parent == Path.home()
