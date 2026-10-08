"""Checks before review (plan §14.3): run the project's `check_command` on a run's code.

The command (for example `pytest -q`, set as `approval_gates.check_command`) runs on
a throwaway export of the run's `HEAD`, never on the repo itself:

  - docker (preferred, required in production): a one-shot container with no
    network, read-only root, all capabilities dropped and resource limits
    (`SandboxManager.run_check`);
  - host (dev only, with the usual warning): a subprocess with a stripped
    environment (no API keys, no proxies) and a timeout.

The result is stored with the run in the project repo and shown in the review panel.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

CheckStatus = Literal["passed", "failed", "timed_out", "skipped"]
OUTPUT_TAIL = 8000
DEFAULT_TIMEOUT = 600.0
HOST_WARNING = "⚠️  DEV MODE: project check running on host. Do not use with untrusted input."


@dataclass
class CheckResult:
    """The outcome of one check run."""

    command: str
    status: CheckStatus
    exit_code: int | None = None
    output: str = ""
    duration_s: float = 0.0
    where: Literal["docker", "host", "none"] = "none"
    reason: str = ""
    # Per test case, from the JUnit report (core/ledger.py): "file::path > name" → status.
    tests: dict[str, str] = field(default_factory=dict)
    report: str | None = None     # the report path, when the command wrote one


def export_head(working_dir: Path, target: Path, ref: str = "HEAD") -> None:
    """Write the files of `ref` (default `HEAD`, no .git) into `target`."""
    archive = target.parent / "head.tar"
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "archive", "--format=tar",
                    "-o", str(archive), ref], cwd=working_dir, check=True,
                   capture_output=True)
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        tar.extractall(target, filter="data")
    archive.unlink()


def _host_env(home: Path) -> dict[str, str]:
    keep = {"PATH", "LANG", "LC_ALL", "TZ"}
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update(HOME=str(home), CI="1", NO_NETWORK="1")
    return env


async def run_project_check(working_dir: Path, command: str, *,
                            sandbox: Literal["auto", "docker", "host"] = "auto",
                            production: bool = False,
                            timeout: float = DEFAULT_TIMEOUT, ref: str = "HEAD",
                            report: str | None = "auto") -> CheckResult:
    """Run `command` on an export of `working_dir` at `ref`. Never raises.

    `report` is where the command writes a JUnit report: "auto" adds the reporter flags for a
    known test runner (core/ledger.py `with_report`), a path uses the command as given, None
    asks for none. The parsed test cases land in `CheckResult.tests`."""
    from core.ledger import parse_junit, with_report

    shown = command
    if report == "auto":
        command, report = with_report(command)
    started = time.monotonic()
    use_docker = False
    if sandbox != "host":
        from worker.sandbox_manager import SandboxManager
        use_docker = SandboxManager.available()
    if not use_docker and (sandbox == "docker" or production):
        return CheckResult(shown, "skipped", where="none",
                           reason="Docker is required to run checks here")
    try:
        with tempfile.TemporaryDirectory(prefix="guudflow-check-") as tmp:
            tree = Path(tmp) / "work"
            export_head(working_dir, tree, ref)
            if use_docker:
                from worker.sandbox_manager import SandboxManager
                manager = SandboxManager()
                await manager.ensure_image()
                code, output = await manager.run_check(tree, command, timeout=timeout)
                where: Literal["docker", "host", "none"] = "docker"
            else:
                log.warning(HOST_WARNING)
                code, output = await asyncio.to_thread(_host_run, tree, command, timeout,
                                                       Path(tmp))
                where = "host"
            tests: dict[str, str] = {}
            report_file = (tree / report).resolve() if report else None
            if report_file is not None and report_file.is_relative_to(tree.resolve()) \
                    and report_file.is_file():
                tests = {t.key: t.status for t in
                         parse_junit(report_file.read_text(encoding="utf-8", errors="replace"))}
    except Exception as exc:  # noqa: BLE001 — a broken check is reported, not raised
        log.exception("project check failed to run")
        return CheckResult(shown, "skipped", reason=f"could not run the check: {exc}",
                           duration_s=round(time.monotonic() - started, 2))
    status: CheckStatus = "timed_out" if code is None else "passed" if code == 0 else "failed"
    return CheckResult(shown, status, exit_code=code, output=output[-OUTPUT_TAIL:],
                       duration_s=round(time.monotonic() - started, 2), where=where,
                       tests=tests, report=report if tests else None)


def _host_run(tree: Path, command: str, timeout: float, home: Path) -> tuple[int | None, str]:
    try:
        proc = subprocess.run(["sh", "-c", command], cwd=tree, env=_host_env(home),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return None, out.decode("utf-8", "replace") if isinstance(out, bytes) else out
    return proc.returncode, proc.stdout + proc.stderr
