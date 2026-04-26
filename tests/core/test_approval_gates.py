"""Tests for approval gates (Phase C.0.5).

Covers the runner-side gate hook + workspace integration. The HTTP
route tests live in `tests/api/test_runs.py`; the cost estimator tests
live in `tests/core/test_cost_estimate.py`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.llm import MockLLMClient
from core.runner import (
    AgentConfig,
    AgentRunner,
    ProjectConfig,
)
from core.signals import Signal, SignalType
from core.tool_manifest import RoleRBAC


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


# ── Runner._request_approval ──────────────────────────────────────────────


async def test_request_approval_returns_true_on_grant(tmp_path: Path):
    """Posts APPROVAL_NEEDED, awaits, returns True when the matching
    APPROVAL_GRANTED signal lands."""
    proj = _project()
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )

    async def grant_after_delay():
        # Wait for the runner to post APPROVAL_NEEDED + park.
        for _ in range(50):
            if runner.workspace.pending_approvals:
                break
            await asyncio.sleep(0.005)
        req = runner.workspace.pending_approvals[-1]
        runner.workspace.post_signal(Signal(
            type=SignalType.APPROVAL_GRANTED,
            summary="ok",
            detail=f"approval_id={req.id}",
            from_agent="user",
        ))

    granter = asyncio.create_task(grant_after_delay())
    ok = await runner._request_approval(kind="test_gate", summary="please approve")
    await granter
    assert ok is True

    # APPROVAL_NEEDED + APPROVAL_GRANTED both on the board.
    types = [s.type for s in runner.workspace.board]
    assert SignalType.APPROVAL_NEEDED in types
    assert SignalType.APPROVAL_GRANTED in types


async def test_request_approval_returns_false_on_deny(tmp_path: Path):
    proj = _project()
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )

    async def deny_after_delay():
        for _ in range(50):
            if runner.workspace.pending_approvals:
                break
            await asyncio.sleep(0.005)
        req = runner.workspace.pending_approvals[-1]
        runner.workspace.post_signal(Signal(
            type=SignalType.APPROVAL_DENIED,
            summary="no",
            detail=f"approval_id={req.id}",
            from_agent="user",
        ))

    denier = asyncio.create_task(deny_after_delay())
    ok = await runner._request_approval(kind="test_gate", summary="approve?")
    await denier
    assert ok is False


async def test_request_approval_carries_estimate_in_detail(tmp_path: Path):
    """The APPROVAL_NEEDED signal's detail includes the cost estimate
    so a CLI / log-only consumer can see the gate context."""
    proj = _project()
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )

    async def grant():
        for _ in range(50):
            if runner.workspace.pending_approvals:
                break
            await asyncio.sleep(0.005)
        req = runner.workspace.pending_approvals[-1]
        runner.workspace.post_signal(Signal(
            type=SignalType.APPROVAL_GRANTED,
            summary="ok",
            detail=f"approval_id={req.id}",
            from_agent="user",
        ))

    asyncio.create_task(grant())
    await runner._request_approval(
        kind="heavy_tier_dispatch",
        summary="dispatch tech_lead",
        estimated_cost_usd=0.42,
    )

    needed = next(
        s for s in runner.workspace.board
        if s.type is SignalType.APPROVAL_NEEDED
    )
    assert "kind=heavy_tier_dispatch" in (needed.detail or "")
    assert "0.42" in (needed.detail or "")


async def test_two_concurrent_approvals_resolve_independently(tmp_path: Path):
    """The runner's await is keyed on approval_id — two concurrent
    requests don't interfere."""
    proj = _project()
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )

    async def resolver(target_kind: str, decision: SignalType):
        # Wait for a request with the matching kind, then resolve only that one.
        for _ in range(100):
            req = next(
                (r for r in runner.workspace.pending_approvals
                 if r.kind == target_kind and r.decision is None),
                None,
            )
            if req is not None:
                runner.workspace.post_signal(Signal(
                    type=decision,
                    summary="resolved",
                    detail=f"approval_id={req.id}",
                    from_agent="user",
                ))
                return
            await asyncio.sleep(0.005)

    grant_task = asyncio.create_task(resolver("kind_a", SignalType.APPROVAL_GRANTED))
    deny_task = asyncio.create_task(resolver("kind_b", SignalType.APPROVAL_DENIED))

    a, b = await asyncio.wait(
        [
            asyncio.create_task(runner._request_approval(kind="kind_a", summary="a")),
            asyncio.create_task(runner._request_approval(kind="kind_b", summary="b")),
        ],
        return_when=asyncio.ALL_COMPLETED,
    )
    results = sorted(t.result() for t in a)
    assert results == [False, True]  # kind_b denied (False), kind_a granted (True)
    await grant_task
    await deny_task


# ── Approval gate config ──────────────────────────────────────────────────


def test_default_approval_gates_have_baseline():
    """Without explicit config, the baseline opinionated defaults apply."""
    proj = _project()
    assert proj.approval_gates["pre_run_cost_preview"] is True
    assert proj.approval_gates["heavy_tier_mid_run_dispatch"] is True
    assert proj.approval_gates["iteration_breach_at"] == 2
    assert proj.approval_gates["per_iteration"] is False
    assert proj.approval_gates["token_budget_usd"] is None


def test_is_gate_enabled_reads_config(tmp_path: Path):
    proj = _project(
        approval_gates={
            "pre_run_cost_preview": False,
            "heavy_tier_mid_run_dispatch": True,
        },
    )
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    assert runner._is_gate_enabled("pre_run_cost_preview") is False
    assert runner._is_gate_enabled("heavy_tier_mid_run_dispatch") is True


def test_is_gate_enabled_falls_back_to_baseline_when_missing(tmp_path: Path):
    """Missing keys fall back to baseline ON for the cost-control gates."""
    proj = _project(approval_gates={})  # empty
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    # Baseline ON keys.
    assert runner._is_gate_enabled("pre_run_cost_preview") is True
    assert runner._is_gate_enabled("heavy_tier_mid_run_dispatch") is True
    # Unknown keys default to OFF.
    assert runner._is_gate_enabled("future_unknown_gate") is False
