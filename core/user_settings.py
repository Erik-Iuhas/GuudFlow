"""User settings persistence — `~/.guudflow/settings.json`.

Track 4. The Studio Settings drawer reads/writes this file. Storage
config and (eventually) other user-tunable variables live here.

Independent of `core/storage.py`'s storage-mode toggle: this file
controls (among other things) the storage_mode itself, so it cannot
live inside the storage location.

Atomic writes via tempfile rename — partial writes never leave the
file in a corrupted state. Reads tolerate missing/corrupted files
silently (return empty dict) — settings are advisory, never blocking.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from core.storage import user_settings_path


_ALLOWED_KEYS: frozenset[str] = frozenset({
    # Storage (Track 4)
    "storage_mode",
    "storage_root",
    "data_root",
    "personas_root",
    # Reserved for future widgets — add as new sections ship.
})


def read_settings() -> dict[str, Any]:
    """Return the current settings dict. Empty {} on missing or corrupted file."""
    path = user_settings_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge `updates` into the current settings and write atomically.

    Returns the resulting full settings dict.

    Unknown keys are dropped silently — keeps a malicious or malformed
    PUT from polluting the file. Add new keys to `_ALLOWED_KEYS` as
    Settings widgets ship.
    """
    path = user_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    current = read_settings()
    for key, value in updates.items():
        if key not in _ALLOWED_KEYS:
            continue
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value

    # Atomic write: tempfile in same dir, then rename.
    fd, tmp_path = tempfile.mkstemp(
        prefix=".settings-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return current


def clear_settings() -> None:
    """Remove the settings file entirely. Used by tests."""
    path = user_settings_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
