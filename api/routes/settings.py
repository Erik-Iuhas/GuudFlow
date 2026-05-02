"""Settings API — Track 4. The Studio Settings drawer reads/writes these.

  GET  /api/settings                       → current effective settings
  PUT  /api/settings                       → update + persist; returns
                                              {settings, restart_required}
  POST /api/settings/validate-storage-root → check a path is usable
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.storage import PersonaStore, ProjectStore, RunStore
from core import storage, user_settings
from worker.queue import JOB_QUEUED, JOB_RUNNING


router = APIRouter()


# ── GET /api/settings ──────────────────────────────────────────────────────


@router.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    """Return the current effective settings.

    `effective` is the resolved config (env-or-file-or-defaults). `persisted`
    is what's in ~/.guudflow/settings.json (may differ if env vars override).
    Studio shows `effective` and warns when `persisted != effective` (env
    overrides settings file).
    """
    cfg = storage.get_storage_config()
    persisted = user_settings.read_settings()

    effective = {
        "storage_mode": cfg.mode,
        "storage_root": str(cfg.storage_root),
        "personas_root": str(cfg.personas_root),
    }

    return {
        "effective": effective,
        "persisted": persisted,
        # Frontend uses this to show an "env override active" banner if so.
        "env_overrides_active": _env_overrides_active(),
    }


def _env_overrides_active() -> bool:
    """True if any GUUDFLOW_* env var is set that would override the settings file."""
    import os
    return any(
        os.environ.get(name)
        for name in (
            "GUUDFLOW_STORAGE_MODE",
            "GUUDFLOW_STORAGE_ROOT",
            "GUUDFLOW_DATA_ROOT",
            "GUUDFLOW_DATA_DIR",  # legacy
            "GUUDFLOW_PERSONAS_ROOT",
        )
    )


# ── PUT /api/settings ──────────────────────────────────────────────────────


class SettingsUpdate(BaseModel):
    storage_mode: str | None = Field(default=None, description="'global' or 'in-directory'")
    storage_root: str | None = Field(default=None, description="path; required for in-directory mode")
    personas_root: str | None = None
    data_root: str | None = None


@router.put("/api/settings")
async def update_settings(
    update: SettingsUpdate, request: Request,
) -> dict[str, Any]:
    """Persist the given settings to ~/.guudflow/settings.json.

    Returns the new persisted settings + a `restart_required` flag.

    Track 4.4 — when no job is in flight, we hot-rebuild the read stores
    (`project_store`, `run_store`, `persona_store`) and `data_root` on
    `app.state` so the new storage_root takes effect immediately. The
    long-running JobRunner keeps its captured `data_root` for any
    in-progress run; that's why we refuse to hot-swap when the queue
    has running/queued jobs and surface `restart_required=true` instead.
    """
    update_dict = update.model_dump(exclude_none=True)

    # Validate storage_mode if provided.
    if "storage_mode" in update_dict:
        mode = update_dict["storage_mode"].lower()
        if mode not in ("global", "in-directory"):
            raise HTTPException(
                status_code=400,
                detail="storage_mode must be 'global' or 'in-directory'",
            )
        update_dict["storage_mode"] = mode

    # Validate storage_root path is usable, but only if mode is (or will be)
    # in-directory. Checking in global mode would block users from saving a
    # placeholder root they intend to use later.
    if "storage_root" in update_dict:
        existing = user_settings.read_settings()
        effective_mode = update_dict.get("storage_mode") or existing.get("storage_mode") or "global"
        if effective_mode == "in-directory":
            check = _validate_storage_root_path(update_dict["storage_root"])
            if not check["valid"]:
                raise HTTPException(status_code=400, detail=check["reason"])

    # Detect changes that affect storage paths so we know whether to
    # hot-reload the stores below.
    pre_cfg = storage.get_storage_config()
    persisted = user_settings.write_settings(update_dict)

    storage_changed = any(
        k in update_dict for k in ("storage_mode", "storage_root", "data_root", "personas_root")
    )

    # If a job is queued/running, refuse to hot-swap — the JobRunner has
    # its own data_root captured for the in-flight run and switching out
    # from under it would corrupt the run. Surface restart_required so the
    # user knows their changes are persisted but pending.
    has_in_flight_job = False
    queue = getattr(request.app.state, "queue", None)
    if queue is not None and storage_changed:
        try:
            jobs = await queue.list(limit=200)
            has_in_flight_job = any(
                j.status in (JOB_QUEUED, JOB_RUNNING) for j in jobs
            )
        except Exception:
            # On any queue read error, fall back to restart_required.
            has_in_flight_job = True

    hot_reloaded = False
    if storage_changed and not has_in_flight_job:
        storage.invalidate_storage_config_cache()
        new_cfg = storage.get_storage_config()
        new_data_root = new_cfg.storage_root.resolve()
        try:
            new_data_root.mkdir(parents=True, exist_ok=True)
            request.app.state.data_root = new_data_root
            request.app.state.project_store = ProjectStore(new_data_root)
            request.app.state.run_store = RunStore(new_data_root / "runs")
            request.app.state.persona_store = PersonaStore(
                new_cfg.personas_root.resolve(),
            )
            hot_reloaded = True
        except OSError:
            # If the new path can't be created, the persisted settings
            # still stand but the live app keeps the old stores; tell
            # the user to restart so the next process picks them up.
            hot_reloaded = False

    restart_required = storage_changed and not hot_reloaded

    # `pre_cfg` retained for callers that want to confirm what changed.
    _ = pre_cfg

    return {
        "persisted": persisted,
        "restart_required": restart_required,
        "hot_reloaded": hot_reloaded,
    }


# ── POST /api/settings/validate-storage-root ───────────────────────────────


class StorageRootCheck(BaseModel):
    path: str


@router.post("/api/settings/validate-storage-root")
async def validate_storage_root(check: StorageRootCheck) -> dict[str, Any]:
    """Check whether a given path is usable as a storage_root.

    Returns:
      valid:   true iff path exists, is a directory, and is writable
      exists:  path exists at all
      writable: directory is writable (best-effort: tries to create a temp file)
      reason:  human-readable error if not valid; null otherwise
    """
    return _validate_storage_root_path(check.path)


def _validate_storage_root_path(path_str: str) -> dict[str, Any]:
    p = Path(path_str)
    if not p.exists():
        return {
            "valid": False,
            "exists": False,
            "writable": False,
            "reason": f"path does not exist: {path_str}",
        }
    if not p.is_dir():
        return {
            "valid": False,
            "exists": True,
            "writable": False,
            "reason": f"path is not a directory: {path_str}",
        }
    # Best-effort write test.
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(dir=str(p), prefix=".gf-write-test-", delete=True):
            pass
        writable = True
    except (OSError, PermissionError) as e:
        return {
            "valid": False,
            "exists": True,
            "writable": False,
            "reason": f"directory not writable: {e}",
        }
    return {
        "valid": True,
        "exists": True,
        "writable": writable,
        "reason": None,
    }
