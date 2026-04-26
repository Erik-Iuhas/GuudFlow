"""Bearer token auth for API routes.

DESIGN §29 rule 63 + §10 invoke spec. A single shared `AGENTFLOW_API_TOKEN`
gates all credentialed routes (invoke, project mutations, etc.). Read-only
routes (list runs, list personas) can be auth-optional in dev mode but
require the token in production (`AGENTFLOW_MODE=production`).

Stage 6 ships single-token auth. Per-user accounts are explicitly out of
scope (CLAUDE.md "Don't build yet": Auth / user accounts).
"""

from __future__ import annotations

import os

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


_bearer_scheme = HTTPBearer(auto_error=False)


def _expected_token() -> str | None:
    """The shared bearer token. Empty/unset means auth is OPEN in dev mode
    and ALWAYS required in production."""
    return os.environ.get("AGENTFLOW_API_TOKEN") or None


def _is_production() -> bool:
    return os.environ.get("AGENTFLOW_MODE") == "production"


async def require_bearer(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """FastAPI dependency. Use on routes that mutate state or invoke runs.

    Behaviour:
      - If `AGENTFLOW_API_TOKEN` is set, the request must present a
        matching bearer token.
      - If unset and `AGENTFLOW_MODE=production`, requests are refused
        outright (no token configured = nothing valid to send).
      - If unset and dev mode, requests pass through.
    """
    expected = _expected_token()
    if expected is None:
        if _is_production():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="AGENTFLOW_API_TOKEN is unset in production mode",
            )
        # Dev permissive
        return

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if credentials.credentials != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
