"""IT Agent — controlled package installation gatekeeper.

DESIGN §21. Event-driven on `PACKAGE_REQUEST` signals. No `depends_on`.
Maintains an allow-list of approved packages (with pinned versions);
auto-approves matches, routes mismatches to human approval.

Stage 5 ships the policy enforcement and signal flow. Actual `pip
install` invocation lives in the worker (Stage 7) inside the sandbox
container — the IT agent posts the approval, the worker executes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.base import BaseAgent
from core.signals import Signal, SignalType


@dataclass
class PackagePolicy:
    """Per-project package install policy.

    `allowlist` maps `name` → exact version pin (e.g.
    `{"requests": "2.31.0"}`). Unpinned names are NOT auto-approved —
    pinning is required.

    `block_list` is a set of names to refuse outright (e.g. known-bad).
    """

    allowlist: dict[str, str] = field(default_factory=dict)
    block_list: set[str] = field(default_factory=set)


class ITAgent(BaseAgent):
    """Reviews PACKAGE_REQUEST signals against a per-project allowlist."""

    role: str = "it_agent"

    def __init__(self, policy: PackagePolicy | None = None, **kwargs):
        super().__init__(role="it_agent", **kwargs)
        self.policy = policy or PackagePolicy()

    async def review_request(self, workspace, request) -> bool:
        """Decide on one PackageRequest. Posts PACKAGE_APPROVED or
        PACKAGE_REJECTED. Returns True on approval."""
        spec = request.package
        name, _, version = spec.partition("==")
        name = name.strip()
        version = version.strip()

        if not version:
            await self._reject(workspace, spec, "package must be pinned to a specific version (name==version)")
            return False
        if name in self.policy.block_list:
            await self._reject(workspace, spec, f"{name} is on the block list")
            return False
        allowed = self.policy.allowlist.get(name)
        if allowed is None:
            await self._reject(workspace, spec, f"{name} not on the allowlist; route to human approval")
            return False
        if allowed != version:
            await self._reject(
                workspace, spec,
                f"{name} pinned to {version}, allowlist requires {allowed}",
            )
            return False
        await self._approve(workspace, spec)
        return True

    async def run(self, workspace) -> str:
        """Process all pending PACKAGE_REQUESTs in one pass."""
        approved = 0
        rejected = 0
        # Take a snapshot — the workspace.pending_package_requests list
        # mutates as the IT agent writes signals if anything else clears
        # entries; here we just iterate the snapshot.
        snapshot = list(workspace.pending_package_requests)
        for req in snapshot:
            ok = await self.review_request(workspace, req)
            approved += int(ok)
            rejected += int(not ok)
        # Clear processed requests.
        workspace.pending_package_requests = [
            r for r in workspace.pending_package_requests
            if r not in snapshot
        ]
        return f"IT processed {len(snapshot)} requests: {approved} approved, {rejected} rejected"

    async def _approve(self, workspace, package: str) -> None:
        await self.signal(
            workspace, SignalType.PACKAGE_APPROVED,
            summary=f"approved: {package}",
        )

    async def _reject(self, workspace, package: str, reason: str) -> None:
        await self.signal(
            workspace, SignalType.PACKAGE_REJECTED,
            summary=f"rejected: {package}",
            detail=reason,
        )
