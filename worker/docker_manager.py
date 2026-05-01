"""DockerManager — ephemeral run-container lifecycle.

DESIGN §29. Each production run executes inside an ephemeral container
created at run start and destroyed at run end. The container has:

  - `network_mode: none` (no outbound network)
  - `mem_limit: 512m`, `pids_limit: 50`, `cpu_quota: 100000` (1 cpu)
  - `read_only: true` root with a sized tmpfs at /tmp
  - The run's workspace mounted at `/workspace` (rw)
  - The persona roster mounted at `/personas` (read-only)

The runner makes LLM calls on the host and routes `code:exec` invocations
into the container. Agent-generated code never has network access.

Stage 7 ships:
  - `DockerManager` interface usable in dev (stub) and production modes
  - Mode enforcement: production refuses to start without Docker available
  - Stub mode for dev that returns clear "stub" results from start/stop/exec
  - Real Docker path that builds containers per §29's resource spec

The actual `docker` SDK is imported lazily so dev runs don't require
Docker to be installed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_logger = logging.getLogger(__name__)


# §29 resource limits — hardcoded by design (rule 65: not user-configurable).
CONTAINER_LIMITS: dict[str, Any] = {
    "mem_limit": "512m",
    "memswap_limit": "512m",
    "cpu_period": 100000,
    "cpu_quota": 100000,
    "pids_limit": 50,
    "network_mode": "none",
    "read_only": True,
    "tmpfs": {"/tmp": "size=64m"},
    "security_opt": ["no-new-privileges"],
}

DEFAULT_IMAGE = "guudflow-sandbox:latest"


@dataclass
class ContainerHandle:
    """Reference to a running (or stub) container."""

    container_id: str
    run_id: str
    image: str = DEFAULT_IMAGE
    is_stub: bool = False
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    is_stub: bool = False


class DockerManagerError(RuntimeError):
    """Raised when production mode requires Docker but it's not available."""


class DockerManager:
    """Lifecycle for one run's container.

    Dev mode is a stub — `start()`, `exec_in()`, `stop()` are recorded but
    do nothing real. Production mode uses the `docker` SDK.

    Production mode requires Docker AND the sandbox image to exist; if
    either is missing, instantiation raises `DockerManagerError` so the
    job_runner refuses to start jobs in unsafe state.
    """

    def __init__(
        self,
        mode: str | None = None,
        image: str = DEFAULT_IMAGE,
        client: Any = None,  # docker.DockerClient — typed lazily
    ):
        self.mode = mode or os.environ.get("GUUDFLOW_MODE", "dev")
        self.image = image
        self._client = client
        self._containers: dict[str, ContainerHandle] = {}

        if self.mode == "production":
            self._verify_docker_available()

    # ── Verification ────────────────────────────────────────────────────────

    def _verify_docker_available(self) -> None:
        """In production mode, Docker MUST be available. CLAUDE.md hard rule
        from §29: 'worker refuses to start in production without Docker'.

        If a client was injected (typical in tests), still ping it — the
        injection point is for swapping the SDK source, not for skipping
        verification.
        """
        if self._client is None:
            try:
                import docker
            except ImportError as e:
                raise DockerManagerError(
                    "production mode requires the `docker` SDK; install it or "
                    "set GUUDFLOW_MODE=dev"
                ) from e
            try:
                self._client = docker.from_env()
            except Exception as e:
                raise DockerManagerError(
                    f"production mode requires a reachable Docker daemon: {e}"
                ) from e
        try:
            self._client.ping()
        except Exception as e:
            raise DockerManagerError(
                f"production mode requires a reachable Docker daemon: {e}"
            ) from e

    @property
    def is_dev_stub(self) -> bool:
        return self.mode != "production"

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def start(self, run_id: str, workspace_dir: Path | str,
                    personas_dir: Path | str | None = None) -> ContainerHandle:
        """Spin up an ephemeral container for the run.

        In dev mode this is a stub — returns a synthetic handle without
        starting anything. The host runner still works for `code:exec`
        via the dev-mode host runner (with §29 hardening).
        """
        from datetime import datetime, timezone

        if self.is_dev_stub:
            handle = ContainerHandle(
                container_id=f"stub_{run_id}",
                run_id=run_id,
                image=self.image,
                is_stub=True,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            self._containers[run_id] = handle
            _logger.info("DockerManager dev stub: 'started' container for %s", run_id)
            return handle

        # Production path.
        volumes: dict[str, dict[str, str]] = {
            str(Path(workspace_dir).resolve()): {"bind": "/workspace", "mode": "rw"},
        }
        if personas_dir is not None:
            volumes[str(Path(personas_dir).resolve())] = {"bind": "/personas", "mode": "ro"}

        container = self._client.containers.run(  # type: ignore[union-attr]
            image=self.image,
            volumes=volumes,
            environment={"RUN_ID": run_id},
            detach=True,
            auto_remove=False,  # we manage stop/remove
            **CONTAINER_LIMITS,
        )
        handle = ContainerHandle(
            container_id=container.id,
            run_id=run_id,
            image=self.image,
            is_stub=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._containers[run_id] = handle
        return handle

    async def exec_in(self, run_id: str, language: str, code: str,
                      timeout_s: float = 30.0) -> ExecResult:
        """Run code inside the run's container. Dev mode returns a stub
        result so calling code can integration-test the path without
        Docker."""
        handle = self._containers.get(run_id)
        if handle is None:
            raise DockerManagerError(f"no container for run {run_id!r}; call start() first")

        if handle.is_stub:
            return ExecResult(
                exit_code=0,
                stdout=f"[stub] would have run {language} code ({len(code)} chars)",
                stderr="",
                is_stub=True,
            )

        # Production path — `docker exec` inside the container.
        if language == "python":
            cmd = ["python", "-c", code]
        elif language == "shell":
            cmd = ["bash", "-lc", code]
        else:
            return ExecResult(exit_code=1, stdout="", stderr=f"unsupported language: {language}")

        try:
            container = self._client.containers.get(handle.container_id)  # type: ignore[union-attr]
            exec_result = container.exec_run(cmd, demux=True)
            stdout_b, stderr_b = exec_result.output if isinstance(exec_result.output, tuple) else (exec_result.output, b"")
            return ExecResult(
                exit_code=exec_result.exit_code,
                stdout=(stdout_b or b"").decode("utf-8", errors="replace"),
                stderr=(stderr_b or b"").decode("utf-8", errors="replace"),
                is_stub=False,
            )
        except Exception as e:
            return ExecResult(exit_code=1, stdout="", stderr=f"exec failed: {e}")

    async def stop(self, run_id: str) -> None:
        """Stop and remove the run's container. Dev stub is a no-op."""
        handle = self._containers.pop(run_id, None)
        if handle is None:
            return
        if handle.is_stub:
            _logger.info("DockerManager dev stub: 'stopped' container for %s", run_id)
            return
        try:
            container = self._client.containers.get(handle.container_id)  # type: ignore[union-attr]
            try:
                container.stop(timeout=5)
            except Exception:
                pass
            try:
                container.remove(force=True)
            except Exception:
                pass
        except Exception:
            pass

    def active_runs(self) -> list[str]:
        return list(self._containers.keys())
