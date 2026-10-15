"""Before a session starts: are the project's test runners installed where agents run?

A project's spec or check command can name a runner (`bun test`, `pytest -q`) that the place
agents run does not have (run findings 2026-10-14, F3). Agents then cannot run any test and
every check they cannot run stays unmet. This says so before the run, so the person can install
the runner or start knowing tests will not run.

Agents run where `OpencodeRun._resolve_mode` puts them: in the Docker sandbox image when
`GUUDFLOW_SANDBOX` allows Docker and the daemon answers, else on this machine (with this
process's PATH, as `_input_sections` checks it for the brief).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from core.harness.capabilities import needed_runners
from core.harness.run import SandboxMode, sandbox_from_env
from core.spec import Spec
from core.work_db import WorkDB

log = logging.getLogger(__name__)

HOST = "this machine"
IMAGE = "the sandbox image"


@dataclass(frozen=True)
class MissingRuntime:
    """A program the project's tests need that is not installed where agents run, or that
    could not be checked there (`checked` False: Docker unreachable, image not built yet)."""

    program: str
    why: str      # the check command or the spec rule that names it
    where: str    # HOST | IMAGE
    checked: bool = True

    def to_dict(self) -> dict[str, str | bool]:
        """JSON form."""
        return asdict(self)


def missing_runtimes(data_root: Path, project_id: str, check_command: str,
                     sandbox: SandboxMode | None = None) -> list[MissingRuntime]:
    """The runners the project's check command and active spec rules name that are missing
    where agents will run, or that cannot be checked there. Empty when nothing is named."""
    spec = Spec(WorkDB(Path(data_root) / "projects" / project_id).read_spec_doc())
    needed = needed_runners(check_command, [(r.id, r.text) for r in spec.rules
                                            if r.status == "active"])
    if not needed:
        return []
    from worker.sandbox_manager import SandboxManager

    mode = sandbox or sandbox_from_env()
    docker = mode != "host" and SandboxManager.available()
    if not docker and mode != "docker":
        return [MissingRuntime(p, needed[p], HOST) for p in sorted(needed)
                if shutil.which(p) is None]
    absent: list[str] | None = None
    if docker:
        try:
            absent = SandboxManager().missing_programs(sorted(needed))
        except Exception:  # noqa: BLE001 — a failed probe means "not checked", not a refusal
            log.warning("could not look for %s in the sandbox image", sorted(needed),
                        exc_info=True)
    if absent is None:
        return [MissingRuntime(p, needed[p], IMAGE, checked=False) for p in sorted(needed)]
    return [MissingRuntime(p, needed[p], IMAGE) for p in absent]
