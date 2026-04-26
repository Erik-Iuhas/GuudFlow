"""CredentialsBroker — RBAC-mediated access to credentialed services.

DESIGN §30. Every credentialed call (API keys, OAuth tokens, MCP servers)
goes through this broker. Agents never hold credentials. The broker:

  - Owns the credentials store (in production, encrypted; in dev, plaintext
    with a warning).
  - Maintains the project's RBAC matrix.
  - Filters tool catalogs at manifest build time (`scopes_for_role`).
  - Validates again at call time (`request`) — defense in depth.
  - Writes an audit log of every grant and denial.

Stage 4 ships this as a STUB: the broker enforces RBAC, runs registered
local handlers (for tests and for the dev-mode `web:search` proxy), and
writes the audit log. Real MCP transport lands in Stage 5.

Default policy: deny. A role with no allowed_scopes entry receives nothing.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from core.tool_manifest import RoleRBAC


CredentialHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
"""Callable signature: (operation, payload) -> result dict.

Registered per-scope. The broker invokes the handler instead of returning
a credential. Used for dev-mode local handlers (e.g. web:search proxy)
and, in Stage 5, for real MCP transport adapters.
"""


@dataclass
class CredentialResult:
    """Result of a `broker.request()`. Mirrors DESIGN §30's contract."""

    granted: bool
    request_id: str
    result: dict[str, Any] | None = None
    reason: str | None = None


@dataclass
class AuditEntry:
    ts: str
    from_agent: str
    scope: str
    operation: str
    decision: str  # "granted" | "denied"
    reason: str | None = None
    redacted_payload_preview: str | None = None


class CredentialsBroker:
    """RBAC-mediated invocation of credentialed services.

    Construct with a project's RBAC matrix and (optional) audit log path.
    Register handlers for scopes via `register_handler(scope, handler)`.
    Agents call `request(role, scope, operation, payload)`.
    """

    def __init__(
        self,
        rbac: dict[str, RoleRBAC],
        audit_log_path: Path | str | None = None,
    ):
        self.rbac = rbac
        self.audit_log_path = Path(audit_log_path) if audit_log_path else None
        self._handlers: dict[str, CredentialHandler] = {}
        self._audit: list[AuditEntry] = []
        self._lock = asyncio.Lock()

    # ── Registration ────────────────────────────────────────────────────────

    def register_handler(self, scope: str, handler: CredentialHandler) -> None:
        """Register a callable that performs the credentialed call for a scope.

        For prefix scopes (e.g. "mcp-github"), the handler is invoked for any
        sub-scope match unless a more specific handler is registered.
        """
        self._handlers[scope] = handler

    def _resolve_handler(self, scope: str) -> CredentialHandler | None:
        # Prefer most-specific match.
        if scope in self._handlers:
            return self._handlers[scope]
        # Fall back to prefix matches, longest-first.
        candidates = [s for s in self._handlers if scope.startswith(s + "-")]
        if not candidates:
            return None
        candidates.sort(key=len, reverse=True)
        return self._handlers[candidates[0]]

    # ── RBAC ────────────────────────────────────────────────────────────────

    def role_grants(self, role: str, scope: str) -> bool:
        rbac = self.rbac.get(role)
        if rbac is None:
            return False
        return rbac.grants(scope)

    def scopes_for_role(self, role: str) -> list[str]:
        rbac = self.rbac.get(role)
        return list(rbac.allowed_scopes) if rbac else []

    # ── Request ─────────────────────────────────────────────────────────────

    async def request(
        self,
        role: str,
        scope: str,
        operation: str,
        payload: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> CredentialResult:
        """Validate scope and invoke the registered handler.

        Returns a CredentialResult. Always writes an audit entry (granted
        or denied) before returning.
        """
        request_id = request_id or str(uuid4())
        payload = payload or {}

        # 1. RBAC check.
        if not self.role_grants(role, scope):
            reason = f"{role} role not granted {scope}"
            await self._audit_record(role, scope, operation, "denied", reason, payload)
            return CredentialResult(granted=False, request_id=request_id, reason=reason)

        # 2. Handler lookup.
        handler = self._resolve_handler(scope)
        if handler is None:
            reason = f"no handler registered for scope {scope}"
            await self._audit_record(role, scope, operation, "denied", reason, payload)
            return CredentialResult(granted=False, request_id=request_id, reason=reason)

        # 3. Invoke and audit.
        try:
            result = await handler(operation, payload)
        except Exception as e:
            reason = f"handler error: {e}"
            await self._audit_record(role, scope, operation, "denied", reason, payload)
            return CredentialResult(granted=False, request_id=request_id, reason=reason)

        await self._audit_record(role, scope, operation, "granted", None, payload)
        return CredentialResult(granted=True, request_id=request_id, result=result)

    # ── Audit ───────────────────────────────────────────────────────────────

    async def _audit_record(
        self,
        from_agent: str,
        scope: str,
        operation: str,
        decision: str,
        reason: str | None,
        payload: dict[str, Any],
    ) -> None:
        entry = AuditEntry(
            ts=datetime.now(timezone.utc).isoformat(),
            from_agent=from_agent,
            scope=scope,
            operation=operation,
            decision=decision,
            reason=reason,
            redacted_payload_preview=_redact_preview(payload),
        )
        async with self._lock:
            self._audit.append(entry)
            if self.audit_log_path is not None:
                self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.audit_log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry.__dict__) + "\n")

    def audit_entries(self) -> list[AuditEntry]:
        """Return a snapshot of in-memory audit entries (defensive copy)."""
        return list(self._audit)


def _redact_preview(payload: dict[str, Any], max_len: int = 80) -> str:
    """Best-effort one-line preview that omits sensitive-looking keys."""
    if not payload:
        return ""
    parts = []
    for k, v in payload.items():
        if any(s in k.lower() for s in ("token", "key", "secret", "password", "auth")):
            parts.append(f"{k}=<redacted>")
        else:
            v_str = str(v)
            if len(v_str) > 30:
                v_str = v_str[:30] + "…"
            parts.append(f"{k}={v_str}")
    line = ", ".join(parts)
    if len(line) > max_len:
        line = line[:max_len] + "…"
    return line
