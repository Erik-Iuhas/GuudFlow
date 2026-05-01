"""Bugs #1 + #2 regression tests — reviewer respawn + downstream cascade.

When a reviewer emits REJECTED and the author is bounced back for a second
pass, two things must happen that M5.C1 originally missed:

1. The reviewer itself must be reset to PENDING so it can re-review after
   the author's second COMPLETE. Without this the run deadlocks.
2. Any agent downstream of the reviewer that already completed against the
   first-pass output must also be reset, otherwise those agents never
   re-run against the corrected work.
"""
from __future__ import annotations

from pathlib import Path

from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.signals import Signal, SignalType
from core.tool_manifest import RoleRBAC
from core.workspace import AgentState


# ── Shared fixtures ───────────────────────────────────────────────────────────


def _two_agent_project(consecutive_fail_threshold: int = 2) -> ProjectConfig:
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


def _three_agent_project() -> ProjectConfig:
    return ProjectConfig(
        project_id="p2",
        goal="build and document",
        agents=[
            AgentConfig(role="dev", description="codes", depends_on=[]),
            AgentConfig(
                role="reviewer", description="reviews", depends_on=["dev"],
                is_reviewer=True,
            ),
            AgentConfig(role="docs", description="writes README",
                        depends_on=["reviewer"]),
        ],
        rbac={
            "dev": RoleRBAC(role="dev", allowed_scopes=[]),
            "reviewer": RoleRBAC(role="reviewer", allowed_scopes=[]),
            "docs": RoleRBAC(role="docs", allowed_scopes=[]),
        },
        consecutive_fail_threshold=2,
    )


def _parallel_project() -> ProjectConfig:
    return ProjectConfig(
        project_id="p3",
        goal="build with sibling",
        agents=[
            AgentConfig(role="dev", description="codes", depends_on=[]),
            AgentConfig(
                role="reviewer", description="reviews", depends_on=["dev"],
                is_reviewer=True,
            ),
            AgentConfig(role="sibling", description="parallel task",
                        depends_on=[]),
        ],
        rbac={
            "dev": RoleRBAC(role="dev", allowed_scopes=[]),
            "reviewer": RoleRBAC(role="reviewer", allowed_scopes=[]),
            "sibling": RoleRBAC(role="sibling", allowed_scopes=[]),
        },
        consecutive_fail_threshold=2,
    )


def _post_rejected(runner: AgentRunner, reviewer_role: str,
                   detail: str = "missing tests") -> None:
    runner.workspace.post_signal(Signal(
        type=SignalType.REJECTED,
        summary=f"REJECTED by {reviewer_role}",
        detail=detail,
        from_agent=reviewer_role,
        iteration=1,
    ))


def _mark_complete(runner: AgentRunner, *roles: str) -> None:
    for role in roles:
        if role not in runner.workspace.completed_agents:
            runner.workspace.completed_agents.append(role)
        runner.workspace.agent_states[role] = AgentState.COMPLETE


# ── Bug #1: reviewer respawn ──────────────────────────────────────────────────


def test_rejection_resets_reviewer_to_pending(tmp_path: Path) -> None:
    """Reviewer that emitted REJECTED must be re-queued to PENDING so the
    scheduler can re-spawn it after the author's second COMPLETE."""
    runner = AgentRunner(
        project=_two_agent_project(), run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    # Simulate the real-world state: both roles completed their first pass
    # (reviewer's terminal signal was REJECTED).
    _mark_complete(runner, "dev", "reviewer")

    _post_rejected(runner, "reviewer", detail="no error handling")

    assert "reviewer" not in runner.workspace.completed_agents, (
        "reviewer must be removed from completed_agents so scheduler can re-spawn it"
    )
    assert runner.workspace.agent_states["reviewer"] is AgentState.PENDING, (
        "reviewer must be PENDING (not READY) so depends_on gating holds until "
        "author re-completes"
    )
    # Author M5.C1 treatment is still intact.
    assert "dev" not in runner.workspace.completed_agents
    assert runner.workspace.agent_states["dev"] is AgentState.READY


def test_reviewer_pending_not_ready(tmp_path: Path) -> None:
    """Reviewer reset must use PENDING, not READY. READY would bypass
    depends_on gating and let the reviewer fire before the author re-finishes."""
    runner = AgentRunner(
        project=_two_agent_project(), run_id="r2",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _mark_complete(runner, "dev", "reviewer")
    _post_rejected(runner, "reviewer")

    state = runner.workspace.agent_states.get("reviewer")
    assert state is not AgentState.READY, (
        "reviewer must be PENDING not READY — READY skips depends_on gating"
    )
    assert state is AgentState.PENDING


# ── Bug #2: downstream cascade ────────────────────────────────────────────────


def test_rejection_cascades_to_downstream_completed(tmp_path: Path) -> None:
    """An agent downstream of the reviewer that already completed in the
    optimistic first pass must be reset, otherwise it never re-runs against
    the corrected second-pass output."""
    runner = AgentRunner(
        project=_three_agent_project(), run_id="r3",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _mark_complete(runner, "dev", "reviewer", "docs")

    _post_rejected(runner, "reviewer", detail="needs error handling")

    assert "docs" not in runner.workspace.completed_agents, (
        "docs depends on reviewer; it must be re-queued when reviewer is reset"
    )
    assert runner.workspace.agent_states["docs"] is AgentState.PENDING


def test_rejection_does_not_reset_unrelated_completed(tmp_path: Path) -> None:
    """A parallel agent that completed independently must not be disturbed
    by a rejection in a different pipeline branch."""
    runner = AgentRunner(
        project=_parallel_project(), run_id="r4",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _mark_complete(runner, "dev", "reviewer", "sibling")

    _post_rejected(runner, "reviewer", detail="x")

    assert "sibling" in runner.workspace.completed_agents, (
        "sibling is not downstream of dev/reviewer — must remain completed"
    )
    assert runner.workspace.agent_states["sibling"] is AgentState.COMPLETE


def test_cascade_is_transitive(tmp_path: Path) -> None:
    """Cascade must walk the full depth: dev → reviewer → docs → final.
    All three downstream agents reset when dev gets bounced."""
    project = ProjectConfig(
        project_id="p4",
        goal="deep chain",
        agents=[
            AgentConfig(role="dev", description="codes", depends_on=[]),
            AgentConfig(role="reviewer", description="reviews",
                        depends_on=["dev"], is_reviewer=True),
            AgentConfig(role="docs", description="documents",
                        depends_on=["reviewer"]),
            AgentConfig(role="final", description="ships",
                        depends_on=["docs"]),
        ],
        rbac={r: RoleRBAC(role=r, allowed_scopes=[])
              for r in ("dev", "reviewer", "docs", "final")},
        consecutive_fail_threshold=2,
    )
    runner = AgentRunner(
        project=project, run_id="r5",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _mark_complete(runner, "dev", "reviewer", "docs", "final")

    _post_rejected(runner, "reviewer", detail="incomplete")

    for role in ("reviewer", "docs", "final"):
        assert role not in runner.workspace.completed_agents, (
            f"{role} should have been cascade-reset"
        )
        assert runner.workspace.agent_states[role] is AgentState.PENDING
    # Author is READY (M5.C1 original behavior).
    assert runner.workspace.agent_states["dev"] is AgentState.READY
