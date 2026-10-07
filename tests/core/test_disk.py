"""Disk report, prune and the worker's check before a session (FIELD_NOTES F2)."""

from __future__ import annotations

import shutil
from collections import namedtuple
from pathlib import Path

import pytest

from core import disk
from worker.job_runner import check_disk_before_run

Usage = namedtuple("Usage", "total used free")
GB = 1024 ** 3


def _free(monkeypatch: pytest.MonkeyPatch, *frees: float) -> None:
    values = iter(frees)
    last = [frees[-1]]

    def usage(_path: object) -> Usage:
        last[0] = next(values, last[0])
        return Usage(250 * GB, 0, int(last[0] * GB))
    monkeypatch.setattr(disk.shutil, "disk_usage", usage)


def test_report_flags_low_space(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _free(monkeypatch, 3)
    report = disk.disk_report(tmp_path)
    assert report.low and report.free_bytes == 3 * GB
    _free(monkeypatch, 50)
    assert not disk.disk_report(tmp_path).low


def test_worker_refuses_to_start_on_an_almost_full_disk(monkeypatch: pytest.MonkeyPatch,
                                                      tmp_path: Path) -> None:
    monkeypatch.delenv("GUUDFLOW_AUTO_PRUNE", raising=False)
    _free(monkeypatch, 0.2)
    problem = check_disk_before_run(tmp_path)
    assert problem is not None and "almost full" in problem and "prune" in problem
    _free(monkeypatch, 3)
    assert check_disk_before_run(tmp_path) is None             # low but workable: a warning


def test_worker_prunes_first_when_configured(monkeypatch: pytest.MonkeyPatch,
                                             tmp_path: Path) -> None:
    monkeypatch.setenv("GUUDFLOW_AUTO_PRUNE", "1")
    _free(monkeypatch, 0.2, 9)
    calls: list[Path] = []
    monkeypatch.setattr(disk, "prune", lambda p: calls.append(p) or disk.PruneResult(["x"], 0))
    assert check_disk_before_run(tmp_path) is None and calls == [tmp_path]


def test_prune_without_docker_is_a_no_op(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(disk.shutil, "which", lambda name: None)
    _free(monkeypatch, 3)
    assert disk.prune(tmp_path).steps == ["docker is not installed; nothing to prune"]


def test_prune_runs_the_three_docker_steps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(disk.shutil, "which", lambda name: "/usr/bin/docker")
    _free(monkeypatch, 3, 11)
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(disk, "_docker", lambda *a: seen.append(a) or "Total reclaimed space: 8GB")
    result = disk.prune(tmp_path)
    assert [a[0] for a in seen] == ["container", "builder", "image"]
    assert "label=guudflow.run" in seen[0]
    assert result.freed_bytes == 8 * GB
