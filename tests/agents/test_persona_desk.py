"""Tests for agents/persona_desk.py — DeskFile, DeskIndex, trigger matching."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agents.persona_desk import (
    AUTO_INJECT_TOKEN_BUDGET,
    DeskFile,
    DeskIndex,
    _estimate_tokens,
    _parse_yaml_frontmatter,
    render_desk_inject_block,
)


# ── _parse_yaml_frontmatter ────────────────────────────────────────────────


def test_parse_yaml_valid():
    text = "---\nname: Test\ntriggers: [api, rest]\npriority: high\n---\n# Body\nHello"
    fm, body = _parse_yaml_frontmatter(text)
    assert fm["name"] == "Test"
    assert "api" in fm["triggers"]
    assert body.strip() == "# Body\nHello"


def test_parse_yaml_no_frontmatter():
    text = "# Just body\nNo frontmatter here."
    fm, body = _parse_yaml_frontmatter(text)
    assert fm == {}
    assert "Just body" in body


def test_parse_yaml_empty_frontmatter():
    text = "---\n---\n# Body"
    fm, body = _parse_yaml_frontmatter(text)
    assert fm == {}


# ── DeskFile.from_path ─────────────────────────────────────────────────────


def _make_desk_file(tmpdir: Path, filename: str, content: str, layer: str = "persona") -> DeskFile | None:
    p = tmpdir / filename
    p.write_text(content, encoding="utf-8")
    return DeskFile.from_path(p, layer)


def test_deskfile_loads_valid(tmp_path):
    content = (
        "---\nname: REST Design\ntriggers: [api, endpoint, rest]\n"
        "priority: high\nkind: reference\n---\n# REST\nUse plural nouns."
    )
    df = _make_desk_file(tmp_path, "rest.md", content)
    assert df is not None
    assert df.name == "REST Design"
    assert "api" in df.triggers
    assert df.priority == "high"
    assert df.kind == "reference"
    assert df.layer == "persona"
    assert "Use plural nouns" in df.body


def test_deskfile_missing_triggers_returns_none(tmp_path):
    content = "---\nname: No Triggers\n---\n# Body"
    df = _make_desk_file(tmp_path, "no_triggers.md", content)
    assert df is None


def test_deskfile_invalid_priority_defaults_to_normal(tmp_path):
    content = "---\ntriggers: [test]\npriority: extreme\n---\n# Body"
    df = _make_desk_file(tmp_path, "p.md", content)
    assert df is not None
    assert df.priority == "normal"


def test_deskfile_from_path_missing_file():
    result = DeskFile.from_path(Path("/nonexistent/path.md"), "persona")
    assert result is None


def test_deskfile_matches_role_empty_agents_is_any_role(tmp_path):
    content = "---\ntriggers: [api]\n---\n# Body"
    df = _make_desk_file(tmp_path, "f.md", content)
    assert df is not None
    assert df.matches_role("backend")
    assert df.matches_role("frontend")
    assert df.matches_role("reviewer")


def test_deskfile_matches_role_specific_agent(tmp_path):
    content = "---\ntriggers: [api]\nagents: [backend]\n---\n# Body"
    df = _make_desk_file(tmp_path, "f.md", content)
    assert df is not None
    assert df.matches_role("backend")
    assert not df.matches_role("frontend")


def test_deskfile_trigger_count_word_boundary(tmp_path):
    content = "---\ntriggers: [api, rest]\n---\n# Body"
    df = _make_desk_file(tmp_path, "f.md", content)
    assert df is not None
    # "api" as whole word
    assert df.trigger_count("design a rest api endpoint") == 2
    # "api" inside word should not match
    assert df.trigger_count("rapid testing") == 0


def test_deskfile_priority_rank():
    # use minimal construction
    df = DeskFile(
        name="x", triggers=["t"], body="b", agents=[], priority="high",
        kind="reference", tags=[], max_inject_tokens=800, layer="persona",
        source_path=Path("x.md"),
    )
    assert df.priority_rank == 2
    df.priority = "normal"
    assert df.priority_rank == 1
    df.priority = "low"
    assert df.priority_rank == 0


def test_deskfile_layer_rank():
    df = DeskFile(
        name="x", triggers=["t"], body="b", agents=[], priority="normal",
        kind="reference", tags=[], max_inject_tokens=800, layer="project",
        source_path=Path("x.md"),
    )
    assert df.layer_rank == 1
    df.layer = "persona"
    assert df.layer_rank == 0


# ── DeskIndex.build ────────────────────────────────────────────────────────


def test_deskindex_build_empty_dirs(tmp_path):
    idx = DeskIndex.build(tmp_path / "nope", tmp_path / "also_nope")
    assert idx.is_empty()


def test_deskindex_build_single_layer(tmp_path):
    desk = tmp_path / "desk"
    desk.mkdir()
    (desk / "rest.md").write_text(
        "---\ntriggers: [api, rest]\n---\n# REST\nContent.", encoding="utf-8"
    )
    idx = DeskIndex.build(desk)
    assert not idx.is_empty()


def test_deskindex_build_two_layers(tmp_path):
    persona_dir = tmp_path / "persona"
    project_dir = tmp_path / "project"
    persona_dir.mkdir()
    project_dir.mkdir()
    (persona_dir / "a.md").write_text("---\ntriggers: [alpha]\n---\nPersona A", encoding="utf-8")
    (project_dir / "b.md").write_text("---\ntriggers: [beta]\n---\nProject B", encoding="utf-8")

    idx = DeskIndex.build(persona_dir, project_dir)
    assert not idx.is_empty()
    # persona file: layer=persona, project file: layer=project
    layers = {df.layer for df, _ in idx.auto_inject("alpha beta", "")}
    assert "persona" in layers
    assert "project" in layers


def test_deskindex_skips_files_without_triggers(tmp_path):
    desk = tmp_path / "desk"
    desk.mkdir()
    (desk / "bad.md").write_text("---\nname: No triggers\n---\n# Body", encoding="utf-8")
    idx = DeskIndex.build(desk)
    assert idx.is_empty()


# ── DeskIndex.auto_inject ──────────────────────────────────────────────────


def test_auto_inject_returns_matching_files(tmp_path):
    desk = tmp_path / "desk"
    desk.mkdir()
    (desk / "rest.md").write_text(
        "---\ntriggers: [api, endpoint]\npriority: normal\n---\n# REST\nAPI design.", encoding="utf-8"
    )
    idx = DeskIndex.build(desk)
    results = idx.auto_inject("design the api endpoint", "")
    assert len(results) == 1
    df, snippet = results[0]
    assert "REST" in snippet or "API" in snippet


def test_auto_inject_no_match_returns_empty(tmp_path):
    desk = tmp_path / "desk"
    desk.mkdir()
    (desk / "rest.md").write_text(
        "---\ntriggers: [api]\npriority: normal\n---\n# REST\nContent.", encoding="utf-8"
    )
    idx = DeskIndex.build(desk)
    results = idx.auto_inject("write tests for the scheduler", "")
    assert results == []


def test_auto_inject_project_layer_wins_over_persona(tmp_path):
    persona_dir = tmp_path / "persona"
    project_dir = tmp_path / "project"
    persona_dir.mkdir()
    project_dir.mkdir()
    (persona_dir / "a.md").write_text(
        "---\ntriggers: [api]\npriority: normal\n---\nPersona content.", encoding="utf-8"
    )
    (project_dir / "b.md").write_text(
        "---\ntriggers: [api]\npriority: normal\n---\nProject content.", encoding="utf-8"
    )
    idx = DeskIndex.build(persona_dir, project_dir)
    results = idx.auto_inject("design an api", "")
    # Project layer has higher layer_rank so it should appear first
    assert results[0][0].layer == "project"


def test_auto_inject_total_tokens_capped(tmp_path):
    desk = tmp_path / "desk"
    desk.mkdir()
    # Create a file with large body that exceeds budget
    big_body = "word " * 3000  # ~15000 tokens
    (desk / "big.md").write_text(
        f"---\ntriggers: [api]\npriority: high\n---\n{big_body}", encoding="utf-8"
    )
    idx = DeskIndex.build(desk)
    results = idx.auto_inject("api endpoint", "")
    total_tokens = sum(_estimate_tokens(snippet) for _, snippet in results)
    assert total_tokens <= AUTO_INJECT_TOKEN_BUDGET + 50  # small rounding tolerance


def test_auto_inject_role_filter(tmp_path):
    desk = tmp_path / "desk"
    desk.mkdir()
    (desk / "backend_only.md").write_text(
        "---\ntriggers: [api]\nagents: [backend]\n---\nBackend only.", encoding="utf-8"
    )
    idx = DeskIndex.build(desk)
    assert len(idx.auto_inject("api design", "backend")) == 1
    assert len(idx.auto_inject("api design", "frontend")) == 0


# ── DeskIndex.search ───────────────────────────────────────────────────────


def test_search_returns_relevant_files(tmp_path):
    desk = tmp_path / "desk"
    desk.mkdir()
    (desk / "auth.md").write_text(
        "---\ntriggers: [auth]\n---\n# Auth\nJWT tokens and refresh patterns.", encoding="utf-8"
    )
    (desk / "rest.md").write_text(
        "---\ntriggers: [api]\n---\n# REST\nHTTP methods and status codes.", encoding="utf-8"
    )
    idx = DeskIndex.build(desk)
    results = idx.search("jwt tokens")
    names = [df.name for df, _ in results]
    assert "auth" in names or any("auth" in n.lower() for n in names)


def test_search_returns_empty_on_no_match(tmp_path):
    desk = tmp_path / "desk"
    desk.mkdir()
    (desk / "auth.md").write_text(
        "---\ntriggers: [auth]\n---\n# Auth\nJWT patterns.", encoding="utf-8"
    )
    idx = DeskIndex.build(desk)
    results = idx.search("completely unrelated xyz123")
    assert results == []


# ── render_desk_inject_block ───────────────────────────────────────────────


def test_render_block_empty():
    assert render_desk_inject_block([]) == ""


def test_render_block_includes_header_and_content(tmp_path):
    desk = tmp_path / "desk"
    desk.mkdir()
    (desk / "rest.md").write_text(
        "---\ntriggers: [api]\n---\n# REST\nUse plural nouns.", encoding="utf-8"
    )
    idx = DeskIndex.build(desk)
    results = idx.auto_inject("api endpoint", "")
    block = render_desk_inject_block(results)
    assert "=== YOUR DESK NOTES (auto-loaded) ===" in block
    assert "=== END DESK NOTES ===" in block
    assert "plural" in block


def test_render_block_includes_handoff_note(tmp_path):
    desk = tmp_path / "desk"
    desk.mkdir()
    (desk / "handoff.md").write_text(
        "---\ntriggers: [schema]\nhandoff_from: backend\nhandoff_note: Follow this carefully\n---\n# Schema",
        encoding="utf-8",
    )
    idx = DeskIndex.build(desk)
    results = idx.auto_inject("schema design", "")
    block = render_desk_inject_block(results)
    assert "HANDED OVER by backend" in block
    assert "Follow this carefully" in block
