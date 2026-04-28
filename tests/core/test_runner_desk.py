"""Runner desk rendering — M3.5 Chunk 4.

`_render_task_desk_block(role)` produces the `=== YOUR DESK ===`
section that's prepended to the user-turn message. Empty desk → empty
string (back-compat with M1). The DESK DISCIPLINE conditional and the
planner archetype prompt addition are exercised via integration in
Chunk 5; here we keep tests focused on the rendering primitive.
"""

from __future__ import annotations

from pathlib import Path

from agents.builtin.architect import ARCHITECT_SYSTEM_PROMPT
from agents.builtin.code_reviewer import REVIEWER_SYSTEM_PROMPT
from agents.builtin.requirements_writer import (
    REQUIREMENTS_WRITER_SYSTEM_PROMPT,
)
from agents.builtin.tech_lead import TECH_LEAD_SYSTEM_PROMPT
from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.workspace import Task, TaskRef


def _runner(tmp_path: Path) -> AgentRunner:
    proj = ProjectConfig(project_id="p1", goal="g", agents=[], rbac={})
    return AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )


def test_empty_desk_returns_empty_string(tmp_path: Path) -> None:
    """Regression guard: M1 INCOMING WORK behaviour preserved when no
    tasks exist for the role."""
    r = _runner(tmp_path)
    assert r._render_task_desk_block("anyone") == ""


def test_single_task_renders_with_acceptance_and_refs(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="abc123",
        story_id="s1",
        assigned_to="developer",
        created_by="architect",
        goal="implement JWT issuer",
        acceptance_criteria=["rs256 signing", "key rotation"],
        out_of_scope=["oauth2 social"],
        references=[
            TaskRef(kind="file", value="docs/auth.md", note="design"),
            TaskRef(kind="commit", value="abc1234"),
        ],
    ))

    block = r._render_task_desk_block("developer")
    assert "=== YOUR DESK ===" in block
    assert "[task:abc123]" in block
    assert "PENDING" in block
    assert "implement JWT issuer" in block
    assert "rs256 signing" in block
    assert "key rotation" in block
    assert "oauth2 social" in block
    assert "docs/auth.md" in block
    assert "design" in block  # the note
    assert "commit: abc1234" in block
    assert "=== END DESK ===" in block


def test_subtask_renders_indented_under_parent(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(
        task_id="parent",
        story_id="s1",
        assigned_to="lead",
        goal="build auth",
        acceptance_criteria=["ship it"],
    ))
    r.workspace.add_task(Task(
        task_id="child",
        story_id="s1",
        assigned_to="lead",
        parent_task_id="parent",
        goal="verifier module",
    ))

    block = r._render_task_desk_block("lead")
    parent_idx = block.index("[task:parent]")
    child_idx = block.index("[task:child]")
    assert parent_idx < child_idx
    # subtasks header appears between parent and child
    subtasks_idx = block.index("subtasks")
    assert parent_idx < subtasks_idx < child_idx
    # child line is indented (5 leading spaces minimum after the bullet)
    child_line = next(
        ln for ln in block.splitlines() if "[task:child]" in ln
    )
    assert child_line.startswith("     ")


def test_completed_tasks_omitted_blocked_separated(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    r.workspace.add_task(Task(task_id="a", story_id="s1", assigned_to="dev", goal="alpha"))
    r.workspace.add_task(Task(task_id="b", story_id="s1", assigned_to="dev", goal="bravo"))
    r.workspace.add_task(Task(task_id="c", story_id="s1", assigned_to="dev", goal="charlie"))

    r.workspace.mutate_task("b", "COMPLETE", closed_by_commit="hash")
    r.workspace.mutate_task("c", "BLOCKED", blocked_on=["a"], cancelled_reason="")

    block = r._render_task_desk_block("dev")
    # COMPLETE filtered out
    assert "[task:b]" not in block
    # BLOCKED visible but in the trailing section
    assert "[task:a]" in block
    assert "[task:c]" in block
    a_idx = block.index("[task:a]")
    sep_idx = block.index("--- blocked")
    c_idx = block.index("[task:c]")
    assert a_idx < sep_idx < c_idx
    assert "blocked_on: a" in block


def test_planner_system_prompts_include_task_assignment_line() -> None:
    """§5.2 — the four planning archetypes have the TASK ASSIGNMENT
    paragraph in their static system prompt (cacheable)."""
    assert "TASK ASSIGNMENT" in TECH_LEAD_SYSTEM_PROMPT
    assert "TASK ASSIGNMENT" in ARCHITECT_SYSTEM_PROMPT
    assert "TASK ASSIGNMENT" in REQUIREMENTS_WRITER_SYSTEM_PROMPT
    assert "assign-task" in TECH_LEAD_SYSTEM_PROMPT
    assert "assign-task" in ARCHITECT_SYSTEM_PROMPT
    assert "assign-task" in REQUIREMENTS_WRITER_SYSTEM_PROMPT


def test_reviewer_system_prompts_include_task_aware_review_line() -> None:
    """§5.4 — both per-step (CodeReviewer) and final-stage (Architect)
    reviewers cross-check closes: against acceptance_criteria using
    the existing git tools."""
    assert "TASK-AWARE REVIEW" in REVIEWER_SYSTEM_PROMPT
    assert "closes: task" in REVIEWER_SYSTEM_PROMPT
    assert "TASK_OUT_OF_SCOPE" in REVIEWER_SYSTEM_PROMPT

    assert "TASK-AWARE REVIEW" in ARCHITECT_SYSTEM_PROMPT
    assert "closes: task" in ARCHITECT_SYSTEM_PROMPT
    assert "TASK_OUT_OF_SCOPE" in ARCHITECT_SYSTEM_PROMPT
