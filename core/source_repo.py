"""Populate `data/projects/{id}/inputs/source_repo/` from a git URL or
a local directory.

DESIGN §33 source kinds `git_url` and `local_path` share a destination
and a secret-scan step; this module owns the underlying copy / clone
operation. The route layer wraps it with HTTP errors, secret scanning,
and the metadata sidecar.

Tests monkey-patch `clone_git_repo` to avoid spawning real subprocesses.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


class SourceCloneError(Exception):
    """Raised when `git clone` exits non-zero."""


async def clone_git_repo(
    url: str,
    branch: str,
    shallow: bool,
    dest: Path,
) -> None:
    """Clone `url` into `dest`. If `dest` already exists, replace it."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    args: list[str] = ["git", "clone"]
    if shallow:
        args += ["--depth", "1"]
    if branch:
        args += ["--branch", branch]
    args += [url, str(dest)]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = stderr.decode("utf-8", errors="replace").strip()
        raise SourceCloneError(msg or f"git clone exited with code {proc.returncode}")


def copy_local_dir(src: Path, dest: Path) -> None:
    """Mirror `src` directory tree into `dest`, replacing if it exists."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
