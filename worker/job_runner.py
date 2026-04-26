"""JobRunner — the worker that drains the queue.

DESIGN §16. A long-running async loop that pulls jobs from the
`JobQueue` (Stage 6), looks up each project's config, constructs an
`AgentRunner`, executes the run, persists results to disk, and updates
job status.

In dev mode the worker can run in-process alongside the API (single
process MVP). In production it's a separate process started via
`python -m worker.job_runner`.

Stage 7 ships:
  - `serialise_project(dict) → ProjectConfig` — turn the stored JSON
    project into the dataclass the runner needs
  - `JobRunner` — the queue-draining loop
  - `run_one(job)` — single-job execution path (testable without a loop)
  - `__main__` entry point
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agents.builtin.memory_writer import MemoryWriter
from core import run_initializer
from core.llm import AnthropicClient, LLMClient
from core.quip_engine import QuipEngine
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.tool_manifest import RoleRBAC
from worker.docker_manager import DockerManager, DockerManagerError
from worker.queue import (
    JOB_COMPLETE,
    JOB_FAILED,
    JOB_RUNNING,
    Job,
    JobQueue,
)


_logger = logging.getLogger(__name__)


# ── Project config (de)serialisation ───────────────────────────────────────


def serialise_project(data: dict[str, Any]) -> ProjectConfig:
    """Turn a stored project dict into a ProjectConfig dataclass.

    The stored format mirrors `ProjectConfig.__dict__`: project_id, goal,
    agents (list of dicts), rbac (dict of role → {allowed_scopes}),
    mcp_servers (list), max_iterations.
    """
    agents: list[AgentConfig] = []
    for a in data.get("agents") or []:
        agents.append(AgentConfig(
            role=a.get("role", ""),
            description=a.get("description", ""),
            depends_on=list(a.get("depends_on") or []),
            system_prompt=a.get("system_prompt", ""),
            declared_local_tools=list(a.get("declared_local_tools") or []),
            declared_mcp_servers=list(a.get("declared_mcp_servers") or []),
            persona_id=a.get("persona_id"),
            model_tier=a.get("model_tier", "solid"),
            agent_class_role_type=a.get("agent_class_role_type"),
            is_reviewer=bool(a.get("is_reviewer", False)),
            is_final_reviewer=bool(a.get("is_final_reviewer", False)),
            max_tool_iterations=int(a.get("max_tool_iterations", 10)),
        ))

    rbac: dict[str, RoleRBAC] = {}
    for role, entry in (data.get("rbac") or {}).items():
        scopes = list((entry or {}).get("allowed_scopes") or [])
        rbac[role] = RoleRBAC(role=role, allowed_scopes=scopes)

    raw_thresholds = data.get("escalation_thresholds") or {}
    escalation_thresholds: dict[str, int] = {}
    if isinstance(raw_thresholds, dict):
        for role, count in raw_thresholds.items():
            try:
                escalation_thresholds[str(role)] = int(count)
            except (TypeError, ValueError):
                continue
    if not escalation_thresholds:
        escalation_thresholds = {"tech_lead": 2}

    # Approval gates (Phase C.0.5). Read from stored config; fall back
    # to the baseline (cost preview ON, HEAVY-tier dispatch ON, iteration
    # breach at 2). Unknown keys pass through so future gates can be
    # added without bumping the schema.
    raw_gates = data.get("approval_gates") or {}
    if not isinstance(raw_gates, dict):
        raw_gates = {}
    approval_gates: dict[str, Any] = {
        "pre_run_cost_preview": True,
        "heavy_tier_mid_run_dispatch": True,
        "iteration_breach_at": 2,
        "per_iteration": False,
        "token_budget_usd": None,
    }
    approval_gates.update(raw_gates)

    return ProjectConfig(
        project_id=data.get("project_id", ""),
        goal=data.get("goal", ""),
        agents=agents,
        rbac=rbac,
        mcp_servers=list(data.get("mcp_servers") or []),
        max_iterations=int(data.get("max_iterations", 5)),
        escalation_thresholds=escalation_thresholds,
        approval_gates=approval_gates,
    )


# ── Persistence ────────────────────────────────────────────────────────────


def _persist_run(run_root: Path, runner: AgentRunner, run_id: str,
                 project_id: str, status: str) -> None:
    """Write meta.json + board.json so the API can list / read this run."""
    run_root.mkdir(parents=True, exist_ok=True)
    meta = {
        "run_id": run_id,
        "project_id": project_id,
        "goal": runner.workspace.goal,
        "status": status,
        "iteration": runner.workspace.iteration,
        "completed_agents": list(runner.workspace.completed_agents),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (run_root / "meta.json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8",
    )

    board = []
    for sig in runner.workspace.board:
        board.append({
            "id": str(sig.id),
            "type": sig.type.value,
            "summary": sig.summary,
            "detail": sig.detail,
            "from_agent": sig.from_agent,
            "from_user": sig.from_user,
            "priority": sig.priority,
            "iteration": sig.iteration,
            "seen_by": list(sig.seen_by),
            "timestamp": sig.timestamp.isoformat(),
        })
    (run_root / "board.json").write_text(
        json.dumps(board, indent=2, default=str), encoding="utf-8",
    )


# ── JobRunner ──────────────────────────────────────────────────────────────


RunnerFactory = Callable[..., AgentRunner]
"""Factory signature: (project, run_id, run_root, llm, *, docker_manager=None) -> AgentRunner.

The trailing keyword arg is positional-via-kwarg so existing factories
that ignore it still work — JobRunner always passes it as a keyword."""


def default_runner_factory(
    project: ProjectConfig,
    run_id: str,
    run_root: Path,
    llm: LLMClient,
    *,
    docker_manager: DockerManager | None = None,
) -> AgentRunner:
    return AgentRunner(
        project=project,
        run_id=run_id,
        run_root=run_root,
        llm=llm,
        memory_writer=MemoryWriter(),
        docker_manager=docker_manager,
    )


class JobRunner:
    """Worker loop. Drains the queue, runs jobs, persists results."""

    def __init__(
        self,
        queue: JobQueue,
        project_loader: Callable[[str], Any],  # project_id → coroutine[dict|None]
        data_root: Path | str,
        llm: LLMClient | None = None,
        docker_manager: DockerManager | None = None,
        run_registry: Any = None,  # optional RunRegistry from api/websocket
        quip_registry: Any = None,  # optional QuipRegistry from api/websocket
        runner_factory: RunnerFactory | None = None,
        poll_timeout_s: float = 1.0,
    ):
        self.queue = queue
        self.project_loader = project_loader
        self.data_root = Path(data_root)
        self.llm = llm
        self.docker = docker_manager or DockerManager()
        self.run_registry = run_registry
        self.quip_registry = quip_registry
        self.runner_factory = runner_factory or default_runner_factory
        self.poll_timeout_s = poll_timeout_s
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        """Main loop. Runs until `stop()` is called."""
        _logger.info("JobRunner started (mode=%s)", self.docker.mode)
        while not self._stop.is_set():
            job = await self.queue.dequeue(timeout=self.poll_timeout_s)
            if job is None:
                continue
            try:
                await self.run_one(job)
            except Exception as e:
                _logger.exception("job %s crashed", job.job_id)
                await self.queue.update_status(job.job_id, JOB_FAILED, error=str(e))

    def _initialize_run(self, job: Job, run_root: Path) -> None:
        """Seed the run's workspace from the project's persistent inputs.

        Order matters:
          1. seed_run: copies project_dir/inputs/* → run/workspace/inputs/
          2. seed_working_from_source: if source_repo present, populate
             working/ + fresh git init + seed commit
          3. bundle_prior_history: per attached prior run with a working/ on
             disk, bundle its history under inputs/from_run_{id}/.history.bundle
          4. generate_inputs_readme: last so it sees the bundles too
        """
        project_dir = self.data_root / "projects" / job.project_id
        run_initializer.seed_run(project_dir, run_root)

        run_inputs = run_root / "workspace" / "inputs"
        working_dir = run_root / "workspace" / "working"

        run_initializer.seed_working_from_source(
            working_dir, run_inputs / "source_repo"
        )

        for meta_entry in run_inputs.glob("from_run_*.meta.json"):
            try:
                meta = json.loads(meta_entry.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            prior_run_id = meta.get("run_id")
            if not prior_run_id:
                continue
            prior_working = (
                self.data_root / "runs" / prior_run_id / "workspace" / "working"
            )
            if prior_working.is_dir():
                bundle_path = run_inputs / f"from_run_{prior_run_id}" / ".history.bundle"
                run_initializer.bundle_prior_history(prior_working, bundle_path)

        run_initializer.generate_inputs_readme(run_inputs)

    async def run_one(self, job: Job) -> None:
        """Execute a single job end-to-end. Idempotent on status updates."""
        await self.queue.update_status(job.job_id, JOB_RUNNING)

        # 1. Load project config.
        try:
            project_data = await self.project_loader(job.project_id)
        except Exception as e:
            await self.queue.update_status(
                job.job_id, JOB_FAILED,
                error=f"project loader failed: {e}",
            )
            return
        if project_data is None:
            await self.queue.update_status(
                job.job_id, JOB_FAILED,
                error=f"project {job.project_id!r} not found",
            )
            return
        project = serialise_project(project_data)

        # 2. Set up paths.
        run_root = self.data_root / "runs" / job.run_id
        run_root.mkdir(parents=True, exist_ok=True)

        # 2.5. Pre-flight initialisation (DESIGN §33). Copy project inputs
        # into the run's workspace/inputs/, seed working/ from any
        # source_repo, bundle attached prior runs' git history, and write
        # the inputs/README.md index.
        try:
            self._initialize_run(job, run_root)
        except Exception as e:
            await self.queue.update_status(
                job.job_id, JOB_FAILED,
                error=f"run initialization failed: {e}",
            )
            return

        # 3. Verify LLM client is configured.
        llm = self.llm or _build_default_llm()

        # 4. Construct runner. Thread docker_manager through so the
        # production code:exec routing in core/runner.py can reach it.
        runner = self.runner_factory(
            project, job.run_id, run_root, llm,
            docker_manager=self.docker,
        )

        # 5. Register live workspace for the WebSocket bridge.
        if self.run_registry is not None:
            try:
                await self.run_registry.register(job.run_id, runner.workspace)
            except Exception:
                pass

        # 5.5. Build a QuipEngine + register for the quip WebSocket. The
        # engine subscribes to the runner so quips emitted by
        # on_run_complete / on_iteration_complete fan out to live clients.
        # Only enabled when a quip_registry is provided so existing tests
        # that don't construct one don't accidentally consume MockLLM
        # turns on quip generation.
        if self.quip_registry is not None:
            quip_log_path = run_root / "quip_log.json"
            quip_engine = QuipEngine(llm=llm, log_path=quip_log_path)
            runner.quip_engine = quip_engine
            try:
                await self.quip_registry.register(job.run_id, quip_engine)
            except Exception:
                pass

        # 6. Container lifecycle (stub in dev, real in production).
        try:
            await self.docker.start(
                run_id=job.run_id,
                workspace_dir=run_root / "workspace",
                personas_dir=self.data_root / "personas",
            )
        except DockerManagerError as e:
            await self.queue.update_status(job.job_id, JOB_FAILED, error=str(e))
            if self.run_registry is not None:
                try:
                    await self.run_registry.unregister(job.run_id)
                except Exception:
                    pass
            return

        # 7. Drive the run.
        status = JOB_COMPLETE
        error: str | None = None
        try:
            result = await runner.run()
            run_status = result.status.value
            if run_status not in ("complete",):
                # MAX_ITERATIONS / STUCK / DEADLINE_EXCEEDED are still
                # successful jobs from the queue's POV; the run just
                # didn't reach success. Surface the status in meta.json.
                pass
        except Exception as e:
            status = JOB_FAILED
            error = f"{type(e).__name__}: {e}"
            run_status = "errored"
            _logger.exception("runner crashed for job %s", job.job_id)
        finally:
            await self.docker.stop(job.run_id)
            if self.run_registry is not None:
                try:
                    await self.run_registry.unregister(job.run_id)
                except Exception:
                    pass
            if self.quip_registry is not None:
                try:
                    await self.quip_registry.unregister(job.run_id)
                except Exception:
                    pass

        # 8. Persist run artifacts.
        try:
            _persist_run(run_root, runner, job.run_id, job.project_id, run_status)
        except Exception:
            _logger.exception("persistence failed for job %s", job.job_id)

        await self.queue.update_status(job.job_id, status, error=error)


def _build_default_llm() -> LLMClient:
    """Default LLM client — Anthropic via SDK. Reads ANTHROPIC_API_KEY
    from env. Tests inject their own client and never hit this path."""
    return AnthropicClient()


# ── CLI entry point ────────────────────────────────────────────────────────


async def _amain() -> None:
    """Standalone worker. Reads queue from a shared queue source.

    For Stage 7 MVP this is sketchy: the API and worker each have their
    own InMemoryJobQueue, which doesn't actually share work cross-process.
    A real shared queue (Redis-backed) lands in a follow-on. For now,
    `python -m worker.job_runner` is mostly useful for documentation;
    real dev usage runs the worker in-process with the API.
    """
    from api.storage import ProjectStore
    from worker.queue import InMemoryJobQueue

    data_root = Path(os.environ.get("AGENTFLOW_DATA_DIR", "./data")).resolve()
    queue = InMemoryJobQueue()
    store = ProjectStore(data_root)

    async def loader(project_id: str):
        return await store.get(project_id)

    worker = JobRunner(
        queue=queue,
        project_loader=loader,
        data_root=data_root,
    )
    await worker.run_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_amain())
