"""Tests for manifesto + project rules loading (M2 §3)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agents.base import BaseAgent


# ── load_manifesto ─────────────────────────────────────────────────────────


def test_load_manifesto_returns_content(tmp_path, monkeypatch):
    # Patch the working directory so BaseAgent finds the project file.
    project_id = "proj-abc"
    manifesto_dir = tmp_path / "data" / "projects" / project_id
    manifesto_dir.mkdir(parents=True)
    manifesto_path = manifesto_dir / "manifesto.md"
    manifesto_path.write_text("# THE OFFICE\n\nRule 1: WORK MOVES THROUGH GIT.", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    result = BaseAgent.load_manifesto(project_id)
    assert "THE OFFICE" in result
    assert "WORK MOVES THROUGH GIT" in result


def test_load_manifesto_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = BaseAgent.load_manifesto("nonexistent-project")
    assert result == ""


def test_load_manifesto_unreadable_returns_empty(tmp_path, monkeypatch):
    project_id = "proj-unreadable"
    manifesto_dir = tmp_path / "data" / "projects" / project_id
    manifesto_dir.mkdir(parents=True)
    # Create directory where file should be (makes it unreadable as file)
    (manifesto_dir / "manifesto.md").mkdir()  # dir, not file

    monkeypatch.chdir(tmp_path)
    result = BaseAgent.load_manifesto(project_id)
    assert result == ""


# ── load_project_rules ─────────────────────────────────────────────────────


def test_load_project_rules_shared_only(tmp_path, monkeypatch):
    project_id = "proj-rules"
    rules_dir = tmp_path / "data" / "projects" / project_id / "persona_rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "_shared.md").write_text("# Shared\n\nAlways write tests.", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    result = BaseAgent.load_project_rules(project_id, None)
    assert "Always write tests" in result


def test_load_project_rules_persona_specific(tmp_path, monkeypatch):
    project_id = "proj-persona"
    rules_dir = tmp_path / "data" / "projects" / project_id / "persona_rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "_shared.md").write_text("Shared rule.", encoding="utf-8")
    (rules_dir / "aria.md").write_text("Aria-specific rule.", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    result = BaseAgent.load_project_rules(project_id, "aria")
    assert "Shared rule" in result
    assert "Aria-specific rule" in result


def test_load_project_rules_separates_with_hr(tmp_path, monkeypatch):
    project_id = "proj-sep"
    rules_dir = tmp_path / "data" / "projects" / project_id / "persona_rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "_shared.md").write_text("Shared.", encoding="utf-8")
    (rules_dir / "bob.md").write_text("Personal.", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    result = BaseAgent.load_project_rules(project_id, "bob")
    assert "---" in result  # separator between shared and personal


def test_load_project_rules_empty_files_excluded(tmp_path, monkeypatch):
    project_id = "proj-empty"
    rules_dir = tmp_path / "data" / "projects" / project_id / "persona_rules"
    rules_dir.mkdir(parents=True)
    # Empty shared file
    (rules_dir / "_shared.md").write_text("   \n  ", encoding="utf-8")
    (rules_dir / "alice.md").write_text("Alice rule.", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    result = BaseAgent.load_project_rules(project_id, "alice")
    assert "---" not in result  # no separator since shared was empty
    assert "Alice rule" in result


def test_load_project_rules_no_rules_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = BaseAgent.load_project_rules("ghost-project", "aria")
    assert result == ""


def test_load_project_rules_no_persona_file(tmp_path, monkeypatch):
    project_id = "proj-nopers"
    rules_dir = tmp_path / "data" / "projects" / project_id / "persona_rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "_shared.md").write_text("Shared only.", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    result = BaseAgent.load_project_rules(project_id, "nonexistent-persona")
    assert "Shared only" in result
    assert "---" not in result


# ── effective_system_prompt layer ordering ─────────────────────────────────


def _make_agent(**kwargs) -> BaseAgent:
    return BaseAgent(role="backend", **kwargs)


@pytest.mark.asyncio
async def test_manifesto_appears_in_prompt():
    agent = _make_agent(
        system_prompt="Do backend work.",
        _manifesto="# THE OFFICE\n\nRule: WORK MOVES THROUGH GIT.",
    )
    prompt = await agent.effective_system_prompt()
    assert "THE OFFICE" in prompt
    assert "WORK MOVES THROUGH GIT" in prompt


@pytest.mark.asyncio
async def test_project_rules_appear_in_prompt():
    agent = _make_agent(
        system_prompt="Do work.",
        _project_rules="Always write tests.\n\nNo debug prints.",
    )
    prompt = await agent.effective_system_prompt()
    assert "Always write tests" in prompt


@pytest.mark.asyncio
async def test_manifesto_before_project_rules():
    agent = _make_agent(
        system_prompt="Do work.",
        _manifesto="MANIFESTO_MARKER",
        _project_rules="RULES_MARKER",
    )
    prompt = await agent.effective_system_prompt()
    manifesto_pos = prompt.index("MANIFESTO_MARKER")
    rules_pos = prompt.index("RULES_MARKER")
    assert manifesto_pos < rules_pos, "Manifesto (3.6) must appear before project rules (3.7)"


@pytest.mark.asyncio
async def test_teammate_brief_before_manifesto():
    agent = _make_agent(
        system_prompt="Do work.",
        _manifesto="MANIFESTO_MARKER",
    )
    # teammate_brief is passed as an arg to effective_system_prompt (used by runner before each LLM call)
    prompt = await agent.effective_system_prompt(teammate_brief="BRIEF_MARKER")
    brief_pos = prompt.index("BRIEF_MARKER")
    manifesto_pos = prompt.index("MANIFESTO_MARKER")
    assert brief_pos < manifesto_pos, "Teammate brief (3.5) must appear before manifesto (3.6)"


@pytest.mark.asyncio
async def test_prompt_ends_with_proud_line():
    agent = _make_agent(system_prompt="Do work.")
    prompt = await agent.effective_system_prompt()
    assert prompt.rstrip().endswith("Make this something you are proud of.")


@pytest.mark.asyncio
async def test_manifesto_absent_when_empty():
    agent = _make_agent(system_prompt="Do work.", _manifesto="")
    prompt = await agent.effective_system_prompt()
    assert "OFFICE RULES" not in prompt


def test_rules_and_manifesto_load_from_the_data_root(tmp_path, monkeypatch):
    """An install with its own data folder (GUUDFLOW_DATA_DIR) passes `projects_root`."""
    monkeypatch.chdir(tmp_path)  # nothing under ./data
    root = tmp_path / "elsewhere" / "projects"
    (root / "p" / "persona_rules").mkdir(parents=True)
    (root / "p" / "manifesto.md").write_text("OFFICE RULE", encoding="utf-8")
    (root / "p" / "persona_rules" / "_shared.md").write_text("SHARED RULE", encoding="utf-8")
    (root / "p" / "persona_rules" / "sol.md").write_text("SOL RULE", encoding="utf-8")
    assert BaseAgent.load_manifesto("p") == ""
    assert BaseAgent.load_manifesto("p", root) == "OFFICE RULE"
    rules = BaseAgent.load_project_rules("p", "sol", root)
    assert "SHARED RULE" in rules and "SOL RULE" in rules
