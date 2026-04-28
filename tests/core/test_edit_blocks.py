"""Tests for core/edit_blocks.py — SEARCH/REPLACE block parser."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from core.edit_blocks import EditBlock, ParseError, ApplyError, parse, apply, ApplyResult


# ── Parser ───────────────────────────────────────────────────────────────────

def test_parse_simple_edit():
    body = (
        "<<<<<<< SEARCH src/main.py\n"
        "    return None\n"
        "=======\n"
        "    return User.from_token(token)\n"
        ">>>>>>> REPLACE\n"
    )
    blocks = parse(body)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.path == "src/main.py"
    assert b.op == "edit"
    assert "return None" in b.search
    assert "return User.from_token(token)" in b.replace


def test_parse_create_empty_search():
    body = (
        "<<<<<<< SEARCH new_file.py\n"
        "=======\n"
        "print('hello')\n"
        ">>>>>>> REPLACE\n"
    )
    blocks = parse(body)
    assert len(blocks) == 1
    assert blocks[0].op == "create"
    assert blocks[0].search == ""
    assert "print('hello')" in blocks[0].replace


def test_parse_delete_content_empty_replace():
    body = (
        "<<<<<<< SEARCH file.py\n"
        "# TODO: remove this\n"
        "=======\n"
        ">>>>>>> REPLACE\n"
    )
    blocks = parse(body)
    assert len(blocks) == 1
    assert blocks[0].op == "delete_content"
    assert blocks[0].replace == ""


def test_parse_delete_file():
    body = (
        "<<<<<<< DELETE_FILE stale.py\n"
        ">>>>>>> END\n"
    )
    blocks = parse(body)
    assert len(blocks) == 1
    assert blocks[0].op == "delete_file"
    assert blocks[0].path == "stale.py"


def test_parse_multiple_blocks():
    body = (
        "<<<<<<< SEARCH a.py\n"
        "old a\n"
        "=======\n"
        "new a\n"
        ">>>>>>> REPLACE\n"
        "\n"
        "<<<<<<< SEARCH b.py\n"
        "old b\n"
        "=======\n"
        "new b\n"
        ">>>>>>> REPLACE\n"
    )
    blocks = parse(body)
    assert len(blocks) == 2
    assert blocks[0].path == "a.py"
    assert blocks[1].path == "b.py"


def test_parse_missing_divider_raises():
    body = (
        "<<<<<<< SEARCH file.py\n"
        "content without divider\n"
    )
    with pytest.raises(ParseError, match="missing '======='"):
        parse(body)


def test_parse_missing_replace_end_raises():
    body = (
        "<<<<<<< SEARCH file.py\n"
        "old\n"
        "=======\n"
        "new\n"
    )
    with pytest.raises(ParseError, match="missing '>>>>>>> REPLACE'"):
        parse(body)


def test_parse_missing_delete_end_raises():
    body = "<<<<<<< DELETE_FILE file.py\n"
    with pytest.raises(ParseError):
        parse(body)


def test_parse_unsafe_path_raises():
    body = (
        "<<<<<<< SEARCH ../etc/passwd\n"
        "=======\n"
        "evil\n"
        ">>>>>>> REPLACE\n"
    )
    with pytest.raises(ParseError, match="unsafe path"):
        parse(body)


def test_parse_unexpected_line_raises():
    body = "some random text\n"
    with pytest.raises(ParseError, match="unexpected line"):
        parse(body)


# ── Applier ──────────────────────────────────────────────────────────────────

def _make_workspace(files: dict[str, str]):
    """Create a minimal mock FileWorkspace."""
    ws = MagicMock()
    ws.exists.side_effect = lambda p: p in files
    ws.read.side_effect = lambda p: files.get(p, "")

    written: dict[str, str] = {}
    deleted: list[str] = []

    def _write(p, content):
        files[p] = content
        written[p] = content

    def _delete(p):
        files.pop(p, None)
        deleted.append(p)

    ws.write.side_effect = _write
    ws.delete.side_effect = _delete
    ws._written = written
    ws._deleted = deleted
    return ws


def test_apply_edit_success():
    ws = _make_workspace({"app.py": "def foo():\n    return 1\n"})
    blocks = [EditBlock(path="app.py", search="    return 1\n", replace="    return 42\n", op="edit")]
    result = apply(blocks, ws)
    assert result.ok
    assert ws._written["app.py"] == "def foo():\n    return 42\n"


def test_apply_create_success():
    ws = _make_workspace({})
    blocks = [EditBlock(path="new.py", search="", replace="print('hi')\n", op="create")]
    result = apply(blocks, ws)
    assert result.ok
    assert ws._written["new.py"] == "print('hi')\n"


def test_apply_create_fails_if_exists():
    ws = _make_workspace({"existing.py": "content"})
    blocks = [EditBlock(path="existing.py", search="", replace="other", op="create")]
    result = apply(blocks, ws)
    assert not result.ok
    assert "FILE_EXISTS" in result.results[0].error


def test_apply_search_not_found():
    ws = _make_workspace({"file.py": "actual content\n"})
    blocks = [EditBlock(path="file.py", search="not here\n", replace="new\n", op="edit")]
    result = apply(blocks, ws)
    assert not result.ok
    assert "SEARCH_NOT_FOUND" in result.results[0].error


def test_apply_multiple_matches():
    ws = _make_workspace({"file.py": "x = 1\nx = 1\n"})
    blocks = [EditBlock(path="file.py", search="x = 1\n", replace="x = 2\n", op="edit")]
    result = apply(blocks, ws)
    assert not result.ok
    assert "MULTIPLE_MATCHES" in result.results[0].error


def test_apply_delete_file():
    ws = _make_workspace({"gone.py": "bye"})
    blocks = [EditBlock(path="gone.py", search="", replace="", op="delete_file")]
    result = apply(blocks, ws)
    assert result.ok
    assert "gone.py" in ws._deleted


def test_apply_delete_file_not_found():
    ws = _make_workspace({})
    blocks = [EditBlock(path="missing.py", search="", replace="", op="delete_file")]
    result = apply(blocks, ws)
    assert not result.ok
    assert "FILE_NOT_FOUND" in result.results[0].error


def test_apply_atomic_all_or_none():
    """If second block fails, first block's write must not have happened."""
    ws = _make_workspace({"a.py": "old a\n"})
    blocks = [
        EditBlock(path="a.py", search="old a\n", replace="new a\n", op="edit"),
        EditBlock(path="b.py", search="does not exist\n", replace="x\n", op="edit"),
    ]
    result = apply(blocks, ws)
    assert not result.ok
    # a.py should NOT have been written (atomic failure)
    assert "a.py" not in ws._written


def test_apply_file_not_found_for_edit():
    ws = _make_workspace({})
    blocks = [EditBlock(path="no.py", search="x\n", replace="y\n", op="edit")]
    result = apply(blocks, ws)
    assert not result.ok
    assert "FILE_NOT_FOUND" in result.results[0].error
