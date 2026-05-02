"""Track 3e — batch fs tools (fs-edit-batch, fs-write-batch).

Covers per-entry success/failure semantics, hard caps, path safety,
and claim/release signal emission.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.tools.fs_batch import FsEditBatch, FsWriteBatch
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


# ── fs-write-batch ──────────────────────────────────────────────────────────


async def test_fs_write_batch_writes_all_entries(fw: FileWorkspace, emitters, claim_log):
    claim, release = emitters
    tool = FsWriteBatch(fw, claim_emitter=claim, release_emitter=release)
    res = await tool.run({"files": [
        {"path": "a.py", "content": "1"},
        {"path": "b.py", "content": "22"},
        {"path": "c.py", "content": "333"},
    ]})
    assert res["ok"] is True
    assert res["written"] == 3
    assert res["failed"] == 0
    assert res["total_bytes"] == 1 + 2 + 3
    # Each entry produces a claim + release.
    assert len(claim_log["claims"]) == 3
    assert len(claim_log["releases"]) == 3
    # Files actually exist on disk.
    assert (await fw.read("a.py")) == "1"
    assert (await fw.read("c.py")) == "333"


async def test_fs_write_batch_rejects_empty_list(fw: FileWorkspace):
    res = await FsWriteBatch(fw).run({"files": []})
    assert "error" in res


async def test_fs_write_batch_rejects_missing_files_arg(fw: FileWorkspace):
    res = await FsWriteBatch(fw).run({})
    assert "error" in res


async def test_fs_write_batch_rejects_oversize(fw: FileWorkspace):
    too_many = [{"path": f"f{i}.py", "content": ""} for i in range(31)]
    res = await FsWriteBatch(fw).run({"files": too_many})
    assert "error" in res
    assert "too many" in res["error"]


async def test_fs_write_batch_partial_failure_keeps_successes(fw: FileWorkspace, emitters):
    claim, release = emitters
    tool = FsWriteBatch(fw, claim_emitter=claim, release_emitter=release)
    res = await tool.run({"files": [
        {"path": "good_a.py", "content": "ok"},
        {"path": "../escape.py", "content": "bad"},  # path safety fails
        {"path": "good_b.py", "content": "also ok"},
    ]})
    assert res["ok"] is True  # batch tool itself succeeded
    assert res["written"] == 2
    assert res["failed"] == 1
    assert res["failures"][0]["path"] == "../escape.py"
    assert "path safety" in res["failures"][0]["error"]
    # The two good entries actually wrote.
    assert (await fw.read("good_a.py")) == "ok"
    assert (await fw.read("good_b.py")) == "also ok"


async def test_fs_write_batch_invalid_entry_shape(fw: FileWorkspace):
    res = await FsWriteBatch(fw).run({"files": [
        "not a dict",  # wrong shape
        {"path": "ok.py", "content": "fine"},
        {"path": "missing.py"},  # missing content
    ]})
    assert res["written"] == 1
    assert res["failed"] == 2


async def test_fs_write_batch_summary_groups_by_directory(fw: FileWorkspace):
    res = await FsWriteBatch(fw).run({"files": [
        {"path": "src/a.py", "content": ""},
        {"path": "src/b.py", "content": ""},
        {"path": "src/c.py", "content": ""},
        {"path": "tests/x.py", "content": ""},
    ]})
    assert res["written"] == 4
    summary = res["paths_summary"]
    assert "src/" in summary
    assert "tests/x.py" in summary


# ── fs-edit-batch ───────────────────────────────────────────────────────────


async def test_fs_edit_batch_applies_all_entries(fw: FileWorkspace, emitters, claim_log):
    await fw.write("a.py", "x = 1\ny = 2")
    await fw.write("b.py", "foo = 'old'")
    claim, release = emitters
    tool = FsEditBatch(fw, claim_emitter=claim, release_emitter=release)
    res = await tool.run({"edits": [
        {"path": "a.py", "find": "x = 1", "replace": "x = 99"},
        {"path": "b.py", "find": "old", "replace": "new"},
    ]})
    assert res["ok"] is True
    assert res["edited"] == 2
    assert res["failed"] == 0
    assert res["total_replacements"] == 2
    assert (await fw.read("a.py")) == "x = 99\ny = 2"
    assert (await fw.read("b.py")) == "foo = 'new'"
    assert len(claim_log["claims"]) == 2
    assert len(claim_log["releases"]) == 2


async def test_fs_edit_batch_search_not_found_per_entry(fw: FileWorkspace):
    await fw.write("a.py", "real content")
    res = await FsEditBatch(fw).run({"edits": [
        {"path": "a.py", "find": "real", "replace": "new"},
        {"path": "a.py", "find": "missing-string", "replace": "x"},
    ]})
    assert res["edited"] == 1
    assert res["failed"] == 1
    assert "SEARCH_NOT_FOUND" in res["failures"][0]["error"]


async def test_fs_edit_batch_multiple_matches_without_replace_all(fw: FileWorkspace):
    await fw.write("a.py", "a a a")
    res = await FsEditBatch(fw).run({"edits": [
        {"path": "a.py", "find": "a", "replace": "b"},
    ]})
    assert res["edited"] == 0
    assert res["failed"] == 1
    assert "MULTIPLE_MATCHES" in res["failures"][0]["error"]


async def test_fs_edit_batch_replace_all_works(fw: FileWorkspace):
    await fw.write("a.py", "a a a")
    res = await FsEditBatch(fw).run({"edits": [
        {"path": "a.py", "find": "a", "replace": "b", "replace_all": True},
    ]})
    assert res["edited"] == 1
    assert res["total_replacements"] == 3
    assert (await fw.read("a.py")) == "b b b"


async def test_fs_edit_batch_path_safety_per_entry(fw: FileWorkspace):
    await fw.write("a.py", "ok")
    res = await FsEditBatch(fw).run({"edits": [
        {"path": "a.py", "find": "ok", "replace": "fine"},
        {"path": "../escape.py", "find": "x", "replace": "y"},
    ]})
    assert res["edited"] == 1
    assert res["failed"] == 1
    assert "path safety" in res["failures"][0]["error"]


async def test_fs_edit_batch_file_not_found_per_entry(fw: FileWorkspace):
    await fw.write("real.py", "x")
    res = await FsEditBatch(fw).run({"edits": [
        {"path": "real.py", "find": "x", "replace": "y"},
        {"path": "ghost.py", "find": "x", "replace": "y"},
    ]})
    assert res["edited"] == 1
    assert res["failed"] == 1
    assert "not found" in res["failures"][0]["error"]


async def test_fs_edit_batch_rejects_empty(fw: FileWorkspace):
    res = await FsEditBatch(fw).run({"edits": []})
    assert "error" in res


async def test_fs_edit_batch_rejects_oversize(fw: FileWorkspace):
    too_many = [{"path": "x.py", "find": "a", "replace": "b"} for _ in range(31)]
    res = await FsEditBatch(fw).run({"edits": too_many})
    assert "error" in res
    assert "too many" in res["error"]


async def test_fs_edit_batch_invalid_entry_shape(fw: FileWorkspace):
    await fw.write("a.py", "ok")
    res = await FsEditBatch(fw).run({"edits": [
        "not a dict",
        {"path": "a.py", "find": "ok", "replace": "fine"},
        {"path": "a.py"},  # missing find/replace
        {"path": "a.py", "find": "", "replace": "x"},  # empty find
    ]})
    assert res["edited"] == 1
    assert res["failed"] == 3


async def test_fs_edit_batch_no_emitters_does_not_crash(fw: FileWorkspace):
    """Tools must work when used standalone without claim/release emitters."""
    await fw.write("a.py", "x = 1")
    res = await FsEditBatch(fw).run({"edits": [
        {"path": "a.py", "find": "x = 1", "replace": "x = 2"},
    ]})
    assert res["edited"] == 1


# ── Token-budget caps (output_tokens guard) ─────────────────────────────────


async def test_fs_write_batch_rejects_oversized_single_entry(fw: FileWorkspace):
    """A single content > _MAX_BYTES_PER_ENTRY (~1K tokens) must be rejected
    with a message pointing the LLM at fs-write."""
    from agents.tools.fs_batch import _MAX_BYTES_PER_ENTRY
    big = "x" * (_MAX_BYTES_PER_ENTRY + 1)
    res = await FsWriteBatch(fw).run({"files": [
        {"path": "a.py", "content": "small"},
        {"path": "b.py", "content": big},
    ]})
    assert "error" in res
    assert "per-file cap" in res["error"]
    assert "fs-write" in res["error"]
    # No partial writes.
    assert not (fw.root / "a.py").exists()


async def test_fs_write_batch_rejects_oversized_total_payload(fw: FileWorkspace):
    """Six 3KB files individually fit but together exceed the batch cap."""
    from agents.tools.fs_batch import _MAX_BYTES_PER_ENTRY, _MAX_BYTES_PER_BATCH
    chunk = "y" * 2_500  # under per-entry cap
    n = (_MAX_BYTES_PER_BATCH // 2_500) + 2
    files = [{"path": f"f{i}.py", "content": chunk} for i in range(n)]
    res = await FsWriteBatch(fw).run({"files": files})
    assert "error" in res
    assert "total batch payload" in res["error"]


async def test_fs_write_batch_accepts_small_batch(fw: FileWorkspace):
    """Five small files should pass through cleanly."""
    res = await FsWriteBatch(fw).run({"files": [
        {"path": f"docs/{name}.md", "content": f"# {name}\nshort body\n"}
        for name in ("a", "b", "c", "d", "e")
    ]})
    assert res.get("ok") is True
    assert res["written"] == 5


async def test_fs_edit_batch_rejects_oversized_single_entry(fw: FileWorkspace):
    """A single find+replace > per-entry cap must be rejected with a
    message pointing the LLM at fs-edit."""
    from agents.tools.fs_batch import _MAX_BYTES_PER_ENTRY
    big_replace = "z" * (_MAX_BYTES_PER_ENTRY + 1)
    res = await FsEditBatch(fw).run({"edits": [
        {"path": "a.py", "find": "x", "replace": big_replace},
    ]})
    assert "error" in res
    assert "per-entry cap" in res["error"]
    assert "fs-edit" in res["error"]
