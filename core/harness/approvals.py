"""Answer opencode permission asks through GuudFlow's approval gate (plan §5.1, §5.9).

When a role's ruleset says `ask` (e.g. `shell: ask`), opencode emits
`permission.asked`. The gate resolves it, in order:

  1. a sticky "always" answer given earlier in this run for the same role,
     permission and pattern → allow, recorded as an automatic APPROVAL_GRANTED;
  2. the project's allow-list (`approval_gates.tool_permission_allow`,
     `{permission: [fnmatch patterns]}`) → allow once;
  3. otherwise ask the human: post APPROVAL_NEEDED (kind `tool_permission`) and
     wait for the APPROVAL_GRANTED / APPROVAL_DENIED the HTTP route posts.
     A grant with `scope=run` becomes sticky for the rest of the run.

Timeouts and anything unexpected resolve to "reject" (default-deny).
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from core.harness.opencode_runner import PermissionReply
from core.signals import Signal, SignalType
from core.workspace import ApprovalRequest, Workspace

KIND = "tool_permission"


@dataclass
class PermissionGate:
    """Per-run permission decisions."""

    workspace: Workspace
    allow_list: dict[str, list[str]] = field(default_factory=dict)
    timeout: float = 900.0
    _sticky: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    async def decide(self, role: str, ask: dict[str, Any]) -> PermissionReply:
        """Resolve one `permission.asked` payload for `role`."""
        permission = str(ask.get("permission", ""))
        patterns = [str(p) for p in ask.get("patterns") or []] or ["*"]
        sticky = self._sticky.get((role, permission), [])
        if sticky and all(_matches(p, sticky) for p in patterns):
            self._post(SignalType.APPROVAL_GRANTED, role,
                       f"auto-approved {permission} {', '.join(patterns)} (always, this run)",
                       {"auto": "sticky", "permission": permission, "patterns": patterns})
            return "once"
        allowed = self.allow_list.get(permission, [])
        if allowed and all(_matches(p, allowed) for p in patterns):
            self._post(SignalType.APPROVAL_GRANTED, role,
                       f"auto-approved {permission} {', '.join(patterns)} (project allow-list)",
                       {"auto": "allow_list", "permission": permission, "patterns": patterns})
            return "once"
        granted, scope = await self._ask_human(role, permission, patterns, ask)
        if not granted:
            return "reject"
        if scope == "run":
            remember = [str(p) for p in ask.get("always") or []] or patterns
            self._sticky.setdefault((role, permission), []).extend(remember)
            return "always"
        return "once"

    async def _ask_human(self, role: str, permission: str, patterns: list[str],
                         ask: dict[str, Any]) -> tuple[bool, str]:
        request = ApprovalRequest(
            id=f"appr_{uuid4().hex[:10]}", kind=KIND,
            summary=f"{role} wants to use {permission}: {', '.join(patterns)}",
            context={"role": role, "permission": permission, "patterns": patterns,
                     "always": list(ask.get("always") or []), "metadata": ask.get("metadata") or {}},
            triggered_by=role, iteration=self.workspace.iteration)
        self.workspace.pending_approvals.append(request)
        decided = asyncio.Event()
        outcome: list[tuple[bool, str]] = []

        def on_signal(sig: Signal) -> None:
            if sig.type not in (SignalType.APPROVAL_GRANTED, SignalType.APPROVAL_DENIED):
                return
            if f"approval_id={request.id}" not in (sig.detail or ""):
                return
            scope = "run" if "scope=run" in (sig.detail or "") else "once"
            outcome.append((sig.type is SignalType.APPROVAL_GRANTED, scope))
            decided.set()

        unsubscribe = self.workspace.subscribe(on_signal)
        try:
            self.workspace.post_signal(Signal(
                type=SignalType.APPROVAL_NEEDED, summary=request.summary, from_agent=role,
                detail=f"approval_id={request.id}; kind={KIND}; context={json.dumps(request.context)}",
                iteration=self.workspace.iteration))
            await asyncio.wait_for(decided.wait(), self.timeout)
        except asyncio.TimeoutError:
            self.workspace.record_approval(request.id, "denied", note="timed out")
            return False, "once"
        finally:
            unsubscribe()
        return outcome[0]

    def _post(self, kind: SignalType, role: str, summary: str, detail: dict[str, Any]) -> None:
        self.workspace.post_signal(Signal(type=kind, summary=summary, from_agent=role,
                                          detail=json.dumps(detail),
                                          iteration=self.workspace.iteration))


def _matches(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(value, p) for p in patterns)
