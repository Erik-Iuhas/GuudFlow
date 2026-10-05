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
    """Track 7 — cap is now 3 levels (root → subtask → sub-subtask).
    Depth-4 still rejected so the manifest-coordination case stays bounded."""
    ws = _ws()
    tool = AssignTask("architect", ws)
    root = await tool.run({"assignee": "lead", "goal": "root"})
    sub = await tool.run({
        "assignee": "lead", "goal": "sub", "parent_task": root["task_id"],
    })
    # Depth-3 is now allowed.
    sub_sub = await tool.run({
        "assignee": "dev", "goal": "sub-sub", "parent_task": sub["task_id"],
    })
    assert sub_sub["ok"] is True
    # Depth-4 must still error.
    deep = await tool.run({
        "assignee": "dev", "goal": "too deep",
        "parent_task": sub_sub["task_id"],
    })
    assert "error" in deep
    assert "depth cap" in deep["error"]


@pytest.mark.asyncio
async def test_assign_task_with_subtasks_creates_parent_plus_children_atomically() -> None:
    """Track 7 — `subtasks` field on assign-task creates parent + children
    in one shot, all assigned to the same assignee."""
    ws = _ws()
    tool = AssignTask("architect", ws)
    res = await tool.run({
        "assignee": "dev",
        "goal": "build the auth module",
        "acceptance": ["JWT issuer", "rate limit"],
        "subtasks": [
            {"goal": "scaffold routes", "acceptance": ["POST /login", "POST /logout"]},
            {"goal": "wire JWT signing"},
        ],
    })
    assert res["ok"] is True
    assert "task_id" in res
    assert "subtask_ids" in res
    assert len(res["subtask_ids"]) == 2

    # Parent + children all assigned to dev; children point at parent.
    parent = ws.tasks[res["task_id"]]
    assert parent.assigned_to == "dev"
    for tid in res["subtask_ids"]:
        child = ws.tasks[tid]
        assert child.assigned_to == "dev"
        assert child.parent_task_id == res["task_id"]
        assert child.status == "PENDING"

    # Acceptance preserved on the first child.
    first_child = ws.tasks[res["subtask_ids"][0]]
    assert first_child.acceptance_criteria == ["POST /login", "POST /logout"]
    # Empty acceptance on the second.
    second_child = ws.tasks[res["subtask_ids"][1]]
    assert second_child.acceptance_criteria == []


@pytest.mark.asyncio
async def test_assign_task_with_empty_subtasks_list_rejects() -> None:
    ws = _ws()
    tool = AssignTask("architect", ws)
    res = await tool.run({
        "assignee": "dev", "goal": "x", "subtasks": [],
    })
    assert "error" in res
    assert "non-empty" in res["error"]


@pytest.mark.asyncio
async def test_assign_task_subtasks_validation_does_not_create_parent() -> None:
    """If subtasks validation fails BEFORE parent creation, no task lands."""
    ws = _ws()
    tool = AssignTask("architect", ws)
    res = await tool.run({
        "assignee": "dev", "goal": "x",
        "subtasks": [{"acceptance": ["bad"]}],  # missing goal
    })
    assert "error" in res
    # Parent did NOT land — full validation was up-front.
    assert ws.tasks == {}


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
    """The three in-pipeline planner archetypes hold the scope. Meta
    is excluded — it runs outside the scheduler at proposal time and
    its job is to set the team up, not to assign work mid-run."""
    assert "assign-task" in Architect.default_rbac_scopes
    assert "assign-task" in RequirementsWriter.default_rbac_scopes
    # Meta does not get this scope — verify by absence.
    assert "assign-task" not in getattr(MetaAgent, "default_rbac_scopes", ())


def test_planner_archetype_classes_declare_assign_task_tool() -> None:
    """default_tools must include assign-task so the LLM sees it on
    the manifest at runtime (declared_local_tools is sourced from
    archetype.default_tools by the meta-agent)."""
    assert "assign-task" in Architect.default_tools
    assert "assign-task" in RequirementsWriter.default_tools
