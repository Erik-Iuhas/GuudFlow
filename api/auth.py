"""Bearer token auth for API routes.

DESIGN §29 rule 63 + §10 invoke spec. A single shared `GUUDFLOW_API_TOKEN`
gates all credentialed routes (invoke, project mutations, etc.). Read-only
routes (list runs, list personas) can be auth-optional in dev mode but
require the token in production (`GUUDFLOW_MODE=production`).

Stage 6 ships single-token auth. Per-user accounts are explicitly out of
scope (CLAUDE.md "Don't build yet": Auth / user accounts).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


_bearer_scheme = HTTPBearer(auto_error=False)


def _expected_token() -> str | None:
    """The shared bearer token. Empty/unset means auth is OPEN in dev mode
    and ALWAYS required in production."""
    return os.environ.get("GUUDFLOW_API_TOKEN") or None


def _is_production() -> bool:
    return os.environ.get("GUUDFLOW_MODE") == "production"


async def require_bearer(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """FastAPI dependency. Use on routes that mutate state or invoke runs.

    Behaviour:
      - If `GUUDFLOW_API_TOKEN` is set, the request must present a
        matching bearer token.
      - If unset and `GUUDFLOW_MODE=production`, requests are refused
        outright (no token configured = nothing valid to send).
      - If unset and dev mode, requests pass through.
    """
    expected = _expected_token()
    if expected is None:
        if _is_production():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="GUUDFLOW_API_TOKEN is unset in production mode",
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


# ── artifact cookie (S33 follow-up) ──────────────────────────────────────────
# `<img>` and `<iframe>` cannot send a bearer header, so Studio trades its token
# for a short-lived cookie, signed with the API token and scoped to one run's
# `artifact-files` path. The master token never appears in a URL.

ARTIFACT_COOKIE = "gf_artifacts"
ARTIFACT_TTL_SECONDS = 3600


def artifact_cookie_path(run_id: str) -> str:
    """The only path the cookie is sent to."""
    return f"/api/runs/{run_id}/artifact-files"


def _sign(key: str, run_id: str, expires: int) -> str:
    return hmac.new(key.encode(), f"artifacts:{run_id}:{expires}".encode(),
                    hashlib.sha256).hexdigest()


def issue_artifact_cookie(run_id: str, now: float | None = None) -> tuple[str, int] | None:
    """(cookie value, expiry) for `run_id`; None when no token is configured (dev open)."""
    key = _expected_token()
    if key is None:
        return None
    expires = int(now if now is not None else time.time()) + ARTIFACT_TTL_SECONDS
    return f"{expires}.{_sign(key, run_id, expires)}", expires


def artifact_cookie_valid(run_id: str, value: str | None, now: float | None = None) -> bool:
    """True if `value` was issued for this run and has not expired."""
    key = _expected_token()
    if key is None or not value or "." not in value:
        return False
    raw_exp, _, sig = value.partition(".")
    if not raw_exp.isdigit() or int(raw_exp) < (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(sig, _sign(key, run_id, int(raw_exp)))


async def require_bearer_or_artifact_cookie(
    request: Request,
    run_id: str,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """Bearer token, or the run's artifact cookie (for `<img>` / `<iframe>` loads)."""
    if artifact_cookie_valid(run_id, request.cookies.get(ARTIFACT_COOKIE)):
        return
    await require_bearer(credentials)
