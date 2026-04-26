"""Tests for the four context/control tools added after Stage 4:
fs:read_range, fs:grep, code:outline, git:status.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.tools.code import CodeOutline
from agents.tools.fs import FsGrep, FsReadRange
from agents.tools.git import GitStatus
from core.file_workspace import FileWorkspace
from core.git_workspace import GitWorkspace
from core.tool_manifest import BASELINE_LOCAL_SCOPES


@pytest.fixture
def fw(tmp_path: Path) -> FileWorkspace:
    return FileWorkspace(tmp_path / "ws")


# ── BASELINE_LOCAL_SCOPES updated ──────────────────────────────────────────


def test_new_tools_are_baseline_scopes():
    """Read-side additions must be baseline so every agent gets them
    without explicit RBAC grant."""
    for scope in ("fs-read_range", "fs-grep", "code-outline", "git-status"):
        assert scope in BASELINE_LOCAL_SCOPES, f"{scope} should be baseline"


# ── fs:read_range ──────────────────────────────────────────────────────────


async def test_fs_read_range_basic(fw: FileWorkspace):
    await fw.write("a.py", "L1\nL2\nL3\nL4\nL5\n")
    res = await FsReadRange(fw).run({"path": "a.py", "start_line": 2, "end_line": 4})
    assert res["ok"] is True
    assert res["content"] == "L2\nL3\nL4\n"
    assert res["start_line"] == 2
    assert res["end_line"] == 4
    assert res["total_lines"] == 5


async def test_fs_read_range_clamps_end_to_total(fw: FileWorkspace):
    await fw.write("a.py", "L1\nL2\nL3\n")
    res = await FsReadRange(fw).run({"path": "a.py", "start_line": 2, "end_line": 100})
    assert res["ok"] is True
    assert res["end_line"] == 3
    assert res["content"] == "L2\nL3\n"


async def test_fs_read_range_start_past_end_of_file(fw: FileWorkspace):
    await fw.write("a.py", "only line\n")
    res = await FsReadRange(fw).run({"path": "a.py", "start_line": 99, "end_line": 100})
    assert "error" in res


async def test_fs_read_range_invalid_args(fw: FileWorkspace):
    await fw.write("a.py", "x\n")
    assert "error" in await FsReadRange(fw).run({"path": "a.py", "start_line": 0, "end_line": 5})
    assert "error" in await FsReadRange(fw).run({"path": "a.py", "start_line": 5, "end_line": 1})
    assert "error" in await FsReadRange(fw).run({"path": "a.py"})


async def test_fs_read_range_path_safety(fw: FileWorkspace):
    res = await FsReadRange(fw).run({"path": "../escape.py", "start_line": 1, "end_line": 5})
    assert "error" in res


async def test_fs_read_range_missing_file(fw: FileWorkspace):
    res = await FsReadRange(fw).run({"path": "ghost.py", "start_line": 1, "end_line": 5})
    assert "error" in res


# ── fs:grep ────────────────────────────────────────────────────────────────


async def test_fs_grep_basic(fw: FileWorkspace):
    await fw.write("a.py", "import os\nfrom auth import login\n")
    await fw.write("b.py", "x = 1\nimport sys\n")
    res = await FsGrep(fw).run({"query": "import"})
    assert res["ok"] is True
    paths = sorted({m["path"] for m in res["matches"]})
    assert paths == ["a.py", "b.py"]
    assert res["count"] >= 3  # 2 in a.py, 1 in b.py


async def test_fs_grep_returns_line_numbers_and_context(fw: FileWorkspace):
    await fw.write("a.py", "L1\nL2 needle here\nL3\nL4\n")
    res = await FsGrep(fw).run({"query": "needle", "context_lines": 1})
    m = res["matches"][0]
    assert m["path"] == "a.py"
    assert m["line_number"] == 2
    assert m["before"] == ["L1"]
    assert m["after"] == ["L3"]
    assert "needle" in m["line"]


async def test_fs_grep_path_glob_filter(fw: FileWorkspace):
    await fw.write("a.py", "TODO: fix")
    await fw.write("b.md", "TODO: write")
    res = await FsGrep(fw).run({"query": "TODO", "path_glob": "*.py"})
    paths = {m["path"] for m in res["matches"]}
    assert "a.py" in paths
    assert "b.md" not in paths


async def test_fs_grep_case_insensitive_default(fw: FileWorkspace):
    await fw.write("a.py", "AuthManager class\nauthmanager call\n")
    res = await FsGrep(fw).run({"query": "authmanager"})
    assert res["count"] == 2


async def test_fs_grep_case_sensitive(fw: FileWorkspace):
    await fw.write("a.py", "AuthManager\nauthmanager\n")
    res = await FsGrep(fw).run({"query": "AuthManager", "case_sensitive": True})
    assert res["count"] == 1


async def test_fs_grep_regex_mode(fw: FileWorkspace):
    await fw.write("a.py", "def foo():\n    pass\ndef bar():\n    pass\n")
    res = await FsGrep(fw).run({"query": r"def\s+\w+\(", "regex": True})
    assert res["count"] == 2


async def test_fs_grep_invalid_regex(fw: FileWorkspace):
    res = await FsGrep(fw).run({"query": "[unclosed", "regex": True})
    assert "error" in res


async def test_fs_grep_max_matches(fw: FileWorkspace):
    await fw.write("a.py", "\n".join(["match"] * 100))
    res = await FsGrep(fw).run({"query": "match", "max_matches": 5})
    assert res["count"] == 5
    assert res["truncated"] is True


async def test_fs_grep_skips_binary_extensions(fw: FileWorkspace):
    await fw.write("a.py", "match here")
    # Path-safety only checks against the working/ root; we can write a
    # non-text-ish file but this just verifies the BINARY_EXTS skip doesn't
    # explode on the working tree containing one.
    await fw.write_binary("img.png", b"\x89PNG\r\n\x1a\n")
    res = await FsGrep(fw).run({"query": "match"})
    paths = {m["path"] for m in res["matches"]}
    assert "a.py" in paths
    assert "img.png" not in paths


async def test_fs_grep_no_matches(fw: FileWorkspace):
    await fw.write("a.py", "x = 1")
    res = await FsGrep(fw).run({"query": "xyzzy_unique"})
    assert res["count"] == 0
    assert res["matches"] == []


async def test_fs_grep_missing_query(fw: FileWorkspace):
    res = await FsGrep(fw).run({})
    assert "error" in res


# ── code:outline ───────────────────────────────────────────────────────────


async def test_code_outline_python_basic(fw: FileWorkspace):
    src = '''
import os
from auth import login

CONST = 42

def helper():
    pass

class AuthManager:
    def __init__(self, config):
        pass

    def login(self, email, password):
        pass

@decorator
def decorated_fn(x, y=2):
    pass
'''
    await fw.write("auth.py", src)
    res = await CodeOutline(fw).run({"path": "auth.py"})
    assert res["ok"] is True
    assert res["language"] == "python"

    # Imports
    assert "os" in res["imports"]
    assert any(i.startswith("from auth") for i in res["imports"])

    # Classes
    assert len(res["classes"]) == 1
    cls = res["classes"][0]
    assert cls["name"] == "AuthManager"
    method_names = [m["name"] for m in cls["methods"]]
    assert any(name.startswith("__init__") for name in method_names)
    assert any(name.startswith("login") for name in method_names)

    # Functions (top-level + decorated)
    fn_names = [f["name"] for f in res["functions"]]
    assert any(n.startswith("helper") for n in fn_names)
    assert any(n.startswith("decorated_fn") for n in fn_names)

    # Constants
    assert "CONST" in res["constants"]


async def test_code_outline_function_signatures_include_params(fw: FileWorkspace):
    await fw.write("a.py", "def add(a, b=2, *args, **kwargs):\n    pass\n")
    res = await CodeOutline(fw).run({"path": "a.py"})
    fn = res["functions"][0]
    assert "(" in fn["name"] and ")" in fn["name"]
    assert "a" in fn["name"] and "b=2" in fn["name"]


async def test_code_outline_javascript(fw: FileWorkspace):
    src = """
import { helper } from './utils';
const PI = 3.14;

class Foo {
  constructor() {}
  greet() {}
}

function bar() {}

const arrow = () => {};
"""
    await fw.write("a.js", src)
    res = await CodeOutline(fw).run({"path": "a.js"})
    assert res["ok"] is True
    assert res["language"] == "javascript"
    assert "./utils" in res["imports"]
    assert any(c["name"] == "Foo" for c in res["classes"])
    fn_names = [f["name"] for f in res["functions"]]
    assert any(n.startswith("bar") for n in fn_names)
    assert any(n.startswith("arrow") for n in fn_names)
    assert "PI" in res["constants"]


async def test_code_outline_unsupported_extension(fw: FileWorkspace):
    await fw.write("README.md", "# hi")
    res = await CodeOutline(fw).run({"path": "README.md"})
    assert "error" in res


async def test_code_outline_missing_file(fw: FileWorkspace):
    res = await CodeOutline(fw).run({"path": "ghost.py"})
    assert "error" in res


async def test_code_outline_path_safety(fw: FileWorkspace):
    res = await CodeOutline(fw).run({"path": "../escape.py"})
    assert "error" in res


async def test_code_outline_lines_are_present(fw: FileWorkspace):
    await fw.write("a.py", "\n\ndef foo():\n    pass\n")
    res = await CodeOutline(fw).run({"path": "a.py"})
    assert res["functions"][0]["line"] == 3


# ── git:status ─────────────────────────────────────────────────────────────


@pytest.fixture
def gw(tmp_path: Path) -> GitWorkspace:
    g = GitWorkspace(tmp_path / "working")
    g.init()
    return g


async def test_git_status_clean_repo(gw: GitWorkspace):
    res = await GitStatus(gw).run({})
    assert res["ok"] is True
    assert res["clean"] is True
    assert res["entries"] == []


async def test_git_status_untracked_file(gw: GitWorkspace):
    (gw.working_dir / "new.py").write_text("x = 1")
    res = await GitStatus(gw).run({})
    assert res["ok"] is True
    assert res["clean"] is False
    states = {(e["path"], e["state"]) for e in res["entries"]}
    assert ("new.py", "untracked") in states


async def test_git_status_modified_file(gw: GitWorkspace):
    (gw.working_dir / "a.py").write_text("v1")
    gw.commit("developer", "feat: a")
    # gw.commit checked out agent/developer; modify there.
    (gw.working_dir / "a.py").write_text("v2")
    res = await GitStatus(gw).run({})
    states = {(e["path"], e["state"]) for e in res["entries"]}
    # Could be reported as modified (unstaged) — exact state depends on git.
    assert any(p == "a.py" for p, _ in states)
    assert res["clean"] is False


async def test_git_status_branch_reported(gw: GitWorkspace):
    res = await GitStatus(gw).run({})
    assert res["branch"] in ("main", "master")
