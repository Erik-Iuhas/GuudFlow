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
from core.llm.registry import ModelRegistry
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


def _is_in_place_source(project_dir: Path) -> bool:
    """Track 4.3 — true if the project's source_repo.meta.json declares
    `kind: "local_path_in_place"`. Used by the worker to skip
    `seed_working_from_source` so we never clobber the user's repo."""
    meta_path = project_dir / "inputs" / "source_repo.meta.json"
    if not meta_path.is_file():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return meta.get("kind") == "local_path_in_place"


def serialise_project(data: dict[str, Any]) -> ProjectConfig:
    """Turn a stored project dict into a ProjectConfig dataclass.

    The stored format mirrors `ProjectConfig.__dict__`: project_id, goal,
    agents (list of dicts), rbac (dict of role → {allowed_scopes}),
    mcp_servers (list), max_iterations.
    """
    agents: list[AgentConfig] = []
    for a in data.get("agents") or []:
        # Track 12 — per-agent sub-LLM model overrides (dict[use_case → model_id]).
        raw_agent_sub_llm = a.get("sub_llm_models_override") or {}
        agent_sub_llm: dict[str, str] = {}
        if isinstance(raw_agent_sub_llm, dict):
            for k, v in raw_agent_sub_llm.items():
                if v:
                    agent_sub_llm[str(k)] = str(v)
        # Track 12 — explicit baseline opt-out list.
        raw_excluded = a.get("excluded_baseline_tools") or []
        excluded_baseline = (
            [str(x) for x in raw_excluded if x]
            if isinstance(raw_excluded, list) else []
        )
        agents.append(AgentConfig(
            role=a.get("role", ""),
            description=a.get("description", ""),
            depends_on=list(a.get("depends_on") or []),
            system_prompt=a.get("system_prompt", ""),
            declared_local_tools=list(a.get("declared_local_tools") or []),
            declared_mcp_servers=list(a.get("declared_mcp_servers") or []),
            persona_id=a.get("persona_id"),
            name=(str(a.get("name") or "").strip() or None),
            model_tier=a.get("model_tier", "solid"),
            model_id=str(a.get("model_id") or ""),
            agent_class_role_type=a.get("agent_class_role_type"),
            is_reviewer=bool(a.get("is_reviewer", False)),
            is_final_reviewer=bool(a.get("is_final_reviewer", False)),
            max_tool_iterations=int(a.get("max_tool_iterations", 10)),
            sub_llm_models_override=agent_sub_llm,
            excluded_baseline_tools=excluded_baseline,
            # Hierarchy fields — were silently dropped, which is why the
            # per-role dispatch rule never fired even though projects.json
            # had `manages` populated.
            manages=list(a.get("manages") or []),
            escalates_to=list(a.get("escalates_to") or []),
            role_type=str(a.get("role_type") or ""),
            capabilities={str(k): str(v) for k, v in (a.get("capabilities") or {}).items()},
            review_of=[str(r) for r in (a.get("review_of") or [])],
            max_review_rounds=int(a.get("max_review_rounds", 2)),
            mcp_tools=([str(t) for t in a["mcp_tools"]]
                       if isinstance(a.get("mcp_tools"), list) else None),
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

    raw_desk_seeds = data.get("desk_seeds") or {}
    desk_seeds: dict[str, list[dict]] = {}
    if isinstance(raw_desk_seeds, dict):
        for persona_id_key, seeds in raw_desk_seeds.items():
            if isinstance(seeds, list):
                desk_seeds[str(persona_id_key)] = seeds

    raw_stories = data.get("stories") or []
    stories: list[dict] = []
    if isinstance(raw_stories, list):
        for s in raw_stories:
            if isinstance(s, dict) and s.get("name") and s.get("goal"):
                stories.append(s)

    raw_pr_mode = str(data.get("pr_mode") or "manual").lower()
    pr_mode = raw_pr_mode if raw_pr_mode in ("manual", "auto") else "manual"

    # Track 12 — validate sub_llm_models against the model registry. Unknown
    # ids fail loudly so typos surface at project-load time, not at the
    # first dispatch call mid-run. Empty / missing dict is valid (everything
    # falls back to cheapest_tool_capable() at dispatch time).
    raw_sub_llm = data.get("sub_llm_models") or {}
    sub_llm_models: dict[str, str] = {}
    if isinstance(raw_sub_llm, dict) and raw_sub_llm:
        from core import storage as _storage
        from core.llm.registry import ModelRegistry as _MR
        try:
            _reg = _MR.for_data_root(_storage.data_root())
            _known = {m.id for m in _reg.list_models()}
        except Exception:
            _known = set()
        for use_case, model_id in raw_sub_llm.items():
            mid = str(model_id or "").strip()
            if not mid:
                continue
            if _known and mid not in _known:
                raise ValueError(
                    f"sub_llm_models[{use_case!r}] = {mid!r} is not in the "
                    "model registry (Settings → Models & providers). Pick a known model "
                    "or remove the entry to fall back to cheapest_tool_capable()."
                )
            sub_llm_models[str(use_case)] = mid

    return ProjectConfig(
        project_id=data.get("project_id", ""),
        goal=data.get("goal", ""),
        agents=agents,
        rbac=rbac,
        mcp_servers=list(data.get("mcp_servers") or []),
        max_iterations=int(data.get("max_iterations", 5)),
        escalation_thresholds=escalation_thresholds,
        approval_gates=approval_gates,
        mode=str(data.get("mode") or "production"),
        training_scenario_id=data.get("training_scenario_id"),
        desk_seeds=desk_seeds,
        stories=stories,
        pr_mode=pr_mode,  # type: ignore[arg-type]
        sub_llm_models=sub_llm_models,
        # opencode is the default harness (plan P6, O15); "legacy" only on request.
        harness="legacy" if data.get("harness") == "legacy" else "opencode",
    )


# ── Persona bootstrapping ──────────────────────────────────────────────────


def _bootstrap_personas(project: ProjectConfig, personas_root: Path) -> None:
    """Create minimal persona directories for any agent persona_ids that
    don't exist on disk yet.

    When the meta-agent proposes `create_new` hires, suggest_persona_id()
    generates a persona_id (e.g. "aria-dev-v1") but nothing writes the
    directory at proposal time. Without this, Persona.load() silently
    fails, the agent runs without memory, and the persona never appears
    in the roster after the run.
    """
    for cfg in project.agents:
        if not cfg.persona_id:
            continue
        persona_dir = personas_root / cfg.persona_id
        if persona_dir.is_dir():
            continue
        try:
            persona_dir.mkdir(parents=True, exist_ok=True)
            # Derive a human-readable name from the persona_id:
            # "aria-dev-v1" → "Aria", "mike-backend-v2" → "Mike"
            name_parts = cfg.persona_id.split("-")
            name = name_parts[0].capitalize() if name_parts else cfg.persona_id
            profile: dict[str, Any] = {
                "persona_id": cfg.persona_id,
                "name": name,
                "specialty": cfg.role,
                "character": f"A focused and competent {cfg.role}.",
                "stake_framing": f"Your work as {cfg.role} shapes the outcome of every run.",
            }
            # M2: include goal from proposal if provided.
            persona_goal = getattr(cfg, "persona_goal", "") or ""
            if persona_goal:
                profile["goal"] = persona_goal
            (persona_dir / "profile.json").write_text(
                json.dumps(profile, indent=2), encoding="utf-8"
            )

            # M2: seed persona desk from role template if one exists.
            desk_dir = persona_dir / "desk"
            desk_dir.mkdir(exist_ok=True)
            template_desk = Path("data/role_templates") / cfg.role / "desk"
            if template_desk.is_dir():
                import shutil as _shutil
                for tmpl_file in template_desk.iterdir():
                    if tmpl_file.is_file() and tmpl_file.suffix == ".md":
                        dest = desk_dir / tmpl_file.name
                        if not dest.exists():
                            _shutil.copy2(tmpl_file, dest)
            # Skeleton files so the persona is never in a partial state if the
            # run crashes before memory_writer completes.
            (persona_dir / "episodes.jsonl").write_text("", encoding="utf-8")
            (persona_dir / "patterns.json").write_text(
                json.dumps({"version": 3, "patterns": [], "anti_patterns": [], "archived": []}, indent=2),
                encoding="utf-8",
            )
            (persona_dir / "reputation.json").write_text(
                json.dumps(
                    {
                        "production": {
                            "total_runs": 0,
                            "completed_runs": 0,
                            "failed_runs": 0,
                            "average_iterations_to_approval": 0.0,
                            "reviewer_first_pass_rate": 0.0,
                            "by_task_type": {},
                            "common_feedback_themes": [],
                        },
                        "training": {
                            "total_runs": 0,
                            "completed_runs": 0,
                            "failed_runs": 0,
                            "average_iterations_to_approval": 0.0,
                            "reviewer_first_pass_rate": 0.0,
                            "by_task_type": {},
                            "common_feedback_themes": [],
                            "graduation_status": "ungraduated",
                            "scenarios_completed": [],
                            "average_rubric_score": 0.0,
                        },
                        "total_episodes": 0,
                        "active_patterns": 0,
                        "active_anti_patterns": 0,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            (persona_dir / "style.json").write_text(
                json.dumps(
                    {
                        "tendencies": {},
                        "reviewer_relationships": {},
                        "project_type_performance": {},
                        "model_compatibility": {},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            # M2: history.json skeleton (append point in memory_writer).
            history_path = persona_dir / "history.json"
            if not history_path.exists():
                history_path.write_text(
                    json.dumps({"entries": []}, indent=2), encoding="utf-8"
                )
        except OSError as exc:
            _logger.error(
                "Failed to bootstrap persona dir %s: %s", persona_dir, exc
            )
            raise


# ── Persistence ────────────────────────────────────────────────────────────


def serialise_signal(sig: Signal) -> dict[str, Any]:
    """Reusable signal-to-dict mapping. Used by both the end-of-run
    `_persist_run` (consolidated board.json) and the incremental
    board persister attached to the workspace (Phase G-prime.0)."""
    return {
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
    }


def serialise_task(task) -> dict[str, Any]:
    """Reusable Task-to-dict mapping for the M3.5 task persister.
    Mirrors `serialise_signal` so the resume loader has a symmetric
    pair of entrypoints."""
    return {
        "task_id": task.task_id,
        "story_id": task.story_id,
        "parent_task_id": task.parent_task_id,
        "assigned_to": task.assigned_to,
        "created_by": task.created_by,
        "created_at_iter": task.created_at_iter,
        "goal": task.goal,
        "acceptance_criteria": list(task.acceptance_criteria),
        "out_of_scope": list(task.out_of_scope),
        "references": [
            {"kind": r.kind, "value": r.value, "note": r.note}
            for r in task.references
        ],
        "status": task.status,
        "started_at_iter": task.started_at_iter,
        "completed_at_iter": task.completed_at_iter,
        "closed_by_commit": task.closed_by_commit,
        "blocked_on": list(task.blocked_on),
        "cancelled_reason": task.cancelled_reason,
        "title": task.title,
        "kind": task.kind,
        "size": task.size,
        "files_hint": list(task.files_hint),
        "depends_on": list(task.depends_on),
        "visual_refs": list(task.visual_refs),
        "column": task.column,
        "review_round": task.review_round,
        "priority": task.priority,
        "column_since": task.column_since,
        "thread": [dict(e) for e in task.thread],
        "backlog_id": task.backlog_id,
    }


def _persist_run(run_root: Path, runner: AgentRunner, run_id: str,
                 project_id: str, status: str) -> None:
    """Write meta.json + board.json so the API can list / read this run.

    `board.json` is the consolidated end-of-run snapshot — readers (the
    runs API, the tournament leaderboard) consume it. The incremental
    `board.jsonl` written by the workspace's board persister
    (Phase G-prime.0) is the crash-safe trail used by the resume
    loader; it co-exists with this file."""
    run_root.mkdir(parents=True, exist_ok=True)
    meta = {
        "run_id": run_id,
        "project_id": project_id,
        "goal": runner.workspace.goal,
        "status": status,
        "iteration": runner.workspace.iteration,
        "completed_agents": list(runner.workspace.completed_agents),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pr_mode": getattr(runner.project, "pr_mode", "manual"),
        # Track 4.4 — absolute path of the agents' working tree. In normal
        # (isolated) runs this is `<run>/workspace/working/`; in in-place
        # mode (Track 4.3) it points at the user's chosen directory. The
        # runs API reads this so Files / Git tabs show the right tree.
        "working_dir": str(runner.files.working),
    }
    # Track 4.2 — surface the latest PR_READY signal so the run-completion
    # view can read the integration branch + gh command without scanning
    # the full board.
    pr_ready = next(
        (sig for sig in reversed(runner.workspace.board)
         if sig.type.value == "PR_READY"),
        None,
    )
    if pr_ready is not None:
        try:
            meta["pr_ready"] = json.loads(pr_ready.detail or "{}")
        except (json.JSONDecodeError, TypeError):
            meta["pr_ready"] = {"summary": pr_ready.summary}
    (run_root / "meta.json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8",
    )

    board = [serialise_signal(sig) for sig in runner.workspace.board]
    (run_root / "board.json").write_text(
        json.dumps(board, indent=2, default=str), encoding="utf-8",
    )


# ── JobRunner ──────────────────────────────────────────────────────────────


RunnerFactory = Callable[..., AgentRunner]
"""Factory signature: (project, run_id, run_root, llm, *, data_root=None,
restored_workspace=None) -> AgentRunner.

The trailing keyword args are passed as keywords so factories that ignore
them still work."""


def default_runner_factory(
    project: ProjectConfig,
    run_id: str,
    run_root: Path,
    llm: LLMClient,
    *,
    data_root: Path | None = None,
    restored_workspace=None,
) -> AgentRunner:
    # Phase F.1.1 — wire the rubric_scorer + scenarios root so training
    # mode produces a RUBRIC_SCORE signal automatically. Production
    # runs ignore both fields.
    from agents.builtin.rubric_scorer import RubricScorer
    scenarios_root = (
        Path(data_root) / "training_camp" / "scenarios"
        if data_root is not None else None
    )
    # Phase G.7 — personas_root so AgentRunner can auto-load personas
    # from cfg.persona_id. Without this, worker-invoked runs that hire
    # a persona never actually attach it, and memory_writer is a no-op.
    personas_root = (
        Path(data_root) / "personas" if data_root is not None else None
    )
    # Phase K.1 — project_dir so AgentRunner can read the roadmap.md.
    project_dir = (
        Path(data_root) / "projects" / project.project_id
        if data_root is not None else None
    )
    # M3: load ModelRegistry when no explicit LLM is injected (production path).
    # Tests inject llm=MockLLMClient; the registry is only loaded for production
    # runs where llm=None.
    model_registry: ModelRegistry | None = None
    if llm is None:
        # The install's saved configuration (Studio "Models & providers") or the
        # shipped defaults, with keys stored from Studio (core/model_config.py).
        try:
            model_registry = ModelRegistry.for_data_root(
                Path(data_root) if data_root is not None else None)
        except Exception:  # noqa: BLE001 — a malformed registry fails the first spawn
            _logger.exception("could not load the model registry")

    # MemoryWriter needs a concrete LLM client (it calls complete() directly
    # for pattern synthesis). Give it the injected client, or resolve a
    # SOLID-tier client from the registry, or fall back to AnthropicClient.
    if llm is not None:
        mw_llm: LLMClient = llm
    elif model_registry is not None:
        try:
            mw_model = model_registry.resolve("solid")
            mw_llm = model_registry.get_client(mw_model)
        except Exception:
            mw_llm = _build_default_llm()
    else:
        mw_llm = _build_default_llm()

    return AgentRunner(
        project=project,
        run_id=run_id,
        run_root=run_root,
        llm=llm,
        memory_writer=MemoryWriter(llm=mw_llm),
        rubric_scorer=RubricScorer(),
        training_scenarios_root=scenarios_root,
        restored_workspace=restored_workspace,
        personas_root=personas_root,
        project_dir=project_dir,
        model_registry=model_registry,
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
        """Main loop. Runs until `stop()` is called or the task is cancelled.

        Exit-cause forensics: when CancelledError fires, dump diagnostics
        to `<data_root>/worker.cancellations.log` so we can finally pin
        down what's killing the worker mid-run (signals, lifespan exit,
        watchfiles reload, etc).
        """
        import os as _os, signal as _signal, time as _time, traceback as _tb
        _logger.info("JobRunner started pid=%d (mode=%s)", _os.getpid(), self.docker.mode)
        # Install a SIGTERM/SIGINT logger so we know WHEN signals land,
        # separately from whatever cancels the asyncio task. Best-effort —
        # signal.signal raises ValueError on Windows for some signals.
        worker_log = self.data_root / "worker.cancellations.log"
        def _signal_tap(signum, frame):
            try:
                with open(worker_log, "a", encoding="utf-8") as f:
                    f.write(f"--- signal {signum} @ {_time.time():.3f} pid={_os.getpid()} ---\n")
                    f.write(_tb.format_stack(frame, limit=10))
                    f.write("\n")
            except Exception:
                pass
        for sig_name in ("SIGTERM", "SIGINT", "SIGBREAK"):
            sig = getattr(_signal, sig_name, None)
            if sig is None:
                continue
            try:
                # Don't replace existing handlers — chain.
                prev = _signal.signal(sig, _signal_tap)
                if prev is not None and not callable(prev):
                    pass
            except (ValueError, OSError):
                pass

        try:
            while not self._stop.is_set():
                job = await self.queue.dequeue(timeout=self.poll_timeout_s)
                if job is None:
                    continue
                try:
                    await self.run_one(job)
                except Exception as e:
                    _logger.exception("job %s crashed", job.job_id)
                    await self.queue.update_status(job.job_id, JOB_FAILED, error=str(e))
        except asyncio.CancelledError:
            try:
                with open(worker_log, "a", encoding="utf-8") as f:
                    f.write(
                        f"--- run_forever CANCELLED @ {_time.time():.3f} pid={_os.getpid()} ---\n"
                        f"_stop.is_set={self._stop.is_set()}\n"
                        f"current_task={asyncio.current_task()!r}\n"
                        f"all tasks ({len(asyncio.all_tasks())}):\n"
                    )
                    for t in asyncio.all_tasks():
                        f.write(f"  - {t!r}\n")
                    f.write(f"\nstack:\n{_tb.format_exc()}\n\n")
            except Exception:
                pass
            raise

    def _initialize_run(self, job: Job, run_root: Path,
                        project_data: dict[str, Any] | None = None) -> None:
        """Seed the run's workspace from the project's persistent inputs.

        Order matters:
          1. seed_run: copies project_dir/inputs/* → run/workspace/inputs/
          2. seed_working_from_source: if source_repo present, populate
             working/ + fresh git init + seed commit
          3. bundle_prior_history: per attached prior run with a working/ on
             disk, bundle its history under inputs/from_run_{id}/.history.bundle
          4. generate_inputs_readme: last so it sees the bundles too
          5. project_runs.prepare_run (plan §13): working/ becomes a clone of
             the project repo's main (seeded from step 2 the first time) and
             the backlog items the run takes are snapshotted
        """
        from core import project_runs
        from core.project_repo import ProjectRepo

        project_dir = self.data_root / "projects" / job.project_id
        run_initializer.seed_run(project_dir, run_root)

        run_inputs = run_root / "workspace" / "inputs"
        working_dir = run_root / "workspace" / "working"
        in_place = _is_in_place_source(project_dir)

        # Track 4.3 — when this project's source repo is `local_path_in_place`,
        # agents work directly in the user's directory; do not seed/clobber it.
        # Once the project repo exists, it (not the original source) is the seed.
        if not in_place and not ProjectRepo(self.data_root, job.project_id).exists():
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
        from core.autopilot import RUN_MARKER
        from core.json_store import write_json
        from core.project_remote import RemoteConfig
        if isinstance((job.payload or {}).get("autopilot"), dict):
            write_json(run_root / RUN_MARKER, job.payload["autopilot"])
        gates = ((project_data.get("approval_gates") if isinstance(project_data, dict)
                  else getattr(project_data, "approval_gates", None)) or {})
        project_runs.prepare_run(self.data_root, job.project_id, job.run_id, run_root,
                                 working_dir, dict(job.payload or {}), in_place=in_place,
                                 remote=RemoteConfig.from_settings(gates.get("project_remote")))

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
        # Autopilot runs carry a per-run spend cap (plan §14.7): it can only tighten
        # the project's own token_budget_usd.
        cap = (job.payload or {}).get("budget_usd")
        if isinstance(cap, (int, float)) and cap > 0:
            gates = dict(project.approval_gates or {})
            own = gates.get("token_budget_usd")
            gates["token_budget_usd"] = (min(float(own), float(cap))
                                         if isinstance(own, (int, float)) and own > 0
                                         else float(cap))
            project.approval_gates = gates

        # 1.5. Bootstrap persona directories for any create_new persona_ids
        # that have no on-disk directory yet. Without this, Persona.load()
        # silently fails and the agent runs without memory + reputation —
        # after the run the persona never appears in the roster.
        personas_root = self.data_root / "personas"
        _bootstrap_personas(project, personas_root)

        # 2. Set up paths.
        run_root = self.data_root / "runs" / job.run_id
        run_root.mkdir(parents=True, exist_ok=True)

        # Phase G-prime.3 — resume branch. When the API enqueues a
        # job with payload.resume = True, skip fresh initialisation
        # (the workspace dir already exists with inputs + working/)
        # and load the prior board + artifacts from disk so the
        # scheduler can pick up where it left off.
        is_resume = bool(job.payload.get("resume"))

        if not is_resume:
            # 2.5. Pre-flight initialisation (DESIGN §33). Copy project inputs
            # into the run's workspace/inputs/, seed working/ from any
            # source_repo, bundle attached prior runs' git history, and write
            # the inputs/README.md index.
            try:
                self._initialize_run(job, run_root, project_data)
            except Exception as e:
                await self.queue.update_status(
                    job.job_id, JOB_FAILED,
                    error=f"run initialization failed: {e}",
                )
                return

        restored_workspace = None
        if is_resume:
            from core.run_loader import restore_workspace as _restore
            try:
                restored_workspace = _restore(run_root, project)
            except Exception as e:
                await self.queue.update_status(
                    job.job_id, JOB_FAILED,
                    error=f"resume state restoration failed: {e}",
                )
                return
            # Phase G.3 — synthetic banner signal so the studio's Run
            # view can show "resumed from iteration N, with K
            # completed agents." Posted via the workspace itself so
            # the persister captures it for future resumes.
            from core.signals import Signal as _Signal, SignalType as _SignalType
            restored_workspace.post_signal(_Signal(
                type=_SignalType.STARTED,
                summary=(
                    f"resumed from iteration {restored_workspace.iteration} "
                    f"({len(restored_workspace.completed_agents)} agents already complete)"
                ),
                from_agent="runner",
                iteration=restored_workspace.iteration,
            ))

        # 3. Resolve LLM client. self.llm is set only when explicitly injected
        # (tests / single-client overrides). None → default_runner_factory
        # loads ModelRegistry and resolves per-agent clients by tier.
        llm = self.llm  # may be None; factory handles it

        # 4. Construct runner. On resume, also thread the restored workspace
        # so the runner uses it instead of constructing a fresh one.
        factory_kwargs: dict[str, Any] = {
            "data_root": self.data_root,
        }
        if restored_workspace is not None:
            factory_kwargs["restored_workspace"] = restored_workspace
        runner = self.runner_factory(
            project, job.run_id, run_root, llm,
            **factory_kwargs,
        )

        # 5. Register live workspace (and runner) for the WebSocket bridge
        #    and the stop endpoint.
        if self.run_registry is not None:
            try:
                await self.run_registry.register(job.run_id, runner.workspace, runner)
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
            quip_engine = QuipEngine(llm=llm or AnthropicClient(), log_path=quip_log_path)
            runner.quip_engine = quip_engine
            try:
                await self.quip_registry.register(job.run_id, quip_engine)
            except Exception:
                pass

        # 5.9. Write an initial meta.json so GET /runs/{run_id} works
        # immediately while the run is live. The frontend uses this to
        # load project config and render the pipeline graph before the
        # run finishes. Overwritten at the end by _persist_run.
        _persist_run(run_root, runner, job.run_id, job.project_id, "running")

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
            from core.project_runs import release_crashed_run
            release_crashed_run(self.data_root, job.project_id, job.run_id, run_root, error)
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

    data_root = Path(os.environ.get("GUUDFLOW_DATA_DIR", "./data")).resolve()
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
