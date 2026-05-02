"""Track 7 — decompose-task tool tests.

Covers both flows:
  - Self-decompose (no parent_task_id) — attaches to synthetic root
  - Receiver further-decompose (explicit parent) — refusal cases
"""
from __future__ import annotations

import pytest

from agents.tools.decompose_task import DecomposeTask
from core.workspace import Task, Workspace


def _ws() -> Workspace:
    return Workspace(run_id="r1", goal="g")


def _seed_synthetic_root(ws: Workspace, role: str) -> str:
    """Mirror the runner's synthetic-root creation (silent infrastructure)."""
    root_id = f"root:{role}"
    ws.tasks[root_id] = Task(
        task_id=root_id, story_id="", assigned_to=role,
        created_by="runner", goal="g", status="IN_PROGRESS",
    )
    ws.task_queues.setdefault(role, []).append(root_id)
    ws.active_subtask[role] = root_id
    return root_id


# ── Self-decompose path ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_self_decompose_attaches_to_synthetic_root() -> None:
    ws = _ws()
    _seed_synthetic_root(ws, "dev")
    tool = DecomposeTask("dev", ws)

    res = await tool.run({"subtasks": [
        {"goal": "scan src/", "acceptance": ["all .py files outlined"]},
        {"goal": "scan tests/"},
    ]})

    assert res["ok"] is True
    assert res["count"] == 2
    assert res["parent_task_id"] == "root:dev"
    assert len(res["subtask_ids"]) == 2

    # Children created with the right parent + assignee.
    for tid in res["subtask_ids"]:
        t = ws.tasks[tid]
        assert t.parent_task_id == "root:dev"
        assert t.assigned_to == "dev"
        assert t.created_by == "dev"
        assert t.status == "PENDING"

    # First subtask preserved acceptance criteria.
    first_id = res["subtask_ids"][0]
    assert ws.tasks[first_id].acceptance_criteria == ["all .py files outlined"]
    # Second subtask had no acceptance — empty list.
    second_id = res["subtask_ids"][1]
    assert ws.tasks[second_id].acceptance_criteria == []


@pytest.mark.asyncio
async def test_self_decompose_preserves_creation_order() -> None:
    ws = _ws()
    _seed_synthetic_root(ws, "dev")
    tool = DecomposeTask("dev", ws)

    res = await tool.run({"subtasks": [
        {"goal": "a"}, {"goal": "b"}, {"goal": "c"}, {"goal": "d"},
    ]})

    children = ws.child_tasks("root:dev")
    assert [t.goal for t in children] == ["a", "b", "c", "d"]
    assert [t.task_id for t in children] == res["subtask_ids"]


# ── Receiver further-decompose path ───────────────────────────────────────


@pytest.mark.asyncio
async def test_explicit_parent_happy_path() -> None:
    ws = _ws()
    # Architect assigned a task to dev (top-level, not under root).
    ws.add_task(Task(
        task_id="t1", story_id="s1", assigned_to="dev",
        created_by="architect", goal="big chunk",
    ))
    tool = DecomposeTask("dev", ws)

    res = await tool.run({
        "subtasks": [{"goal": "part 1"}, {"goal": "part 2"}],
        "parent_task_id": "t1",
    })
    assert res["ok"] is True
    assert res["parent_task_id"] == "t1"
    assert all(ws.tasks[tid].parent_task_id == "t1" for tid in res["subtask_ids"])


# ── Refusal cases ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refuses_unknown_parent() -> None:
    ws = _ws()
    _seed_synthetic_root(ws, "dev")
    tool = DecomposeTask("dev", ws)

    res = await tool.run({
        "subtasks": [{"goal": "x"}],
        "parent_task_id": "ghost",
    })
    assert "error" in res
    assert "ghost" in res["error"]


@pytest.mark.asyncio
async def test_refuses_parent_assigned_to_someone_else() -> None:
    ws = _ws()
    _seed_synthetic_root(ws, "dev")
    ws.add_task(Task(
        task_id="t1", story_id="s1", assigned_to="reviewer",
        created_by="architect", goal="not your task",
    ))
    tool = DecomposeTask("dev", ws)

    res = await tool.run({
        "subtasks": [{"goal": "x"}],
        "parent_task_id": "t1",
    })
    assert "error" in res
    assert "reviewer" in res["error"]


@pytest.mark.asyncio
async def test_refuses_parent_with_existing_children() -> None:
    ws = _ws()
    ws.add_task(Task(
        task_id="t1", story_id="s1", assigned_to="dev",
        created_by="architect", goal="parent",
    ))
    ws.add_task(Task(
        task_id="t2", story_id="s1", parent_task_id="t1", assigned_to="dev",
        created_by="dev", goal="existing child",
    ))
    tool = DecomposeTask("dev", ws)

    res = await tool.run({
        "subtasks": [{"goal": "another"}],
        "parent_task_id": "t1",
    })
    assert "error" in res
    assert "children" in res["error"]


@pytest.mark.asyncio
async def test_refuses_empty_subtasks_list() -> None:
    ws = _ws()
    _seed_synthetic_root(ws, "dev")
    tool = DecomposeTask("dev", ws)

    res = await tool.run({"subtasks": []})
    assert "error" in res
    assert "non-empty" in res["error"]


@pytest.mark.asyncio
async def test_refuses_subtask_without_goal() -> None:
    ws = _ws()
    _seed_synthetic_root(ws, "dev")
    tool = DecomposeTask("dev", ws)

    res = await tool.run({"subtasks": [{"acceptance": ["x"]}]})
    assert "error" in res
    assert "goal" in res["error"]


@pytest.mark.asyncio
async def test_refuses_when_synthetic_root_already_decomposed() -> None:
    """Self-decompose twice: second call must refuse, otherwise the
    agent loses track of which decomposition is live."""
    ws = _ws()
    _seed_synthetic_root(ws, "dev")
    tool = DecomposeTask("dev", ws)

    first = await tool.run({"subtasks": [{"goal": "a"}, {"goal": "b"}]})
    assert first["ok"]
    second = await tool.run({"subtasks": [{"goal": "c"}]})
    assert "error" in second
    assert "children" in second["error"]


@pytest.mark.asyncio
async def test_validates_all_subtasks_before_writing() -> None:
    """If subtask 3 of 4 is malformed, no children get created."""
    ws = _ws()
    _seed_synthetic_root(ws, "dev")
    tool = DecomposeTask("dev", ws)

    res = await tool.run({"subtasks": [
        {"goal": "a"},
        {"goal": "b"},
        {"acceptance": ["bad"]},  # missing goal
        {"goal": "d"},
    ]})
    assert "error" in res
    assert ws.child_tasks("root:dev") == []  # no partial state


# ── Smart-parent gate (independence validator) ─────────────────────────────


@pytest.mark.asyncio
async def test_validator_rejects_chained_subtasks() -> None:
    """The runner injects a validator that classifies subtask sets as
    INDEPENDENT vs CHAINED. Chained sets must be refused before any
    workspace mutation, with the validator's reason surfaced as the error."""
    ws = _ws()
    _seed_synthetic_root(ws, "dev")

    async def _stub_validator(specs):
        return {"ok": False, "reason": "subtask 2 imports from subtask 1"}

    tool = DecomposeTask("dev", ws, independence_validator=_stub_validator)
    res = await tool.run({"subtasks": [
        {"goal": "build state schema"},
        {"goal": "use state schema for entities"},
    ]})
    assert "error" in res
    assert "imports from subtask 1" in res["error"]
    # No partial state.
    assert ws.child_tasks("root:dev") == []


@pytest.mark.asyncio
async def test_validator_passes_independent_subtasks() -> None:
    ws = _ws()
    _seed_synthetic_root(ws, "dev")

    async def _stub_validator(specs):
        return {"ok": True, "reason": "no inter-subtask references"}

    tool = DecomposeTask("dev", ws, independence_validator=_stub_validator)
    res = await tool.run({"subtasks": [
        {"goal": "fix lint warning in util.py"},
        {"goal": "fix lint warning in helper.py"},
    ]})
    assert res.get("ok") is True
    assert len(res["subtask_ids"]) == 2


@pytest.mark.asyncio
async def test_validator_skips_when_only_one_subtask() -> None:
    """Single subtask can't be coupled with anything — skip the gate."""
    ws = _ws()
    _seed_synthetic_root(ws, "dev")

    called = {"count": 0}

    async def _stub_validator(specs):
        called["count"] += 1
        return {"ok": False, "reason": "should not be called"}

    tool = DecomposeTask("dev", ws, independence_validator=_stub_validator)
    res = await tool.run({"subtasks": [{"goal": "do the only thing"}]})
    assert res.get("ok") is True
    assert called["count"] == 0


@pytest.mark.asyncio
async def test_validator_failure_fails_open() -> None:
    """If the validator raises (network error, bad model), allow the
    decomposition through rather than block work on infrastructure issues."""
    ws = _ws()
    _seed_synthetic_root(ws, "dev")

    async def _broken_validator(specs):
        raise RuntimeError("API down")

    tool = DecomposeTask("dev", ws, independence_validator=_broken_validator)
    res = await tool.run({"subtasks": [{"goal": "a"}, {"goal": "b"}]})
    assert res.get("ok") is True
