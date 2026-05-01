"""M3.5 — task handoff integration tests.

Exercises the runner's `_handle_task_closures` and
`_maybe_auto_start_task` paths without spinning up a full LLM-backed
run. Each test plants tasks via `Workspace.add_task`, builds a
synthetic CompactBlock for the COMMIT, and verifies the resulting
state transitions on the workspace.

Per chunks.md: chunks 4–5 land the basic case; the full integration
matrix (resume, user_comment routing, depth caps via the API) lives
in Chunks 6–8.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core import protocol
from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.signals import Signal, SignalType
from core.workspace import AgentState, Task, TaskRef


def _runner(tmp_path: Path) -> AgentRunner:
    proj = ProjectConfig(project_id="p1", goal="g", agents=[], rbac={})
    return AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )


def _commit_block(closes: str | None = None) -> protocol.CompactBlock:
    fields: dict[str, str] = {"summary": "did the work"}
    if closes:
        fields["closes"] = closes
    return protocol.CompactBlock(kind="COMMIT", fields=fields)


# ── closes: bookkeeping ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_closes_marks_task_complete_and_emits_signal(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="developer",
        created_by="architect", goal="ship issuer",
    ))
    r.workspace.mutate_task("t1", "IN_PROGRESS", iteration=1)
    # Drain prior signals.
    starting_count = sum(1 for s in r.workspace.board if s.type == SignalType.TASK_COMPLETED)

    await r._handle_task_closures(
        "developer", _commit_block(closes="task:t1"), commit_hash="abc1234",
    )

    task = r.workspace.tasks["t1"]
    assert task.status == "COMPLETE"
    assert task.closed_by_commit == "abc1234"

    completed = [s for s in r.workspace.board if s.type == SignalType.TASK_COMPLETED]
    assert len(completed) == starting_count + 1


@pytest.mark.asyncio
async def test_closes_accepts_bare_id_as_well_as_prefixed(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="dev", goal="x",
    ))
    r.workspace.mutate_task("t1", "IN_PROGRESS")
    await r._handle_task_closures("dev", _commit_block(closes="t1"), commit_hash="hash")
    assert r.workspace.tasks["t1"].status == "COMPLETE"


@pytest.mark.asyncio
async def test_closing_other_roles_task_emits_warning_does_not_block(tmp_path: Path) -> None:
    """§4.3 — runner-side check is bookkeeping, not gating. The COMMIT
    is not blocked; the warning surfaces alongside it for the reviewer."""
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="developer", goal="x",
    ))
    r.workspace.mutate_task("t1", "IN_PROGRESS")

    await r._handle_task_closures(
        "wrong_role", _commit_block(closes="task:t1"), commit_hash="h",
    )
    # Task untouched.
    assert r.workspace.tasks["t1"].status == "IN_PROGRESS"
    # Warning posted.
    warnings = [s for s in r.workspace.board if s.type == SignalType.TASK_OUT_OF_SCOPE]
    assert len(warnings) == 1
    assert "wrong_role" in warnings[0].summary
    assert "developer" in warnings[0].summary


@pytest.mark.asyncio
async def test_closing_unknown_task_emits_warning(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    await r._handle_task_closures(
        "dev", _commit_block(closes="task:does_not_exist"), commit_hash="h",
    )
    warnings = [s for s in r.workspace.board if s.type == SignalType.TASK_OUT_OF_SCOPE]
    assert len(warnings) == 1
    assert "unknown" in warnings[0].summary


@pytest.mark.asyncio
async def test_closing_already_complete_emits_warning(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="dev", goal="x",
    ))
    r.workspace.mutate_task("t1", "IN_PROGRESS")
    r.workspace.mutate_task("t1", "COMPLETE", closed_by_commit="prior")

    await r._handle_task_closures("dev", _commit_block(closes="task:t1"), commit_hash="new")
    warnings = [s for s in r.workspace.board if s.type == SignalType.TASK_OUT_OF_SCOPE]
    assert len(warnings) == 1
    assert "COMPLETE" in warnings[0].detail


@pytest.mark.asyncio
async def test_closes_with_no_commit_hash_emits_warning(tmp_path: Path) -> None:
    """When _auto_commit returned None (clean working tree) but the
    agent emits closes: anyway, surface a TASK_OUT_OF_SCOPE warning so
    the reviewer knows nothing actually shipped against the named task.
    The closure itself still proceeds — bookkeeping, not gating."""
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="dev", goal="x",
    ))
    r.workspace.mutate_task("t1", "IN_PROGRESS")

    await r._handle_task_closures(
        "dev", _commit_block(closes="task:t1"), commit_hash=None,
    )

    warnings = [s for s in r.workspace.board if s.type == SignalType.TASK_OUT_OF_SCOPE]
    summaries = [w.summary for w in warnings]
    assert any("no git commit" in s for s in summaries)
    # The closure still fires — we don't block work product on this.
    assert r.workspace.tasks["t1"].status == "COMPLETE"
    assert r.workspace.tasks["t1"].closed_by_commit == ""


@pytest.mark.asyncio
async def test_commit_without_closes_no_op(tmp_path: Path) -> None:
    """Regression guard: M1 COMMIT flow unchanged when closes: absent."""
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="dev", goal="x",
    ))
    r.workspace.mutate_task("t1", "IN_PROGRESS")
    snapshot = list(r.workspace.board)

    await r._handle_task_closures("dev", _commit_block(closes=None), commit_hash="h")

    assert r.workspace.tasks["t1"].status == "IN_PROGRESS"
    assert r.workspace.board == snapshot


@pytest.mark.asyncio
async def test_handle_task_closures_ignores_non_commit_blocks(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="t1", story_id="s", assigned_to="dev", goal="x",
    ))
    verify_block = protocol.CompactBlock(
        kind="VERIFY", fields={"result": "PASS"},
    )
    await r._handle_task_closures("dev", verify_block, commit_hash="h")
    assert r.workspace.tasks["t1"].status == "PENDING"


# ── subtask cascade ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_closing_last_child_auto_closes_parent(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="parent", story_id="s", assigned_to="lead", goal="auth",
    ))
    r.workspace.add_task(Task(
        task_id="c1", story_id="s", assigned_to="dev",
        parent_task_id="parent", goal="issuer",
    ))
    r.workspace.add_task(Task(
        task_id="c2", story_id="s", assigned_to="dev",
        parent_task_id="parent", goal="verifier",
    ))
    r.workspace.mutate_task("parent", "IN_PROGRESS")
    r.workspace.mutate_task("c1", "IN_PROGRESS")
    r.workspace.mutate_task("c2", "IN_PROGRESS")

    # Close the first child — parent should NOT cascade yet.
    await r._handle_task_closures("dev", _commit_block(closes="task:c1"), commit_hash="h1")
    assert r.workspace.tasks["c1"].status == "COMPLETE"
    assert r.workspace.tasks["parent"].status == "IN_PROGRESS"

    # Close the last child — parent cascades.
    await r._handle_task_closures("dev", _commit_block(closes="task:c2"), commit_hash="h2")
    assert r.workspace.tasks["c2"].status == "COMPLETE"
    parent = r.workspace.tasks["parent"]
    assert parent.status == "COMPLETE"
    assert parent.closed_by_commit == "h2"


@pytest.mark.asyncio
async def test_parent_does_not_cascade_when_child_blocked(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="parent", story_id="s", assigned_to="lead", goal="p",
    ))
    r.workspace.add_task(Task(
        task_id="c1", story_id="s", assigned_to="dev",
        parent_task_id="parent", goal="c1",
    ))
    r.workspace.add_task(Task(
        task_id="c2", story_id="s", assigned_to="dev",
        parent_task_id="parent", goal="c2",
    ))
    r.workspace.mutate_task("parent", "IN_PROGRESS")
    r.workspace.mutate_task("c1", "IN_PROGRESS")
    r.workspace.mutate_task("c2", "BLOCKED", blocked_on=["external"])

    await r._handle_task_closures("dev", _commit_block(closes="task:c1"), commit_hash="h")
    assert r.workspace.tasks["parent"].status == "IN_PROGRESS"


# ── TASK_STARTED auto-emit ──────────────────────────────────────────────────


def test_auto_start_picks_fifo_pending(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(task_id="t1", story_id="s", assigned_to="dev", goal="a"))
    r.workspace.add_task(Task(task_id="t2", story_id="s", assigned_to="dev", goal="b"))

    r._maybe_auto_start_task("dev", tool_arguments={"path": "x"})

    assert r.workspace.tasks["t1"].status == "IN_PROGRESS"
    assert r.workspace.tasks["t2"].status == "PENDING"
    started = [s for s in r.workspace.board if s.type == SignalType.TASK_STARTED]
    assert len(started) == 1


def test_auto_start_honours_explicit_task_id_in_args(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(task_id="t1", story_id="s", assigned_to="dev", goal="a"))
    r.workspace.add_task(Task(task_id="t2", story_id="s", assigned_to="dev", goal="b"))

    r._maybe_auto_start_task("dev", tool_arguments={"task_id": "task:t2"})
    assert r.workspace.tasks["t1"].status == "PENDING"
    assert r.workspace.tasks["t2"].status == "IN_PROGRESS"


def test_auto_start_no_op_when_already_in_progress(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(task_id="t1", story_id="s", assigned_to="dev", goal="a"))
    r.workspace.mutate_task("t1", "IN_PROGRESS")
    started_before = sum(1 for s in r.workspace.board if s.type == SignalType.TASK_STARTED)

    r._maybe_auto_start_task("dev")
    assert sum(1 for s in r.workspace.board if s.type == SignalType.TASK_STARTED) == started_before


def test_auto_start_no_op_when_desk_empty(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r._maybe_auto_start_task("dev")  # should not raise


def test_auto_start_ignores_unknown_task_id_falls_back_to_fifo(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(task_id="t1", story_id="s", assigned_to="dev", goal="a"))
    r._maybe_auto_start_task("dev", tool_arguments={"task_id": "task:bogus"})
    # Falls back to FIFO pickup rather than emitting noise.
    assert r.workspace.tasks["t1"].status == "IN_PROGRESS"


# ── End-to-end mini flow: assign → start → close → cascade ──────────────────


@pytest.mark.asyncio
async def test_end_to_end_assign_start_close_cascade(tmp_path: Path) -> None:
    """Architect assigns parent + 2 children to developer; developer
    closes both children via COMMIT closes:; parent auto-cascades."""
    r = _runner(tmp_path)
    ws = r.workspace

    # Architect plants the work.
    ws.add_task(Task(
        task_id="parent", story_id="s", assigned_to="developer",
        created_by="architect", goal="auth",
        references=[TaskRef(kind="file", value="docs/auth.md", note="design")],
    ))
    ws.add_task(Task(
        task_id="c1", story_id="s", assigned_to="developer",
        created_by="architect", parent_task_id="parent", goal="issuer",
        acceptance_criteria=["rs256"],
    ))
    ws.add_task(Task(
        task_id="c2", story_id="s", assigned_to="developer",
        created_by="architect", parent_task_id="parent", goal="verifier",
        acceptance_criteria=["validates"],
    ))

    # Developer's first tool call kicks off auto-start.
    r._maybe_auto_start_task("developer", tool_arguments={"path": "src/issuer.py"})
    assert ws.tasks["parent"].status == "IN_PROGRESS"

    # Developer closes c1.
    await r._handle_task_closures(
        "developer", _commit_block(closes="task:c1"), commit_hash="hash1",
    )
    assert ws.tasks["c1"].status == "COMPLETE"
    assert ws.tasks["parent"].status == "IN_PROGRESS"

    # Developer closes c2 — parent cascades.
    await r._handle_task_closures(
        "developer", _commit_block(closes="task:c2"), commit_hash="hash2",
    )
    assert ws.tasks["c2"].status == "COMPLETE"
    assert ws.tasks["parent"].status == "COMPLETE"
    assert ws.tasks["parent"].closed_by_commit == "hash2"


# ── USER_COMMENT routing (Chunk 6, DESIGN §38.12 §4.5) ─────────────────────


def _runner_with_team(tmp_path: Path, *team: tuple[str, str]) -> AgentRunner:
    """Construct a runner with a team. Each entry is
    (role, agent_class_role_type) — the role_type drives planner
    detection in the routing path."""
    agents = [
        AgentConfig(role=role, agent_class_role_type=rt) for role, rt in team
    ]
    proj = ProjectConfig(project_id="p1", goal="g", agents=agents, rbac={})
    return AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )


def _post_user_comment(ws: Any, text: str = "what's the status?") -> Signal:
    sig = Signal(type=SignalType.USER_COMMENT, summary=text, from_user=True)
    ws.post_signal(sig)
    return sig


def test_user_comment_lands_on_tech_lead_desk(tmp_path: Path) -> None:
    r = _runner_with_team(
        tmp_path, ("lead", "tech_lead"), ("dev", None),
    )
    sig = _post_user_comment(r.workspace, "please add unit tests")

    desk = r.workspace.desk_for("lead")
    assert len(desk) == 1
    task = desk[0]
    assert task.task_id.startswith("comment_")
    assert "respond to user comment" in task.goal
    assert "please add unit tests" in task.goal
    assert task.created_by == "user"
    # The comment is marked seen by the absorbing planner so their
    # check_user_board() doesn't double-process.
    assert "lead" in sig.seen_by


def test_user_comment_with_no_planner_no_synthetic_task(tmp_path: Path) -> None:
    """Empty-planner team falls back to today's check_user_board flow."""
    r = _runner_with_team(
        tmp_path, ("dev", None), ("reviewer", "code_reviewer"),
    )
    sig = _post_user_comment(r.workspace)
    assert r.workspace.tasks == {}
    assert sig.seen_by == []  # no planner absorbed it


def test_priority_tech_lead_over_architect(tmp_path: Path) -> None:
    r = _runner_with_team(
        tmp_path,
        ("design_lead", "architect"),
        ("eng_lead", "tech_lead"),
        ("requirements", "requirements_writer"),
    )
    _post_user_comment(r.workspace)

    assert len(r.workspace.desk_for("eng_lead")) == 1
    assert len(r.workspace.desk_for("design_lead")) == 0
    assert len(r.workspace.desk_for("requirements")) == 0


def test_priority_falls_through_to_architect_when_tech_lead_running(tmp_path: Path) -> None:
    r = _runner_with_team(
        tmp_path,
        ("eng_lead", "tech_lead"),
        ("design_lead", "architect"),
    )
    r.workspace.agent_states["eng_lead"] = AgentState.RUNNING
    _post_user_comment(r.workspace)

    assert len(r.workspace.desk_for("eng_lead")) == 0
    assert len(r.workspace.desk_for("design_lead")) == 1


def test_all_planners_running_no_synthetic_task(tmp_path: Path) -> None:
    """Defers to check_user_board() at next iteration."""
    r = _runner_with_team(
        tmp_path,
        ("eng_lead", "tech_lead"),
        ("design_lead", "architect"),
    )
    r.workspace.agent_states["eng_lead"] = AgentState.RUNNING
    r.workspace.agent_states["design_lead"] = AgentState.RUNNING
    _post_user_comment(r.workspace)

    assert r.workspace.tasks == {}


def test_user_comment_priority_also_routed(tmp_path: Path) -> None:
    r = _runner_with_team(tmp_path, ("lead", "tech_lead"))
    sig = Signal(
        type=SignalType.USER_COMMENT_PRIORITY,
        summary="urgent question",
        from_user=True, priority="high",
    )
    r.workspace.post_signal(sig)
    desk = r.workspace.desk_for("lead")
    assert len(desk) == 1


def test_active_story_attached_as_reference(tmp_path: Path) -> None:
    r = _runner_with_team(tmp_path, ("lead", "tech_lead"))
    r.workspace.active_story["lead"] = "story_xyz"
    _post_user_comment(r.workspace)

    desk = r.workspace.desk_for("lead")
    assert len(desk[0].references) == 1
    assert desk[0].references[0].kind == "story"
    assert desk[0].references[0].value == "story_xyz"
    assert desk[0].story_id == "story_xyz"


def test_idle_planner_marked_ready(tmp_path: Path) -> None:
    r = _runner_with_team(tmp_path, ("lead", "tech_lead"))
    _post_user_comment(r.workspace)
    assert r.workspace.agent_states["lead"] is AgentState.READY


def test_running_planner_state_unchanged(tmp_path: Path) -> None:
    """Don't clobber a RUNNING planner's state — the scheduler will
    pick the new desk entry up on the next iteration boundary."""
    r = _runner_with_team(
        tmp_path, ("lead", "tech_lead"), ("design", "architect"),
    )
    r.workspace.agent_states["lead"] = AgentState.RUNNING
    _post_user_comment(r.workspace)
    # tech_lead is RUNNING → architect picks it up; tech_lead state untouched.
    assert r.workspace.agent_states["lead"] is AgentState.RUNNING
    assert r.workspace.agent_states["design"] is AgentState.READY


def test_duplicate_comment_signal_no_duplicate_task(tmp_path: Path) -> None:
    """Subscriber idempotency. Resume replay or duplicate posts must
    not create two tasks for the same comment."""
    r = _runner_with_team(tmp_path, ("lead", "tech_lead"))
    sig = _post_user_comment(r.workspace)
    # Manually fire the routing again with the same signal.
    r._on_user_comment_routing(sig)
    desk = r.workspace.desk_for("lead")
    assert len(desk) == 1


def test_routing_ignores_non_user_comment_signals(tmp_path: Path) -> None:
    r = _runner_with_team(tmp_path, ("lead", "tech_lead"))
    r.workspace.post_signal(Signal(
        type=SignalType.GIT_COMMIT, summary="x", from_agent="dev",
    ))
    assert r.workspace.tasks == {}


def test_planner_system_prompts_include_user_comment_addendum() -> None:
    """§4.5 — planners get a one-liner about user comments arriving
    on their desk being the team's interface to the user."""
    from agents.builtin.architect import ARCHITECT_SYSTEM_PROMPT
    from agents.builtin.requirements_writer import (
        REQUIREMENTS_WRITER_SYSTEM_PROMPT,
    )
    from agents.builtin.tech_lead import TECH_LEAD_SYSTEM_PROMPT

    for prompt in (
        TECH_LEAD_SYSTEM_PROMPT,
        ARCHITECT_SYSTEM_PROMPT,
        REQUIREMENTS_WRITER_SYSTEM_PROMPT,
    ):
        assert "USER COMMENTS" in prompt
        assert "comment_" in prompt
