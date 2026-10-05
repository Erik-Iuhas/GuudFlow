"""Small JSON documents shared by the API and the worker (separate processes).

`locked_json(path, default)` holds an exclusive lock file next to `path`, yields
the parsed document and writes it back atomically when the block ends without
an exception. Used for the project backlog and the project repo's run states.
A lock older than `STALE_SECONDS` is assumed abandoned (crashed holder) and taken over.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

STALE_SECONDS = 60.0
WAIT_SECONDS = 15.0


class JsonStoreBusy(RuntimeError):
    """The lock could not be taken within `WAIT_SECONDS`."""


@contextmanager
def file_lock(path: Path, wait: float = WAIT_SECONDS) -> Iterator[None]:
    """Exclusive lock via an O_EXCL lock file (portable; no fcntl on Windows)."""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > STALE_SECONDS:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise JsonStoreBusy(f"{path.name} is locked by another process") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def read_json(path: Path, default: Any) -> Any:  # Any: free-form JSON document
    """Parse `path`, or return `default` when it is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, value: Any) -> None:  # Any: free-form JSON document
    """Write atomically (temp file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def locked_json(path: Path, default: Any) -> Iterator[Any]:  # Any: free-form JSON document
    """Read-modify-write `path` under the lock; saved only if the block succeeds."""
    with file_lock(path):
        value = read_json(path, default)
        yield value
        write_json(path, value)
