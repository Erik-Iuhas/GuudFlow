"""Tests for agents/tools/fs.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.tools.fs import (
    FsCreate,
    FsDelete,
    FsEdit,
    FsExists,
    FsList,
    FsRead,
    FsRename,
    FsWrite,
)
from core.file_workspace import FileWorkspace


@pytest.fixture
def fw(tmp_path: Path) -> FileWorkspace:
    return FileWorkspace(tmp_path / "ws")


@pytest.fixture
def claim_log() -> dict[str, list]:
    return {"claims": [], "releases": []}


@pytest.fixture
def emitters(claim_log: dict):
    async def claim(intent: str, path: str) -> None:
        claim_log["claims"].append((intent, path))

    async def release(path: str, outcome: str, commit_hash: str | None) -> None:
        claim_log["releases"].append((path, outcome, commit_hash))

    return claim, release


# ── Read tools ──────────────────────────────────────────────────────────────


async def test_fs_read_happy(fw: FileWorkspace):
    await fw.write("a.py", "print(1)")
    res = await FsRead(fw).run({"path": "a.py"})
    assert res["ok"] is True
    assert res["content"] == "print(1)"
    assert res["size"] == len("print(1)")


async def test_fs_read_missing(fw: FileWorkspace):
    res = await FsRead(fw).run({"path": "ghost.py"})
    assert "error" in res


async def test_fs_read_path_safety(fw: FileWorkspace):
    res = await FsRead(fw).run({"path": "../escape.py"})
    assert "error" in res


async def test_fs_read_missing_arg(fw: FileWorkspace):
    res = await FsRead(fw).run({})
    assert "error" in res


async def test_fs_list_basic(fw: FileWorkspace):
    await fw.write("a.py", "")
    await fw.write("b.py", "")
    await fw.write("sub/c.py", "")
    res = await FsList(fw).run({})
    assert res["files"] == ["a.py", "b.py"]


async def test_fs_list_recursive(fw: FileWorkspace):
    await fw.write("a.py", "")
    await fw.write("sub/b.py", "")
    res = await FsList(fw).run({"recursive": True})
    assert "sub/b.py" in res["files"]


async def test_fs_exists_true(fw: FileWorkspace):
    await fw.write("there.py", "")
    res = await FsExists(fw).run({"path": "there.py"})
    assert res["ok"] is True
    assert res["exists"] is True


async def test_fs_exists_false(fw: FileWorkspace):
    res = await FsExists(fw).run({"path": "ghost.py"})
    assert res["exists"] is False


# ── Write tools — claim emission ────────────────────────────────────────────


async def test_fs_write_emits_claim_and_release(fw: FileWorkspace, emitters, claim_log):
    claim, release = emitters
    tool = FsWrite(fw, claim_emitter=claim, release_emitter=release)
    res = await tool.run({"path": "auth.py", "content": "x = 1"})
    assert res["ok"] is True
    assert claim_log["claims"] == [("edit", "auth.py")]
    assert claim_log["releases"] == [("auth.py", "committed", None)]


async def test_fs_write_aborts_release_on_path_safety(fw: FileWorkspace, emitters, claim_log):
    claim, release = emitters
    tool = FsWrite(fw, claim_emitter=claim, release_emitter=release)
    res = await tool.run({"path": "../escape.py", "content": "x"})
    assert "error" in res
    # Path safety happens BEFORE the claim is emitted (in our impl, claim
    # is emitted first then write fails — release with aborted).
    assert claim_log["releases"] and claim_log["releases"][0][1] == "aborted"


async def test_fs_write_no_emitters_runs_silently(fw: FileWorkspace):
    res = await FsWrite(fw).run({"path": "x.py", "content": "x"})
    assert res["ok"] is True


async def test_fs_create_errors_if_exists(fw: FileWorkspace):
    await fw.write("there.py", "old")
    res = await FsCreate(fw).run({"path": "there.py"})
    assert "error" in res
    # Original content unchanged
    assert await fw.read("there.py") == "old"


async def test_fs_create_creates_empty(fw: FileWorkspace, emitters, claim_log):
    claim, release = emitters
    res = await FsCreate(fw, claim_emitter=claim, release_emitter=release).run({"path": "new.py"})
    assert res["ok"] is True
    assert res["created"] is True
    assert await fw.read("new.py") == ""
    assert claim_log["claims"] == [("create", "new.py")]


async def test_fs_create_with_content(fw: FileWorkspace):
    res = await FsCreate(fw).run({"path": "new.py", "content": "x = 1"})
    assert res["ok"] is True
    assert await fw.read("new.py") == "x = 1"


# ── fs:edit ────────────────────────────────────────────────────────────────


async def test_fs_edit_replaces_single_match(fw: FileWorkspace, emitters):
    claim, release = emitters
    await fw.write("a.py", "x = 1\ny = 2\n")
    res = await FsEdit(fw, claim, release).run({
        "path": "a.py", "find": "x = 1", "replace": "x = 99",
    })
    assert res["ok"] is True
    assert res["replacements"] == 1
    assert await fw.read("a.py") == "x = 99\ny = 2\n"


async def test_fs_edit_multiple_matches_requires_replace_all(fw: FileWorkspace):
    await fw.write("a.py", "x = 1\nx = 1\n")
    res = await FsEdit(fw).run({"path": "a.py", "find": "x = 1", "replace": "x = 9"})
    assert "error" in res
    assert "occurs 2 times" in res["error"]


async def test_fs_edit_replace_all(fw: FileWorkspace):
    await fw.write("a.py", "x = 1\nx = 1\nx = 1\n")
    res = await FsEdit(fw).run({
        "path": "a.py", "find": "x = 1", "replace": "x = 9", "replace_all": True,
    })
    assert res["ok"] is True
    assert res["replacements"] == 3
    assert await fw.read("a.py") == "x = 9\nx = 9\nx = 9\n"


async def test_fs_edit_find_not_present(fw: FileWorkspace):
    await fw.write("a.py", "y = 1")
    res = await FsEdit(fw).run({"path": "a.py", "find": "x = 1", "replace": "x = 9"})
    assert "error" in res


async def test_fs_edit_missing_file(fw: FileWorkspace):
    res = await FsEdit(fw).run({"path": "ghost.py", "find": "x", "replace": "y"})
    assert "error" in res


async def test_fs_edit_empty_find(fw: FileWorkspace):
    await fw.write("a.py", "x = 1")
    res = await FsEdit(fw).run({"path": "a.py", "find": "", "replace": "y"})
    assert "error" in res


# ── fs:delete ──────────────────────────────────────────────────────────────


async def test_fs_delete_file(fw: FileWorkspace, emitters, claim_log):
    claim, release = emitters
    await fw.write("doomed.py", "x")
    res = await FsDelete(fw, claim, release).run({"path": "doomed.py"})
    assert res["ok"] is True
    assert not await fw.exists("doomed.py")
    assert ("delete", "doomed.py") in claim_log["claims"]


async def test_fs_delete_missing(fw: FileWorkspace):
    res = await FsDelete(fw).run({"path": "ghost.py"})
    assert "error" in res


# ── fs:rename ──────────────────────────────────────────────────────────────


async def test_fs_rename_file(fw: FileWorkspace, emitters, claim_log):
    claim, release = emitters
    await fw.write("old.py", "content")
    res = await FsRename(fw, claim, release).run({"from": "old.py", "to": "new.py"})
    assert res["ok"] is True
    assert not await fw.exists("old.py")
    assert await fw.read("new.py") == "content"
    intents = [c[0] for c in claim_log["claims"]]
    assert intents.count("rename") == 2  # one claim per side


async def test_fs_rename_missing_source(fw: FileWorkspace):
    res = await FsRename(fw).run({"from": "ghost.py", "to": "new.py"})
    assert "error" in res
