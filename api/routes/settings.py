"""Settings API — Track 4. The Studio Settings drawer reads/writes these.

  GET  /api/settings                       → current effective settings
  PUT  /api/settings                       → update + persist; returns
                                              {settings, restart_required}
  POST /api/settings/validate-storage-root → check a path is usable
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core import storage, user_settings


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
        "working_tree_mode": cfg.working_tree_mode,
        "storage_base": str(cfg.storage_base) if cfg.storage_base else None,
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
            "GUUDFLOW_WORKING_TREE_MODE",
        )
    )


# ── PUT /api/settings ──────────────────────────────────────────────────────


class SettingsUpdate(BaseModel):
    storage_mode: str | None = Field(default=None, description="'global' or 'in-directory'")
    storage_root: str | None = Field(default=None, description="path; required for in-directory mode")
    personas_root: str | None = None
    data_root: str | None = None
    working_tree_mode: str | None = Field(
        default=None,
        description="'isolated' (default; copy to workspace/working) or 'in-place' (agents work in storage_root directly; in-directory mode only)",
    )


@router.put("/api/settings")
async def update_settings(update: SettingsUpdate) -> dict[str, Any]:
    """Persist the given settings to ~/.guudflow/settings.json.

    Returns the new persisted settings + a `restart_required` flag.
    Mode change always requires restart; storage_root change does not
    (next run will pick it up).
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

    # Validate working_tree_mode if provided.
    if "working_tree_mode" in update_dict:
        wt = update_dict["working_tree_mode"].lower()
        if wt not in ("isolated", "in-place"):
            raise HTTPException(
                status_code=400,
                detail="working_tree_mode must be 'isolated' or 'in-place'",
            )
        update_dict["working_tree_mode"] = wt

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

    # Compare against the running EFFECTIVE config, not just the persisted
    # dict. Setting storage_mode=global when defaults already gave global
    # is not a change requiring restart.
    current_effective_mode = storage.get_storage_config().mode
    persisted = user_settings.write_settings(update_dict)

    mode_changed = (
        "storage_mode" in update_dict
        and current_effective_mode != update_dict["storage_mode"]
    )
    current_effective_wt = storage.get_storage_config().working_tree_mode
    wt_changed = (
        "working_tree_mode" in update_dict
        and current_effective_wt != update_dict["working_tree_mode"]
    )

    return {
        "persisted": persisted,
        "restart_required": mode_changed or wt_changed,
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
