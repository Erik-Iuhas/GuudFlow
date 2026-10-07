"""Disk space on the host and a prune of what sandbox builds leave behind (FIELD_NOTES F2).

Sandbox image builds and test runs can fill the disk; once it is full, git, SQLite and the
worker all fail in confusing ways. `disk_report` says how much is free (Studio warns below a
threshold) and `prune` removes Docker build cache, dangling images and stopped GuudFlow
sandbox containers. Nothing here deletes project data, run folders or tagged images.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

GB = 1024 ** 3
WARN_FREE_BYTES = int(float(os.environ.get("GUUDFLOW_DISK_WARN_GB", "5")) * GB)
SANDBOX_LABEL = "guudflow.run"


@dataclass(frozen=True)
class DiskReport:
    """Free space where GuudFlow keeps its data."""

    path: str
    free_bytes: int
    total_bytes: int
    warn_below_bytes: int

    @property
    def low(self) -> bool:
        """Below the warning threshold."""
        return self.free_bytes < self.warn_below_bytes


def disk_report(path: Path, warn_below: int = WARN_FREE_BYTES) -> DiskReport:
    """Free and total bytes on the filesystem holding `path`."""
    usage = shutil.disk_usage(path)
    return DiskReport(path=str(path), free_bytes=usage.free, total_bytes=usage.total,
                      warn_below_bytes=warn_below)


@dataclass(frozen=True)
class PruneResult:
    """What a prune did, one line per step."""

    steps: list[str]
    freed_bytes: int


def _docker(*args: str) -> str:
    proc = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"docker {args[0]} failed")
    return proc.stdout.strip()


def prune(path: Path) -> PruneResult:
    """Remove Docker build cache, dangling images and stopped GuudFlow sandbox containers."""
    before = shutil.disk_usage(path).free
    steps: list[str] = []
    if shutil.which("docker") is None:
        return PruneResult(steps=["docker is not installed; nothing to prune"], freed_bytes=0)
    for label, args in (
        ("stopped sandbox containers",
         ("container", "prune", "-f", "--filter", f"label={SANDBOX_LABEL}")),
        ("build cache", ("builder", "prune", "-f")),
        ("dangling images", ("image", "prune", "-f")),
    ):
        try:
            out = _docker(*args)
            last = out.splitlines()[-1] if out else "done"
            steps.append(f"{label}: {last}")
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            steps.append(f"{label}: failed ({e})")
            log.warning("prune of %s failed: %s", label, e)
    return PruneResult(steps=steps, freed_bytes=max(0, shutil.disk_usage(path).free - before))
