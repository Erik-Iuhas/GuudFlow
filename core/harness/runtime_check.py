"""Before a session starts: are the project's test runners installed where agents run?

A project's spec or check command can name a runner (`bun test`, `pytest -q`) that the place
agents run does not have (run findings 2026-10-14, F3). Agents then cannot run any test and
every check they cannot run stays unmet. This says so before the run, so the person can install
the runner or start knowing tests will not run.

Agents run where `OpencodeRun._resolve_mode` puts them: in the Docker sandbox image when
`GUUDFLOW_SANDBOX` allows Docker and the daemon answers, else on this machine.
"""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from core.harness.capabilities import needed_runners
from core.harness.run import SandboxMode, sandbox_from_env
from core.spec import Spec
from core.work_db import WorkDB


@dataclass(frozen=True)
class MissingRuntime:
    """A program the project's tests need that is not installed where agents run."""

    program: str
    why: str      # the check command or the spec rule that names it
    where: str    # "this machine" | "the sandbox image"

    def to_dict(self) -> dict[str, str]:
        """JSON form."""
        return asdict(self)


def missing_runtimes(data_root: Path, project_id: str, check_command: str,
                     sandbox: SandboxMode | None = None) -> list[MissingRuntime]:
    """The runners the project's check command and active spec rules name that are missing
    where agents will run. Empty when nothing is named, or when the sandbox image is not
    built yet (it is built at run start, so nothing can be said before)."""
    spec = Spec(WorkDB(Path(data_root) / "projects" / project_id).read_spec_doc())
    needed = needed_runners(check_command, [(r.id, r.text) for r in spec.rules
                                            if r.status == "active"])
    if not needed:
        return []
    from worker.sandbox_manager import SandboxManager

    if (sandbox or sandbox_from_env()) != "host" and SandboxManager.available():
        absent = SandboxManager().missing_programs(sorted(needed))
        where = "the sandbox image"
    else:
        absent = [p for p in sorted(needed) if shutil.which(p) is None]
        where = "this machine"
    return [MissingRuntime(program=p, why=needed[p], where=where) for p in absent or []]
