"""Tests for core.project_checks (plan §14.3): the project's check on a run's code."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.project_checks import run_project_check


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work, check=True)
    for name, text in files.items():
        (work / name).write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "x"],
                   cwd=work, check=True)
    (work / "uncommitted.txt").write_text("not part of HEAD")
    return work


async def test_host_check_passes_fails_and_times_out(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    work = _repo(tmp_path, {"ok.sh": "exit 0\n"})
    ok = await run_project_check(work, "sh ok.sh && ls", sandbox="host")
    assert ok.status == "passed" and ok.where == "host"
    assert "ok.sh" in ok.output and "uncommitted.txt" not in ok.output   # HEAD only
    env = await run_project_check(work, "env", sandbox="host")
    assert "sk-secret" not in env.output and "NO_NETWORK=1" in env.output
    bad = await run_project_check(work, "echo broken; exit 3", sandbox="host")
    assert bad.status == "failed" and bad.exit_code == 3 and "broken" in bad.output
    slow = await run_project_check(work, "sleep 5", sandbox="host", timeout=0.5)
    assert slow.status == "timed_out"
    assert not (work / ".pytest_cache").exists()                           # repo untouched


async def test_host_checks_are_refused_in_production(tmp_path: Path, monkeypatch) -> None:
    import worker.sandbox_manager as sm
    monkeypatch.setattr(sm.SandboxManager, "available", staticmethod(lambda: False))
    work = _repo(tmp_path, {"a.txt": "a"})
    result = await run_project_check(work, "true", sandbox="auto", production=True)
    assert result.status == "skipped" and "Docker" in result.reason


def _docker() -> bool:
    from worker.sandbox_manager import DEFAULT_IMAGE, SandboxManager
    return SandboxManager.available() and SandboxManager()._image_exists(DEFAULT_IMAGE)  # noqa: SLF001


@pytest.mark.skipif(not _docker(), reason="Docker or the sandbox image is not available")
async def test_docker_check_has_no_network(tmp_path: Path) -> None:
    work = _repo(tmp_path, {"t.py": "print('tests ran')\n"})
    ok = await run_project_check(work, "python3 t.py", sandbox="docker")
    assert ok.status == "passed" and ok.where == "docker" and "tests ran" in ok.output
    net = await run_project_check(
        work, "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\"",
        sandbox="docker")
    assert net.status == "failed"
