"""M2 end-to-end integration tests — spec §9 assertions.

Covers:
1. Project creation seeds manifesto.md + persona_rules/_shared.md
2. DeskIndex builds two-layer index; project layer overrides persona layer
3. Auto-inject fires for matched task, respects token budget
4. desk-search tool returns relevant file snippets
5. desk-push writes file + posts DESK_HANDOFF signal
6. inputs/README.md generated with file summaries
7. meta_proposal reads inputs/README.md into MetaInputs.readme_index
8. history.json appended atomically by memory_writer
9. history.json rotates at 1000 entries (oldest → archive.jsonl)
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


# ── helpers ────────────────────────────────────────────────────────────────


def _write_desk(directory: Path, filename: str, triggers: list[str], body: str, priority: str = "normal") -> None:
    trigger_str = "[" + ", ".join(f'"{t}"' for t in triggers) + "]"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(
        f"---\nname: {filename.replace('.md','')}\ntriggers: {trigger_str}\npriority: {priority}\n---\n{body}",
        encoding="utf-8",
    )


# ── 1. Project creation seeds workspace files ──────────────────────────────


@pytest.mark.asyncio
async def test_project_creation_seeds_manifesto_and_rules(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Seed the default manifesto so it gets copied
    default_dir = tmp_path / "data" / "manifestos"
    default_dir.mkdir(parents=True)
    (default_dir / "default.md").write_text("# THE OFFICE\n\nRule 1: WORK MOVES THROUGH GIT.", encoding="utf-8")

    from api.storage import ProjectStore
    store = ProjectStore(tmp_path / "data")
    project = await store.create({
        "project_id": "m2-test-proj",
        "name": "M2 Test",
    })

    manifesto = tmp_path / "data" / "projects" / "m2-test-proj" / "manifesto.md"
    assert manifesto.exists(), "manifesto.md must be created on project creation"
    assert "THE OFFICE" in manifesto.read_text(encoding="utf-8")

    shared_rules = tmp_path / "data" / "projects" / "m2-test-proj" / "persona_rules" / "_shared.md"
    assert shared_rules.exists(), "persona_rules/_shared.md must be created on project creation"


@pytest.mark.asyncio
async def test_project_creation_seeds_manifesto_fallback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # No default manifesto on disk — should fall back to placeholder
    from api.storage import ProjectStore
    store = ProjectStore(tmp_path / "data")
    await store.create({"project_id": "fallback-proj", "name": "Fallback"})

    manifesto = tmp_path / "data" / "projects" / "fallback-proj" / "manifesto.md"
    assert manifesto.exists()
    content = manifesto.read_text(encoding="utf-8")
    assert "Project Manifesto" in content


# ── 2. DeskIndex two-layer merge ────────────────────────────────────────────


def test_two_layer_desk_merge(tmp_path):
    from agents.persona_desk import DeskIndex

    persona_dir = tmp_path / "persona_desk"
    project_dir = tmp_path / "project_desk"
    _write_desk(persona_dir, "rest.md", ["api", "rest"], "# REST\nPersona REST patterns.")
    _write_desk(project_dir, "auth.md", ["auth", "jwt"], "# Auth\nProject JWT rules.", priority="high")

    idx = DeskIndex.build(persona_dir, project_dir)
    assert not idx.is_empty()

    # Both layers visible in combined search
    results = idx.auto_inject("design the api rest auth jwt flow", "")
    layer_sources = {df.layer for df, _ in results}
    assert "persona" in layer_sources
    assert "project" in layer_sources


def test_project_layer_ranks_above_persona_for_same_trigger(tmp_path):
    from agents.persona_desk import DeskIndex

    persona_dir = tmp_path / "persona_desk"
    project_dir = tmp_path / "project_desk"
    _write_desk(persona_dir, "auth-persona.md", ["auth"], "Persona auth doc.", priority="normal")
    _write_desk(project_dir, "auth-project.md", ["auth"], "Project auth doc.", priority="normal")

    idx = DeskIndex.build(persona_dir, project_dir)
    results = idx.auto_inject("auth design", "")
    assert results[0][0].layer == "project", "Project overlay must appear first"


# ── 3. Auto-inject respects 2000-token budget ──────────────────────────────


def test_auto_inject_token_budget_respected(tmp_path):
    from agents.persona_desk import DeskIndex, AUTO_INJECT_TOKEN_BUDGET, _estimate_tokens

    desk = tmp_path / "desk"
    desk.mkdir()
    for i in range(5):
        big_body = f"word{i} " * 600  # ~600 tokens each
        (desk / f"doc{i}.md").write_text(
            f"---\nname: doc{i}\ntriggers: [api]\npriority: high\n---\n{big_body}",
            encoding="utf-8",
        )

    idx = DeskIndex.build(desk)
    results = idx.auto_inject("api design", "")
    total = sum(_estimate_tokens(s) for _, s in results)
    assert total <= AUTO_INJECT_TOKEN_BUDGET + 50


# ── 4. desk-search tool ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_desk_search_returns_matches(tmp_path):
    from agents.persona_desk import DeskIndex
    from agents.tools.desk_search import DeskSearchTool

    desk = tmp_path / "desk"
    _write_desk(desk, "jwt.md", ["jwt", "auth"], "# JWT\nAccess and refresh tokens.")
    _write_desk(desk, "rest.md", ["api", "rest"], "# REST\nHTTP methods.")

    idx = DeskIndex.build(desk)
    tool = DeskSearchTool(desk_index=idx, role="backend")

    result = await tool.run({"query": "jwt tokens auth"})
    assert "results" in result
    assert len(result["results"]) >= 1
    names = [r["name"] for r in result["results"]]
    assert any("jwt" in n.lower() or "JWT" in n for n in names)


@pytest.mark.asyncio
async def test_desk_search_empty_index(tmp_path):
    from agents.persona_desk import DeskIndex
    from agents.tools.desk_search import DeskSearchTool

    idx = DeskIndex.build(tmp_path / "nonexistent")
    tool = DeskSearchTool(desk_index=idx, role="backend")
    result = await tool.run({"query": "anything"})
    assert result.get("error") == "DESK_EMPTY"


@pytest.mark.asyncio
async def test_desk_search_no_matches(tmp_path):
    from agents.persona_desk import DeskIndex
    from agents.tools.desk_search import DeskSearchTool

    desk = tmp_path / "desk"
    _write_desk(desk, "auth.md", ["auth"], "# Auth\nJWT tokens.")
    idx = DeskIndex.build(desk)
    tool = DeskSearchTool(desk_index=idx, role="backend")
    result = await tool.run({"query": "completely unrelated zzz xyz"})
    assert result.get("error") == "NO_MATCHES"


# ── 5. desk-push writes file + posts signal ────────────────────────────────


@pytest.mark.asyncio
async def test_desk_push_creates_file_in_project_overlay(tmp_path):
    from agents.tools.desk_push import DeskPushTool

    desks_root = tmp_path / "persona_desks"
    workspace = MagicMock()
    workspace.post_signal = MagicMock()

    tool = DeskPushTool(
        sender_role="backend",
        project_id="proj",
        project_desks_root=desks_root,
        workspace=workspace,
        role_to_persona={"frontend": "kai"},
    )
    result = await tool.run({
        "target_role": "frontend",
        "doc_name": "auth-contract",
        "body": "# Auth Contract\nAll endpoints use Bearer.",
        "kind": "api-contract",
        "triggers": ["auth", "bearer"],
    })
    assert result["ok"] is True
    dest = Path(result["storage_path"])
    assert dest.exists()
    workspace.post_signal.assert_called_once()
    # Signal summary contains DESK_HANDOFF
    sig = workspace.post_signal.call_args[0][0]
    assert "DESK_HANDOFF" in sig.summary
    assert sig.from_agent == "backend"


@pytest.mark.asyncio
async def test_desk_push_written_file_parseable_as_deskfile(tmp_path):
    from agents.persona_desk import DeskFile
    from agents.tools.desk_push import DeskPushTool

    desks_root = tmp_path / "persona_desks"
    tool = DeskPushTool(
        sender_role="backend",
        project_id="proj",
        project_desks_root=desks_root,
        workspace=None,
        role_to_persona={"frontend": "kai"},
    )
    result = await tool.run({
        "target_role": "frontend",
        "doc_name": "my-schema",
        "body": "# Schema\nDetails here.",
        "triggers": ["schema", "design"],
        "kind": "design-schema",
        "priority": "high",
    })
    dest = Path(result["storage_path"])
    df = DeskFile.from_path(dest, "project")
    assert df is not None
    assert df.handoff_from == "backend"
    assert "schema" in df.triggers or "design" in df.triggers


# ── 6. inputs/README.md generation ────────────────────────────────────────


@pytest.mark.asyncio
async def test_inputs_readme_written_on_run(tmp_path):
    from core.file_workspace import FileWorkspace

    run_dir = tmp_path / "run1"
    inputs_dir = run_dir / "inputs"
    inputs_dir.mkdir(parents=True)
    (inputs_dir / "spec.md").write_text("# Spec\nDetails.", encoding="utf-8")
    (inputs_dir / "data.csv").write_text("a,b,c\n1,2,3\n", encoding="utf-8")

    fw = FileWorkspace(run_dir)
    readme_content = (
        "# inputs/\n\nFiles available to read at runtime (read-only).\n\n"
        "- spec.md — (markdown, 16 B)\n"
        "- data.csv — (csv, 12 B)\n"
    )
    await fw.write_input_readme(readme_content)

    readme = inputs_dir / "README.md"
    assert readme.exists()
    content = readme.read_text(encoding="utf-8")
    assert "inputs/" in content


# ── 7. meta_proposal reads inputs/README.md ────────────────────────────────


def test_meta_proposal_reads_readme_index(tmp_path, monkeypatch):
    from core.meta_proposal import MetaInputs, read_meta_inputs

    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    (inputs_dir / "README.md").write_text(
        "# inputs/\n\n- spec.md — project requirements\n- data.csv — sample data",
        encoding="utf-8",
    )
    (inputs_dir / "spec.md").write_text("# Spec", encoding="utf-8")

    meta = read_meta_inputs(tmp_path, project_id="test-proj", goal="Build something")
    assert meta.readme_index != "", "readme_index must be populated when README.md exists"
    assert "spec.md" in meta.readme_index


def test_meta_proposal_render_prepends_readme(tmp_path):
    from core.meta_proposal import MetaInputs, render_inputs

    meta = MetaInputs(
        project_id="test-proj",
        goal="Build something",
        readme_index="- spec.md — requirements\n",
    )
    rendered = render_inputs(meta)
    assert "spec.md — requirements" in rendered
    assert "Input files index" in rendered


# ── 8. history.json appended by memory_writer ──────────────────────────────


def test_history_append_creates_entry(tmp_path):
    from agents.builtin.memory_writer import MemoryWriter  # noqa: F401 — imported for __new__
    from agents.persona import Persona
    from unittest.mock import MagicMock

    persona_dir = tmp_path / "personas" / "aria"
    persona_dir.mkdir(parents=True)
    (persona_dir / "history.json").write_text('{"entries": []}', encoding="utf-8")

    persona = MagicMock(spec=Persona)
    persona._dir = str(persona_dir)
    persona.profile = MagicMock()
    persona.profile.name = "Aria"

    workspace = MagicMock()
    workspace.board = []
    workspace.agent_states = {}
    workspace.iteration = 3

    project = MagicMock()
    project.project_id = "test-proj"

    agent = MemoryWriter.__new__(MemoryWriter)
    agent._append_history(persona, "backend", workspace, "run-001", project)

    history = json.loads((persona_dir / "history.json").read_text(encoding="utf-8"))
    assert len(history["entries"]) == 1
    entry = history["entries"][0]
    assert entry["run_id"] == "run-001"
    assert entry["project_id"] == "test-proj"
    assert entry["role"] == "backend"
    assert entry["outcome"] == "completed"
    assert entry["branch"] == "agent/backend"


def test_history_append_is_idempotent_across_multiple_runs(tmp_path):
    from agents.builtin.memory_writer import MemoryWriter  # noqa: F401 — imported for __new__
    from agents.persona import Persona
    from unittest.mock import MagicMock

    persona_dir = tmp_path / "personas" / "bob"
    persona_dir.mkdir(parents=True)
    (persona_dir / "history.json").write_text('{"entries": []}', encoding="utf-8")

    persona = MagicMock(spec=Persona)
    persona._dir = str(persona_dir)
    persona.profile = MagicMock()
    persona.profile.name = "Bob"

    workspace = MagicMock()
    workspace.board = []
    workspace.agent_states = {}
    workspace.iteration = 1

    project = MagicMock()
    project.project_id = "proj"

    agent = MemoryWriter.__new__(MemoryWriter)
    for i in range(5):
        workspace.iteration = i
        agent._append_history(persona, "reviewer", workspace, f"run-{i:03d}", project)

    history = json.loads((persona_dir / "history.json").read_text(encoding="utf-8"))
    assert len(history["entries"]) == 5
    run_ids = [e["run_id"] for e in history["entries"]]
    assert run_ids == [f"run-{i:03d}" for i in range(5)]


# ── 9. history.json rotation at 1000 entries ──────────────────────────────


# ── 10. desk_seeds materialization (spec §9) ───────────────────────────────


def test_meta_agent_desk_seeds_materialized_in_proposal(tmp_path):
    """Spec §9: meta-agent proposal with desk_seeds parses correctly."""
    from core.meta_proposal import parse_proposal_args
    from agents.catalog import list_all_archetypes

    archetypes = list_all_archetypes()
    if not archetypes:
        pytest.skip("no archetypes registered")

    args = {
        "rationale": "Adding GraphQL conventions.",
        "agents": [
            {
                "role": "backend",
                "archetype": archetypes[0].name,
                "depends_on": [],
                "persona": {"action": "create_new", "name_hint": "Tom"},
            }
        ],
        "desk_seeds": {
            "tom-backend-v1": [
                {
                    "name": "graphql-conventions",
                    "triggers": ["graphql", "schema"],
                    "body": "# GraphQL\nUse PascalCase for types.",
                    "kind": "convention",
                    "priority": "high",
                },
            ]
        },
    }

    result = parse_proposal_args(args, archetypes, [])
    assert "tom-backend-v1" in result.desk_seeds
    seeds = result.desk_seeds["tom-backend-v1"]
    assert len(seeds) == 1
    assert seeds[0]["name"] == "graphql-conventions"
    assert "graphql" in seeds[0]["triggers"]


def test_desk_seeds_with_missing_required_fields_warns(tmp_path):
    """Seeds missing name/triggers/body are skipped with a warning."""
    from core.meta_proposal import parse_proposal_args
    from agents.catalog import list_all_archetypes

    archetypes = list_all_archetypes()
    if not archetypes:
        pytest.skip("no archetypes registered")

    args = {
        "rationale": "Test",
        "agents": [{"role": "backend", "archetype": archetypes[0].name, "depends_on": []}],
        "desk_seeds": {
            "tom-v1": [
                {"name": "incomplete"},  # missing triggers + body
            ]
        },
    }
    result = parse_proposal_args(args, archetypes, [])
    assert any("missing required fields" in w for w in result.warnings)
    assert "tom-v1" not in result.desk_seeds


def test_role_template_seeds_new_persona_desk(tmp_path):
    """Spec §9: bootstrap a new persona; assert persona desk contains role template files."""
    from worker.job_runner import _bootstrap_personas
    from core.runner import ProjectConfig, AgentConfig

    # Create fake role templates relative to cwd
    template_dir = tmp_path / "data" / "role_templates" / "backend" / "desk"
    template_dir.mkdir(parents=True)
    (template_dir / "rest-design.md").write_text(
        "---\ntriggers: [api]\n---\n# REST Design\n", encoding="utf-8"
    )
    (template_dir / "auth-patterns.md").write_text(
        "---\ntriggers: [auth]\n---\n# Auth\n", encoding="utf-8"
    )

    personas_root = tmp_path / "personas"
    project = ProjectConfig(
        project_id="test",
        goal="Test",
        agents=[AgentConfig(role="backend", persona_id="newbie-v1")],
    )

    import os
    cwd_before = os.getcwd()
    os.chdir(tmp_path)
    try:
        _bootstrap_personas(project, personas_root)
    finally:
        os.chdir(cwd_before)

    persona_desk = personas_root / "newbie-v1" / "desk"
    assert persona_desk.is_dir()
    assert (persona_desk / "rest-design.md").exists()
    assert (persona_desk / "auth-patterns.md").exists()


def test_no_role_template_means_empty_default_desk(tmp_path):
    """Spec §9: new persona for unknown role gets an empty desk (not a crash)."""
    from worker.job_runner import _bootstrap_personas
    from core.runner import ProjectConfig, AgentConfig

    personas_root = tmp_path / "personas"
    project = ProjectConfig(
        project_id="test",
        goal="Test",
        agents=[AgentConfig(role="unknown_role_xyz", persona_id="ghost-v1")],
    )

    import os
    cwd_before = os.getcwd()
    os.chdir(tmp_path)
    try:
        _bootstrap_personas(project, personas_root)
    finally:
        os.chdir(cwd_before)

    persona_desk = personas_root / "ghost-v1" / "desk"
    assert persona_desk.is_dir(), "desk dir must exist even when no template"
    md_files = list(persona_desk.glob("*.md"))
    assert md_files == [], "desk must be empty when no role template"


def test_history_rotation_archives_overflow(tmp_path):
    from agents.builtin.memory_writer import MemoryWriter  # noqa: F401 — imported for __new__
    from agents.persona import Persona
    from unittest.mock import MagicMock

    persona_dir = tmp_path / "personas" / "max"
    persona_dir.mkdir(parents=True)

    # Pre-seed with 1000 entries
    existing = {"entries": [{"run_id": f"old-{i}", "role": "backend"} for i in range(1000)]}
    (persona_dir / "history.json").write_text(json.dumps(existing), encoding="utf-8")

    persona = MagicMock(spec=Persona)
    persona._dir = str(persona_dir)
    persona.profile = MagicMock()
    persona.profile.name = "Max"

    workspace = MagicMock()
    workspace.board = []
    workspace.agent_states = {}
    workspace.iteration = 0

    project = MagicMock()
    project.project_id = "proj"

    agent = MemoryWriter.__new__(MemoryWriter)
    agent._append_history(persona, "backend", workspace, "run-new", project)

    history = json.loads((persona_dir / "history.json").read_text(encoding="utf-8"))
    # Inline store must be capped at 1000
    assert len(history["entries"]) == 1000
    # Archive must exist and contain the oldest overflow entry
    archive = persona_dir / "history.archive.jsonl"
    assert archive.exists()
    lines = [l for l in archive.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) >= 1
    first_archived = json.loads(lines[0])
    assert first_archived["run_id"] == "old-0"
    # Newest entry is still in inline store
    run_ids = [e["run_id"] for e in history["entries"]]
    assert "run-new" in run_ids
