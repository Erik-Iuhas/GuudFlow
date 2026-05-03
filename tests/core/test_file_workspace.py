"""Tests for core/file_workspace.py.

Path-safety is the headline test surface here — DESIGN §29 lists path escape
as the #2 threat. Every form of escape attempt must raise PathSafetyError.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from core.file_workspace import FileWorkspace, PathSafetyError


@pytest.fixture
def fw(tmp_path: Path) -> FileWorkspace:
    return FileWorkspace(tmp_path / "ws")


# ── Construction ────────────────────────────────────────────────────────────


def test_constructor_creates_subdirs(tmp_path: Path):
    fw = FileWorkspace(tmp_path / "ws")
    assert (tmp_path / "ws" / "working").is_dir()
    assert (tmp_path / "ws" / "inputs").is_dir()
    assert (tmp_path / "ws" / "deliverables").is_dir()


def test_constructor_idempotent(tmp_path: Path):
    FileWorkspace(tmp_path / "ws")
    FileWorkspace(tmp_path / "ws")  # should not error
    assert (tmp_path / "ws" / "working").is_dir()


# ── Happy-path read/write ───────────────────────────────────────────────────


async def test_write_then_read_roundtrip(fw: FileWorkspace):
    await fw.write("hello.py", "print('hi')")
    assert await fw.read("hello.py") == "print('hi')"


async def test_write_creates_parent_dirs(fw: FileWorkspace):
    await fw.write("src/utils/helpers.py", "x = 1")
    assert await fw.read("src/utils/helpers.py") == "x = 1"


async def test_append_extends_existing_file(fw: FileWorkspace):
    await fw.write("log.txt", "line1\n")
    await fw.append("log.txt", "line2\n")
    assert await fw.read("log.txt") == "line1\nline2\n"


async def test_binary_roundtrip(fw: FileWorkspace):
    payload = bytes(range(256))
    await fw.write_binary("blob.bin", payload)
    assert await fw.read_binary("blob.bin") == payload


async def test_exists(fw: FileWorkspace):
    assert await fw.exists("nope.txt") is False
    await fw.write("yes.txt", "x")
    assert await fw.exists("yes.txt") is True


async def test_delete_file(fw: FileWorkspace):
    await fw.write("doomed.txt", "x")
    await fw.delete("doomed.txt")
    assert await fw.exists("doomed.txt") is False


async def test_delete_directory(fw: FileWorkspace):
    await fw.write("sub/a.txt", "a")
    await fw.write("sub/b.txt", "b")
    await fw.delete("sub")
    assert await fw.exists("sub") is False


async def test_list_non_recursive(fw: FileWorkspace):
    await fw.write("a.py", "")
    await fw.write("b.py", "")
    await fw.write("sub/c.py", "")
    assert await fw.list() == ["a.py", "b.py"]


async def test_list_recursive(fw: FileWorkspace):
    await fw.write("a.py", "")
    await fw.write("sub/b.py", "")
    await fw.write("sub/deep/c.py", "")
    assert await fw.list(recursive=True) == ["a.py", "sub/b.py", "sub/deep/c.py"]


async def test_list_empty_returns_empty(fw: FileWorkspace):
    assert await fw.list() == []


# ── Path safety: absolute paths ─────────────────────────────────────────────


async def test_absolute_path_rejected(fw: FileWorkspace):
    bad = "C:\\Windows\\System32" if sys.platform == "win32" else "/etc/passwd"
    with pytest.raises(PathSafetyError, match="absolute"):
        await fw.read(bad)


async def test_write_absolute_rejected(fw: FileWorkspace):
    bad = "C:\\bad.txt" if sys.platform == "win32" else "/tmp/bad.txt"
    with pytest.raises(PathSafetyError):
        await fw.write(bad, "x")


# ── Path safety: parent traversal ───────────────────────────────────────────


async def test_parent_traversal_rejected(fw: FileWorkspace):
    with pytest.raises(PathSafetyError, match="outside"):
        await fw.read("../escape.txt")


async def test_deeply_nested_traversal_rejected(fw: FileWorkspace):
    with pytest.raises(PathSafetyError, match="outside"):
        await fw.read("a/b/../../../../escape.txt")


async def test_traversal_into_inputs_rejected(fw: FileWorkspace):
    """The default base is working/. Trying to read an inputs/ file via
    traversal must fail — agents should use read_input() explicitly."""
    with pytest.raises(PathSafetyError):
        await fw.read("../inputs/foo.txt")


async def test_write_traversal_rejected(fw: FileWorkspace):
    with pytest.raises(PathSafetyError):
        await fw.write("../bad.py", "x")


async def test_delete_traversal_rejected(fw: FileWorkspace):
    with pytest.raises(PathSafetyError):
        await fw.delete("../foo")


async def test_exists_returns_false_on_unsafe_path(fw: FileWorkspace):
    """exists() must not raise on unsafe paths — it returns False so callers
    can probe without try/except. (Distinct from read/write which raise.)"""
    assert await fw.exists("../escape.txt") is False


# ── Path safety: symlinks ───────────────────────────────────────────────────


def _try_symlink(src: Path, dst: Path) -> bool:
    try:
        dst.symlink_to(src)
        return True
    except (OSError, NotImplementedError):
        return False


async def test_symlink_escape_rejected(fw: FileWorkspace, tmp_path: Path):
    """A symlink inside working/ pointing OUTSIDE the workspace must not
    let an agent read or write the target."""
    outside = tmp_path / "secret.txt"
    outside.write_text("classified")

    link = fw.working / "leak"
    if not _try_symlink(outside, link):
        pytest.skip("symlinks unavailable on this platform/account")

    with pytest.raises(PathSafetyError, match="outside"):
        await fw.read("leak")


# ── Inputs ──────────────────────────────────────────────────────────────────


async def test_read_input(fw: FileWorkspace):
    (fw.inputs / "spec.md").write_text("# spec")
    assert await fw.read_input("spec.md") == "# spec"


async def test_input_traversal_rejected(fw: FileWorkspace):
    with pytest.raises(PathSafetyError):
        await fw.read_input("../working/secret.py")


async def test_list_inputs(fw: FileWorkspace):
    (fw.inputs / "a.md").write_text("")
    (fw.inputs / "sub").mkdir()
    (fw.inputs / "sub" / "b.md").write_text("")
    assert await fw.list_inputs() == ["a.md", "sub/b.md"]


# ── Promotion ───────────────────────────────────────────────────────────────


async def test_promote_default_path(fw: FileWorkspace):
    await fw.write("app.py", "main")
    await fw.promote("app.py")
    assert (fw.deliverables / "app.py").read_text() == "main"


async def test_promote_explicit_path(fw: FileWorkspace):
    await fw.write("internal/app.py", "main")
    await fw.promote("internal/app.py", "app.py")
    assert (fw.deliverables / "app.py").read_text() == "main"


async def test_promote_creates_deliverable_subdirs(fw: FileWorkspace):
    await fw.write("src/utils.py", "u = 1")
    await fw.promote("src/utils.py")
    assert (fw.deliverables / "src" / "utils.py").read_text() == "u = 1"


async def test_promote_missing_source_raises(fw: FileWorkspace):
    with pytest.raises(FileNotFoundError):
        await fw.promote("nope.py")


async def test_promote_traversal_rejected(fw: FileWorkspace):
    await fw.write("ok.py", "x")
    with pytest.raises(PathSafetyError):
        await fw.promote("../ok.py")
    with pytest.raises(PathSafetyError):
        await fw.promote("ok.py", "../escape.py")


# ── absolute() ──────────────────────────────────────────────────────────────


def test_absolute_returns_working_root(fw: FileWorkspace):
    assert fw.absolute() == fw.working


def test_absolute_for_subpath(fw: FileWorkspace):
    assert fw.absolute("src/app.py") == (fw.working / "src" / "app.py").resolve()


def test_absolute_traversal_rejected(fw: FileWorkspace):
    with pytest.raises(PathSafetyError):
        fw.absolute("../escape.py")


# ── Defensive working/ prefix strip (run_445b1f6974 regression) ─────────


async def test_leading_working_prefix_is_stripped_on_write(
    fw: FileWorkspace, caplog,
):
    """Codestral / Mistral agents reliably emit
    `fs-write("working/docs/x.md")` because the continuation brief
    mentions `working/`. Without the strip, that resolved to
    `<root>/working/working/docs/x.md` — a doubled silo invisible
    to any agent that called `fs-list("docs")` correctly."""
    import logging
    caplog.set_level(logging.WARNING)
    await fw.write("working/docs/requirements.md", "FR-1: do thing")
    # File lands at the SINGLE working/ root, not doubled.
    assert (fw.working / "docs" / "requirements.md").is_file()
    assert not (fw.working / "working" / "docs" / "requirements.md").exists()
    # And we logged a warning so operators see when this fires.
    assert any(
        "redundant leading 'working/'" in rec.message for rec in caplog.records
    )


async def test_leading_working_prefix_is_stripped_on_read(fw: FileWorkspace):
    """Reads use the same _safe_path so a confused agent calling
    fs-read("working/docs/x.md") still finds the file written at
    the canonical relative path."""
    await fw.write("docs/x.md", "hello")
    assert await fw.read("working/docs/x.md") == "hello"


async def test_dot_slash_working_prefix_also_stripped(fw: FileWorkspace):
    """Some models emit `./working/foo`; same strip rule applies."""
    await fw.write("./working/notes.md", "x")
    assert (fw.working / "notes.md").is_file()


def test_plain_working_no_slash_is_left_alone(fw: FileWorkspace):
    """A path that is literally `working` (no trailing slash, no
    children) could legitimately be a file or directory name. Don't
    strip; resolve normally under the workspace root."""
    p = fw.absolute("working")
    assert p == (fw.working / "working").resolve()


def test_only_first_working_segment_is_stripped(fw: FileWorkspace):
    """`working/working/x` strips ONE leading `working/`, leaving the
    second segment intact in case it's a real subdirectory."""
    p = fw.absolute("working/working/x.py")
    assert p == (fw.working / "working" / "x.py").resolve()
