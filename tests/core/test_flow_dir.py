"""Track 4 — core/flow_dir.py: idempotent scaffold + safety checks."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.flow_dir import FlowDirError, SCHEMA_VERSION, init_flow_dir


def test_init_creates_expected_structure(tmp_path):
    flow = init_flow_dir(tmp_path)
    assert flow == tmp_path / ".flow"
    assert flow.is_dir()
    assert (flow / "projects").is_dir()
    assert (flow / "runs").is_dir()
    assert (flow / "config.toml").is_file()
    assert (flow / ".gitignore").is_file()


def test_config_toml_contains_schema_version(tmp_path):
    flow = init_flow_dir(tmp_path)
    contents = (flow / "config.toml").read_text(encoding="utf-8")
    assert f"schema_version = {SCHEMA_VERSION}" in contents
    assert "created_at" in contents


def test_gitignore_excludes_scratch(tmp_path):
    flow = init_flow_dir(tmp_path)
    contents = (flow / ".gitignore").read_text(encoding="utf-8")
    assert "scratch/" in contents


def test_idempotent_on_initialised_dir(tmp_path):
    """Calling on already-initialised .flow/ is a no-op (no exception)."""
    init_flow_dir(tmp_path)
    # Mark the config.toml so we can detect overwrites.
    config = tmp_path / ".flow" / "config.toml"
    config.write_text(config.read_text() + "# touched by test\n")
    marker_content = config.read_text()

    init_flow_dir(tmp_path)  # second call

    # Marker still there — file was not regenerated.
    assert config.read_text() == marker_content


def test_refuses_to_overwrite_non_flow_directory(tmp_path):
    """If .flow/ exists with content but no config.toml, refuse to operate."""
    fake_flow = tmp_path / ".flow"
    fake_flow.mkdir()
    (fake_flow / "important_user_data.txt").write_text("data")

    with pytest.raises(FlowDirError, match="Refusing to overwrite"):
        init_flow_dir(tmp_path)


def test_initialises_empty_existing_flow_dir(tmp_path):
    """An empty `.flow/` (no content) is treated as fresh — initialise it."""
    empty_flow = tmp_path / ".flow"
    empty_flow.mkdir()
    init_flow_dir(tmp_path)
    assert (empty_flow / "config.toml").is_file()


def test_target_must_exist(tmp_path):
    nonexistent = tmp_path / "nope"
    with pytest.raises(FlowDirError, match="does not exist"):
        init_flow_dir(nonexistent)


def test_target_must_be_a_directory(tmp_path):
    file_target = tmp_path / "file.txt"
    file_target.write_text("not a dir")
    with pytest.raises(FlowDirError, match="must be a directory"):
        init_flow_dir(file_target)
