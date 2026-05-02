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
    _strip_commit_trailers,
    _strip_diff_index_lines,
    _strip_npm_noise,
    _strip_pip_noise,
    _strip_pytest_banners,
    _strip_trailing_whitespace,
    _truncate_oversize_diff,
    compress,
    compress_code_exec,
    compress_git_changed_files,
    compress_git_diff,
    compress_git_log,
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
