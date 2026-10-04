"""Tests for approval gates (Phase C.0.5).

Covers the runner-side gate hook + workspace integration. The HTTP
route tests live in `tests/api/test_runs.py`; the cost estimator tests
live in `tests/core/test_cost_estimate.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.llm import MockLLMClient
from core.runner import (
    AgentRunner,
    ProjectConfig,
)


def _project(**overrides) -> ProjectConfig:
    base = dict(
        project_id="p1",
        goal="do a thing",
        agents=[],
        rbac={},
        max_iterations=3,
    )
    base.update(overrides)
    return ProjectConfig(**base)


# ── ApprovalRequest workspace helpers ──────────────────────────────────────


async def test_find_pending_approval_returns_match(tmp_path: Path):
    proj = _project()
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    # Park an approval manually.
    from core.workspace import ApprovalRequest
    req = ApprovalRequest(id="appr_abc", kind="test", summary="x")
    runner.workspace.pending_approvals.append(req)

    found = runner.workspace.find_pending_approval("appr_abc")
    assert found is req


async def test_find_pending_approval_skips_decided(tmp_path: Path):
    proj = _project()
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    from core.workspace import ApprovalRequest
    req = ApprovalRequest(id="appr_done", kind="test", summary="x")
    req.decision = "granted"
    runner.workspace.pending_approvals.append(req)

    assert runner.workspace.find_pending_approval("appr_done") is None


async def test_record_approval_updates_state(tmp_path: Path):
    proj = _project()
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    from core.workspace import ApprovalRequest
    req = ApprovalRequest(id="appr_x", kind="test", summary="x")
    runner.workspace.pending_approvals.append(req)

    updated = runner.workspace.record_approval("appr_x", "granted", note="lgtm")
    assert updated is req
    assert req.decision == "granted"
    assert req.decision_note == "lgtm"
    assert req.decided_at is not None


async def test_record_approval_rejects_invalid_decision(tmp_path: Path):
    proj = _project()
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    from core.workspace import ApprovalRequest
    runner.workspace.pending_approvals.append(
        ApprovalRequest(id="appr_x", kind="test", summary="x"),
    )
    with pytest.raises(ValueError):
        runner.workspace.record_approval("appr_x", "MAYBE")


# ── Approval gate config ──────────────────────────────────────────────────


def test_default_approval_gates_have_baseline():
    """Without explicit config, the baseline opinionated defaults apply."""
    proj = _project()
    assert proj.approval_gates["pre_run_cost_preview"] is True
    assert proj.approval_gates["heavy_tier_mid_run_dispatch"] is True
    assert proj.approval_gates["iteration_breach_at"] == 2
    assert proj.approval_gates["per_iteration"] is False
    assert proj.approval_gates["token_budget_usd"] is None
