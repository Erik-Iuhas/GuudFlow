"""Track 4 — core/user_settings.py: atomic write, allow-list, corrupted-file safety."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import storage, user_settings


@pytest.fixture
def temp_settings(monkeypatch, tmp_path):
    """Point the settings file at a tmp location for each test."""
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(storage, "_USER_SETTINGS_PATH", settings_path)
    storage.reset_storage_config_for_tests()
    return settings_path


def test_read_missing_file_returns_empty(temp_settings):
    assert user_settings.read_settings() == {}


def test_write_persists_allowed_keys(temp_settings):
    result = user_settings.write_settings({
        "storage_mode": "in-directory",
        "storage_root": "/some/path",
    })
    assert result == {"storage_mode": "in-directory", "storage_root": "/some/path"}
    # Re-read from disk to confirm it landed.
    on_disk = json.loads(temp_settings.read_text())
    assert on_disk == result


def test_write_drops_unknown_keys(temp_settings):
    """Disallowed keys silently dropped — keeps malicious/malformed PUT from polluting."""
    result = user_settings.write_settings({
        "storage_mode": "global",
        "evil_key": "should be ignored",
        "another_unknown": 42,
    })
    assert result == {"storage_mode": "global"}
    assert "evil_key" not in result


def test_write_merges_with_existing(temp_settings):
    """Successive writes merge rather than replace."""
    user_settings.write_settings({"storage_mode": "in-directory"})
    user_settings.write_settings({"storage_root": "/path/to/dir"})
    final = user_settings.read_settings()
    assert final == {"storage_mode": "in-directory", "storage_root": "/path/to/dir"}


def test_write_none_value_removes_key(temp_settings):
    user_settings.write_settings({"storage_mode": "in-directory", "storage_root": "/p"})
    user_settings.write_settings({"storage_root": None})
    final = user_settings.read_settings()
    assert "storage_mode" in final
    assert "storage_root" not in final


def test_atomic_write_no_partial_file(temp_settings, monkeypatch):
    """If write fails partway, the existing file is not corrupted.

    Simulate by making os.replace fail after the tempfile is written.
    """
    user_settings.write_settings({"storage_mode": "global"})
    original = user_settings.read_settings()

    import os
    real_replace = os.replace

    call_count = [0]

    def failing_replace(*args, **kwargs):
        call_count[0] += 1
        raise OSError("simulated failure")

    monkeypatch.setattr("core.user_settings.os.replace", failing_replace)
    with pytest.raises(OSError):
        user_settings.write_settings({"storage_mode": "in-directory"})

    # Original file unchanged.
    assert user_settings.read_settings() == original


def test_read_corrupted_file_returns_empty(temp_settings):
    """Malformed JSON should not raise — settings are advisory."""
    temp_settings.write_text("not valid json {{{")
    assert user_settings.read_settings() == {}


def test_write_creates_parent_directory(temp_settings, tmp_path):
    """If ~/.guudflow/ doesn't exist yet, write_settings creates it."""
    nested = tmp_path / "subdir" / "settings.json"
    import core.user_settings as us
    import core.storage as st
    st._USER_SETTINGS_PATH = nested  # point at deeper nested path
    st.reset_storage_config_for_tests()

    user_settings.write_settings({"storage_mode": "global"})
    assert nested.exists()
    assert nested.parent.is_dir()


def test_clear_settings_removes_file(temp_settings):
    user_settings.write_settings({"storage_mode": "global"})
    assert temp_settings.exists()
    user_settings.clear_settings()
    assert not temp_settings.exists()


def test_clear_settings_missing_file_no_op(temp_settings):
    """clear_settings on a non-existent file should not raise."""
    assert not temp_settings.exists()
    user_settings.clear_settings()  # should not raise
