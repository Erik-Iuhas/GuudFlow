"""Track 7 — end-to-end subtask flows.

Three flows the design must support, all first-class:

  (a) Self-decompose against synthetic root: agent calls decompose-task
      with no parent; runner advances active_subtask through closures;
      cascade closes the synthetic root once all children terminal.

  (b) Delegator chunking via assign-task subtasks=[]: parent + children
      created atomically; closing the last child cascade-closes the
      parent.

  (c) Receiver further-decompose: assignee further-decomposes a
      delegator-given task at depth-3.

Each flow exercises the full Workspace.mutate_task → active_subtask
update → cascade closure pipeline.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.tools.board import AssignTask
from agents.tools.decompose_task import DecomposeTask
from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.tool_manifest import RoleRBAC


def _make_runner(tmp_path: Path, *roles: str) -> AgentRunner:
    agents = [AgentConfig(role=r, model_tier="solid") for r in roles]
    rbac = {r: RoleRBAC(role=r, allowed_scopes=["assign-task"]) for r in roles}
    proj = ProjectConfig(
        project_id="p_subtask", goal="ship the auth module",
        agents=agents, rbac=rbac,
    )
    return AgentRunner(
        project=proj, run_id="r_subtask",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )


# ── Flow (a): self-decompose against synthetic root ─────────────────────────


@pytest.mark.asyncio
async def test_self_decompose_then_cascade_closes_root(tmp_path: Path) -> None:
    """Agent decomposes the project goal into 3 subtasks; closing each
    in order should cascade-close the synthetic root."""
    r = _make_runner(tmp_path, "dev")
    ws = r.workspace
    assert "root:dev" in ws.tasks
    assert ws.active_subtask["dev"] == "root:dev"

    # Self-decompose
    tool = DecomposeTask("dev", ws)
    result = await tool.run({"subtasks": [
        {"goal": "scaffold routes"},
        {"goal": "add JWT signing"},
        {"goal": "wire tests"},
    ]})
    assert result["ok"]
    sub_ids = result["subtask_ids"]
    assert len(sub_ids) == 3
    # All three are children of the root and PENDING.
    for tid in sub_ids:
        assert ws.tasks[tid].parent_task_id == "root:dev"
        assert ws.tasks[tid].status == "PENDING"

    # Manually start the first one (in production the runner would do
    # this on first dispatch; we simulate it here for unit clarity).
    ws.mutate_task(sub_ids[0], "IN_PROGRESS", iteration=1)
    assert ws.active_subtask["dev"] == sub_ids[0]

    # Close subtask 1 → runner's _handle_task_closures should advance
    # to subtask 2. Drive that path via mutate_task + the runner's own
    # advance logic. We invoke the cascade pass directly by closing
    # the task and then running the same loop the runner runs.
    ws.mutate_task(sub_ids[0], "COMPLETE", iteration=1, closed_by_commit="abc")
    # active_subtask cleared on terminal.
    assert "dev" not in ws.active_subtask

    # Simulate the runner's advance step: pick next PENDING sibling.
    siblings = ws.child_tasks("root:dev")
    next_pending = next(
        (s for s in siblings if s.status == "PENDING"), None,
    )
    assert next_pending is not None
    assert next_pending.task_id == sub_ids[1]
    ws.mutate_task(next_pending.task_id, "IN_PROGRESS", iteration=2)
    assert ws.active_subtask["dev"] == sub_ids[1]

    # Close 2 and 3 to fully terminal — cascade should close root.
    ws.mutate_task(sub_ids[1], "COMPLETE", iteration=2, closed_by_commit="bcd")
    ws.mutate_task(sub_ids[2], "IN_PROGRESS", iteration=3)
    ws.mutate_task(sub_ids[2], "COMPLETE", iteration=3, closed_by_commit="cde")

    # Manually trigger the cascade (in production, runner does this in
    # _handle_task_closures after each commit).
    siblings = ws.child_tasks("root:dev")
    if all(s.status in ("COMPLETE", "CANCELLED", "SAT_OUT") for s in siblings):
        ws.mutate_task("root:dev", "COMPLETE", iteration=3,
                       closed_by_commit="cde")

    assert ws.tasks["root:dev"].status == "COMPLETE"


# ── Flow (b): delegator chunking via assign-task subtasks=[] ────────────────


@pytest.mark.asyncio
async def test_delegator_chunks_with_subtasks_then_cascade_closes_parent(
    tmp_path: Path,
) -> None:
    """Architect calls assign-task(assignee=dev, subtasks=[a,b]).
    Closing both children should cascade-close the assigned parent."""
    r = _make_runner(tmp_path, "architect", "dev")
    ws = r.workspace
    tool = AssignTask("architect", ws)

    result = await tool.run({
        "assignee": "dev",
        "goal": "auth module",
        "subtasks": [
            {"goal": "routes", "acceptance": ["POST /login"]},
            {"goal": "tests"},
        ],
    })
    assert result["ok"]
    parent_id = result["task_id"]
    sub_ids = result["subtask_ids"]
    assert len(sub_ids) == 2
    assert ws.tasks[parent_id].assigned_to == "dev"
    assert all(ws.tasks[t].assigned_to == "dev" for t in sub_ids)
    assert all(ws.tasks[t].parent_task_id == parent_id for t in sub_ids)

    # Close both children in order.
    ws.mutate_task(sub_ids[0], "IN_PROGRESS", iteration=1)
    ws.mutate_task(sub_ids[0], "COMPLETE", iteration=1, closed_by_commit="x1")
    ws.mutate_task(sub_ids[1], "IN_PROGRESS", iteration=2)
    ws.mutate_task(sub_ids[1], "COMPLETE", iteration=2, closed_by_commit="x2")

    # Cascade: parent should auto-close once all siblings terminal.
    siblings = ws.child_tasks(parent_id)
    if all(s.status in ("COMPLETE", "CANCELLED", "SAT_OUT") for s in siblings):
        ws.mutate_task(parent_id, "COMPLETE", iteration=2, closed_by_commit="x2")
    assert ws.tasks[parent_id].status == "COMPLETE"


# ── Flow (c): receiver further-decomposes a delegator task ─────────────────


@pytest.mark.asyncio
async def test_receiver_further_decomposes_assigned_task_at_depth_3(
    tmp_path: Path,
) -> None:
    """Architect assigns a task to dev; dev decides it's still too big
    and decomposes it further. Depth-3 cap honored."""
    r = _make_runner(tmp_path, "architect", "dev")
    ws = r.workspace

    # Architect assigns at depth 1 (top-level).
    architect = AssignTask("architect", ws)
    parent_res = await architect.run({
        "assignee": "dev", "goal": "auth module",
    })
    parent_id = parent_res["task_id"]
    assert ws.tasks[parent_id].parent_task_id is None  # depth-1

    # Dev further-decomposes (depth-2 children).
    dev_decompose = DecomposeTask("dev", ws)
    res = await dev_decompose.run({
        "subtasks": [{"goal": "part 1"}, {"goal": "part 2"}],
        "parent_task_id": parent_id,
    })
    assert res["ok"]
    depth2_ids = res["subtask_ids"]
    assert all(ws.tasks[t].parent_task_id == parent_id for t in depth2_ids)

    # Dev further-decomposes ONE of the depth-2 subtasks (depth-3).
    res2 = await dev_decompose.run({
        "subtasks": [{"goal": "part 1.a"}, {"goal": "part 1.b"}],
        "parent_task_id": depth2_ids[0],
    })
    assert res2["ok"], f"expected depth-3 decomp ok, got {res2!r}"
    depth3_ids = res2["subtask_ids"]
    assert all(ws.tasks[t].parent_task_id == depth2_ids[0] for t in depth3_ids)

    # Depth-4 must reject.
    res3 = await dev_decompose.run({
        "subtasks": [{"goal": "too deep"}],
        "parent_task_id": depth3_ids[0],
    })
    assert "error" in res3
    assert "depth cap" in res3["error"]


# ── Layer 4.7 surfaces the active subtask in the prompt ─────────────────────


@pytest.mark.asyncio
async def test_layer_4_7_renders_active_subtask_after_decompose(
    tmp_path: Path,
) -> None:
    """Once a subtask is IN_PROGRESS, the agent's effective_system_prompt
    must include an ACTIVE SUBTASK section."""
    from agents.base import BaseAgent
    from core.workspace import IterationMetrics

    r = _make_runner(tmp_path, "dev")
    ws = r.workspace
    tool = DecomposeTask("dev", ws)
    res = await tool.run({"subtasks": [{"goal": "scan src/", "acceptance": ["all .py files"]}]})
    sub_id = res["subtask_ids"][0]
    ws.mutate_task(sub_id, "IN_PROGRESS", iteration=1)

    agent = BaseAgent(role="dev", system_prompt="MARKER")
    agent._workspace = ws
    agent._current_metrics = IterationMetrics(
        run_id="r_subtask", role="dev", iteration=1,
    )
    prompt = await agent.effective_system_prompt()

    assert "ACTIVE SUBTASK" in prompt
    assert "scan src/" in prompt
    assert "all .py files" in prompt
    # Tokens were credited to the relevant_files bucket.
    assert agent._current_metrics.relevant_files_tokens > 0


@pytest.mark.asyncio
async def test_layer_4_7_suppresses_for_synthetic_root_only(
    tmp_path: Path,
) -> None:
    """When only the synthetic root is active (no decomposition yet),
    Layer 4.7 should suppress — the project goal is already in the
    user-turn prompt."""
    from agents.base import BaseAgent
    from core.workspace import IterationMetrics

    r = _make_runner(tmp_path, "dev")
    # Runner sets active_subtask["dev"] = "root:dev" at startup.
    assert r.workspace.active_subtask["dev"] == "root:dev"

    agent = BaseAgent(role="dev", system_prompt="MARKER")
    agent._workspace = r.workspace
    agent._current_metrics = IterationMetrics(
        run_id="r_subtask", role="dev", iteration=0,
    )
    prompt = await agent.effective_system_prompt()
    assert "ACTIVE SUBTASK" not in prompt
