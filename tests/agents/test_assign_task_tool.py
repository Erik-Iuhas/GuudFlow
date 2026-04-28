"""assign-task tool + RBAC scope — M3.5 Chunk 3.

Two surfaces under test:

1. AssignTask itself — argument validation, task creation, signal
   emission. Stateless tool contract per BaseTool.
2. RBAC integration via ToolManifestBuilder — only the four planning
   archetypes (tech_lead, architect, requirements_writer, meta) hold
   the `assign-task` scope by default.
"""

from __future__ import annotations

import pytest

from agents.builtin.architect import Architect
from agents.builtin.meta import MetaAgent
from agents.builtin.requirements_writer import RequirementsWriter
from agents.builtin.tech_lead import TechLead
from agents.tools.board import AssignTask
from core.signals import SignalType
from core.tool_manifest import (
    LocalToolEntry,
    RoleRBAC,
    ToolManifestBuilder,
)
from core.workspace import Workspace


def _ws() -> Workspace:
    return Workspace(run_id="r1", goal="g")


# ── AssignTask tool unit tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assign_task_creates_task_and_emits_signals() -> None:
    ws = _ws()
    tool = AssignTask("architect", ws)

    result = await tool.run({
        "assignee": "developer",
        "goal": "implement issuer",
        "acceptance": ["rs256 signing", "key rotation"],
        "out_of_scope": ["oauth2 social"],
        "references": [
            {"kind": "file", "value": "docs/auth.md", "note": "design"},
        ],
    })

    assert result["ok"] is True
    task_id = result["task_id"]
    assert task_id and task_id in ws.tasks
    assert result["assignee"] == "developer"
    assert result["warnings"] == []

    task = ws.tasks[task_id]
    assert task.assigned_to == "developer"
    assert task.created_by == "architect"
    assert task.acceptance_criteria == ["rs256 signing", "key rotation"]
    assert task.out_of_scope == ["oauth2 social"]
    assert len(task.references) == 1
    assert task.references[0].kind == "file"

    assigned = [s for s in ws.board if s.type == SignalType.TASK_ASSIGNED]
    assert len(assigned) == 1
    proto = [
        s for s in ws.board
        if s.type == SignalType.PROTOCOL and (s.detail or "").startswith("ASSIGN_TASK")
    ]
    assert len(proto) == 1
    assert "assignee" not in proto[0].detail  # rendered protocol, not JSON


@pytest.mark.asyncio
async def test_assign_task_subtask_path() -> None:
    ws = _ws()
    tool = AssignTask("architect", ws)
    parent = await tool.run({"assignee": "lead", "goal": "build auth"})
    child = await tool.run({
        "assignee": "developer",
        "goal": "build issuer",
        "parent_task": parent["task_id"],
    })
    assert child["ok"] is True
    assert ws.tasks[child["task_id"]].parent_task_id == parent["task_id"]


@pytest.mark.asyncio
async def test_assign_task_subtask_depth_cap_returns_error() -> None:
    ws = _ws()
    tool = AssignTask("architect", ws)
    grand = await tool.run({"assignee": "lead", "goal": "g"})
    parent = await tool.run({
        "assignee": "lead",
        "goal": "p",
        "parent_task": grand["task_id"],
    })
    leaf = await tool.run({
        "assignee": "dev",
        "goal": "leaf",
        "parent_task": parent["task_id"],
    })
    assert "error" in leaf
    assert "depth cap" in leaf["error"]


@pytest.mark.asyncio
async def test_assign_task_empty_acceptance_returns_warning() -> None:
    ws = _ws()
    tool = AssignTask("architect", ws)
    result = await tool.run({"assignee": "developer", "goal": "do the thing"})
    assert result["ok"] is True
    assert result["warnings"]
    assert "acceptance" in result["warnings"][0]


@pytest.mark.asyncio
async def test_assign_task_missing_goal_returns_error() -> None:
    ws = _ws()
    tool = AssignTask("architect", ws)
    result = await tool.run({"assignee": "developer"})
    assert "error" in result
    assert "goal" in result["error"]


@pytest.mark.asyncio
async def test_assign_task_invalid_ref_kind_returns_error() -> None:
    ws = _ws()
    tool = AssignTask("architect", ws)
    result = await tool.run({
        "assignee": "developer",
        "goal": "g",
        "references": [{"kind": "url", "value": "https://example.com"}],
    })
    assert "error" in result
    assert "kind" in result["error"]


@pytest.mark.asyncio
async def test_assign_task_defaults_story_from_active_story() -> None:
    ws = _ws()
    ws.active_story["architect"] = "story_xyz"
    tool = AssignTask("architect", ws)
    result = await tool.run({"assignee": "developer", "goal": "g"})
    task = ws.tasks[result["task_id"]]
    assert task.story_id == "story_xyz"


# ── RBAC manifest integration ───────────────────────────────────────────────


def _builder_for(allowed: list[str]) -> ToolManifestBuilder:
    """Builder with `assign-task` registered as a local entry and the
    given role-RBAC. Lets us exercise just the gating path."""
    registry = {
        AssignTask.name: LocalToolEntry(
            name=AssignTask.name,
            description=AssignTask.description,
            input_schema=AssignTask.input_schema,
        ),
    }
    rbac = {"the_role": RoleRBAC(role="the_role", allowed_scopes=allowed)}
    return ToolManifestBuilder(local_registry=registry, mcp_catalogs={}, rbac=rbac)


def test_specialist_without_scope_cannot_see_assign_task() -> None:
    builder = _builder_for(allowed=["fs-write"])
    manifest = builder.build_for_agent(
        role="the_role",
        declared_local_tools=[AssignTask.name, "fs-write"],
        declared_mcp_servers=[],
    )
    names = {d.name for d in manifest.descriptors}
    assert AssignTask.name not in names


def test_planner_with_scope_sees_assign_task() -> None:
    builder = _builder_for(allowed=["assign-task"])
    manifest = builder.build_for_agent(
        role="the_role",
        declared_local_tools=[AssignTask.name],
        declared_mcp_servers=[],
    )
    names = {d.name for d in manifest.descriptors}
    assert AssignTask.name in names
    desc = next(d for d in manifest.descriptors if d.name == AssignTask.name)
    assert desc.scope == "assign-task"


def test_planner_archetype_classes_declare_assign_task_scope() -> None:
    """The four planning archetypes (DESIGN §38.12 D2) hold the scope.
    Concrete classes pulled in to confirm the meta-agent / catalog read
    the right defaults at proposal time."""
    assert "assign-task" in TechLead.default_rbac_scopes
    assert "assign-task" in Architect.default_rbac_scopes
    assert "assign-task" in RequirementsWriter.default_rbac_scopes
    assert "assign-task" in MetaAgent.default_rbac_scopes


def test_planner_archetype_classes_declare_assign_task_tool() -> None:
    """default_tools must include assign-task so the LLM sees it on
    the manifest at runtime (declared_local_tools is sourced from
    archetype.default_tools by the meta-agent)."""
    assert "assign-task" in TechLead.default_tools
    assert "assign-task" in Architect.default_tools
    assert "assign-task" in RequirementsWriter.default_tools
