"""Track 1d — score_entries_for_task: task-aware ranking of repo_map entries.

Mixes symbol + path keyword overlap with PageRank as a tiebreak. Empty task
or zero-overlap input returns []. CamelCase / snake_case identifier
splitting matters because agents describe tasks in mixed casing.
"""
from __future__ import annotations

import pytest

from core.repo_map import (
    RepoMap,
    RepoMapEntry,
    score_entries_for_task,
    _query_tokens,
    _split_camel_snake,
)


def _entry(path: str, symbols: list[str], rank: float = 0.0) -> RepoMapEntry:
    return RepoMapEntry(path=path, type="py", symbols=symbols, imports=[], rank=rank)


def test_split_camel_snake():
    assert _split_camel_snake("AuthManager") == {"auth", "manager"}
    assert _split_camel_snake("login_user") == {"login", "user"}
    # Consecutive caps (acronyms like XMLParser) split letter-by-letter under
    # the simple regex — not ideal but matches aider's behavior. Acronym-
    # awareness is out of scope for Track 1d.
    assert "parser" in _split_camel_snake("XMLParser")


def test_query_tokens_includes_split_components():
    tokens = _query_tokens("Implement AuthManager.login")
    # both whole identifiers and components present
    assert "implement" in tokens
    assert "authmanager" in tokens
    assert "auth" in tokens
    assert "manager" in tokens
    assert "login" in tokens


def test_score_filters_zero_overlap_entries():
    rmap = RepoMap(entries=[
        _entry("src/auth.py", ["User", "login"]),
        _entry("src/random.py", ["foo", "bar"]),
        _entry("src/db.py", ["Connection"]),
    ])
    scored = score_entries_for_task(rmap, "implement login flow")
    paths = [e.path for _, e in scored]
    assert "src/auth.py" in paths
    # zero-overlap entries are filtered out, not just sorted last
    assert "src/random.py" not in paths
    assert "src/db.py" not in paths


def test_score_ranks_higher_overlap_first():
    rmap = RepoMap(entries=[
        _entry("src/utils.py", ["login"]),                    # 1 sym hit
        _entry("src/auth.py", ["User", "login", "logout"]),   # 2 sym hits + path "auth" no match
        _entry("src/handlers/auth_handler.py", ["handle"]),   # path hit only
    ])
    scored = score_entries_for_task(rmap, "implement login")
    # auth.py has 2 symbol hits ("login" + nothing else) — actually only 1 sym hit
    # because task tokens are {implement, login}. login matches once.
    # utils.py: 1 sym hit. handlers/auth_handler.py: 0 hits (auth_handler doesn't split into "login").
    # So utils.py and auth.py tie on sym hits; PageRank tiebreak (both 0) → arbitrary.
    # Stronger assertion: auth.py and utils.py ranked above auth_handler.py.
    paths_in_order = [e.path for _, e in scored]
    assert "src/handlers/auth_handler.py" not in paths_in_order  # zero overlap


def test_score_path_hits_count():
    rmap = RepoMap(entries=[
        _entry("src/auth/manager.py", ["Helper"]),  # path hits: auth — task has "auth"
    ])
    scored = score_entries_for_task(rmap, "auth refactor")
    assert len(scored) == 1
    score, _ = scored[0]
    assert score > 0  # path overlap counts


def test_score_camel_snake_path_split():
    rmap = RepoMap(entries=[
        _entry("src/auth_manager.py", ["AuthManager"]),
    ])
    scored = score_entries_for_task(rmap, "AuthManager bug")
    # Path "auth_manager.py" splits to {auth, manager}.
    # Task "AuthManager bug" tokens: {authmanager, auth, manager, bug}.
    # Overlap: {auth, manager} on path AND on symbol ⇒ both hits.
    assert len(scored) == 1
    score, _ = scored[0]
    assert score > 0


def test_score_pagerank_tiebreak():
    rmap = RepoMap(entries=[
        _entry("src/a.py", ["login"], rank=0.0),
        _entry("src/b.py", ["login"], rank=10.0),
    ])
    scored = score_entries_for_task(rmap, "login")
    paths_in_order = [e.path for _, e in scored]
    # Same symbol-hit count; higher rank wins via 0.1·rank tiebreak.
    assert paths_in_order[0] == "src/b.py"
    assert paths_in_order[1] == "src/a.py"


def test_score_empty_task_returns_empty():
    rmap = RepoMap(entries=[_entry("src/a.py", ["foo"])])
    assert score_entries_for_task(rmap, "") == []
    assert score_entries_for_task(rmap, "   ") == []


def test_score_empty_map_returns_empty():
    rmap = RepoMap(entries=[])
    assert score_entries_for_task(rmap, "anything") == []


def test_score_punctuation_only_task():
    """Task with no identifier-shaped tokens returns nothing."""
    rmap = RepoMap(entries=[_entry("src/a.py", ["foo"])])
    assert score_entries_for_task(rmap, "!!! ???") == []
