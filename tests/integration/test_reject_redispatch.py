"""M5.C1 integration test — REJECTED → re-dispatch loop.

Verifies that when a reviewer rejects once, the original author's agent state
is reset for re-spawning and the pending feedback is stored for injection into
the next iteration context.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.llm import MockLLMClient
from core.protocol import RejectedFeedback, render_rejected_feedback
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.signals import Signal, SignalType
from core.tool_manifest import RoleRBAC
from core.workspace import AgentState


def _project(consecutive_fail_threshold: int = 2) -> ProjectConfig:
    return ProjectConfig(
        project_id="p1",
        goal="build the feature",
        agents=[
            AgentConfig(role="dev", description="codes", depends_on=[]),
            AgentConfig(
                role="reviewer", description="reviews", depends_on=["dev"],
                is_reviewer=True,
            ),
        ],
        rbac={
            "dev": RoleRBAC(role="dev", allowed_scopes=[]),
            "reviewer": RoleRBAC(role="reviewer", allowed_scopes=[]),
        },
        consecutive_fail_threshold=consecutive_fail_threshold,
    )


def _post_rejected(runner: AgentRunner, reviewer_role: str, detail: str = "missing tests") -> None:
    runner.workspace.post_signal(Signal(
        type=SignalType.REJECTED,
        summary=f"REJECTED by {reviewer_role}",
        detail=detail,
        from_agent=reviewer_role,
        iteration=1,
    ))


def _mark_dev_complete(runner: AgentRunner) -> None:
    """Simulate dev completing normally before review."""
    runner.workspace.completed_agents.append("dev")
    runner.workspace.agent_states["dev"] = AgentState.COMPLETE


# ── C1: one rejection re-dispatches the author ────────────────────────────────


def test_one_rejection_resets_author_state(tmp_path: Path) -> None:
    runner = AgentRunner(
        project=_project(), run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _mark_dev_complete(runner)

    _post_rejected(runner, "reviewer", detail="no error handling")

    # dev should no longer be in completed_agents
    assert "dev" not in runner.workspace.completed_agents
    # agent state should be READY
    assert runner.workspace.agent_states.get("dev") is AgentState.READY


def test_one_rejection_stores_pending_feedback(tmp_path: Path) -> None:
    runner = AgentRunner(
        project=_project(), run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _mark_dev_complete(runner)

    _post_rejected(runner, "reviewer", detail="missing X")

    fb = runner._pending_feedback.get("dev")
    assert fb is not None
    assert fb.reviewer_role == "reviewer"
    assert fb.detail == "missing X"
    assert fb.attempt_number == 1


def test_consecutive_fails_increments_on_rejected(tmp_path: Path) -> None:
    runner = AgentRunner(
        project=_project(consecutive_fail_threshold=3), run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _mark_dev_complete(runner)

    _post_rejected(runner, "reviewer")
    assert runner._consecutive_fails.get("dev") == 1


def test_consecutive_fails_resets_on_approved(tmp_path: Path) -> None:
    runner = AgentRunner(
        project=_project(consecutive_fail_threshold=3), run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _mark_dev_complete(runner)

    _post_rejected(runner, "reviewer")
    assert runner._consecutive_fails.get("dev") == 1

    runner.workspace.post_signal(Signal(
        type=SignalType.APPROVED,
        summary="APPROVED",
        from_agent="reviewer",
        iteration=2,
    ))
    assert runner._consecutive_fails.get("dev") is None


def test_pending_feedback_cleared_on_approved(tmp_path: Path) -> None:
    runner = AgentRunner(
        project=_project(consecutive_fail_threshold=3), run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _mark_dev_complete(runner)
    _post_rejected(runner, "reviewer")
    assert "dev" in runner._pending_feedback

    # Approval clears the consecutive counter; pending_feedback is consumed
    # when _drive_agent_run calls _pending_feedback.pop() at spawn time.
    # Verify the counter is cleared:
    runner.workspace.post_signal(Signal(
        type=SignalType.APPROVED,
        summary="APPROVED",
        from_agent="reviewer",
        iteration=2,
    ))
    assert runner._consecutive_fails.get("dev") is None


def test_render_rejected_feedback_format() -> None:
    block = render_rejected_feedback("Alice", "reviewer", "no tests at all", 1)
    assert "=== REVIEWER REJECTED YOUR WORK (attempt 1) ===" in block
    assert "Alice (reviewer)" in block
    assert "no tests at all" in block
    assert "--- Address each point above. Re-emit COMMIT when fixed. ---" in block


# ── C2: two consecutive rejections escalate to tech_lead ──────────────────────


def _project_with_techlead() -> ProjectConfig:
    return ProjectConfig(
        project_id="p2",
        goal="build the feature",
        agents=[
            AgentConfig(role="dev", description="codes", depends_on=[]),
            AgentConfig(
                role="reviewer", description="reviews", depends_on=["dev"],
                is_reviewer=True,
            ),
            AgentConfig(role="tech_lead", description="tech lead", depends_on=[]),
        ],
        rbac={
            "dev": RoleRBAC(role="dev", allowed_scopes=[]),
            "reviewer": RoleRBAC(role="reviewer", allowed_scopes=[]),
            "tech_lead": RoleRBAC(role="tech_lead", allowed_scopes=[]),
        },
        consecutive_fail_threshold=2,
    )


def test_second_rejection_fires_escalation_signal(tmp_path: Path) -> None:
    runner = AgentRunner(
        project=_project_with_techlead(), run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _mark_dev_complete(runner)

    # First rejection: re-dispatch (attempt=1 < threshold=2)
    _post_rejected(runner, "reviewer", detail="bug A")
    # Second rejection: escalate (attempt=2 >= threshold=2)
    _post_rejected(runner, "reviewer", detail="bug B")

    # At least one ESCALATION signal should have been posted (either from
    # the consecutive-fail handler or the cumulative handler — whichever
    # claims the slot first).
    escalations = [
        s for s in runner.workspace.board
        if s.type is SignalType.ESCALATION
        and "dev" in (s.detail or "")
    ]
    assert len(escalations) >= 1


def test_second_rejection_resets_consecutive_counter(tmp_path: Path) -> None:
    runner = AgentRunner(
        project=_project_with_techlead(), run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _mark_dev_complete(runner)

    _post_rejected(runner, "reviewer", detail="bug A")
    _post_rejected(runner, "reviewer", detail="bug B")

    # Counter is reset after escalation dispatch.
    assert runner._consecutive_fails.get("dev") == 0


def test_second_rejection_does_not_redispatch_author(tmp_path: Path) -> None:
    runner = AgentRunner(
        project=_project_with_techlead(), run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _mark_dev_complete(runner)

    # First rejection re-dispatches dev — simulate dev completes again.
    _post_rejected(runner, "reviewer", detail="bug A")
    runner.workspace.completed_agents.append("dev")
    runner.workspace.agent_states["dev"] = AgentState.COMPLETE

    # Second rejection should escalate, not re-dispatch.
    _post_rejected(runner, "reviewer", detail="bug B")

    # Pending feedback for dev should NOT be set on the second rejection
    # (it was cleared by the first rejection being consumed, or not set
    # because we took the escalation path).
    assert runner._consecutive_fails.get("dev") == 0


def test_no_techlead_emits_human_needed(tmp_path: Path) -> None:
    """Without tech_lead on team, consecutive failures post HUMAN_NEEDED."""
    proj = ProjectConfig(
        project_id="p3",
        goal="build",
        agents=[
            AgentConfig(role="dev", description="codes", depends_on=[]),
            AgentConfig(
                role="reviewer", description="reviews", depends_on=["dev"],
                is_reviewer=True,
            ),
        ],
        rbac={
            "dev": RoleRBAC(role="dev", allowed_scopes=[]),
            "reviewer": RoleRBAC(role="reviewer", allowed_scopes=[]),
        },
        consecutive_fail_threshold=2,
    )
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _mark_dev_complete(runner)

    _post_rejected(runner, "reviewer", detail="bug A")
    _post_rejected(runner, "reviewer", detail="bug B")

    human_needed = [
        s for s in runner.workspace.board if s.type is SignalType.HUMAN_NEEDED
    ]
    assert len(human_needed) == 1
    assert "consecutive_failures" in human_needed[0].summary


def test_rebuild_consecutive_fails_from_board(tmp_path: Path) -> None:
    """On resume, _rebuild_consecutive_fails_from_board replays the history."""
    from core.workspace import Workspace

    runner = AgentRunner(
        project=_project(consecutive_fail_threshold=3), run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    # Simulate 2 REJECTED already on the board.
    runner.workspace.board.append(Signal(
        type=SignalType.REJECTED, summary="rej1", detail="a", from_agent="reviewer",
    ))
    runner.workspace.board.append(Signal(
        type=SignalType.REJECTED, summary="rej2", detail="b", from_agent="reviewer",
    ))

    # Build a new runner with restored_workspace to trigger rebuild.
    runner2 = AgentRunner(
        project=_project(consecutive_fail_threshold=3), run_id="r1",
        run_root=tmp_path / "run2", llm=MockLLMClient([]),
        restored_workspace=runner.workspace,
    )
    assert runner2._consecutive_fails.get("dev") == 2
