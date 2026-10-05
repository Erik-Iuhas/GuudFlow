"""AgentRunner: owns one run's workspace and drives it on the opencode harness.

DESIGN §3.3 (scheduler), §17 (file workspace), §23 (quips), §34 (memory
reconciliation), §35 (training mode) and docs/OPENCODE_HARNESS_PLAN.md §3.1.

Responsibilities:
  1. Initialise workspace state: board / task / artifact persisters, the
     file workspace and the run's git repo.
  2. Hand the agent work to `core.harness.run.OpencodeRun`, which drives the
     unchanged scheduler with one opencode session per spawn.
  3. After the run: training rubric scoring, the memory writer, the quip
     engine's closing flourish, the retrospective and the project playbook.

The in-process tool-use loop this class used to contain was retired in work
breakdown S15 (restore point: commit e80822f). Old runs stay viewable from
their saved boards; the legacy harness can no longer start a run.

The runner is constructed with a project config and run-time handles, then
`await runner.run()` drives the whole thing.
"""

from __future__ import annotations

import json
import logging
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from agents.base import BaseAgent
from core.file_workspace import FileWorkspace
from core.git_workspace import GitWorkspace
from core.llm import LLMClient
from core.llm.registry import ModelRegistry
from core.scheduler import Scheduler, SchedulerResult, SchedulerStatus
from core.signals import Signal, SignalType
from core.tool_manifest import RoleRBAC
from core.workspace import Artifact, Task, Workspace

if TYPE_CHECKING:
    from agents.persona import Persona
    from core.harness.run import HarnessSettings
    from core.quip_engine import QuipEngine

log = logging.getLogger(__name__)

LEGACY_HARNESS_RETIRED = (
    "the legacy harness was retired; old runs remain viewable from their saved boards"
)
"""Error text for a project that still asks for `harness: "legacy"`."""

@dataclass
class AgentConfig:
    """One agent slot in a project config — what the runner needs to
    instantiate a BaseAgent (or a subclass from the registry).

    `agent_class` is None for prompt-defined agents (use BaseAgent
    directly). `model_tier` maps to a real model id at run start.
    """

    role: str
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    system_prompt: str = ""
    declared_local_tools: list[str] = field(default_factory=list)
    declared_mcp_servers: list[str] = field(default_factory=list)
    persona_id: str | None = None
    model_tier: str = "solid"  # "heavy" | "solid" | "fast"
    # Optional explicit model id (e.g. "mistral-large-latest"). When set
    # AND the model's tier matches `model_tier`, the registry returns this
    # model. Empty string means "first available model in tier" (the
    # default behaviour). Studio's ModelPicker writes both fields so the
    # provider+model dropdowns persist correctly across reloads.
    model_id: str = ""
    agent_class_role_type: str | None = None  # registry lookup; None = generic BaseAgent
    is_reviewer: bool = False
    # Phase C.4: final-stage reviewer (architect pattern). Auto-depends
    # on every non-final-reviewer role at runtime so it runs LAST.
    # Triggers the heavy_tier_mid_run_dispatch gate when enabled per
    # project. Distinct from is_reviewer (which is one of N peer
    # reviewers); a final reviewer sees the whole merged result.
    is_final_reviewer: bool = False
    max_tool_iterations: int = 30  # cap on tool-use loop iterations within one run() call (extendable on user approval)
    # How many times to re-prompt on a protocol parse failure before giving up.
    # On exhaustion the runner emits a synthetic [VERIFY] FAIL instead of crashing.
    max_protocol_retries: int = 3
    # M2 — one-sentence goal for the persona filling this slot. Written to
    # profile.json on create_new bootstrap; used in teammate brief (Layer 3.5).
    persona_goal: str = ""
    # Track 12 — per-agent override for sub-LLM dispatch model selection.
    # Maps use_case → registered model id (data/models.json). Checked
    # before the project-level `sub_llm_models`. Empty dict (default)
    # means agent inherits whatever the project has set, which itself
    # falls back to ModelRegistry.cheapest_tool_capable() if missing.
    sub_llm_models_override: dict[str, str] = field(default_factory=dict)
    # Track 12 — explicit opt-out of baseline tools (read-class tools that
    # are normally always granted, like fs-read / git-log). Most users
    # shouldn't touch this; it exists for tightly scoped agents that
    # genuinely shouldn't see, say, the git read tools. Studio's tool
    # picker exposes this via the un-locked baseline checkbox.
    excluded_baseline_tools: list[str] = field(default_factory=list)
    # Explicit hierarchy. Empty lists fall back to today's derived behavior:
    # manages → RBAC-derived can_assign_to; escalates_to → tech_lead if present.
    manages: list[str] = field(default_factory=list)
    escalates_to: list[str] = field(default_factory=list)
    # opencode harness (docs/OPENCODE_HARNESS_PLAN.md §5.7). `role_type` picks the
    # capability defaults and edit scope; empty means inferred from role/is_reviewer.
    # `capabilities` holds per-role overrides of the seven toggles.
    role_type: str = ""
    capabilities: dict[str, str] = field(default_factory=dict)
    # Review edge (plan §4.5): which roles a reviewer sends work back to (default:
    # its depends_on) and how many rework rounds before escalation.
    review_of: list[str] = field(default_factory=list)
    max_review_rounds: int = 2


@dataclass
class ProjectConfig:
    """The project's run-time config. The meta-agent produces this
    (Stage 5+); for tests and dev runs it can be hand-constructed.

    `escalation_thresholds` (DESIGN §23): mapping of escalation handler
    role -> rejection count that triggers them. Default `{tech_lead: 2}`.
    Set a threshold to 0 to disable that handler.
    """

    project_id: str
    goal: str
    agents: list[AgentConfig] = field(default_factory=list)
    rbac: dict[str, RoleRBAC] = field(default_factory=dict)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    max_iterations: int = 5
    escalation_thresholds: dict[str, int] = field(
        default_factory=lambda: {"tech_lead": 2},
    )
    # Approval gates (Phase C.0.5). Maps gate-name → enabled flag (or
    # threshold for `iteration_breach_at`). Defaults are opinionated:
    # cost preview before run + confirm before HEAVY-tier mid-run
    # dispatch + confirm at iteration breach >= 2.
    approval_gates: dict[str, Any] = field(
        default_factory=lambda: {
            "pre_run_cost_preview": True,
            "heavy_tier_mid_run_dispatch": True,
            "iteration_breach_at": 2,
            "per_iteration": False,
            "token_budget_usd": None,
        },
    )
    # Run mode (DESIGN §35, Phase F.1). "production" is the default;
    # "training" tags episodes as training=True so reputation aggregates
    # split, and triggers the rubric_scorer at end-of-run.
    mode: str = "production"
    # M1 §7b — story seeds. Each entry becomes a STORY_OPENED at run start.
    # Shape: {"name": str, "goal": str, "owner_role": str | None,
    #         "parent_story_id": str | None}.
    stories: list[dict[str, Any]] = field(default_factory=list)
    # M2: project-overlay desk seeds from meta-agent proposal.
    # dict[persona_id → list[DeskFileSeed dicts]]. Runner materializes before
    # _build_agents() so DeskIndex sees the files on first iteration.
    desk_seeds: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # When mode == "training", the rubric_scorer reads this scenario
    # bundle from disk to score the run. Stored on the project at
    # invoke time by the training launcher.
    training_scenario_id: str | None = None
    # M5: consecutive-fail threshold for the redispatch loop. After this many
    # consecutive REJECTED on the same role (without an intervening APPROVED),
    # tech_lead is escalated instead of bouncing the work back again.
    # Separate from escalation_thresholds (which is cumulative across the run).
    consecutive_fail_threshold: int = 2
    # Track 4.2 — when a run completes, `auto` merges the leaf branch into main
    # (sandbox-friendly), `manual` lands work on an integration branch
    # (`flow/story-<slug>` if a story is open, else `flow/run-<id>`) and surfaces
    # a paste-ready `gh pr create` command. Default `manual` for real-repo safety.
    pr_mode: Literal["manual", "auto"] = "manual"
    # Track 12 — per-use-case sub-LLM model picks. Maps use_case name
    # (e.g. "summarise-subtask", "protocol-cleanup", "llm-dispatch") to a
    # registered model id from data/models.json. Empty dict (default) means
    # every use_case falls back to ModelRegistry.cheapest_tool_capable() at
    # dispatch time. Validated at project-load — unknown ids fail loudly.
    sub_llm_models: dict[str, str] = field(default_factory=dict)
    # Which agent harness runs this project. Only "opencode" can start a run;
    # "legacy" is kept so old project configs load (and are refused clearly).
    harness: Literal["legacy", "opencode"] = "opencode"


def _detect_dependency_cycle(agents: list["AgentConfig"]) -> list[str] | None:
    """Return one cycle (as a role list, no repeated tail) or None if acyclic."""
    deps = {a.role: list(a.depends_on or []) for a in agents}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {r: WHITE for r in deps}
    stack: list[str] = []

    def visit(role: str) -> list[str] | None:
        if color.get(role, BLACK) == GRAY:
            i = stack.index(role)
            return stack[i:]
        if color.get(role, BLACK) == BLACK:
            return None
        color[role] = GRAY
        stack.append(role)
        for dep in deps.get(role, []):
            if dep not in deps:
                continue
            cyc = visit(dep)
            if cyc is not None:
                return cyc
        stack.pop()
        color[role] = BLACK
        return None

    for r in deps:
        if color[r] == WHITE:
            cyc = visit(r)
            if cyc is not None:
                return cyc
    return None


class AgentRunner:
    """One run. Owns the workspace, persisters, git repo and post-run hooks."""

    def __init__(
        self,
        project: ProjectConfig,
        run_id: str,
        run_root: Path | str,
        llm: LLMClient | None = None,
        memory_writer: BaseAgent | None = None,
        quip_engine: "QuipEngine | None" = None,
        max_wall_seconds: float | None = 1800.0,  # 30 min run-level budget
        rubric_scorer: BaseAgent | None = None,
        training_scenarios_root: Path | None = None,
        restored_workspace: "Workspace | None" = None,
        personas_root: Path | None = None,
        project_dir: Path | None = None,
        model_registry: ModelRegistry | None = None,
    ):
        self.project = project
        self.run_id = run_id
        self.run_root = Path(run_root)
        # opencode harness knobs (core.harness.run.HarnessSettings); None = defaults.
        self.harness_settings: "HarnessSettings | None" = None
        self.llm = llm
        self._model_registry = model_registry
        self.memory_writer = memory_writer
        self.quip_engine = quip_engine
        self.max_wall_seconds = max_wall_seconds
        # Training Camp (DESIGN §35). The rubric_scorer is optional even in
        # training mode: a run without one simply produces no RUBRIC_SCORE.
        self.rubric_scorer = rubric_scorer
        self.training_scenarios_root = (
            Path(training_scenarios_root) if training_scenarios_root else None
        )
        # Personas are loaded from cfg.persona_id under personas_root so that
        # worker-invoked runs accumulate memory and reputation.
        self.personas_root = Path(personas_root) if personas_root else None
        self._personas: dict[str, "Persona"] | None = None
        self.project_dir = Path(project_dir) if project_dir else None
        # Project-level roadmap document (Phase K.1), read once. Empty or
        # missing file -> empty string -> the prompt layer is omitted.
        self._project_context = ""
        if project_dir is not None:
            roadmap_path = Path(project_dir) / "context" / "roadmap.md"
            if roadmap_path.is_file():
                try:
                    content = roadmap_path.read_text(encoding="utf-8")
                    if content.strip():
                        self._project_context = content
                except OSError:
                    pass

        self._scheduler: Scheduler | None = None  # set by OpencodeRun once it exists
        # Always empty on the opencode harness (no in-process agent objects). The
        # runs API reads it for the live workers snapshot and tolerates a missing role.
        self.agents: dict[str, BaseAgent] = {}

        # Phase G-prime.3: when restoring a prior run use the restored
        # workspace; the runner-bound hooks (persisters) are re-attached below.
        if restored_workspace is not None:
            self.workspace = restored_workspace
        else:
            self.workspace = Workspace(
                run_id=run_id,
                goal=project.goal,
                max_iterations=project.max_iterations,
            )
        # Incremental persistence so a crashed run leaves a complete trail on
        # disk for the resume loader. board.jsonl co-exists with the
        # end-of-run consolidated board.json; readers tolerate either.
        self.workspace.set_board_persister(self._build_board_persister())
        self.workspace.set_task_persister(self._build_task_persister())
        self.workspace.set_artifact_persister(self._build_artifact_persister())
        # Run-level deadline, checked by the scheduler.
        if max_wall_seconds is not None:
            self.workspace.deadline_at = _time.time() + max_wall_seconds
        # Per-project `local_path_in_place` source repo: agents work directly
        # in the user's directory instead of a fresh copy under
        # `<run>/workspace/working/`.
        working_override = self._resolve_in_place_working_dir()
        self.files = FileWorkspace(
            self.run_root / "workspace", working_override=working_override,
        )
        self.workspace.files = self.files
        self.git = GitWorkspace(
            self.files.working,
            emit=self._git_signal_emitter,
        )
        self.git.init()

    def _resolve_in_place_working_dir(self) -> Path | None:
        """Track 4.3 — return the user's directory if this project's source
        repo is `local_path_in_place`, else None (fall back to the run's
        own `workspace/working/`).

        Reads `<project_dir>/inputs/source_repo.meta.json`. Missing meta or
        any other kind returns None silently — there is nothing to override.
        """
        if self.project_dir is None:
            return None
        meta_path = self.project_dir / "inputs" / "source_repo.meta.json"
        if not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if meta.get("kind") != "local_path_in_place":
            return None
        raw = meta.get("path")
        if not raw:
            return None
        candidate = Path(str(raw))
        if not candidate.is_dir():
            return None
        return candidate

    # ── Board persister (Phase G-prime.0) ───────────────────────────────────

    def _build_board_persister(self):
        """Return a callable that appends each new signal to board.jsonl
        in the run root. JSONL chosen over rewriting board.json so each
        write is O(1) and crash-safe (POSIX small appends are atomic).

        Returns None if the run root can't be created — the run will
        proceed without crash-safe persistence; the end-of-run
        consolidated board.json still gets written by `_persist_run`.
        """
        from worker.job_runner import serialise_signal

        try:
            self.run_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        board_path = self.run_root / "board.jsonl"

        def _persist(signal: Signal) -> None:
            try:
                line = json.dumps(serialise_signal(signal), default=str)
                with open(board_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                # Disk write failures shouldn't break the run. The
                # consolidated board.json at end-of-run is the
                # safety net.
                pass

        return _persist

    def _build_task_persister(self):
        """Return a callable that appends each task mutation to
        `{run_root}/tasks.jsonl` (M3.5 chunk 7). Mirrors the board
        persister: JSONL append per call, swallow OSError so a broken
        disk doesn't take the run down. The signal trail in
        board.jsonl remains the source of truth — tasks.jsonl is the
        cached query view that `restore_workspace` replays first.
        """
        from worker.job_runner import serialise_task
        try:
            self.run_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        tasks_path = self.run_root / "tasks.jsonl"

        def _persist(task: Task) -> None:
            try:
                line = json.dumps(serialise_task(task), default=str)
                with open(tasks_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

        return _persist

    def _build_artifact_persister(self):
        """Returns a callable that writes the full artifacts dict to
        `{run_root}/artifacts.json`. Whole-dict rewrite is fine because
        artifact-write events are rare (one per completed agent) and
        the dict is small. Used by the resume loader (G-prime.1) so a
        resumed run reads completed agents' outputs from disk."""
        try:
            self.run_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        artifacts_path = self.run_root / "artifacts.json"

        def _persist(artifacts: dict[str, Artifact]) -> None:
            try:
                payload = {
                    role: {
                        "agent_role": a.agent_role,
                        "type": a.type,
                        "content": a.content,
                        "iteration": a.iteration,
                        "timestamp": a.timestamp.isoformat(),
                    }
                    for role, a in artifacts.items()
                }
                artifacts_path.write_text(
                    json.dumps(payload, indent=2, default=str),
                    encoding="utf-8",
                )
            except OSError:
                pass

        return _persist

    # ── Git signal callback ─────────────────────────────────────────────────

    def _git_signal_emitter(self, signal_type: SignalType, summary: str, detail: str | None) -> None:
        """Bridge from GitWorkspace events to the workspace board."""
        self.workspace.post_signal(Signal(
            type=signal_type,
            summary=summary,
            detail=detail,
            from_agent="git",
        ))


    # ── Force-stop ──────────────────────────────────────────────────────────

    async def force_stop(self) -> None:
        """Cancel all in-flight agent tasks immediately.

        Called by the stop endpoint when the user wants an instant halt rather
        than waiting for the current LLM calls to finish. Sets pending_human
        first so no new agents are spawned, then cancels the scheduler's tasks.
        """
        from core.workspace import HumanRequest

        if self.workspace.pending_human is None:
            self.workspace.pending_human = HumanRequest(
                from_agent="system",
                question="Run stopped by user.",
                iteration=self.workspace.iteration,
            )

        if self._scheduler is not None:
            await self._scheduler.force_cancel()

    # ── Live agent config hot-reload ─────────────────────────────────────────

    def update_agent_config(self, role: str, partial: dict[str, Any]) -> None:
        """Apply `partial` to the project's config for `role`.

        Raises KeyError for an unknown role. The opencode harness reads the
        role config when it next spawns the role, so a change takes effect on
        the next spawn rather than mid-turn.
        """
        cfg = next((c for c in self.project.agents if c.role == role), None)
        if cfg is None:
            raise KeyError(f"unknown role {role!r}")
        allowed = {
            "declared_local_tools",
            "model_tier",
            "model_id",
            "description",
            "max_tool_iterations",
            "max_protocol_retries",
        }
        for key, value in partial.items():
            if key in allowed and hasattr(cfg, key):
                setattr(cfg, key, value)

    # ── Personas ────────────────────────────────────────────────────────────

    def _load_personas(self) -> "dict[str, Persona]":
        """Load the persona behind each role that has `persona_id` (cached).

        A missing or malformed persona directory is tolerated: that role has no
        persona (the memory writer skips it) rather than failing the run.
        """
        if self._personas is not None:
            return self._personas
        loaded: "dict[str, Persona]" = {}
        if self.personas_root is not None:
            from agents.persona import Persona

            for cfg in self.project.agents:
                if not cfg.persona_id:
                    continue
                try:
                    persona = Persona.load(cfg.persona_id, self.personas_root)
                    persona._dir = self.personas_root / cfg.persona_id
                    loaded[cfg.role] = persona
                except Exception:  # noqa: BLE001 — a bad persona must not take the run down
                    log.warning("%s: could not load persona %r", cfg.role, cfg.persona_id,
                                exc_info=True)
        self._personas = loaded
        return loaded

    # ── Run ─────────────────────────────────────────────────────────────────

    def _prepare_team(self) -> None:
        """Validate the team before the scheduler starts.

        A final reviewer with an empty `depends_on` is made to depend on every
        other role that cannot depend on it (so it runs last). A dependency
        cycle posts FAILED and raises, since the scheduler could never start.
        """
        non_final_roles = [c.role for c in self.project.agents if not c.is_final_reviewer]
        reverse_deps: dict[str, set[str]] = {c.role: set() for c in self.project.agents}
        for c in self.project.agents:
            for dep in c.depends_on or []:
                if dep in reverse_deps:
                    reverse_deps[dep].add(c.role)

        def _transitive_dependents(role: str) -> set[str]:
            seen: set[str] = set()
            stack = [role]
            while stack:
                cur = stack.pop()
                for nxt in reverse_deps.get(cur, ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            return seen

        for cfg in self.project.agents:
            if cfg.is_final_reviewer and not cfg.depends_on:
                # Skip roles that transitively depend on us: filling in the
                # reverse direction would create a cycle.
                blockers = _transitive_dependents(cfg.role)
                cfg.depends_on = [
                    r for r in non_final_roles if r != cfg.role and r not in blockers
                ]

        cycle = _detect_dependency_cycle(self.project.agents)
        if cycle:
            chain = " → ".join(cycle + [cycle[0]])
            self.workspace.post_signal(Signal(
                type=SignalType.FAILED,
                summary=f"dependency cycle detected: {chain}",
                detail=(
                    "Run cannot start because the agent dependency graph "
                    "contains a cycle. Edit depends_on on the listed roles."
                ),
                from_agent="scheduler",
            ))
            raise RuntimeError(f"dependency cycle detected: {chain}")

    def _attach_personas(self) -> None:
        """Record who plays each role: hired personas, commit authors, quip voices."""
        personas = self._load_personas()
        for cfg in self.project.agents:
            if cfg.role in personas and cfg.persona_id:
                self.workspace.hired_personas[cfg.role] = cfg.persona_id
        # Each commit carries the persona's display name (e.g. "Sarah (developer)").
        self.git._hired_personas = {
            cfg.role: (personas[cfg.role].profile.name if cfg.role in personas else cfg.role)
            or cfg.role
            for cfg in self.project.agents
        }
        if self.quip_engine is None:
            return
        from core.quip_engine import AgentVoice

        voices = []
        for cfg in self.project.agents:
            persona = personas.get(cfg.role)
            if persona is not None:
                name = persona.profile.name or cfg.role
                char = persona.character_layer() or (
                    f"You are {name}, a specialist on a software team."
                )
            else:
                name = cfg.role.capitalize()
                char = f"You are the {cfg.role} on a software team. {cfg.description}".strip()
            voices.append(AgentVoice(role=cfg.role, name=name, character=char))
        self.quip_engine.set_voices(voices)
        # Dynamic voice synthesis needs workspace access.
        self.quip_engine.set_workspace(self.workspace, self.personas_root)

    async def run(self) -> SchedulerResult:
        """Run the project on the opencode harness and return the scheduler result.

        Raises ValueError for a project that asks for the retired legacy harness.
        """
        if self.project.harness != "opencode":
            raise ValueError(LEGACY_HARNESS_RETIRED)
        return await self._run_opencode()

    async def _run_opencode(self) -> SchedulerResult:
        """Run on the opencode harness (docs/OPENCODE_HARNESS_PLAN.md).

        Reuses this runner's workspace, persisters, git repo and post-run hooks;
        core.harness.run.OpencodeRun replaces the agent loop.
        """
        from core.harness.run import OpencodeRun

        self._prepare_team()
        self._attach_personas()
        result = await OpencodeRun(self, self.harness_settings).run()
        if result.status is SchedulerStatus.STOPPED:
            self._close_project_run(stopped=True)
            return result
        # Training rubric scoring runs before the memory writer so the
        # RUBRIC_SCORE signal is on the board before the episode is recorded.
        if (
            self.project.mode == "training"
            and self.rubric_scorer is not None
            and self.training_scenarios_root is not None
            and hasattr(self.rubric_scorer, "on_run_complete")
        ):
            try:
                await self.rubric_scorer.on_run_complete(  # type: ignore[union-attr]
                    workspace=self.workspace,
                    project=self.project,
                    run_id=self.run_id,
                    run_root=self.run_root,
                    scenarios_root=self.training_scenarios_root,
                )
            except Exception as e:  # noqa: BLE001 — scoring failures must not fail the run
                self.workspace.post_signal(Signal(
                    type=SignalType.FAILED, summary=f"rubric_scorer failed: {e}",
                    from_agent="runner",
                ))
        if self.memory_writer is not None:
            try:
                await self._run_memory_writer(result)
            except Exception as e:  # noqa: BLE001 — memory failures must not fail the run
                self.workspace.post_signal(Signal(
                    type=SignalType.FAILED, summary=f"memory_writer failed: {e}",
                    from_agent="runner",
                ))
        if self.quip_engine is not None:
            try:
                await self.quip_engine.on_run_complete(self.workspace, result)
            except Exception:
                log.exception("quip engine failed at run end")
        # Retrospective, carry-forward and playbook update (plan §11.4). The
        # playbook lives under the data root; runs outside <data>/runs/ (tests,
        # ad-hoc CLI dirs) write their retrospective but touch no playbook.
        from core.harness.retrospective import finalize_run

        data_root = (self.run_root.parent.parent
                     if self.run_root.parent.name == "runs" else None)
        finalize_run(self.run_root, self.run_id, self.project.project_id, data_root)
        self._close_project_run(stopped=False)
        return result

    def _data_root(self) -> Path | None:
        """`<data>` when the run lives at `<data>/runs/<id>` (else tests / ad-hoc dirs)."""
        return self.run_root.parent.parent if self.run_root.parent.name == "runs" else None

    def _close_project_run(self, *, stopped: bool) -> None:
        """Hand the run back to its project (plan §13): submit it for a human decision
        and record what happened to the backlog items it took."""
        from core.backlog import card_outcomes
        from core.project_runs import close_run

        data_root = self._data_root()
        if data_root is None or not self.project.project_id:
            return
        summaries: dict[str, str] = {}
        for sig in self.workspace.board:
            if sig.type is not SignalType.AGENT_RESULT:
                continue
            try:
                res = (json.loads(sig.detail or "{}").get("result") or {})
            except (ValueError, AttributeError):
                continue
            if isinstance(res, dict) and res.get("task_id") and res.get("summary"):
                summaries[str(res["task_id"])] = str(res["summary"])[:300]
        submitted = close_run(data_root, self.project.project_id, self.run_id, self.run_root,
                              self.git.working_dir,
                              card_outcomes(self.workspace.tasks.values(), summaries),
                              stopped=stopped)
        if submitted is not None:
            from core.backlog import Backlog
            outcomes = {i.id: str(i.pending or "not_started") for i in
                        Backlog(data_root, self.project.project_id).in_run(self.run_id)}
            self.workspace.post_signal(Signal(
                type=SignalType.RUN_SUBMITTED, from_agent="runner",
                summary=(f"run submitted to the project: {submitted.commits} commit(s), "
                         f"{len(submitted.files)} file(s), waiting for your review"
                         if submitted.state == "pending"
                         else "run made no changes to the project"),
                detail=json.dumps({"state": submitted.state, "base": submitted.base,
                                   "head": submitted.head, "commits": submitted.commits,
                                   "files": submitted.files[:200], "backlog": outcomes})))

    def _build_teammate_brief(self, role: str) -> str:
        """Build the TEAM block (Layer 3.5) for `role`.

        Hard-capped at 300 tokens worth (~240 words) by truncating commit
        subjects and descriptors. Built from the role configs and the board
        only: no subjective content.
        """
        from core.protocol import TeammateEntry, render_teammate_brief

        entries: list[TeammateEntry] = []
        for cfg in self.project.agents:
            state = self.workspace.agent_states.get(cfg.role, None)
            if cfg.role in self.workspace.completed_agents:
                status = "DONE"
            elif state and state.value == "sat_out":
                status = "SAT_OUT"
            elif state and state.value == "running":
                status = "RUNNING"
            else:
                status = "PENDING"

            last_commit = ""
            if status == "DONE" and self.workspace.git is not None:
                for commit in reversed(self.workspace.git.commits):
                    if commit.agent == cfg.role:
                        last_commit = commit.message[:60]
                        break

            if cfg.persona_id:
                persona_name = cfg.persona_id.split("-")[0].capitalize()
            else:
                persona_name = cfg.role.capitalize()

            entries.append(TeammateEntry(
                role=cfg.role,
                persona_name=persona_name,
                descriptor=(cfg.description or "")[:80],
                is_self=(cfg.role == role),
                status=status,
                last_commit_subject=last_commit,
                escalation_target="",
            ))

        return render_teammate_brief(role, entries)

    async def _run_memory_writer(self, result: SchedulerResult) -> None:
        """Run the memory_writer once per persona that participated.

        The writer expects an `on_run_complete` async hook that takes
        the workspace + scheduler result.
        """
        if hasattr(self.memory_writer, "on_run_complete"):
            await self.memory_writer.on_run_complete(  # type: ignore[union-attr]
                workspace=self.workspace,
                project=self.project,
                run_id=self.run_id,
                run_root=self.run_root,
                personas=dict(self._load_personas()),
            )
