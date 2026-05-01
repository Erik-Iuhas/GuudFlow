"""GitWorkspace branch naming: persona-prefixed vs. fallback to agent/."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.git_workspace import GitWorkspace, _persona_slug


# ── _persona_slug ────────────────────────────────────────────────────────────


def test_slug_simple_name():
    assert _persona_slug("Diana Park") == "diana_park"


def test_slug_single_word():
    assert _persona_slug("Mike") == "mike"


def test_slug_special_chars():
    assert _persona_slug("O'Brien") == "o_brien"


def test_slug_multiple_spaces():
    assert _persona_slug("Jean  Luc") == "jean_luc"


def test_slug_empty_falls_back():
    assert _persona_slug("") == "agent"


def test_slug_all_special():
    assert _persona_slug("---") == "agent"


# ── agent_branch naming ───────────────────────────────────────────────────────


def _make_gw(tmp_path: Path, personas: dict[str, str] | None = None) -> GitWorkspace:
    gw = GitWorkspace(tmp_path / "repo", hired_personas=personas)
    gw.init()
    return gw


def test_no_personas_uses_agent_prefix(tmp_path: Path):
    gw = _make_gw(tmp_path)
    branch = gw.agent_branch("developer")
    assert branch == "agent/developer"


def test_with_persona_uses_slug_prefix(tmp_path: Path):
    gw = _make_gw(tmp_path, {"developer": "Diana Park"})
    branch = gw.agent_branch("developer")
    assert branch == "diana_park/developer"


def test_with_single_word_persona(tmp_path: Path):
    gw = _make_gw(tmp_path, {"reviewer": "Mike"})
    branch = gw.agent_branch("reviewer")
    assert branch == "mike/reviewer"


def test_role_without_persona_falls_back(tmp_path: Path):
    gw = _make_gw(tmp_path, {"dev_a": "Alice"})
    # dev_b has no persona entry
    branch = gw.agent_branch("dev_b")
    assert branch == "agent/dev_b"


def test_agent_branch_idempotent(tmp_path: Path):
    gw = _make_gw(tmp_path, {"dev": "Alice"})
    b1 = gw.agent_branch("dev")
    b2 = gw.agent_branch("dev")
    assert b1 == b2
    assert len([h for h in gw.repo.heads if h.name == b1]) == 1
