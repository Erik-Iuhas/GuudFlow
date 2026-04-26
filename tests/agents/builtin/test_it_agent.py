"""Tests for agents/builtin/it_agent.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.builtin.it_agent import ITAgent, PackagePolicy
from core.signals import SignalType
from core.workspace import PackageRequest, Workspace


@pytest.fixture
def workspace() -> Workspace:
    return Workspace(run_id="r1", goal="x")


def _request(name: str, role: str = "developer", reason: str = "need it") -> PackageRequest:
    return PackageRequest(from_agent=role, package=name, reason=reason, iteration=0)


async def test_approves_allowlisted_pinned(workspace: Workspace):
    it = ITAgent(policy=PackagePolicy(allowlist={"requests": "2.31.0"}))
    workspace.pending_package_requests.append(_request("requests==2.31.0"))
    msg = await it.run(workspace)
    types = [s.type for s in workspace.board]
    assert SignalType.PACKAGE_APPROVED in types
    assert "1 approved" in msg


async def test_rejects_unpinned(workspace: Workspace):
    it = ITAgent(policy=PackagePolicy(allowlist={"requests": "2.31.0"}))
    workspace.pending_package_requests.append(_request("requests"))
    await it.run(workspace)
    rejections = [s for s in workspace.board if s.type is SignalType.PACKAGE_REJECTED]
    assert rejections
    assert "pinned" in rejections[0].detail


async def test_rejects_off_allowlist(workspace: Workspace):
    it = ITAgent(policy=PackagePolicy(allowlist={"requests": "2.31.0"}))
    workspace.pending_package_requests.append(_request("evil-package==1.0"))
    await it.run(workspace)
    rejections = [s for s in workspace.board if s.type is SignalType.PACKAGE_REJECTED]
    assert rejections


async def test_rejects_blocked(workspace: Workspace):
    it = ITAgent(policy=PackagePolicy(
        allowlist={"requests": "2.31.0"},
        block_list={"requests"},
    ))
    workspace.pending_package_requests.append(_request("requests==2.31.0"))
    await it.run(workspace)
    types = [s.type for s in workspace.board]
    assert SignalType.PACKAGE_REJECTED in types
    assert SignalType.PACKAGE_APPROVED not in types


async def test_rejects_version_mismatch(workspace: Workspace):
    it = ITAgent(policy=PackagePolicy(allowlist={"requests": "2.31.0"}))
    workspace.pending_package_requests.append(_request("requests==2.99.0"))
    await it.run(workspace)
    rejections = [s for s in workspace.board if s.type is SignalType.PACKAGE_REJECTED]
    assert rejections
    assert "2.31.0" in rejections[0].detail


async def test_processes_multiple(workspace: Workspace):
    it = ITAgent(policy=PackagePolicy(allowlist={"a": "1.0", "b": "2.0"}))
    workspace.pending_package_requests.extend([
        _request("a==1.0"),
        _request("b==2.0"),
        _request("c==3.0"),  # off allowlist
    ])
    msg = await it.run(workspace)
    approved = sum(1 for s in workspace.board if s.type is SignalType.PACKAGE_APPROVED)
    rejected = sum(1 for s in workspace.board if s.type is SignalType.PACKAGE_REJECTED)
    assert approved == 2
    assert rejected == 1
    assert workspace.pending_package_requests == []
