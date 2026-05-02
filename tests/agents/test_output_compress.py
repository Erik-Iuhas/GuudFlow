"""Track 3c — tool output compression strategies + registry behaviour."""
from __future__ import annotations

import json

import pytest

from agents.tools.output_compress import (
    CompressedResult,
    _dedupe_repeated_lines,
    _group_files_by_dir,
    _serialise_tokens,
    _squash_blank_runs,
    _strip_cargo_banners,
    _strip_commit_trailers,
    _strip_diff_index_lines,
    _strip_go_test_pass_noise,
    _strip_npm_noise,
    _strip_pip_noise,
    _strip_pytest_banners,
    _strip_ruff_warnings,
    _strip_trailing_whitespace,
    _truncate_oversize_diff,
    compress,
    compress_code_exec,
    compress_fs_grep,
    compress_fs_list,
    compress_git_changed_files,
    compress_git_diff,
    compress_git_log,
    compress_git_status,
)


# ── Shared text strategies ─────────────────────────────────────────────────


def test_squash_blank_runs():
    text = "a\n\n\n\nb\n\n\nc"
    new_text, hit = _squash_blank_runs(text)
    assert hit is True
    assert new_text == "a\n\nb\n\nc"


def test_squash_blank_runs_no_op():
    text = "a\n\nb\nc"
    new_text, hit = _squash_blank_runs(text)
    assert hit is False
    assert new_text == text


def test_strip_trailing_whitespace():
    text = "line1   \nline2\t\nline3"
    new_text, hit = _strip_trailing_whitespace(text)
    assert hit is True
    assert new_text == "line1\nline2\nline3"


def test_dedupe_repeated_lines():
    text = "x\n" + "same\n" * 8 + "y"
    new_text, hit = _dedupe_repeated_lines(text, threshold=5)
    assert hit is True
    assert "× 8" in new_text
    assert new_text.count("same") == 1  # only the original line + the marker


def test_dedupe_repeated_lines_below_threshold():
    text = "x\n" + "same\n" * 3 + "y"
    new_text, hit = _dedupe_repeated_lines(text, threshold=5)
    assert hit is False
    assert new_text == text


def test_dedupe_does_not_collapse_blank_lines():
    """Blank-line dedup is _squash_blank_runs's job, not the line-dedup's."""
    text = "x\n" + "\n" * 8 + "y"
    new_text, hit = _dedupe_repeated_lines(text, threshold=5)
    # Blank lines have empty stripped form — should NOT be collapsed.
    assert hit is False


# ── code-exec compressor ───────────────────────────────────────────────────


_PYTEST_FIXTURE = """============================= test session starts ==============================
platform linux -- Python 3.11.3, pytest-8.0.0, pluggy-1.4.0
rootdir: /home/user/project
configfile: pytest.ini
plugins: anyio-3.7.1, asyncio-0.23.0
collected 5 items

tests/test_a.py .....                                                    [100%]

============================== 5 passed in 0.42s ===============================
"""


def test_strip_pytest_banners():
    new_text, hit = _strip_pytest_banners(_PYTEST_FIXTURE)
    assert hit is True
    # Banner lines gone, but the dot-line and final summary still present.
    assert "test session starts" not in new_text
    assert "platform linux" not in new_text
    assert "collected 5 items" not in new_text
    assert "5 passed in 0.42s" in new_text  # MUST keep the summary


def test_strip_npm_noise():
    text = "npm WARN deprecated foo@1.0.0\nadded 5 packages\nnpm WARN deprecated bar@2.0.0\n"
    new_text, hit = _strip_npm_noise(text)
    assert hit is True
    assert "npm WARN" not in new_text
    assert "added 5 packages" in new_text


def test_strip_pip_noise():
    text = "Looking in indexes: https://pypi.org/simple\nCollecting requests\nDownloading requests-2.31.0.whl\nrequests==2.31.0\n"
    new_text, hit = _strip_pip_noise(text)
    assert hit is True
    assert "Looking in indexes" not in new_text
    assert "Collecting requests" not in new_text
    assert "Downloading requests" not in new_text
    assert "requests==2.31.0" in new_text


def test_compress_code_exec_full():
    """End-to-end: pytest output through the full strategy chain."""
    long_output = _PYTEST_FIXTURE + "\n\n\n" + ("repeat\n" * 10)
    result = {"ok": True, "stdout": long_output, "stderr": "", "exit_code": 0}
    cr = compress_code_exec(result)
    assert cr.original_tokens > cr.compressed_tokens
    assert cr.tokens_saved > 0
    assert "pytest_banner" in cr.strategies_applied
    assert "repeated_lines" in cr.strategies_applied
    # Result preserves dict shape.
    assert cr.result["ok"] is True
    assert cr.result["exit_code"] == 0


def test_compress_code_exec_empty_strings():
    """Empty stdout/stderr → no-op, no crash."""
    result = {"ok": True, "stdout": "", "stderr": "", "exit_code": 0}
    cr = compress_code_exec(result)
    assert cr.tokens_saved >= 0
    assert cr.result == result


def test_compress_code_exec_no_text_fields():
    """Result without stdout/stderr → identity-shaped output."""
    result = {"ok": True, "exit_code": 0}
    cr = compress_code_exec(result)
    assert cr.result == result


# ── git-log compressor ─────────────────────────────────────────────────────


def test_strip_commit_trailers():
    msg = "feat: add auth\n\nDetails about auth.\n\nSigned-off-by: Alice\nCo-Authored-By: Bob\nReviewed-by: Carol"
    new_msg, hit = _strip_commit_trailers(msg)
    assert hit is True
    assert "Signed-off-by" not in new_msg
    assert "Co-Authored-By" not in new_msg
    assert "Reviewed-by" not in new_msg
    assert "feat: add auth" in new_msg
    assert "Details about auth" in new_msg


def test_compress_git_log_strips_trailers():
    result = {
        "ok": True,
        "branch": "main",
        "commits": [
            {"hash": "abc123", "branch": "main", "author": "A", "ts": "2026-01-01T00:00:00Z",
             "message": "feat: x\n\nSigned-off-by: A"},
            {"hash": "def456", "branch": "main", "author": "B", "ts": "2026-01-02T00:00:00Z",
             "message": "fix: y\n\nCo-Authored-By: B"},
        ],
    }
    cr = compress_git_log(result)
    assert "commit_trailers" in cr.strategies_applied
    for c in cr.result["commits"]:
        assert "Signed-off-by" not in c["message"]
        assert "Co-Authored-By" not in c["message"]


def test_compress_git_log_truncates_long_history():
    result = {
        "ok": True,
        "branch": "main",
        "commits": [
            {"hash": f"h{i:03d}", "branch": "main", "author": "A",
             "ts": "2026-01-01T00:00:00Z", "message": f"commit {i}"}
            for i in range(30)
        ],
    }
    cr = compress_git_log(result)
    assert "history_truncated" in cr.strategies_applied
    out_commits = cr.result["commits"]
    # 10 head + 1 omitted marker + 5 tail = 16
    assert len(out_commits) == 16
    assert any("_omitted" in c for c in out_commits if isinstance(c, dict))


def test_compress_git_log_short_history_no_truncation():
    result = {
        "ok": True,
        "commits": [
            {"hash": f"h{i}", "message": f"commit {i}"} for i in range(5)
        ],
    }
    cr = compress_git_log(result)
    assert "history_truncated" not in cr.strategies_applied
    assert len(cr.result["commits"]) == 5


# ── git-diff compressor ────────────────────────────────────────────────────


def test_strip_diff_index_lines():
    text = "diff --git a/foo b/foo\nindex abc123..def456 100644\n--- a/foo\n+++ b/foo\n@@ -1 +1 @@\n-old\n+new\n"
    new_text, hit = _strip_diff_index_lines(text)
    assert hit is True
    assert "index abc123..def456" not in new_text
    assert "diff --git a/foo b/foo" in new_text  # header survives


def test_truncate_oversize_diff():
    big = "x" * 30000  # ~7500 tokens
    new_text, hit = _truncate_oversize_diff(big, max_tokens=5000)
    assert hit is True
    assert "diff truncated" in new_text


def test_truncate_oversize_diff_no_op():
    small = "x" * 100
    new_text, hit = _truncate_oversize_diff(small, max_tokens=5000)
    assert hit is False
    assert new_text == small


def test_compress_git_diff_full():
    diff_text = (
        "diff --git a/src/foo.py b/src/foo.py\n"
        "index abc..def 100644\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-old line\n"
        "+new line\n"
        "\n\n\n\n"
    )
    result = {"ok": True, "diff": diff_text, "branch_a": "feature", "branch_b": "main"}
    cr = compress_git_diff(result)
    assert "diff_index_lines" in cr.strategies_applied
    assert "blank_runs" in cr.strategies_applied
    assert "index abc..def" not in cr.result["diff"]
    assert cr.result["branch_a"] == "feature"


# ── git-changed-files compressor ───────────────────────────────────────────


def test_group_files_by_dir():
    files = [
        "src/api/auth.py", "src/api/users.py", "src/api/posts.py",
        "src/db/conn.py",
        "tests/test_auth.py", "tests/test_users.py",
        "README.md",
    ]
    summary = _group_files_by_dir(files)
    assert "src/api/{auth.py, posts.py, users.py}" in summary
    assert "src/db/conn.py" in summary  # single-file dirs render flat
    assert "tests/{test_auth.py, test_users.py}" in summary
    assert "README.md" in summary


def test_group_files_by_dir_truncates_at_top_20():
    files = [f"d{i}/file.py" for i in range(30)]
    summary = _group_files_by_dir(files)
    assert "more directories" in summary


def test_compress_git_changed_files_groups_long_lists():
    files = [
        "src/api/a.py", "src/api/b.py", "src/api/c.py",
        "src/db/d.py", "src/db/e.py", "src/db/f.py",
        "tests/g.py", "tests/h.py",
    ]
    result = {"ok": True, "files": files, "count": len(files), "branch_a": "x"}
    cr = compress_git_changed_files(result)
    assert "dir_grouping" in cr.strategies_applied
    assert "summary" in cr.result
    assert "files" not in cr.result  # bulk list dropped
    assert cr.result["files_full_count"] == len(files)
    assert cr.tokens_saved > 0


def test_compress_git_changed_files_short_list_no_op():
    """≤ 5 files isn't worth grouping — keep the list as-is."""
    result = {"ok": True, "files": ["a.py", "b.py"], "count": 2}
    cr = compress_git_changed_files(result)
    assert "dir_grouping" not in cr.strategies_applied
    assert cr.result["files"] == ["a.py", "b.py"]


# ── Registry / dispatch ────────────────────────────────────────────────────


def test_compress_dispatches_to_registered_compressor():
    """compress(name, dict) routes by name; unknown names return identity."""
    result = {"ok": True, "diff": "diff --git a/x b/x\nindex abc..def 100644\n"}
    cr = compress("git-diff", result)
    assert cr.tokens_saved > 0


def test_compress_unknown_tool_returns_identity():
    result = {"ok": True, "anything": "x"}
    cr = compress("not-a-tool", result)
    assert cr.tokens_saved == 0
    assert cr.result == result
    assert cr.strategies_applied == []


def test_compress_non_dict_returns_identity():
    """A string result (rare) flows through cleanly."""
    cr = compress("git-diff", "raw string output")
    assert cr.result == "raw string output"
    assert cr.tokens_saved == 0


def test_compress_buggy_compressor_falls_through_to_identity():
    """Register a deliberately-broken compressor; assert compress() doesn't raise."""
    from agents.tools.output_compress import register, _REGISTRY

    def _broken(_result: dict) -> CompressedResult:
        raise RuntimeError("boom")

    original = _REGISTRY.get("test-broken")
    register("test-broken", _broken)
    try:
        cr = compress("test-broken", {"ok": True})
        assert cr.result == {"ok": True}
        assert cr.tokens_saved == 0
    finally:
        if original is None:
            _REGISTRY.pop("test-broken", None)
        else:
            _REGISTRY["test-broken"] = original


# ── Idempotency ────────────────────────────────────────────────────────────


def test_code_exec_compression_idempotent():
    """Running a compressed result through compression again should be a near-no-op."""
    result = {"ok": True, "stdout": _PYTEST_FIXTURE, "stderr": "", "exit_code": 0}
    cr1 = compress_code_exec(result)
    cr2 = compress_code_exec(cr1.result)
    # Second pass yields ≤ 5 token delta (allow tiny variance from regex re-application).
    assert abs(cr2.compressed_tokens - cr1.compressed_tokens) <= 5


def test_git_log_compression_idempotent():
    result = {
        "ok": True,
        "commits": [
            {"hash": f"h{i}", "message": f"feat: thing {i}\n\nSigned-off-by: A"}
            for i in range(8)
        ],
    }
    cr1 = compress_git_log(result)
    cr2 = compress_git_log(cr1.result)
    assert abs(cr2.compressed_tokens - cr1.compressed_tokens) <= 5


# ── code-exec framework-specific banners (RTK parity) ──────────────────────


def test_strip_cargo_banners():
    text = (
        "   Compiling crate-foo v0.1.0 (/path/to/foo)\n"
        "   Compiling crate-bar v2.3.4\n"
        "   Updating crates.io index\n"
        "   Downloaded serde v1.0.0\n"
        "    Finished `test` profile [unoptimized + debuginfo] target(s) in 5.21s\n"
        "running 3 tests\n"
        "test tests::a ... ok\n"
        "test result: ok. 3 passed; 0 failed\n"
    )
    new_text, hit = _strip_cargo_banners(text)
    assert hit is True
    assert "Compiling" not in new_text
    assert "Updating crates.io" not in new_text
    assert "Downloaded" not in new_text
    assert "Finished" not in new_text
    assert "running 3 tests" not in new_text
    # Test result summary preserved.
    assert "test result: ok" in new_text
    assert "test tests::a" in new_text


def test_strip_go_test_pass_noise():
    text = (
        "=== RUN   TestFoo\n"
        "--- PASS: TestFoo (0.00s)\n"
        "=== RUN   TestBar\n"
        "--- FAIL: TestBar (0.01s)\n"
        "    bar_test.go:42: expected 1, got 2\n"
        "FAIL\n"
    )
    new_text, hit = _strip_go_test_pass_noise(text)
    assert hit is True
    assert "=== RUN" not in new_text
    assert "--- PASS:" not in new_text
    # Failures retained.
    assert "--- FAIL: TestBar" in new_text
    assert "expected 1, got 2" in new_text
    assert "FAIL" in new_text


def test_strip_ruff_warnings_keeps_findings():
    text = (
        "warning: `nonexistent.py` does not exist\n"
        "src/auth.py:42:1: F401 imported but unused\n"
        "Found 1 error.\n"
    )
    new_text, hit = _strip_ruff_warnings(text)
    assert hit is True
    assert "does not exist" not in new_text
    # Findings + summary preserved.
    assert "F401" in new_text
    assert "Found 1 error" in new_text


def test_compress_code_exec_with_cargo_output():
    """End-to-end: cargo test output through the full strategy chain."""
    cargo_out = (
        "   Compiling foo v0.1.0\n"
        "   Compiling bar v0.2.0\n"
        "    Finished `test` profile in 4.21s\n"
        "running 5 tests\n"
        "test tests::a ... ok\n"
        "test tests::b ... ok\n"
        "test result: ok. 5 passed; 0 failed\n"
    )
    cr = compress_code_exec({"ok": True, "stdout": cargo_out})
    assert "cargo_banner" in cr.strategies_applied
    assert cr.tokens_saved > 0


# ── fs-list compressor ─────────────────────────────────────────────────────


def test_compress_fs_list_groups_long_lists():
    files = [
        "src/api/a.py", "src/api/b.py", "src/api/c.py",
        "src/db/d.py", "src/db/e.py", "src/db/f.py",
        "tests/g.py", "tests/h.py",
    ]
    result = {"ok": True, "path": "", "recursive": True, "files": files, "count": 8}
    cr = compress_fs_list(result)
    assert "dir_grouping" in cr.strategies_applied
    assert "summary" in cr.result
    assert "files" not in cr.result
    assert cr.result["files_full_count"] == 8
    assert cr.tokens_saved > 0


def test_compress_fs_list_short_list_no_op():
    """≤ 5 files isn't worth grouping — keep the list as-is."""
    files = ["a.py", "b.py", "c.py"]
    result = {"ok": True, "files": files, "count": 3}
    cr = compress_fs_list(result)
    assert "dir_grouping" not in cr.strategies_applied
    assert cr.result["files"] == files


def test_compress_fs_list_preserves_tagged_dict_entries():
    """fs-list returns a mix of str (plain files) and dict (story-tagged files).
    Dict entries carry semantic info — they must be preserved separately."""
    files = [
        "src/a.py", "src/b.py", "src/c.py", "src/d.py", "src/e.py", "src/f.py",
        {"path": "docs/spec.md", "story": "auth-redesign"},
        {"path": "docs/api.md", "changed_in_story": True},
    ]
    result = {"ok": True, "files": files, "count": 8}
    cr = compress_fs_list(result)
    assert "dir_grouping" in cr.strategies_applied
    assert "summary" in cr.result
    assert "tagged_files" in cr.result
    assert len(cr.result["tagged_files"]) == 2
    # The tagged files keep their story info intact.
    assert any(t.get("story") == "auth-redesign" for t in cr.result["tagged_files"])


# ── fs-grep compressor ─────────────────────────────────────────────────────


def test_compress_fs_grep_groups_matches_by_file():
    matches = [
        {"path": "src/auth.py", "line_number": 1, "line": "import os", "before": [], "after": []},
        {"path": "src/auth.py", "line_number": 5, "line": "from db import x", "before": [], "after": []},
        {"path": "src/db.py", "line_number": 3, "line": "import os", "before": [], "after": []},
        {"path": "src/utils.py", "line_number": 2, "line": "import os", "before": [], "after": []},
        {"path": "src/main.py", "line_number": 1, "line": "import os", "before": [], "after": []},
        {"path": "src/cli.py", "line_number": 1, "line": "import os", "before": [], "after": []},
    ]
    result = {"ok": True, "query": "import", "matches": matches, "count": 6, "files_searched": 5}
    cr = compress_fs_grep(result)
    assert "group_by_path" in cr.strategies_applied or "drop_empty_context" in cr.strategies_applied
    assert "matches_by_file" in cr.result
    assert "matches" not in cr.result
    # Each path appears once now, with its sub-matches.
    paths = [block["path"] for block in cr.result["matches_by_file"]]
    assert len(paths) == len(set(paths))


def test_compress_fs_grep_drops_empty_context_lists():
    matches = [
        {"path": "a.py", "line_number": i, "line": f"x{i}", "before": [], "after": []}
        for i in range(10)
    ]
    result = {"ok": True, "matches": matches, "count": 10}
    cr = compress_fs_grep(result)
    assert "drop_empty_context" in cr.strategies_applied
    # Cleaned matches don't carry empty before/after fields.
    block = cr.result["matches_by_file"][0]
    for m in block["matches"]:
        assert "before" not in m
        assert "after" not in m


def test_compress_fs_grep_within_file_dedup():
    """Same line text matched at multiple line numbers — render once with × N."""
    matches = [
        {"path": "src/auth.py", "line_number": 1, "line": "import os", "before": [], "after": []},
        {"path": "src/auth.py", "line_number": 50, "line": "import os", "before": [], "after": []},
        {"path": "src/auth.py", "line_number": 100, "line": "import os", "before": [], "after": []},
        {"path": "src/auth.py", "line_number": 200, "line": "import os", "before": [], "after": []},
        {"path": "src/auth.py", "line_number": 300, "line": "import os", "before": [], "after": []},
        {"path": "src/auth.py", "line_number": 7, "line": "from db import x", "before": [], "after": []},
    ]
    result = {"ok": True, "matches": matches, "count": 6}
    cr = compress_fs_grep(result)
    assert "within_file_dedup" in cr.strategies_applied
    block = cr.result["matches_by_file"][0]
    # The repeated "import os" entry now has occurrences=5.
    repeated = [m for m in block["matches"] if m.get("line") == "import os"]
    assert len(repeated) == 1
    assert repeated[0]["occurrences"] == 5


def test_compress_fs_grep_short_no_op():
    """≤ 5 matches is too small to benefit from grouping — leave it alone."""
    matches = [{"path": "a.py", "line_number": 1, "line": "x"}]
    result = {"ok": True, "matches": matches, "count": 1}
    cr = compress_fs_grep(result)
    assert cr.strategies_applied == []
    assert cr.result == result


# ── git-status compressor ──────────────────────────────────────────────────


def test_compress_git_status_groups_by_state_and_dir():
    entries = [
        {"path": "src/api/a.py", "state": "modified", "staged": False},
        {"path": "src/api/b.py", "state": "modified", "staged": False},
        {"path": "src/api/c.py", "state": "modified", "staged": False},
        {"path": "tests/test_a.py", "state": "added", "staged": True},
        {"path": "tests/test_b.py", "state": "added", "staged": True},
        {"path": "tmp/scratch.txt", "state": "untracked", "staged": False},
    ]
    result = {"ok": True, "branch": "feat-auth", "clean": False, "entries": entries}
    cr = compress_git_status(result)
    assert "state_grouping" in cr.strategies_applied
    assert "by_state" in cr.result
    assert "entries" not in cr.result
    assert cr.result["entries_count"] == 6
    # State buckets exist with grouped paths.
    assert "modified" in cr.result["by_state"]
    assert "added (staged)" in cr.result["by_state"]
    assert "untracked" in cr.result["by_state"]
    # Path-grouping inside each bucket worked.
    assert "src/api/" in cr.result["by_state"]["modified"]


def test_compress_git_status_clean_branch_no_op():
    """Clean branch — no entries — returns identity."""
    result = {"ok": True, "branch": "main", "clean": True, "entries": []}
    cr = compress_git_status(result)
    assert cr.strategies_applied == []
    assert cr.result == result


def test_compress_git_status_short_list_no_op():
    """Few entries — not worth grouping."""
    entries = [{"path": "a.py", "state": "modified", "staged": False}]
    result = {"ok": True, "branch": "x", "clean": False, "entries": entries}
    cr = compress_git_status(result)
    assert cr.strategies_applied == []


# ── Idempotency for the new compressors ──────────────────────────────────


def test_fs_list_idempotent():
    files = [f"src/dir{i}/file.py" for i in range(20)]
    result = {"ok": True, "files": files, "count": 20}
    cr1 = compress_fs_list(result)
    cr2 = compress_fs_list(cr1.result)
    assert abs(cr2.compressed_tokens - cr1.compressed_tokens) <= 5


def test_git_status_idempotent():
    entries = [
        {"path": f"src/d{i}/f.py", "state": "modified", "staged": False}
        for i in range(15)
    ]
    result = {"ok": True, "branch": "x", "clean": False, "entries": entries}
    cr1 = compress_git_status(result)
    cr2 = compress_git_status(cr1.result)
    assert abs(cr2.compressed_tokens - cr1.compressed_tokens) <= 5
