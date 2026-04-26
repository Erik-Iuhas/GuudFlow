"""Tests for core.source_repo — clone/copy helpers used by D-prime Chunk 2."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core import source_repo


# ── copy_local_dir ─────────────────────────────────────────────────────────


def test_copy_local_dir_happy_path(tmp_path: Path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("aaa", encoding="utf-8")
    (src / "sub" / "b.txt").write_text("bbb", encoding="utf-8")

    dest = tmp_path / "dest"
    source_repo.copy_local_dir(src, dest)

    assert (dest / "a.txt").read_text(encoding="utf-8") == "aaa"
    assert (dest / "sub" / "b.txt").read_text(encoding="utf-8") == "bbb"


def test_copy_local_dir_replaces_existing(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "fresh.txt").write_text("new", encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "stale.txt").write_text("old", encoding="utf-8")

    source_repo.copy_local_dir(src, dest)

    assert (dest / "fresh.txt").read_text(encoding="utf-8") == "new"
    assert not (dest / "stale.txt").exists()


def test_copy_local_dir_creates_parent(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "x").write_text("x", encoding="utf-8")

    dest = tmp_path / "deep" / "nested" / "dest"
    source_repo.copy_local_dir(src, dest)
    assert (dest / "x").read_text(encoding="utf-8") == "x"


# ── clone_git_repo (subprocess mocked) ─────────────────────────────────────


class _FakeProc:
    def __init__(self, returncode: int, stderr: bytes = b""):
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self):
        return (b"", self._stderr)


def _patch_subprocess(monkeypatch, returncode: int, stderr: bytes = b"", recorder: list | None = None):
    async def fake_create(*args, **kwargs):
        if recorder is not None:
            recorder.append(args)
        return _FakeProc(returncode=returncode, stderr=stderr)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)


async def test_clone_git_repo_runs_git_clone(tmp_path: Path, monkeypatch):
    recorded: list = []
    _patch_subprocess(monkeypatch, returncode=0, recorder=recorded)
    dest = tmp_path / "out"
    await source_repo.clone_git_repo(
        "https://example.com/repo.git",
        branch="main",
        shallow=True,
        dest=dest,
    )
    assert recorded, "subprocess was not invoked"
    args = recorded[0]
    assert args[0] == "git"
    assert args[1] == "clone"
    assert "--depth" in args
    assert "1" in args
    assert "--branch" in args
    assert "main" in args
    assert "https://example.com/repo.git" in args
    assert str(dest) in args


async def test_clone_git_repo_omits_shallow(tmp_path: Path, monkeypatch):
    recorded: list = []
    _patch_subprocess(monkeypatch, returncode=0, recorder=recorded)
    await source_repo.clone_git_repo(
        "https://example.com/repo.git",
        branch="main",
        shallow=False,
        dest=tmp_path / "out",
    )
    assert "--depth" not in recorded[0]


async def test_clone_git_repo_raises_on_nonzero_exit(tmp_path: Path, monkeypatch):
    _patch_subprocess(monkeypatch, returncode=128, stderr=b"fatal: not a git repository\n")
    with pytest.raises(source_repo.SourceCloneError) as exc_info:
        await source_repo.clone_git_repo(
            "https://example.com/repo.git",
            branch="main",
            shallow=True,
            dest=tmp_path / "out",
        )
    assert "fatal" in str(exc_info.value)


async def test_clone_git_repo_replaces_existing(tmp_path: Path, monkeypatch):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "stale.txt").write_text("old", encoding="utf-8")

    _patch_subprocess(monkeypatch, returncode=0)
    await source_repo.clone_git_repo("u", "main", True, dest)
    # Our fake doesn't repopulate; we just assert the prior content was wiped.
    assert not (dest / "stale.txt").exists()
