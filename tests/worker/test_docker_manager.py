"""Tests for worker/docker_manager.py — dev stub + production guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from worker.docker_manager import (
    CONTAINER_LIMITS,
    ContainerHandle,
    DockerManager,
    DockerManagerError,
    ExecResult,
)


# ── Resource limits constants (§29 rule 65) ─────────────────────────────────


def test_container_limits_match_design_spec():
    """DESIGN §29 hardcodes these — they are not user-configurable."""
    assert CONTAINER_LIMITS["network_mode"] == "none"
    assert CONTAINER_LIMITS["mem_limit"] == "512m"
    assert CONTAINER_LIMITS["pids_limit"] == 50
    assert CONTAINER_LIMITS["read_only"] is True
    assert "no-new-privileges" in CONTAINER_LIMITS["security_opt"]


# ── Dev mode (stub) ─────────────────────────────────────────────────────────


async def test_dev_mode_start_returns_stub_handle(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GUUDFLOW_MODE", "dev")
    dm = DockerManager()
    assert dm.is_dev_stub is True

    handle = await dm.start("run_1", tmp_path / "ws")
    assert isinstance(handle, ContainerHandle)
    assert handle.is_stub is True
    assert handle.run_id == "run_1"
    assert handle.container_id.startswith("stub_")


async def test_dev_mode_exec_returns_stub_result(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GUUDFLOW_MODE", "dev")
    dm = DockerManager()
    await dm.start("run_1", tmp_path / "ws")
    res = await dm.exec_in("run_1", "python", "print('x')")
    assert isinstance(res, ExecResult)
    assert res.is_stub is True
    assert res.exit_code == 0


async def test_dev_mode_exec_unknown_run_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GUUDFLOW_MODE", "dev")
    dm = DockerManager()
    with pytest.raises(DockerManagerError, match="no container"):
        await dm.exec_in("ghost_run", "python", "x")


async def test_dev_mode_stop_clears_container(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GUUDFLOW_MODE", "dev")
    dm = DockerManager()
    await dm.start("run_1", tmp_path / "ws")
    assert "run_1" in dm.active_runs()
    await dm.stop("run_1")
    assert "run_1" not in dm.active_runs()


async def test_dev_mode_stop_unknown_is_noop(monkeypatch):
    monkeypatch.setenv("GUUDFLOW_MODE", "dev")
    dm = DockerManager()
    # Should not raise.
    await dm.stop("ghost")


async def test_dev_mode_multiple_runs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GUUDFLOW_MODE", "dev")
    dm = DockerManager()
    await dm.start("a", tmp_path / "a")
    await dm.start("b", tmp_path / "b")
    assert set(dm.active_runs()) == {"a", "b"}


# ── Production mode (without docker available) ─────────────────────────────


def test_production_without_docker_sdk_raises(monkeypatch):
    """If `docker` is importable but the daemon is unreachable, fail
    fast. CLAUDE.md hard rule from §29."""
    monkeypatch.setenv("GUUDFLOW_MODE", "production")

    # Simulate Docker SDK unavailable by injecting a client that errors on ping.
    class FakeBrokenClient:
        def ping(self):
            raise ConnectionError("daemon unreachable")

    # Force the verify path to use our fake client.
    with pytest.raises(DockerManagerError):
        DockerManager(mode="production", client=FakeBrokenClient())


def test_production_with_passing_client_constructs(monkeypatch):
    """Injecting a passing client is the way the real worker hands a
    pre-validated docker client to DockerManager. (The SDK ping is the
    only check at construction time.)"""
    class FakeClient:
        def ping(self):
            return True

    dm = DockerManager(mode="production", client=FakeClient())
    assert dm.is_dev_stub is False


def test_explicit_mode_overrides_env(monkeypatch):
    monkeypatch.setenv("GUUDFLOW_MODE", "production")
    # Explicit mode='dev' wins; we don't hit the production verification.
    dm = DockerManager(mode="dev")
    assert dm.is_dev_stub is True
