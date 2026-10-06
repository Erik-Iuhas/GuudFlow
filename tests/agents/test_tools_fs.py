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
    FsWriteAppend,
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
    assert claim_log["releases"] == [("auth.py", "written", None)]


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


# ── Story-scoped diff (replaces inline-fence filter) ────────────────────────


class _StubStory:
    def __init__(self, base_ref: str | None) -> None:
        self.base_ref = base_ref
        self.closed = False
        self.name = "ghost-ai"


class _StubWorkspace:
    def __init__(self, base_ref: str | None, role: str = "dev") -> None:
        self.active_story = {role: "story_xyz"}
        self.stories = {"story_xyz": _StubStory(base_ref)}


@pytest.fixture
def git_fw(tmp_path: Path):
    """A FileWorkspace whose `working/` is a real git repo."""
    from core.git_workspace import GitWorkspace

    ws_root = tmp_path / "ws"
    fw = FileWorkspace(ws_root)
    gw = GitWorkspace(working_dir=fw.working, emit=None)
    gw.init()
    return fw, gw


async def test_fs_diff_returns_unified_diff_against_story_base(git_fw):
    from agents.tools.fs import FsDiff

    fw, gw = git_fw
    # Initial commit so base_ref is meaningful.
    await fw.write("docs/spec.md", "# Spec\n\noriginal line\n")
    gw.repo.git.add(A=True)
    gw.repo.index.commit("seed")
    base_ref = gw.head_sha()

    # Now mutate the doc — this is "what's new for the story".
    await fw.write("docs/spec.md", "# Spec\n\noriginal line\nnew story line\n")

    workspace = _StubWorkspace(base_ref)
    res = await FsDiff(fw, workspace=workspace, role="dev", git=gw).run(
        {"path": "docs/spec.md"}
    )
    assert res["ok"] is True
    assert res["base_ref"] == base_ref
    assert "new story line" in res["diff"]
    assert "+new story line" in res["diff"]


async def test_fs_diff_falls_back_to_full_content_without_base_ref(git_fw):
    from agents.tools.fs import FsDiff

    fw, gw = git_fw
    await fw.write("docs/spec.md", "hello\n")

    workspace = _StubWorkspace(base_ref=None)
    res = await FsDiff(fw, workspace=workspace, role="dev", git=gw).run(
        {"path": "docs/spec.md"}
    )
    assert res["ok"] is True
    assert res["content"] == "hello\n"
    assert "note" in res
    assert "diff" not in res


async def test_fs_list_marks_changed_in_story(git_fw):
    fw, gw = git_fw
    await fw.write("docs/a.md", "original\n")
    await fw.write("docs/b.md", "untouched\n")
    gw.repo.git.add(A=True)
    gw.repo.index.commit("seed")
    base_ref = gw.head_sha()

    # Modify only a.md.
    await fw.write("docs/a.md", "original\nnew line\n")

    workspace = _StubWorkspace(base_ref)
    res = await FsList(fw, workspace=workspace, role="dev", git=gw).run(
        {"path": "docs", "recursive": False}
    )
    assert res["ok"] is True
    by_path = {
        e["path"] if isinstance(e, dict) else e: e for e in res["files"]
    }
    a = by_path["docs/a.md"]
    b = by_path.get("docs/b.md")
    assert isinstance(a, dict)
    assert a["changed_in_story"] is True
    # b is untouched; either appears as a plain string or dict with False.
    if isinstance(b, dict):
        assert b.get("changed_in_story") is False


async def test_fs_list_no_active_story_omits_changed_flag(fw: FileWorkspace):
    await fw.write("docs/spec.md", "hello\n")
    res = await FsList(fw).run({"path": "docs"})
    assert res["ok"] is True
    # No story context → entries are plain strings (no changed_in_story).
    for entry in res["files"]:
        if isinstance(entry, dict):
            assert "changed_in_story" not in entry


# ── FsWriteAppend ──────────────────────────────────────────────────────────


async def test_fs_write_append_appends_to_existing(fw: FileWorkspace, emitters, claim_log):
    claim, release = emitters
    await fw.write("doc.md", "# Heading\n")
    tool = FsWriteAppend(fw, claim_emitter=claim, release_emitter=release)
    res = await tool.run({"path": "doc.md", "content": "body line 1\n"})
    assert res["ok"] is True
    assert res["bytes_appended"] == len("body line 1\n".encode("utf-8"))
    assert res["total_bytes"] == len("# Heading\nbody line 1\n".encode("utf-8"))
    assert await fw.read("doc.md") == "# Heading\nbody line 1\n"
    assert claim_log["claims"] == [("edit", "doc.md")]
    assert claim_log["releases"] == [("doc.md", "appended", None)]


async def test_fs_write_append_chunks_build_a_big_file(fw: FileWorkspace):
    """The killer use case: build a file in N chunks each well under
    the model's max_tokens cap."""
    await FsCreate(fw).run({"path": "big.md", "content": ""})
    tool = FsWriteAppend(fw)
    for i in range(5):
        res = await tool.run({"path": "big.md", "content": f"## chunk {i}\n" + "x" * 200 + "\n"})
        assert res["ok"] is True
    final = await fw.read("big.md")
    assert "## chunk 0" in final
    assert "## chunk 4" in final
    # 5 chunks × ~213 bytes each = ~1065 bytes
    assert len(final) > 1000


async def test_fs_write_append_refuses_nonexistent_file(fw: FileWorkspace):
    """Force the agent to be explicit: fs-create first, then append."""
    res = await FsWriteAppend(fw).run({"path": "missing.md", "content": "hi"})
    assert "error" in res
    assert "fs-create" in res["error"].lower()


async def test_fs_write_append_rejects_path_safety(fw: FileWorkspace):
    res = await FsWriteAppend(fw).run({"path": "../escape.md", "content": "x"})
    assert "error" in res


async def test_fs_write_append_rejects_missing_args(fw: FileWorkspace):
    assert "error" in await FsWriteAppend(fw).run({"content": "x"})
    assert "error" in await FsWriteAppend(fw).run({"path": "x.md"})


async def test_fs_write_append_no_emitters_runs_silently(fw: FileWorkspace):
    """Standalone usage (no claim/release emitters) must not crash."""
    await fw.write("x.md", "header\n")
    res = await FsWriteAppend(fw).run({"path": "x.md", "content": "body\n"})
    assert res["ok"] is True


def test_closest_matches_ranks_the_nearest_lines_first():
    from agents.tools.fs import closest_matches

    content = "alpha\nbeta gamma\ndelta\nepsilon\n"
    close = closest_matches("beta gamme", content, top_n=2)
    assert len(close) == 2
    assert "beta gamma" in close[0]
    assert close[0].startswith("  Line 2-2:")
