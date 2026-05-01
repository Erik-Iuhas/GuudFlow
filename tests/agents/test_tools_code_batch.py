"""Track 3e — code-outline-batch.

Outlines multiple files in one tool call. Per-path failures (missing,
unsupported language) reported individually.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.tools.code_batch import CodeOutlineBatch
from core.file_workspace import FileWorkspace


@pytest.fixture
def fw(tmp_path: Path) -> FileWorkspace:
    return FileWorkspace(tmp_path / "ws")


async def test_code_outline_batch_outlines_multiple_files(fw: FileWorkspace):
    await fw.write("a.py", "def foo():\n    return 1\n\ndef bar():\n    return 2\n")
    await fw.write("b.py", "class Baz:\n    def m(self):\n        pass\n")
    res = await CodeOutlineBatch(fw).run({"paths": ["a.py", "b.py"]})
    assert res["ok"] is True
    assert res["outlined"] == 2
    assert res["failed"] == 0
    paths = [o["path"] for o in res["outlines"]]
    assert "a.py" in paths
    assert "b.py" in paths


async def test_code_outline_batch_rejects_empty(fw: FileWorkspace):
    res = await CodeOutlineBatch(fw).run({"paths": []})
    assert "error" in res


async def test_code_outline_batch_rejects_oversize(fw: FileWorkspace):
    res = await CodeOutlineBatch(fw).run({"paths": [f"f{i}.py" for i in range(11)]})
    assert "error" in res
    assert "too many" in res["error"]


async def test_code_outline_batch_partial_failures(fw: FileWorkspace):
    await fw.write("real.py", "def foo():\n    pass\n")
    res = await CodeOutlineBatch(fw).run({"paths": [
        "real.py",
        "ghost.py",        # missing
        "data.json",       # unsupported language
    ]})
    assert res["outlined"] == 1
    assert res["failed"] == 2
    failure_paths = [f["path"] for f in res["failures"]]
    assert "ghost.py" in failure_paths
    assert "data.json" in failure_paths


async def test_code_outline_batch_invalid_path_entry(fw: FileWorkspace):
    res = await CodeOutlineBatch(fw).run({"paths": [42, ""]})
    assert res["outlined"] == 0
    assert res["failed"] == 2
