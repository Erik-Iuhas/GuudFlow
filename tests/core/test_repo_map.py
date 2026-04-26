"""Tests for core/repo_map.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.repo_map import build, render_compact, update_incremental
from core.workspace import RepoMap


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


# ── Parsing ─────────────────────────────────────────────────────────────────


def test_python_parsing(tmp_path: Path):
    _write(tmp_path, "app.py", """
import os
from utils import helper

class Foo:
    pass

def bar():
    pass
""")
    _write(tmp_path, "utils.py", "def helper():\n    pass\n")
    rm = build(tmp_path)
    by_path = {e.path: e for e in rm.entries}

    app = by_path["app.py"]
    assert "Foo" in app.symbols
    assert "bar" in app.symbols
    assert "utils.py" in app.imports  # resolved to local file

    utils = by_path["utils.py"]
    assert "helper" in utils.symbols


def test_python_decorated_definitions(tmp_path: Path):
    _write(tmp_path, "x.py", """
@decorator
def decorated_fn():
    pass

@some_dec
class DecoratedClass:
    pass
""")
    rm = build(tmp_path)
    entry = rm.entries[0]
    assert "decorated_fn" in entry.symbols
    assert "DecoratedClass" in entry.symbols


def test_python_relative_dotted_import_resolution(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/sub.py", "x = 1\n")
    _write(tmp_path, "main.py", "from pkg.sub import x\n")
    rm = build(tmp_path)
    by_path = {e.path: e for e in rm.entries}
    assert "pkg/sub.py" in by_path["main.py"].imports


def test_javascript_parsing(tmp_path: Path):
    _write(tmp_path, "app.js", """
import { helper } from './utils';

class Foo {}

function bar() {}

const arrow = () => {};
""")
    _write(tmp_path, "utils.js", "export function helper() {}\n")
    rm = build(tmp_path)
    by_path = {e.path: e for e in rm.entries}
    app = by_path["app.js"]
    assert "Foo" in app.symbols
    assert "bar" in app.symbols
    assert "arrow" in app.symbols
    assert "utils.js" in app.imports


def test_external_imports_dropped(tmp_path: Path):
    _write(tmp_path, "app.py", "import requests\nimport numpy\n")
    rm = build(tmp_path)
    assert rm.entries[0].imports == []  # neither is in repo


def test_unsupported_files_ignored(tmp_path: Path):
    _write(tmp_path, "README.md", "# title")
    _write(tmp_path, "config.toml", "x = 1")
    _write(tmp_path, "app.py", "x = 1")
    rm = build(tmp_path)
    paths = [e.path for e in rm.entries]
    assert paths == ["app.py"]


def test_dotfiles_skipped(tmp_path: Path):
    _write(tmp_path, "app.py", "x = 1\n")
    _write(tmp_path, ".git/objects/foo.py", "secret = 1\n")
    rm = build(tmp_path)
    paths = [e.path for e in rm.entries]
    assert paths == ["app.py"]


# ── PageRank ────────────────────────────────────────────────────────────────


def test_widely_imported_file_ranks_higher(tmp_path: Path):
    _write(tmp_path, "core.py", "def shared():\n    pass\n")
    for i in range(5):
        _write(tmp_path, f"app{i}.py", "from core import shared\n")
    rm = build(tmp_path)
    top = rm.entries[0]
    assert top.path == "core.py"


def test_pagerank_stable_on_isolated_files(tmp_path: Path):
    _write(tmp_path, "a.py", "x = 1")
    _write(tmp_path, "b.py", "x = 2")
    rm = build(tmp_path)
    # Both files have rank close to 1/n; neither should be 0.
    for e in rm.entries:
        assert e.rank > 0


# ── Incremental update ──────────────────────────────────────────────────────


def test_incremental_picks_up_new_file(tmp_path: Path):
    _write(tmp_path, "a.py", "def a():\n    pass\n")
    rm1 = build(tmp_path)
    assert {e.path for e in rm1.entries} == {"a.py"}

    _write(tmp_path, "b.py", "def b():\n    pass\n")
    rm2 = update_incremental(rm1, tmp_path, ["b.py"])
    assert {e.path for e in rm2.entries} == {"a.py", "b.py"}


def test_incremental_drops_deleted_file(tmp_path: Path):
    _write(tmp_path, "a.py", "x = 1")
    _write(tmp_path, "b.py", "x = 2")
    rm1 = build(tmp_path)
    (tmp_path / "b.py").unlink()
    rm2 = update_incremental(rm1, tmp_path, ["b.py"])
    assert {e.path for e in rm2.entries} == {"a.py"}


def test_incremental_picks_up_symbol_changes(tmp_path: Path):
    _write(tmp_path, "a.py", "def old_name():\n    pass\n")
    rm1 = build(tmp_path)
    assert "old_name" in rm1.entries[0].symbols

    _write(tmp_path, "a.py", "def new_name():\n    pass\n")
    rm2 = update_incremental(rm1, tmp_path, ["a.py"])
    by_path = {e.path: e for e in rm2.entries}
    assert "new_name" in by_path["a.py"].symbols
    assert "old_name" not in by_path["a.py"].symbols


def test_incremental_equals_full_rebuild_on_full_changeset(tmp_path: Path):
    _write(tmp_path, "core.py", "def s():\n    pass\n")
    _write(tmp_path, "a.py", "from core import s\n")
    _write(tmp_path, "b.py", "from core import s\n")
    rm_full = build(tmp_path)
    rm_inc = update_incremental(
        RepoMap(),
        tmp_path,
        ["core.py", "a.py", "b.py"],
    )
    full_paths = sorted(e.path for e in rm_full.entries)
    inc_paths = sorted(e.path for e in rm_inc.entries)
    assert full_paths == inc_paths


# ── Rendering ───────────────────────────────────────────────────────────────


def test_render_compact_includes_symbols_and_imports(tmp_path: Path):
    _write(tmp_path, "core.py", "def shared():\n    pass\n")
    _write(tmp_path, "app.py", "from core import shared\nclass App:\n    pass\n")
    rm = build(tmp_path)
    text = render_compact(rm, max_tokens=2000)
    assert "core.py" in text
    assert "app.py" in text
    assert "shared" in text
    assert "App" in text


def test_render_compact_truncates_at_budget(tmp_path: Path):
    for i in range(50):
        _write(tmp_path, f"f{i}.py", f"def fn_{i}():\n    pass\n")
    rm = build(tmp_path)
    text = render_compact(rm, max_tokens=50)
    # Truncation marker present, and we should NOT have all 50 entries.
    assert "truncated" in text
    assert text.count("\n") < 50


def test_render_compact_empty_repo_returns_empty(tmp_path: Path):
    rm = build(tmp_path)
    assert render_compact(rm) == ""
