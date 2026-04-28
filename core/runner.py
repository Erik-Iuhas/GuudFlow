"""AgentRunner — orchestrates a complete run.

DESIGN §3.3 (scheduler), §5 (visual feedback), §17 (file workspace),
§19 (git), §20 (effective_system_prompt), §23 (escalation/quips),
§29 (security), §31 (tool manifest), §32 (file claims), §34 (memory
reconciliation), §35 (training mode).

Responsibilities:
  1. Initialise workspace state (file_workspace, git, repo_map).
  2. Build per-agent ToolManifest via ToolManifestBuilder + the broker's
     RBAC view. Inject manifests, broker, context_manager, LLM into agents.
  3. Drive the scheduler with one AgentSpec per role; each AgentSpec's
     runner is a wrapper that invokes the agent's tool-use loop.
  4. Handle SCREENSHOT_READY by calling Playwright on the host (Stage 6
     wires real Playwright; here a callback the user injects).
  5. Handle APPROVED by merging the agent's branch into main.
  6. On run completion, trigger memory_writer + quip_engine.
  7. Inject encouragement between iterations.

The runner is constructed with a project config and run-time handles,
then `await runner.run()` drives the whole thing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from agents.base import BaseAgent, ToolNotAvailable
from agents.tools.base import BaseTool
from agents.tools.board import AssignTask, BoardMessage
from agents.tools.fs import (
    FsCreate,
    FsDelete,
    FsEdit,
    FsExists,
    FsGrep,
    FsList,
    FsRead,
    FsReadRange,
    FsRename,
    FsWrite,
)
from agents.tools.git import GitChangedFiles, GitDiff, GitLog, GitRead, GitStatus
from core import protocol
from core.context_manager import ContextManager
from core.credentials_broker import CredentialsBroker
from core.file_workspace import FileWorkspace
from core.git_workspace import GitWorkspace
from core.llm import LLMClient, LLMMessage, ToolCall
from core.llm.registry import ModelRegistry
from core.loop_guard import LoopGuard, LoopGuardConfig
from core.repo_map import build as build_repo_map, update_incremental
from core.scheduler import (
    AgentSpec,
    InterruptToken,
    Scheduler,
    SchedulerResult,
    SchedulerStatus,
    WaitingForPeer,
    WaitingForSkipConfirm,
)
from core.signals import Signal, SignalType
from core.tool_manifest import (
    LocalToolEntry,
    MCPToolEntry,
    RoleRBAC,
    ToolManifest,
    ToolManifestBuilder,
)
from core.workspace import (
    AgentState,
    ApprovalRequest,
    Artifact,
    CommitRecord,
    Workspace,
)


ScreenshotCallback = Callable[[Path, str], Awaitable[Path]]
"""(working_file_path, viewport) → screenshot output path. Stage 6 wires Playwright."""


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
    agent_class_role_type: str | None = None  # registry lookup; None = generic BaseAgent
    is_reviewer: bool = False
    # Phase C.4: final-stage reviewer (architect pattern). Auto-depends
    # on every non-final-reviewer role at runtime so it runs LAST.
    # Triggers the heavy_tier_mid_run_dispatch gate when enabled per
    # project. Distinct from is_reviewer (which is one of N peer
    # reviewers); a final reviewer sees the whole merged result.
    is_final_reviewer: bool = False
    max_tool_iterations: int = 10  # cap on tool-use loop iterations within one run() call
    # LoopGuard config — per-attempt wall-clock + repetition detection.
    # None means use LoopGuardConfig defaults.
    loop_guard: LoopGuardConfig | None = None
    # M2 — one-sentence goal for the persona filling this slot. Written to
    # profile.json on create_new bootstrap; used in teammate brief (Layer 3.5).
    persona_goal: str = ""


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


# Default model id per tier. The runner may override via project config.
TIER_TO_MODEL: dict[str, str] = {
    "heavy": "claude-opus-4-7",
    "solid": "claude-sonnet-4-6",
    "fast": "claude-haiku-4-5-20251001",
}


def _split_paths(raw: str) -> list[str]:
    """Split a comma-separated path list. Trims whitespace and drops empties."""
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _extract_input_summary(rel_path: str, content: str) -> str:
    """One-line summary for an inputs/ file (M2 §6)."""
    import re as _re
    suffix = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
    content = content.strip()

    if suffix in ("md", "markdown", "rst", "txt"):
        # First H1 heading, else first 80 chars
        m = _re.search(r"^#\s+(.+)", content, _re.MULTILINE)
        if m:
            return m.group(1).strip()[:80]
        first = content.replace("\n", " ")[:80].strip()
        return first if first else "(empty)"

    if suffix == "json":
        try:
            import json as _json
            d = _json.loads(content)
            if isinstance(d, dict):
                for key in ("description", "name", "title"):
                    if key in d and isinstance(d[key], str):
                        return d[key][:80]
            keys = list(d.keys()) if isinstance(d, dict) else []
            return f"JSON file ({len(keys)} keys)" if keys else "JSON file"
        except Exception:
            return "JSON file"

    if suffix in ("py", "js", "ts", "jsx", "tsx", "go", "rs", "java", "c", "cpp", "h"):
        # First docstring or comment
        m = _re.search(r'"""(.+?)"""', content, _re.DOTALL)
        if m:
            return m.group(1).strip().split("\n")[0][:80]
        m = _re.search(r"#\s+(.+)", content)
        if m:
            return m.group(1).strip()[:80]
        m = _re.search(r"//\s*(.+)", content)
        if m:
            return m.group(1).strip()[:80]
        return f"Source file ({suffix})"

    first = content.replace("\n", " ")[:80].strip()
    return first if first else "(empty)"


_STORY_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "be", "this", "that", "it", "by", "as", "at", "from",
    "ship", "add", "implement", "build", "make", "create", "do", "done",
})


def _summary_matches_goal(summary: str, goal: str, threshold: float = 0.5) -> bool:
    """≥`threshold` keyword overlap between summary and goal (M1 §7b heuristic)."""
    def _tokens(text: str) -> set[str]:
        return {
            tok.strip(".,;:!?'\"()[]")
            for tok in text.lower().split()
        } - _STORY_STOPWORDS - {""}

    g = _tokens(goal)
    s = _tokens(summary)
    if not g:
        return False
    overlap = len(g & s)
    return (overlap / len(g)) >= threshold


def _extract_field(detail: str | None, key: str) -> str | None:
    """Pull `key=value;` out of a Signal.detail string.

    Used by the resume subscriber where SKIP_GRANTED/DENIED signals encode
    skip_id (and redirected_task) in the detail text rather than JSON. Returns
    None if the field isn't present.
    """
    if not detail:
        return None
    # Try JSON first
    try:
        data = json.loads(detail)
        if isinstance(data, dict) and key in data:
            return str(data[key])
    except (ValueError, TypeError):
        pass
    # Fall back to "key=value" parse
    for chunk in detail.split(";"):
        chunk = chunk.strip()
        if chunk.startswith(f"{key}="):
            return chunk[len(key) + 1 :].strip()
    return None


class AgentRunner:
    """One run. Owns the workspace, scheduler, agents, broker, LLM."""

    def __init__(
        self,
        project: ProjectConfig,
        run_id: str,
        run_root: Path | str,
        llm: LLMClient | None = None,
        broker: CredentialsBroker | None = None,
        screenshot_callback: ScreenshotCallback | None = None,
        memory_writer: BaseAgent | None = None,
        quip_engine: "QuipEngine | None" = None,
        agent_factory: "AgentFactory | None" = None,
        max_wall_seconds: float | None = 1800.0,  # 30 min run-level budget
        docker_manager: "DockerManager | None" = None,
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
        self.llm = llm
        self._model_registry = model_registry
        self.broker = broker or CredentialsBroker(rbac=project.rbac,
                                                  audit_log_path=self.run_root / "access_log.jsonl")
        self.screenshot_callback = screenshot_callback
        self.memory_writer = memory_writer
        self.quip_engine = quip_engine
        self.agent_factory = agent_factory or default_agent_factory
        self.max_wall_seconds = max_wall_seconds
        self.docker_manager = docker_manager
        # Training Camp (DESIGN §35, Phase F.1.1). The rubric_scorer is
        # optional even in training mode — runs without one will simply
        # not produce a RUBRIC_SCORE signal.
        self.rubric_scorer = rubric_scorer
        self.training_scenarios_root = (
            Path(training_scenarios_root) if training_scenarios_root else None
        )
        # Phase G.7 (post-G fix). Auto-load personas from cfg.persona_id
        # at _build_agents time so worker-invoked runs accumulate
        # memory + reputation just like test-injected runs do. Without
        # this, every demo / training / hired-persona run completes
        # without updating reputation — silent product failure.
        self.personas_root = Path(personas_root) if personas_root else None
        # M2: store project_dir for manifesto + rules + desk loading.
        self.project_dir = Path(project_dir) if project_dir else None
        # Phase K.1 — project-level roadmap document. Read once at
        # construction; threaded into each agent's prompt as Layer 3.8.
        # Empty / missing file → empty string → layer omitted (no
        # difference from today's runs).
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

        # Escalation tracking (DESIGN §23, Phase C.3). _rejection_counts
        # accumulates REJECTED signals per upstream role; once a role
        # crosses an escalation threshold, the corresponding handler in
        # `project.escalation_thresholds` (e.g. tech_lead) is dispatched.
        # `_escalations_fired[handler] = {roles already escalated for}`
        # so each (handler, role) pair fires at most once per run.
        self._rejection_counts: dict[str, int] = {}
        self._escalations_fired: dict[str, set[str]] = {}
        self._escalation_tasks: list[asyncio.Task] = []
        self._scheduler: Scheduler | None = None  # set in run() after creation
        # M1: track outstanding interrupts so signal subscribers can resume.
        # Maps role → InterruptToken. Cleared by _resume_token_for_role().
        self._open_interrupts: dict[str, "InterruptToken"] = {}
        # Serialize git operations across parallel agents — a shared
        # working directory with concurrent checkouts causes race conditions
        # where one agent's `git add -A` picks up another agent's file writes.
        self._git_lock = asyncio.Lock()

        # Set up workspace state. Phase G-prime.3 — when restoring from
        # a prior run, use the restored workspace and skip fresh
        # construction. The restored workspace already has board +
        # completed_agents + agent_states + artifacts; we just need
        # to re-attach the live hooks (subscriber + persisters) since
        # those are runner-bound, not persisted.
        if restored_workspace is not None:
            self.workspace = restored_workspace
        else:
            self.workspace = Workspace(
                run_id=run_id,
                goal=project.goal,
                max_iterations=project.max_iterations,
            )
        # Subscribe to the board so we can count REJECTED signals and
        # fire escalations when thresholds are crossed.
        self.workspace.subscribe(self._on_signal_for_escalation)
        # M1: subscribe so ANSWER / SKIP_GRANTED / SKIP_DENIED / HUMAN_RESPONDED
        # routes back to scheduler.resume() and unparks the asker.
        self.workspace.subscribe(self._on_signal_for_resume)
        # M2: subscribe so DESK_HANDOFF refreshes target's DeskIndex and fires quip.
        self.workspace.subscribe(self._on_desk_handoff)
        # Phase G-prime.0 — incremental board persistence so a crashed
        # mid-run leaves a complete trail on disk for the resume loader.
        # board.jsonl co-exists with the end-of-run consolidated
        # board.json; both readers and writers tolerate either source.
        self.workspace.set_board_persister(self._build_board_persister())
        # Phase G-prime.1 — artifact persistence. When a completed
        # agent's artifact is set via workspace.set_artifact, persist
        # the full dict so a resumed run can read the agent's output
        # without re-running it.
        self.workspace.set_artifact_persister(
            self._build_artifact_persister()
        )
        # Run-level deadline — checked by the scheduler.
        if max_wall_seconds is not None:
            import time as _time
            self.workspace.deadline_at = _time.time() + max_wall_seconds
        self.files = FileWorkspace(self.run_root / "workspace")
        self.workspace.files = self.files
        self.git = GitWorkspace(
            self.files.working,
            emit=self._git_signal_emitter,
        )
        self.git.init()

        # Manifest infrastructure.
        self._mcp_catalogs: dict[str, list[MCPToolEntry]] = {}
        self._local_registry: dict[str, LocalToolEntry] = {}
        self._build_local_registry()
        self.manifest_builder = ToolManifestBuilder(
            local_registry=self._local_registry,
            mcp_catalogs=self._mcp_catalogs,
            rbac=project.rbac,
        )

        # Repo map (built once at init; incremental updates on GIT_COMMIT).
        self.workspace.repo_map = build_repo_map(self.files.working)
        self.context_manager = ContextManager(
            workspace=self.workspace,
            files=self.files,
            git=self.git,
        )

        # Agents — instantiated via factory + injected with everything they need.
        self.agents: dict[str, BaseAgent] = {}

    # ── Local tool registry ─────────────────────────────────────────────────

    def _build_local_registry(self) -> None:
        """Catalogue every built-in tool we know how to instantiate.

        The catalog drives manifest filtering. Actual instances are created
        per-agent in `_instantiate_local_tools`."""
        for cls in (
            FsRead, FsList, FsExists, FsReadRange, FsGrep,
            FsCreate, FsWrite, FsEdit, FsDelete, FsRename,
            GitDiff, GitChangedFiles, GitLog, GitRead, GitStatus,
            BoardMessage, AssignTask,
        ):
            self._local_registry[cls.name] = LocalToolEntry(
                name=cls.name,
                description=cls.description,
                input_schema=getattr(cls, "input_schema", {}) or {},
            )
        # Tools imported lazily to avoid circular deps:
        from agents.tools.code import CodeExec, CodeOutline
        from agents.tools.data import DataCsv, DataJson, DataXml, DataYaml
        from agents.tools.doc import DocHtml, DocMarkdown
        from agents.tools.math_tool import MathEval
        from agents.tools.text import TextDiff, TextEstimateTokens
        from agents.tools.time_tool import TimeDiff, TimeNow
        from agents.tools.web import WebFetch, WebSearch
        from agents.tools.desk_search import DeskSearchTool
        from agents.tools.desk_push import DeskPushTool
        for cls in (
            CodeExec, CodeOutline,
            DataCsv, DataJson, DataXml, DataYaml,
            DocHtml, DocMarkdown,
            MathEval,
            TextDiff, TextEstimateTokens,
            TimeDiff, TimeNow,
            WebFetch, WebSearch,
            DeskSearchTool, DeskPushTool,
        ):
            self._local_registry[cls.name] = LocalToolEntry(
                name=cls.name,
                description=cls.description,
                input_schema=getattr(cls, "input_schema", {}) or {},
            )

    def _build_desk_index(self, cfg: "AgentConfig") -> "Any":
        """Build a DeskIndex for this agent merging persona-default + project overlay.

        Returns None if neither layer exists (avoids importing persona_desk on
        runs where no personas are loaded — keeps startup fast).
        """
        persona_desk_dir: Path | None = None
        project_desk_dir: Path | None = None

        if cfg.persona_id and self.personas_root is not None:
            d = self.personas_root / cfg.persona_id / "desk"
            if d.is_dir():
                persona_desk_dir = d

        if self.project_dir is not None and cfg.persona_id:
            d = self.project_dir / "persona_desks" / cfg.persona_id
            if d.is_dir():
                project_desk_dir = d

        if persona_desk_dir is None and project_desk_dir is None:
            return None

        from agents.persona_desk import DeskIndex
        return DeskIndex.build(persona_desk_dir, project_desk_dir)

    def _instantiate_local_tools(self, role: str) -> dict[str, BaseTool]:
        """Create the BaseTool instances for the tools available to this role."""
        import os
        from agents.tools.code import (
            CodeExec,
            CodeOutline,
            make_container_runner,
            make_dev_host_runner,
        )
        from agents.tools.data import DataCsv, DataJson, DataXml, DataYaml
        from agents.tools.doc import DocHtml, DocMarkdown
        from agents.tools.math_tool import MathEval
        from agents.tools.text import TextDiff, TextEstimateTokens
        from agents.tools.time_tool import TimeDiff, TimeNow
        from agents.tools.web import WebFetch, WebSearch

        # Claim emitters wire the fs:write/edit/etc. tools to BaseAgent's
        # claim/release helpers. We use closures over the role name so
        # claim signals are attributed to the right agent.
        async def claim_emitter(intent: str, path: str) -> None:
            agent = self.agents.get(role)
            if agent is None:
                return
            await agent.claim_files(self.workspace, [path], intent=intent)

        async def release_emitter(path: str, outcome: str, commit_hash: str | None) -> None:
            agent = self.agents.get(role)
            if agent is None:
                return
            await agent.release_files(self.workspace, [path], outcome=outcome, commit_hash=commit_hash)

        # code:exec runner — production mode + a docker_manager routes
        # through the sandbox container; dev mode (or production without
        # a docker_manager) uses the hardened host runner. The host
        # runner refuses production+host_runner combos via its own mode
        # check, so a misconfigured production deploy fails loudly.
        in_production = os.environ.get("AGENTFLOW_MODE") == "production"
        if in_production and self.docker_manager is not None:
            code_runner = make_container_runner(self.docker_manager, self.run_id)
            code_exec_tool = CodeExec(runner=code_runner, is_dev_host_runner=False)
        else:
            code_exec_audit = self.run_root / "code_exec_audit.jsonl"
            host_runner = make_dev_host_runner(
                cwd=self.files.working,
                audit_path=code_exec_audit,
            )
            code_exec_tool = CodeExec(runner=host_runner, is_dev_host_runner=True)

        return {
            FsRead.name: FsRead(self.files),
            FsList.name: FsList(self.files),
            FsExists.name: FsExists(self.files),
            FsReadRange.name: FsReadRange(self.files),
            FsGrep.name: FsGrep(self.files),
            FsCreate.name: FsCreate(self.files, claim_emitter, release_emitter),
            FsWrite.name: FsWrite(self.files, claim_emitter, release_emitter),
            FsEdit.name: FsEdit(self.files, claim_emitter, release_emitter),
            FsDelete.name: FsDelete(self.files, claim_emitter, release_emitter),
            FsRename.name: FsRename(self.files, claim_emitter, release_emitter),
            BoardMessage.name: BoardMessage(role, self.workspace),
            AssignTask.name: AssignTask(role, self.workspace, runner=self),
            GitDiff.name: GitDiff(self.git),
            GitChangedFiles.name: GitChangedFiles(self.git),
            GitLog.name: GitLog(self.git),
            GitRead.name: GitRead(self.git),
            GitStatus.name: GitStatus(self.git),
            CodeExec.name: code_exec_tool,
            CodeOutline.name: CodeOutline(self.files),
            DataCsv.name: DataCsv(self.files),
            DataJson.name: DataJson(self.files),
            DataXml.name: DataXml(self.files),
            DataYaml.name: DataYaml(self.files),
            DocHtml.name: DocHtml(self.files),
            DocMarkdown.name: DocMarkdown(self.files),
            MathEval.name: MathEval(),
            TextDiff.name: TextDiff(self.files),
            TextEstimateTokens.name: TextEstimateTokens(self.files),
            TimeDiff.name: TimeDiff(),
            TimeNow.name: TimeNow(),
            WebFetch.name: WebFetch(),
            WebSearch.name: WebSearch(),
        }
        # M2: add desk tools wired to this agent's desk index.
        from agents.tools.desk_search import DeskSearchTool
        from agents.tools.desk_push import DeskPushTool
        agent = self.agents.get(role)
        desk_index = getattr(agent, "_desk_index", None) if agent else None
        tools[DeskSearchTool.name] = DeskSearchTool(desk_index=desk_index, role=role)
        # desk-push: resolve project desks root and role→persona map
        if self.project_dir is not None:
            project_id = self.project.project_id if hasattr(self.project, "project_id") else ""
            desks_root = self.project_dir / "persona_desks"
            role_to_persona = {
                cfg.role: cfg.persona_id
                for cfg in self.project.agents if cfg.persona_id
            }
            tools[DeskPushTool.name] = DeskPushTool(
                sender_role=role,
                project_id=project_id,
                project_desks_root=desks_root,
                workspace=self.workspace,
                role_to_persona=role_to_persona,
            )
        return tools

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
        sig = Signal(
            type=signal_type,
            summary=summary,
            detail=detail,
            from_agent=None,  # system-emitted
        )
        # We can't easily set from_user=False / from_agent=None in current
        # validation. Wrap with a synthetic system role.
        sig = Signal(
            type=signal_type,
            summary=summary,
            detail=detail,
            from_agent="git",
        )
        self.workspace.post_signal(sig)
        # Update repo_map after a commit.
        if signal_type is SignalType.GIT_COMMIT:
            try:
                self.workspace.repo_map = build_repo_map(self.files.working)
            except Exception:
                pass

    # ── Agent setup ─────────────────────────────────────────────────────────

    def _build_agents(self) -> None:
        # Phase C.4: auto-fill depends_on for any is_final_reviewer agent
        # whose depends_on is empty. The architect (and any other final
        # reviewer) needs to run AFTER every other non-final agent.
        # Only fills when empty — if the user explicitly set depends_on
        # we respect that.
        non_final_roles = [
            c.role for c in self.project.agents if not c.is_final_reviewer
        ]
        for cfg in self.project.agents:
            if cfg.is_final_reviewer and not cfg.depends_on:
                cfg.depends_on = [
                    r for r in non_final_roles if r != cfg.role
                ]

        for cfg in self.project.agents:
            agent = self.agent_factory(cfg)
            agent.role = cfg.role
            agent.description = cfg.description
            agent.depends_on = list(cfg.depends_on)
            if cfg.system_prompt:
                agent.system_prompt = cfg.system_prompt
            agent.declared_local_tools = list(cfg.declared_local_tools)
            agent.declared_mcp_servers = list(cfg.declared_mcp_servers)
            # Inject runtime handles.
            agent._llm, agent._model = self._resolve_llm_for_tier(cfg.model_tier)
            agent._broker = self.broker
            agent._context_manager = self.context_manager
            agent._manifest = self.manifest_builder.build_for_agent(
                role=cfg.role,
                declared_local_tools=cfg.declared_local_tools,
                declared_mcp_servers=cfg.declared_mcp_servers,
            )
            agent._local_tools = self._instantiate_local_tools(cfg.role)
            agent._current_task = self.project.goal
            # Phase K.1 — project context (roadmap.md). Empty when the
            # project has no roadmap; layer is then omitted from the
            # effective prompt.
            agent._project_context = self._project_context
            # Phase G.7 — auto-attach the persona from cfg.persona_id.
            # The persona's directory must exist under personas_root;
            # malformed / missing persona dirs are tolerated silently
            # (memory_writer becomes a no-op for that role rather than
            # taking the run down).
            if cfg.persona_id and self.personas_root is not None:
                try:
                    from agents.persona import Persona
                    persona = Persona.load(cfg.persona_id, self.personas_root)
                    persona._dir = self.personas_root / cfg.persona_id
                    agent.persona = persona
                except Exception:
                    pass

            # M2: inject manifesto (Layer 3.6) + project rules (Layer 3.7).
            if self.project_dir is not None:
                project_id = self.project.project_id if hasattr(self.project, "project_id") else ""
                if project_id:
                    from agents.base import BaseAgent as _BA
                    agent._manifesto = _BA.load_manifesto(project_id)
                    persona_id = cfg.persona_id or None
                    agent._project_rules = _BA.load_project_rules(project_id, persona_id)

            # M2: build DeskIndex (persona-default + project-overlay) and inject.
            agent._desk_index = self._build_desk_index(cfg)

            self.agents[cfg.role] = agent

    # ── Main run loop ───────────────────────────────────────────────────────

    async def run(self) -> SchedulerResult:
        # M2: materialize meta-agent desk_seeds into project overlay BEFORE
        # _build_agents() so DeskIndex sees them on first iteration.
        self._materialize_desk_seeds()

        self._build_agents()

        # M2: generate inputs/README.md index before agents start so
        # meta-agent (and any agent) can read a one-line summary of
        # every input file without walking the whole directory.
        await self._generate_inputs_readme()

        # Populate quip voices from the now-built agents so each quip
        # is spoken in the right persona's voice.
        # Populate workspace.hired_personas from agents that have a persona.
        for cfg in self.project.agents:
            agent = self.agents[cfg.role]
            if agent.persona is not None and cfg.persona_id:
                self.workspace.hired_personas[cfg.role] = cfg.persona_id

        if self.quip_engine is not None:
            from core.quip_engine import AgentVoice
            voices = []
            for role, agent in self.agents.items():
                if agent.persona is not None:
                    name = agent.persona.profile.name or role
                    char = agent.persona.character_layer() or (
                        f"You are {name}, a specialist on a software team."
                    )
                else:
                    name = role.capitalize()
                    char = f"You are the {role} on a software team. {agent.description}".strip()
                voices.append(AgentVoice(role=role, name=name, character=char))
            self.quip_engine.set_voices(voices)
            # M1: give the engine workspace access so dynamic voice synthesis
            # works for escalation side-tasks and peer-respond tasks.
            self.quip_engine.set_workspace(self.workspace, self.personas_root)

        # Build AgentSpecs for the scheduler. Each spec's runner wraps
        # the agent's run() and handles iteration tracking + commit
        # finalisation.
        specs: list[AgentSpec] = []
        for cfg in self.project.agents:
            agent = self.agents[cfg.role]
            specs.append(AgentSpec(
                role=cfg.role,
                runner=self._make_scheduler_runner(agent, cfg),
                depends_on=list(cfg.depends_on),
            ))

        scheduler = Scheduler(specs, self.workspace, scope="pipeline")
        self._scheduler = scheduler  # M1: stored for ASK/skip interrupt calls

        # M1 §9: inject scheduler reference so ask_human() and
        # request_visual_feedback() can park via interrupt() instead of
        # mutating workspace.pending_human directly.
        for agent in self.agents.values():
            agent._scheduler = scheduler

        # M1 §7b: open stories from proposal seeds before the scheduler runs
        # so every protocol message can be auto-tagged with the active story.
        self._open_seed_stories()

        result = await scheduler.run()

        # Wait for any in-flight escalation handlers (Phase C.3) so
        # their signals land before memory_writer / quip_engine fire.
        await self._await_escalation_tasks()

        # M1 §7b: close any stories still open at run end as `deferred`.
        self._close_remaining_stories()

        # Post-run: training rubric scoring (Phase F.1.1) runs first so
        # the RUBRIC_SCORE signal is on the board before the
        # memory_writer records its episode. Production runs (no
        # rubric_scorer / no training_scenario_id) skip this entirely.
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
            except Exception as e:
                # Scoring failures shouldn't fail the run.
                self.workspace.post_signal(Signal(
                    type=SignalType.FAILED,
                    summary=f"rubric_scorer failed: {e}",
                    from_agent="runner",
                ))

        # Post-run: trigger memory_writer if any persona agents participated.
        if self.memory_writer is not None:
            try:
                await self._run_memory_writer(result)
            except Exception as e:
                # Memory write failures shouldn't fail the run.
                self.workspace.post_signal(Signal(
                    type=SignalType.FAILED,
                    summary=f"memory_writer failed: {e}",
                    from_agent="runner",
                ))

        # Quip engine — final flourish (post-run). Fire-and-forget if attached.
        if self.quip_engine is not None:
            try:
                await self.quip_engine.on_run_complete(self.workspace, result)
            except Exception:
                pass

        return result

    def _make_scheduler_runner(
        self,
        agent: BaseAgent,
        cfg: AgentConfig,
    ) -> Callable[[Workspace], Awaitable[None]]:
        """Build the per-agent runner the scheduler calls."""

        async def runner(workspace: Workspace) -> None:
            # Resume from human pause? On re-spawn after HUMAN_RESPONDED,
            # state was WAITING_FOR_HUMAN. Just continue — agent's run()
            # decides what to do with new user_board entries.
            agent.iteration = workspace.iteration

            # M1: build and inject teammate brief before every iteration.
            # Rebuilding per-spawn ensures it reflects newly completed agents.
            try:
                agent._teammate_brief = self._build_teammate_brief(cfg.role)
            except Exception:
                agent._teammate_brief = ""

            # Phase C.4: final-stage reviewers (architects) trigger the
            # heavy_tier_mid_run_dispatch gate before they actually
            # spend tokens. This is the "office loops back with the
            # architect" confirmation: the team's work is done, the
            # per-step reviewers have approved, but before the architect
            # runs its HEAVY-tier design check we ask the user.
            if cfg.is_final_reviewer and self._is_gate_enabled("heavy_tier_mid_run_dispatch"):
                granted = await self._request_approval(
                    kind="heavy_tier_mid_run_dispatch",
                    summary=f"Run final-stage architect {cfg.role}",
                    triggered_by=cfg.role,
                    context={
                        "role": cfg.role,
                        "is_final_reviewer": True,
                        "model_tier": cfg.model_tier,
                    },
                )
                if not granted:
                    # User said no — record as completed (gate denial is
                    # the audit) but skip the actual work. The run still
                    # finishes cleanly without architect output.
                    workspace.completed_agents.append(cfg.role)
                    workspace.agent_states[cfg.role] = AgentState.COMPLETE
                    await agent.signal(
                        workspace, SignalType.COMPLETE,
                        summary=f"{cfg.role} skipped (gate denied)",
                    )
                    return

            await agent.signal(workspace, SignalType.STARTED, summary=f"started ({cfg.role})")

            # Quip: agent reacts to receiving the baton (handoff moment).
            if self.quip_engine is not None:
                try:
                    await self.quip_engine.on_agent_started(cfg.role, workspace)
                except Exception:
                    pass

            try:
                drive_result = await self._drive_agent_run(agent, cfg)
            except Exception as e:
                workspace.agent_states[cfg.role] = AgentState.FAILED
                await agent.signal(
                    workspace, SignalType.FAILED,
                    summary=f"{cfg.role} crashed",
                    detail=str(e),
                )
                raise

            # M1: handle REQUEST_SKIP before treating the block as work output.
            if (
                isinstance(drive_result, protocol.CompactBlock)
                and drive_result.kind == "REQUEST_SKIP"
                and self._scheduler is not None
            ):
                skip_id = _uuid.uuid4().hex
                reason = drive_result.fields.get("reason", "no reason given")
                evidence = _split_paths(drive_result.fields.get("evidence_paths", ""))
                token = self._scheduler.interrupt(
                    cfg.role, "skip",
                    WaitingForSkipConfirm(
                        confirmer_role="tech_lead",
                        skip_id=skip_id,
                        deadline_ts=_time.monotonic() + 90,
                    ),
                )
                self._open_interrupts[cfg.role] = token
                workspace.post_signal(Signal(
                    type=SignalType.PROTOCOL,
                    summary=f"REQUEST_SKIP {cfg.role}: {reason}",
                    from_agent=cfg.role,
                    detail=f"skip_id={skip_id}; evidence={evidence}",
                ))
                if self.quip_engine is not None:
                    try:
                        await self.quip_engine.emit_quip(
                            persona=cfg.role,
                            text=f"Sitting this one out — {reason}",
                            event_type="skip_request",
                        )
                    except Exception:
                        pass
                # M1: spawn the confirmer to grant or deny.
                self._dispatch_skip_confirmation(
                    confirmer_role="tech_lead",
                    requesting_role=cfg.role,
                    skip_id=skip_id,
                    reason=reason,
                    evidence_paths=evidence,
                )
                # Don't mark complete — scheduler holds until SKIP_GRANTED/DENIED
                return

            # M1: handle ASK CompactBlock — park in WAITING_FOR_PEER.
            if (
                isinstance(drive_result, protocol.CompactBlock)
                and drive_result.kind == "ASK"
                and self._scheduler is not None
            ):
                # One-outstanding-ASK rule (M1 §8): if this agent already has
                # an open interrupt, reject the second ASK with a synthetic
                # VERIFY_FAIL signal and let the scheduler re-spawn it.
                if cfg.role in self._open_interrupts:
                    workspace.post_signal(Signal(
                        type=SignalType.PROTOCOL,
                        summary=f"VERIFY_FAIL {cfg.role}: multiple outstanding ASKs",
                        from_agent=cfg.role,
                        detail="emit ANSWER receipt before next ASK",
                    ))
                    return

                ask_id = drive_result.fields.get("ask_id") or _uuid.uuid4().hex
                target_role = drive_result.fields.get("to", "tech_lead")
                question = drive_result.fields.get("question", "")
                context_paths = _split_paths(drive_result.fields.get("context_paths", ""))

                token = self._scheduler.interrupt(
                    cfg.role, "peer",
                    WaitingForPeer(
                        target_role=target_role,
                        ask_id=ask_id,
                        deadline_ts=_time.monotonic() + 120,
                    ),
                )
                self._open_interrupts[cfg.role] = token
                workspace.post_signal(Signal(
                    type=SignalType.AGENT_MESSAGE,
                    summary=f"ASK {cfg.role} → {target_role}: {question}",
                    from_agent=cfg.role,
                    detail=json.dumps({
                        "ask_id": ask_id,
                        "from": cfg.role,
                        "to": target_role,
                        "question": question,
                        "context_paths": context_paths,
                    }),
                ))
                if self.quip_engine is not None:
                    try:
                        target_name = self._persona_name(target_role)
                        await self.quip_engine.emit_quip(
                            persona=cfg.role,
                            text=f"Hey {target_name}, {question}",
                            addressed_to=target_role,
                            event_type="peer_question",
                        )
                    except Exception:
                        pass
                # M1: spawn the target on a respond side-task.
                self._dispatch_peer_response(
                    target_role=target_role,
                    asker_role=cfg.role,
                    ask_id=ask_id,
                    question=question,
                    context_paths=context_paths,
                )
                # Don't mark complete — return without completing the agent.
                return

            # Auto-commit any uncommitted changes on the agent's branch.
            commit_hash = await self._auto_commit(cfg.role, workspace)

            # M3.5 — process the COMMIT's optional `closes: task:<id>`
            # trailer (DESIGN §38.12 §4.3). Validates assignee + state,
            # mutates each closed task to COMPLETE, cascades parent
            # closure, and posts TASK_OUT_OF_SCOPE warnings on
            # invariant violations. The COMMIT itself is not blocked.
            self._handle_task_closures(cfg.role, drive_result, commit_hash)

            # `drive_result` is either a parsed CompactBlock (the runner
            # extracted a trailing protocol block from the LLM response)
            # or a raw string (subclasses that override `run()` and the
            # explicit failure paths inside `_generic_tool_use_loop`).
            artifact_text, verify_result = self._artifact_from_drive_result(drive_result)

            # Record the artifact (Phase G-prime.1: routes through
            # set_artifact so the persister fires).
            workspace.set_artifact(cfg.role, Artifact(
                agent_role=cfg.role,
                type="text",
                content=artifact_text,
                iteration=workspace.iteration,
            ))
            workspace.completed_agents.append(cfg.role)
            workspace.agent_states[cfg.role] = AgentState.COMPLETE

            # Reviewer verdict comes from the parsed [VERIFY] block. If
            # the agent overrode run() and returned a raw string, fall
            # back to the legacy "REJECTED" string sniff.
            if cfg.is_reviewer:
                if verify_result is not None:
                    final_signal = (
                        SignalType.APPROVED if verify_result == "PASS"
                        else SignalType.REJECTED
                    )
                elif artifact_text.lstrip().upper().startswith("REJECTED"):
                    final_signal = SignalType.REJECTED
                else:
                    final_signal = SignalType.APPROVED
            else:
                final_signal = SignalType.COMPLETE

            await agent.signal(
                workspace,
                final_signal,
                summary=f"{cfg.role} complete",
                detail=commit_hash,
            )

            # Quip: agent reacts to completing their own work.
            if self.quip_engine is not None:
                try:
                    if cfg.is_reviewer:
                        verdict = (
                            "APPROVED" if final_signal is SignalType.APPROVED
                            else "REJECTED"
                        )
                        await self.quip_engine.on_review_verdict(
                            cfg.role, verdict, workspace
                        )
                    else:
                        await self.quip_engine.on_agent_complete(cfg.role, workspace)
                except Exception:
                    pass

            # On APPROVED only: merge upstream agent branches into main.
            # REJECTED leaves the branches alone for the next iteration.
            if cfg.is_reviewer and final_signal is SignalType.APPROVED:
                for dep_role in cfg.depends_on:
                    branch = workspace.git.agent_branches.get(dep_role)
                    if branch:
                        ok = self.git.merge(branch, into="main")
                        if ok:
                            workspace.git.merged.append(branch)
                    # M1 §7b: auto-close the dep's active story if its
                    # latest commit summary matches the story goal.
                    latest = next(
                        (c for c in reversed(workspace.git.commits)
                         if c.agent == dep_role),
                        None,
                    )
                    if latest is not None:
                        self._maybe_auto_close_story(dep_role, latest.message)
                # Phase G.5 — auto-tag the review point. The tag lives
                # on whatever commit `main` points at after the merge,
                # so users can list review points and (in a follow-up
                # phase) rewind to a known-good tree state.
                tag_name = f"review/{workspace.iteration}/{cfg.role}"
                self.git.tag(
                    tag_name,
                    message=f"{cfg.role} approved at iteration {workspace.iteration}",
                )

        return runner

    # ── M1: teammate brief & incoming-work ─────────────────────────────────

    def _build_teammate_brief(self, role: str) -> str:
        """Build the TEAM block (Layer 3.5) for `role`.

        Hard-capped at 300 tokens worth (~240 words) by truncating commit
        subjects and descriptors.
        """
        from core.protocol import TeammateEntry, render_teammate_brief

        entries: list[TeammateEntry] = []
        for cfg in self.project.agents:
            agent = self.agents.get(cfg.role)
            if agent is None:
                continue

            # Descriptor: goal field (M2 adds it), fall back to first sentence of character
            descriptor = ""
            if agent.persona is not None:
                goal = getattr(agent.persona.profile, "goal", "") or ""
                if goal:
                    descriptor = goal[:80]
                else:
                    char = agent.persona.character_layer() or ""
                    descriptor = (char.split(".")[0])[:80]
            else:
                descriptor = (agent.description or "")[:80]

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
            if status == "DONE":
                for commit in reversed(self.workspace.git.commits):
                    if commit.agent == cfg.role:
                        last_commit = commit.message[:60]
                        break

            # Escalation target (default tech_lead if any role is tech_lead)
            escalation_target = ""
            if cfg.role == role:
                tech_roles = [c.role for c in self.project.agents if c.role == "tech_lead"]
                if tech_roles:
                    escalation_target = "tech_lead"

            persona_name = ""
            if agent.persona is not None:
                persona_name = agent.persona.profile.name or cfg.role
            else:
                persona_name = cfg.role.capitalize()

            entries.append(TeammateEntry(
                role=cfg.role,
                persona_name=persona_name,
                descriptor=descriptor,
                is_self=(cfg.role == role),
                status=status,
                last_commit_subject=last_commit,
                escalation_target=escalation_target,
            ))

        return render_teammate_brief(role, entries)

    def _handle_task_closures(
        self,
        role: str,
        drive_result: "protocol.CompactBlock | str",
        commit_hash: str | None,
    ) -> None:
        """Process the COMMIT block's `closes:` field per design §4.3.

        Bookkeeping only — runner-side validation that each named task
        is on the committing agent's desk and IN_PROGRESS. Verifying
        that the work actually matches the design is the reviewer's
        job (they have git-changed-files + git-diff). Invariant
        violations post TASK_OUT_OF_SCOPE warnings; the COMMIT itself
        is never blocked. Cascades parent task closure when the last
        child of a parent closes (§4.4).
        """
        if not isinstance(drive_result, protocol.CompactBlock):
            return
        if drive_result.kind != "COMMIT":
            return
        raw_closes = drive_result.fields.get("closes", "").strip()
        if not raw_closes:
            return

        # Accept either bare ids ("t1") or task: prefixed ("task:t1");
        # the design §2.3 wire format prefers prefixed but the agent
        # may emit the bare id and the runner shouldn't reject either.
        ids: list[str] = []
        for tok in raw_closes.split("|"):
            tok = tok.strip()
            if not tok:
                continue
            if tok.startswith("task:"):
                tok = tok[len("task:"):]
            ids.append(tok)
        if not ids:
            return

        ws = self.workspace
        iteration = ws.iteration

        def _warn(summary: str, detail: str) -> None:
            ws.post_signal(Signal(
                type=SignalType.TASK_OUT_OF_SCOPE,
                summary=summary,
                from_agent=role,
                iteration=iteration,
                detail=detail,
            ))

        closed_parent_ids: set[str] = set()
        for tid in ids:
            task = ws.tasks.get(tid)
            if task is None:
                _warn(
                    f"{role} commit closes unknown task:{tid}",
                    f"task_id={tid}",
                )
                continue
            if task.assigned_to != role:
                _warn(
                    f"{role} commit closes task:{tid} assigned to {task.assigned_to}",
                    f"task_id={tid};expected_role={task.assigned_to}",
                )
                continue
            if task.status not in ("IN_PROGRESS", "PENDING"):
                _warn(
                    f"{role} commit closes task:{tid} in state {task.status}",
                    f"task_id={tid};state={task.status}",
                )
                continue

            ws.mutate_task(
                tid, "COMPLETE",
                iteration=iteration,
                closed_by_commit=commit_hash or "",
            )
            if task.parent_task_id:
                closed_parent_ids.add(task.parent_task_id)

        # Subtask cascade (§4.4): close any parent whose remaining
        # children are all COMPLETE / CANCELLED. The parent's
        # closed_by_commit is taken from the last child's commit so
        # the audit trail is complete.
        for parent_id in closed_parent_ids:
            parent = ws.tasks.get(parent_id)
            if parent is None or parent.status == "COMPLETE":
                continue
            siblings = ws.child_tasks(parent_id)
            if not siblings:
                continue
            if all(s.status in ("COMPLETE", "CANCELLED", "SAT_OUT") for s in siblings):
                last_commit = ""
                for s in siblings:
                    if s.closed_by_commit:
                        last_commit = s.closed_by_commit
                ws.mutate_task(
                    parent_id, "COMPLETE",
                    iteration=iteration,
                    closed_by_commit=last_commit,
                )

    def _maybe_auto_start_task(
        self,
        role: str,
        tool_arguments: dict[str, Any] | None = None,
    ) -> None:
        """Auto-emit TASK_STARTED per design §4.2.

        Called once per agent iteration after the first tool call lands.
        If the tool call's arguments reference a `task_id` that's on the
        agent's desk and PENDING, mark that task IN_PROGRESS. Otherwise
        FIFO-pick the first PENDING desk task. No-op for reviewers and
        for agents with no PENDING desk items.
        """
        ws = self.workspace
        # Don't double-start: skip if anything on the desk is already in flight.
        desk = ws.desk_for(role)
        if not desk:
            return
        if any(t.status == "IN_PROGRESS" for t in desk):
            return
        pending = [t for t in desk if t.status == "PENDING"]
        if not pending:
            return

        target_id: str | None = None
        if tool_arguments:
            ref = tool_arguments.get("task_id") or tool_arguments.get("task")
            if isinstance(ref, str):
                ref = ref[len("task:"):] if ref.startswith("task:") else ref
                if any(t.task_id == ref for t in pending):
                    target_id = ref
        if target_id is None:
            target_id = pending[0].task_id

        ws.mutate_task(target_id, "IN_PROGRESS", iteration=ws.iteration)

    def _render_task_desk_block(self, role: str) -> str:
        """Render the `=== YOUR DESK ===` block for `role` (M3.5 §4.1).

        Returns "" when the desk is empty so the user-turn message is
        byte-identical to today's behaviour for projects that don't use
        the planner-driven task flow. Subtasks render indented under
        their parent (depth cap = 2 levels, enforced at add_task time).
        """
        desk = self.workspace.desk_for(role)
        if not desk:
            return ""

        # Group: top-level tasks first (parent_task_id is None), each
        # followed by its still-open children. BLOCKED/CANCELLED render
        # at the bottom with a divider.
        top_level = [t for t in desk if t.parent_task_id is None]
        children_by_parent: dict[str, list] = {}
        for t in desk:
            if t.parent_task_id is not None:
                children_by_parent.setdefault(t.parent_task_id, []).append(t)

        active_lines: list[str] = []
        blocked_lines: list[str] = []

        def _render_one(task: Any, indent: str = "") -> list[str]:
            lines = [
                f"{indent}[task:{task.task_id}] {task.status} — {task.goal}"
            ]
            if task.acceptance_criteria:
                lines.append(f"{indent}  acceptance:")
                for crit in task.acceptance_criteria:
                    lines.append(f"{indent}    - {crit}")
            if task.out_of_scope:
                lines.append(f"{indent}  out_of_scope:")
                for o in task.out_of_scope:
                    lines.append(f"{indent}    - {o}")
            if task.references:
                lines.append(f"{indent}  See:")
                for ref in task.references:
                    note = f"  ({ref.note})" if ref.note else ""
                    lines.append(f"{indent}    {ref.kind}: {ref.value}{note}")
            if task.status == "BLOCKED" and task.blocked_on:
                lines.append(
                    f"{indent}  blocked_on: " + ", ".join(task.blocked_on)
                )
            return lines

        for parent in top_level:
            block = _render_one(parent)
            children = children_by_parent.get(parent.task_id, [])
            if children:
                block.append("  └─ subtasks:")
                for child in children:
                    block.extend(_render_one(child, indent="     "))
            (blocked_lines if parent.status in ("BLOCKED", "CANCELLED")
             else active_lines).extend(block)
            active_lines.append("") if parent.status not in ("BLOCKED", "CANCELLED") else blocked_lines.append("")

        # Children whose parent is no longer on the desk (e.g. completed
        # parents whose children are still open) render under "Other".
        orphans = [
            t for t in desk
            if t.parent_task_id is not None
            and not any(p.task_id == t.parent_task_id for p in top_level)
        ]
        if orphans:
            active_lines.append("Other:")
            for o in orphans:
                active_lines.extend(_render_one(o, indent="  "))
            active_lines.append("")

        out = ["=== YOUR DESK ===", ""]
        out.extend(active_lines)
        if blocked_lines:
            out.append("--- blocked / cancelled ---")
            out.extend(blocked_lines)
        out.append("=== END DESK ===")
        return "\n".join(out).rstrip() + "\n"

    def _build_incoming_work(self, cfg: AgentConfig) -> tuple[str, bool]:
        """Build the INCOMING WORK block for cfg.role.

        Returns (block_text, upstream_incomplete) where upstream_incomplete
        is True when the ASK-enforcement rule should fire.
        """
        from core.protocol import UpstreamEntry, render_incoming_work

        if not cfg.depends_on:
            return "", False

        entries: list[UpstreamEntry] = []
        any_incomplete = False
        _stop_words = frozenset({"work", "done", "finished", "complete", "ok", "completed"})

        for dep_role in cfg.depends_on:
            # Check if sat-out
            dep_state = self.workspace.agent_states.get(dep_role)
            if dep_state and dep_state.value == "sat_out":
                # Find the REQUEST_SKIP info from the board
                skip_reason = ""
                skip_granted_by = ""
                for sig in reversed(self.workspace.board):
                    if sig.type == SignalType.PROTOCOL and sig.from_agent == dep_role:
                        if sig.detail and "REQUEST_SKIP" in (sig.detail or ""):
                            skip_reason = sig.summary
                            break
                entries.append(UpstreamEntry(
                    role=dep_role,
                    persona_name=self._persona_name(dep_role),
                    commit_subject="",
                    commit_hash="",
                    summary="",
                    files=[],
                    sat_out=True,
                    sat_out_reason=skip_reason or "no work in this scope",
                    sat_out_granted_by=skip_granted_by or "tech_lead",
                ))
                continue

            # Look for latest COMMIT on board
            latest_commit: CommitRecord | None = None
            for commit in reversed(self.workspace.git.commits):
                if commit.agent == dep_role:
                    latest_commit = commit
                    break

            if latest_commit is None:
                any_incomplete = True
                entries.append(UpstreamEntry(
                    role=dep_role,
                    persona_name=self._persona_name(dep_role),
                    commit_subject="(no commits)",
                    commit_hash="",
                    summary="",
                    files=[],
                ))
                continue

            summary = latest_commit.message or ""
            # Check empty-upstream rule
            words = [w for w in summary.lower().split() if w not in _stop_words]
            if len(words) < 3:
                any_incomplete = True

            entries.append(UpstreamEntry(
                role=dep_role,
                persona_name=self._persona_name(dep_role),
                commit_subject=latest_commit.message,
                commit_hash=latest_commit.hash,
                summary=summary,
                files=latest_commit.files,
            ))

        block = render_incoming_work(entries)
        if any_incomplete and entries:
            # Append the enforcement directive
            incomplete_roles = [
                e.role for e in entries
                if not e.sat_out and (not e.commit_hash or len([
                    w for w in e.summary.lower().split()
                    if w not in _stop_words
                ]) < 3)
            ]
            if incomplete_roles:
                directive = "\n".join(
                    f"UPSTREAM INCOMPLETE: {r} produced no usable handoff.\n"
                    f"You MUST emit ASK targeting {r} before any ACTION/DELTA/COMMIT."
                    for r in incomplete_roles
                )
                block = block + "\n\n" + directive if block else directive

        return block, any_incomplete

    def _materialize_desk_seeds(self) -> None:
        """Write desk_seeds from the proposal into each persona's project overlay.

        Called before _build_agents() so DeskIndex sees the seeded files on first
        iteration. Files are written to:
          data/projects/{id}/persona_desks/{persona_id}/{name}.md

        Idempotent — overwrites if a seed with the same name was previously written.
        Safe to call when desk_seeds is empty.
        """
        if not self.project.desk_seeds or self.project_dir is None:
            return
        project_id = self.project.project_id if hasattr(self.project, "project_id") else ""
        import logging as _logging
        _log = _logging.getLogger(__name__)
        for persona_id, seeds in self.project.desk_seeds.items():
            if not seeds:
                continue
            target_dir = self.project_dir / "persona_desks" / persona_id
            target_dir.mkdir(parents=True, exist_ok=True)
            for seed in seeds:
                name = str(seed.get("name", "")).strip().lower().replace(" ", "-")
                if not name:
                    continue
                triggers = seed.get("triggers") or []
                body = str(seed.get("body", ""))
                kind = str(seed.get("kind", "convention"))
                priority = str(seed.get("priority", "normal"))
                trigger_str = "[" + ", ".join(f'"{t}"' for t in triggers) + "]"
                content = (
                    f"---\n"
                    f"name: {name}\n"
                    f"triggers: {trigger_str}\n"
                    f"kind: {kind}\n"
                    f"priority: {priority}\n"
                    f"tags: [\"project-seed\", \"from:meta\"]\n"
                    f"---\n"
                    f"{body}"
                )
                dest = target_dir / f"{name}.md"
                try:
                    dest.write_text(content, encoding="utf-8")
                    _log.debug("desk_seed: wrote %s for persona %s", name, persona_id)
                except OSError as e:
                    _log.warning("desk_seed: failed to write %s for %s: %s", name, persona_id, e)

    async def _generate_inputs_readme(self) -> None:
        """Write inputs/README.md listing every input file with a one-line summary.

        Safe to call when inputs/ is empty. Idempotent — overwrites on each run start.
        """
        try:
            paths = await self.files.list_inputs(recursive=True)
            if not paths:
                return
            entries: list[str] = []
            for rel_path in paths:
                if rel_path == "README.md":
                    continue
                try:
                    content = await self.files.read_input(rel_path)
                    summary = _extract_input_summary(rel_path, content)
                except OSError:
                    summary = "(unreadable)"
                entries.append(f"- `{rel_path}` — {summary}")
            if not entries:
                return
            readme = (
                "# inputs/\n\n"
                "Files available to read at runtime (read-only).\n\n"
                + "\n".join(entries)
            )
            await self.files.write_input_readme(readme)
        except Exception:
            pass  # README generation failure never breaks the run

    def _build_desk_inject(self, agent: BaseAgent, cfg: "AgentConfig", task: str) -> str:
        """Build the auto-injected desk notes block for this iteration."""
        desk_index = getattr(agent, "_desk_index", None)
        if desk_index is None:
            return ""
        try:
            from agents.persona_desk import render_desk_inject_block
            items = desk_index.auto_inject(task, role=cfg.role)
            return render_desk_inject_block(items)
        except Exception:
            return ""

    def _resolve_llm_for_tier(self, tier: str) -> tuple[LLMClient, str]:
        """Return (client, model_id) for an agent's tier.

        Priority:
          1. self.llm — explicitly injected client (tests; single-client production).
             When set, ALL agents share it; model string still varies by tier.
          2. self._model_registry — per-agent client from the registry.
             Production multi-model path: fast-tier agent may get Groq, heavy gets Opus.
          3. Error — no client configured.
        """
        if self.llm is not None:
            return self.llm, TIER_TO_MODEL.get(tier, TIER_TO_MODEL["solid"])
        if self._model_registry is not None:
            try:
                model_info = self._model_registry.resolve(tier)
                return self._model_registry.get_client(model_info), model_info.id
            except Exception:
                pass
        raise RuntimeError(
            f"AgentRunner has no LLM client and no model_registry for tier={tier!r}. "
            "Inject an llm or provide a model_registry."
        )

    def _persona_name(self, role: str) -> str:
        agent = self.agents.get(role)
        if agent and agent.persona is not None:
            return agent.persona.profile.name or role.capitalize()
        return role.capitalize()

    async def _drive_agent_run(
        self,
        agent: BaseAgent,
        cfg: AgentConfig,
    ) -> "protocol.CompactBlock | str":
        """Run the agent's tool-use loop.

        For agents that override `run()`, defer to it (returns a raw
        string — the override owns its output format). Otherwise, drive
        a generic LLM tool-use loop and return a parsed CompactBlock.
        """
        cls_run = type(agent).run
        if cls_run is not BaseAgent.run:
            return await agent.run(self.workspace)

        # Generic prompt-defined agent: drive a tool-use loop.
        return await self._generic_tool_use_loop(agent, cfg)

    async def _llm_with_backoff(
        self,
        agent: BaseAgent,
        messages: list[LLMMessage],
    ) -> "LLMResponse":
        """Call agent.llm(), retrying on rate-limit errors.

        Posts a BLOCKED signal before each wait so the board shows the
        agent is standing by rather than failed. Respects the Retry-After
        header when present; falls back to exponential backoff capped at
        120 s. Gives up after 5 retries and re-raises.
        """
        from core.llm.errors import RateLimitError as _RateLimitError

        for attempt in range(6):
            try:
                return await agent.llm(messages)
            except _RateLimitError as exc:
                if attempt >= 5:
                    raise
                # Respect Retry-After header; default exponential backoff.
                wait_s: float = exc.retry_after_s if exc.retry_after_s is not None else min(
                    10.0 * (2 ** attempt), 120.0
                )
                await agent.signal(
                    self.workspace,
                    SignalType.BLOCKED,
                    summary=f"rate limited — waiting {wait_s:.0f}s (attempt {attempt + 1}/5)",
                )
                await asyncio.sleep(wait_s)

        # Unreachable — the loop above always returns or raises.
        raise RuntimeError("_llm_with_backoff: loop exhausted without returning")

    @staticmethod
    def _artifact_from_drive_result(
        result: "protocol.CompactBlock | str",
    ) -> tuple[str, str | None]:
        """Translate `_drive_agent_run` output into (artifact_text,
        verify_result). `verify_result` is "PASS"/"FAIL"/None — used by
        reviewers to drive APPROVED/REJECTED."""
        if isinstance(result, protocol.CompactBlock):
            verify_result: str | None = None
            if result.kind == "VERIFY":
                verify_result = result.fields.get("result")
            return protocol.render_compact(result), verify_result
        return result or "", None

    async def _generic_tool_use_loop(
        self,
        agent: BaseAgent,
        cfg: AgentConfig,
    ) -> "protocol.CompactBlock | str":
        # Build a context block about every upstream dependency — listing
        # which files they committed so the agent knows what artifacts exist.
        upstream_notes: list[str] = []
        for dep_role in cfg.depends_on:
            dep_commits = [c for c in self.workspace.git.commits if c.agent == dep_role]
            if not dep_commits:
                upstream_notes.append(
                    f"⚠ '{dep_role}' completed but produced NO file commits — "
                    f"do not assume any artifacts exist on disk. "
                    f"You must create them yourself or work from the goal alone."
                )
            else:
                # Collect the files committed by this upstream agent
                dep_files: list[str] = []
                for commit in dep_commits:
                    if hasattr(commit, "files") and commit.files:
                        dep_files.extend(commit.files)
                if dep_files:
                    file_list = "\n  ".join(dep_files[:30])
                    upstream_notes.append(
                        f"'{dep_role}' committed {len(dep_files)} file(s):\n  {file_list}\n"
                        f"Read these files to understand what has been produced before starting your own work."
                    )
                else:
                    # commits recorded but no file metadata — list commit messages instead
                    msgs = "; ".join(c.message for c in dep_commits[:5])
                    upstream_notes.append(
                        f"'{dep_role}' made {len(dep_commits)} commit(s): {msgs}. "
                        f"Read the working directory to see what files were created."
                    )

        upstream_ctx = (
            "\n\nUpstream dependencies summary:\n" + "\n".join(upstream_notes)
            if upstream_notes else ""
        )

        # Inject any AGENT_MESSAGE signals directed at this role so the agent
        # sees peer questions/notes without reading the raw board themselves.
        import json as _json
        pending_messages: list[str] = []
        for sig in self.workspace.board:
            if sig.type != SignalType.AGENT_MESSAGE or not sig.detail:
                continue
            try:
                d = _json.loads(sig.detail)
            except Exception:
                continue
            if d.get("to") == cfg.role:
                pending_messages.append(
                    f"  • From {d.get('from', '?')}: {d.get('message', '')}"
                )
        messages_ctx = (
            "\n\nMessages from other agents waiting for your response:\n"
            + "\n".join(pending_messages)
            if pending_messages else ""
        )

        # M1: build the INCOMING WORK block for this agent's upstream deps.
        incoming_work_block, upstream_incomplete = self._build_incoming_work(cfg)

        task_prompt = (
            f"Project goal: {self.project.goal}\n\n"
            f"Your role: {cfg.role}. {cfg.description}{upstream_ctx}{messages_ctx}\n\n"
            f"START WORKING NOW. Call `fs-list` or `git-changed-files` as your very "
            f"first tool call to understand what already exists on disk. Then read, "
            f"build, or review as your role requires. Do not describe what you will do — "
            f"do it. End with the required protocol block once your work is complete."
        )
        if incoming_work_block:
            task_prompt = incoming_work_block + "\n\n" + task_prompt

        # M3.5: prepend the per-agent task desk (DESIGN §38.12 §4.1).
        # Empty desks emit nothing, preserving M1 behaviour byte-for-byte.
        task_desk_block = self._render_task_desk_block(cfg.role)
        if task_desk_block:
            # DESK DISCIPLINE prompt addition (§5.3). Specialists with
            # non-empty desks need an explicit instruction to work the
            # desk top-to-bottom and close each task with `closes:`.
            # Reviewers don't get this — they verify against the
            # closer's desk via git-diff, not their own desk.
            if not cfg.is_reviewer:
                task_desk_block = (
                    task_desk_block
                    + "\nDESK DISCIPLINE: Your desk lists explicit tasks with acceptance "
                    "criteria. Work the desk top-to-bottom. Close each task with a "
                    "COMMIT that names it via `closes: task:<id>`. If a criterion is "
                    "unclear, emit ASK to the task's created_by role before working on it.\n"
                )
            task_prompt = task_desk_block + "\n" + task_prompt

        # M2: prepend desk auto-inject block (triggered notes from persona desk).
        desk_block = self._build_desk_inject(agent, cfg, self.project.goal)
        if desk_block:
            task_prompt = desk_block + "\n\n" + task_prompt

        messages: list[LLMMessage] = [LLMMessage(role="user", content=task_prompt)]
        guard = LoopGuard(cfg.loop_guard)
        nudges: list[str] = []  # accumulated for the next user turn
        protocol_retries_used = 0
        ask_enforcement_failures = 0  # track how many times we've caught non-ASK on empty upstream

        for iteration in range(cfg.max_tool_iterations):
            if guard.deadline_exceeded():
                return f"(loop guard: wall-clock budget exhausted after {guard.elapsed_seconds:.0f}s)"

            response = await self._llm_with_backoff(agent, messages)

            if response.stop_reason == "tool_use" and response.tool_calls:
                # Echo assistant turn (including tool_use blocks) — keep raw if available.
                assistant_blocks: list[dict[str, Any]] = []
                if response.text:
                    assistant_blocks.append({"type": "text", "text": response.text})
                for tc in response.tool_calls:
                    assistant_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                messages.append(LLMMessage(role="assistant", content=assistant_blocks))

                tool_results: list[dict[str, Any]] = []
                aborted_reason: str | None = None
                for tc_idx, tc in enumerate(response.tool_calls):
                    # M3.5 — auto-emit TASK_STARTED on the first tool
                    # call of the agent's run (DESIGN §38.12 §4.2). If
                    # the call references a task_id from the desk, mark
                    # that task; otherwise FIFO-pick the first PENDING.
                    if iteration == 0 and tc_idx == 0 and not cfg.is_reviewer:
                        self._maybe_auto_start_task(cfg.role, tc.arguments)

                    try:
                        result = await agent.call_tool(self.workspace, tc.name, tc.arguments)
                        is_error = bool(result.get("error")) or result.get("ok") is False
                    except (ToolNotAvailable, Exception) as e:
                        result = {"error": f"{type(e).__name__}: {e}"}
                        is_error = True

                    verdict = guard.record(tc.name, tc.arguments, result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": json.dumps(result, default=str),
                        **({"is_error": True} if is_error else {}),
                    })
                    if verdict.nudge:
                        nudges.append(verdict.nudge)
                    if verdict.abort:
                        aborted_reason = verdict.reason
                        break

                messages.append(LLMMessage(role="user", content=tool_results))

                if aborted_reason:
                    return f"(loop guard: {aborted_reason})"

                # If a nudge was produced, surface it as a follow-up user
                # message. The LLM sees an explicit "stop doing that" signal
                # before its next turn.
                if nudges:
                    combined = "\n\n".join(f"[LOOP GUARD] {n}" for n in nudges)
                    messages.append(LLMMessage(role="user", content=combined))
                    nudges.clear()
                continue

            # end_turn (or anything else): the LLM has stopped calling
            # tools. Extract the trailing protocol block. On miss / parse
            # error, send one nudge and re-enter the loop. Strict.
            text = response.text or ""
            try:
                block = protocol.extract_trailing(text)
                parse_error: str | None = None
            except ValueError as e:
                block = None
                parse_error = str(e)

            if block is not None:
                # M1: ASK enforcement — if UPSTREAM INCOMPLETE was declared and
                # agent's first block is not ASK, inject synthetic VERIFY_FAIL.
                # Reviewers are exempt: they are expected to emit [VERIFY], not [ASK].
                if upstream_incomplete and not cfg.is_reviewer and block.kind not in ("ASK",) and ask_enforcement_failures < 3:
                    ask_enforcement_failures += 1
                    messages.append(LLMMessage(role="assistant", content=text))
                    messages.append(LLMMessage(
                        role="user",
                        content=(
                            "[VERIFY] FAIL\n"
                            "  detail: ignored UPSTREAM INCOMPLETE directive — "
                            "you emitted ACTION/COMMIT before ASK; re-read the incoming "
                            "work block and emit [ASK] targeting the incomplete upstream role."
                        ),
                    ))
                    continue
                return block

            if protocol_retries_used >= 1:
                detail = parse_error or "no [COMMIT]/[VERIFY]/[ACTION]/[STATUS] block found at end of response"
                raise RuntimeError(
                    f"agent {cfg.role!r} failed to emit a valid protocol block "
                    f"after retry: {detail}"
                )
            protocol_retries_used += 1

            messages.append(LLMMessage(role="assistant", content=text))
            nudge_msg = (
                "[PROTOCOL] Your last response did not end with a valid "
                "compact protocol block. Re-emit your final answer ending "
                "with one of [COMMIT] / [VERIFY] / [ACTION] / [ASK] / [STATUS] as "
                "specified in the system prompt. Block must be the last "
                "thing in your response, with required fields filled in."
            )
            if parse_error:
                nudge_msg += f"\nParse error: {parse_error}"
            messages.append(LLMMessage(role="user", content=nudge_msg))
            continue

        return "(max tool iterations reached)"

    async def _auto_commit(self, role: str, workspace: Workspace) -> str | None:
        """Stage and commit any uncommitted changes on the agent's branch.

        Serialized via _git_lock so parallel agents don't race on the
        shared working directory — concurrent `git add -A` + checkout
        would mix files from different agents into the wrong branch.
        """
        async with self._git_lock:
            try:
                branch = self.git.agent_branch(role)
                workspace.git.agent_branches[role] = branch
            except Exception:
                return None

            status = self.git.status()
            if status.clean:
                return None

            changed_files = [e.path for e in status.entries]

            try:
                info = self.git.commit(role, message=f"feat: {role} iteration {workspace.iteration}")
            except Exception:
                return None

            workspace.git.commits.append(CommitRecord(
                hash=info.hash,
                branch=info.branch,
                agent=role,
                message=info.message,
                iteration=workspace.iteration,
                files=changed_files,
            ))
            # Post a GIT_COMMIT signal attributed to the agent role so the
            # board (and the Studio git-activity tab) can surface it.
            workspace.post_signal(Signal(
                type=SignalType.GIT_COMMIT,
                summary=f"{role}: committed {info.hash[:7]} — {info.message}",
                from_agent=role,
                detail=info.hash,
            ))
            return info.hash

    # ── Memory writer ───────────────────────────────────────────────────────

    # ── Approval gates (Phase C.0.5) ────────────────────────────────────────

    def _is_gate_enabled(self, gate_name: str) -> bool:
        """Read a boolean gate from project config. Falls back to True
        for the baseline-ON gates if the config dict is missing the key
        (so projects pre-dating C.0.5 still get the safer defaults)."""
        gates = self.project.approval_gates or {}
        if gate_name in gates:
            return bool(gates[gate_name])
        # Baseline ON for the cost-control gates.
        return gate_name in (
            "pre_run_cost_preview",
            "heavy_tier_mid_run_dispatch",
        )

    async def _request_approval(
        self,
        kind: str,
        summary: str,
        estimated_cost_usd: float | None = None,
        context: dict[str, Any] | None = None,
        triggered_by: str | None = None,
    ) -> bool:
        """Pause the runner until the user grants or denies an approval
        request. Posts an APPROVAL_NEEDED signal, parks the gated
        operation in `workspace.pending_approvals`, and awaits the
        matching APPROVAL_GRANTED / APPROVAL_DENIED signal posted by
        the HTTP route.

        Returns True on grant, False on deny. The caller is responsible
        for skipping the gated work on False.
        """
        from uuid import uuid4

        request = ApprovalRequest(
            id=f"appr_{uuid4().hex[:10]}",
            kind=kind,
            summary=summary,
            estimated_cost_usd=estimated_cost_usd,
            context=dict(context or {}),
            triggered_by=triggered_by,
            iteration=self.workspace.iteration,
        )
        self.workspace.pending_approvals.append(request)

        decided = asyncio.Event()
        decision_holder: list[SignalType] = []

        def _on_signal(sig: Signal) -> None:
            if sig.type not in (SignalType.APPROVAL_GRANTED, SignalType.APPROVAL_DENIED):
                return
            if not sig.detail or f"approval_id={request.id}" not in sig.detail:
                return
            decision_holder.append(sig.type)
            decided.set()

        unsubscribe = self.workspace.subscribe(_on_signal)
        try:
            self.workspace.post_signal(Signal(
                type=SignalType.APPROVAL_NEEDED,
                summary=summary,
                detail=(
                    f"approval_id={request.id}; kind={kind}; "
                    f"estimated_cost_usd={estimated_cost_usd}"
                ),
                from_agent="runner",
                iteration=self.workspace.iteration,
            ))
            await decided.wait()
        finally:
            unsubscribe()

        return decision_holder and decision_holder[0] is SignalType.APPROVAL_GRANTED

    # ── M2: desk handoff subscriber ─────────────────────────────────────────

    def _on_desk_handoff(self, sig: Signal) -> None:
        """Refresh target agent's DeskIndex after a desk-push and fire quip."""
        if sig.type is not SignalType.PROTOCOL:
            return
        detail = sig.detail or ""
        if "DESK_HANDOFF" not in detail:
            return
        try:
            import json as _json
            payload = _json.loads(detail)
        except (ValueError, TypeError):
            return
        if payload.get("type") != "DESK_HANDOFF":
            return

        target_role = payload.get("target_role", "")
        from_role = payload.get("from_role", "")
        doc_name = payload.get("doc_name", "")
        note = payload.get("note", "")

        # Rebuild target agent's DeskIndex so the new file is loaded.
        target_agent = self.agents.get(target_role)
        if target_agent is not None:
            for cfg in self.project.agents:
                if cfg.role == target_role:
                    target_agent._desk_index = self._build_desk_index(cfg)
                    # Also refresh the desk-search tool instance for this agent.
                    tool = target_agent._local_tools.get("desk-search")
                    if tool is not None:
                        tool._index = target_agent._desk_index  # type: ignore[attr-defined]
                    break

        # Emit quip for the handoff.
        if self.quip_engine is not None:
            try:
                from_name = self._persona_name(from_role)
                target_name = self._persona_name(target_role)
                text = f"{from_name} → {target_name}: handed over \"{doc_name}\""
                if note:
                    text += f" — {note}"
                import asyncio as _asyncio
                _asyncio.ensure_future(
                    self.quip_engine.emit_quip(
                        persona=from_role,
                        text=text,
                        event_type="desk_handoff",
                    )
                )
            except Exception:
                pass

    # ── Escalation (DESIGN §23, Phase C.3) ──────────────────────────────────

    def _on_signal_for_escalation(self, sig: Signal) -> None:
        """Workspace subscriber. Counts REJECTED signals and triggers
        escalation handlers when thresholds are crossed.

        REJECTED is posted by reviewers when their final artifact text
        starts with "REJECTED". The rejected upstream role(s) are taken
        from the reviewer's `depends_on` — that's the work that didn't
        pass review.
        """
        if sig.type is not SignalType.REJECTED:
            return
        if not sig.from_agent:
            return
        reviewer_cfg = next(
            (c for c in self.project.agents if c.role == sig.from_agent),
            None,
        )
        if reviewer_cfg is None:
            return
        for target in reviewer_cfg.depends_on:
            self._rejection_counts[target] = self._rejection_counts.get(target, 0) + 1
            self._maybe_escalate(target)

    def _on_signal_for_resume(self, sig: Signal) -> None:
        """Workspace subscriber. Resolves outstanding interrupts on receipt of
        ANSWER, SKIP_GRANTED, SKIP_DENIED, or HUMAN_RESPONDED.

        Flow:
          - ANSWER (AGENT_MESSAGE with "ANSWER " prefix in summary, ask_id in
            detail JSON) → find role with matching open peer interrupt; resume.
          - SKIP_GRANTED → mark_sat_out; clear interrupt.
          - SKIP_DENIED → resume with redirected_task; clear interrupt.
          - HUMAN_RESPONDED → resume any open human interrupt with the value.
        """
        if self._scheduler is None:
            return

        try:
            if sig.type is SignalType.AGENT_MESSAGE and sig.summary.startswith("ANSWER "):
                payload = json.loads(sig.detail or "{}")
                ask_id = payload.get("ask_id", "")
                answer_body = payload.get("answer", "") or payload.get("body", "")
                if not ask_id:
                    return
                role = self._role_with_token_id(ask_id, kind="peer")
                if role is None:
                    return
                token = self._open_interrupts.pop(role)
                self._scheduler.resume(token, value=answer_body)
                return

            if sig.type is SignalType.PROTOCOL and sig.summary.startswith("SKIP_GRANTED"):
                skip_id = _extract_field(sig.detail, "skip_id")
                if not skip_id:
                    return
                role = self._role_with_token_id(skip_id, kind="skip")
                if role is None:
                    return
                self._open_interrupts.pop(role, None)
                self._scheduler.mark_sat_out(role)
                return

            if sig.type is SignalType.PROTOCOL and sig.summary.startswith("SKIP_DENIED"):
                skip_id = _extract_field(sig.detail, "skip_id")
                redirected = _extract_field(sig.detail, "redirected_task") or ""
                if not skip_id:
                    return
                role = self._role_with_token_id(skip_id, kind="skip")
                if role is None:
                    return
                token = self._open_interrupts.pop(role)
                self._scheduler.resume(token, value=redirected)
                return

            if sig.type is SignalType.HUMAN_RESPONDED:
                # Resume any agents currently waiting on a human interrupt.
                for role, token in list(self._open_interrupts.items()):
                    if token.kind != "human":
                        continue
                    self._open_interrupts.pop(role, None)
                    self._scheduler.resume(token, value=sig.detail or sig.summary or "")
                return

            if sig.type is SignalType.PEER_TIMEOUT:
                # Scheduler reaped a stale peer interrupt. Re-dispatch
                # tech_lead with the original ASK so the asker can resume.
                # Spec §8: "tech_lead posts ANSWER for ask_id (escalation
                # path) → READY".
                ask_id = _extract_field(sig.detail, "ask_id")
                asker_role = sig.from_agent or ""
                if not ask_id or not asker_role:
                    return
                # Find the original ASK on the board to recover the question.
                original = self._find_original_ask(asker_role, ask_id)
                if original is None:
                    return
                self._open_interrupts.pop(asker_role, None)
                self._dispatch_peer_response(
                    target_role="tech_lead",
                    asker_role=asker_role,
                    ask_id=ask_id,
                    question=original.get("question", ""),
                    context_paths=original.get("context_paths", []),
                )
                return
        except Exception:
            # Subscribers must never break post_signal — swallow and move on.
            return

    def _find_original_ask(self, asker_role: str, ask_id: str) -> dict | None:
        """Find the original AGENT_MESSAGE ASK signal payload by ask_id."""
        for sig in reversed(self.workspace.board):
            if sig.type is not SignalType.AGENT_MESSAGE:
                continue
            if sig.from_agent != asker_role:
                continue
            if not sig.summary.startswith("ASK "):
                continue
            try:
                payload = json.loads(sig.detail or "{}")
            except (ValueError, TypeError):
                continue
            if payload.get("ask_id") == ask_id:
                return payload
        return None

    def _role_with_token_id(self, token_id: str, kind: str) -> str | None:
        for role, tok in self._open_interrupts.items():
            if tok.kind == kind and tok.id == token_id:
                return role
        return None

    # ── M1 stories ─────────────────────────────────────────────────────────

    def _open_seed_stories(self) -> None:
        """Emit STORY_OPENED for each story seed in the project config.

        Each seed: {"name": str, "goal": str, "owner_role": str | None,
        "parent_story_id": str | None}. Updates `workspace.stories` and
        `workspace.active_story` so subsequent protocol messages can be
        auto-tagged.
        """
        from core.workspace import StoryState

        for seed in self.project.stories:
            name = seed.get("name", "").strip()
            goal = seed.get("goal", "").strip()
            if not name or not goal:
                continue
            story_id = _uuid.uuid4().hex
            owner = seed.get("owner_role")
            parent = seed.get("parent_story_id")

            self.workspace.stories[story_id] = StoryState(
                story_id=story_id,
                name=name,
                goal=goal,
                owner_role=owner,
                parent_story_id=parent,
                opened_at_iteration=self.workspace.iteration,
            )
            # Per-agent active-story pointer: owner gets it directly; team-wide
            # stories (owner=None) become the active story for any role with
            # no more-specific assignment.
            if owner:
                self.workspace.active_story[owner] = story_id
            else:
                for cfg in self.project.agents:
                    self.workspace.active_story.setdefault(cfg.role, story_id)

            # Emit the STORY_OPENED message via the protocol render helper so
            # the board contains the canonical text for downstream tooling.
            rendered = protocol.render_story_opened(
                story_id=story_id,
                name=name,
                goal=goal,
                owner_role=owner,
                parent_story_id=parent,
            )
            self.workspace.post_signal(Signal(
                type=SignalType.PROTOCOL,
                summary=f"STORY_OPENED {name}",
                from_agent="runner",
                detail=rendered,
            ))

    def _active_story_for(self, role: str) -> str | None:
        """Return the active (open) story_id for a role, or None."""
        story_id = self.workspace.active_story.get(role)
        if story_id is None:
            return None
        story = self.workspace.stories.get(story_id)
        if story is None or story.closed:
            return None
        return story_id

    def _close_remaining_stories(self) -> None:
        """Mark any still-open stories as `deferred` at run end (M1 §7b)."""
        for story_id, story in list(self.workspace.stories.items()):
            if story.closed:
                continue
            story.closed = True
            story.closed_outcome = "deferred"
            story.closed_summary = "still open at run end"
            rendered = protocol.render_story_closed(
                story_id=story_id,
                outcome="deferred",
                summary=story.closed_summary,
            )
            self.workspace.post_signal(Signal(
                type=SignalType.PROTOCOL,
                summary=f"STORY_CLOSED {story.name} (deferred)",
                from_agent="runner",
                detail=rendered,
            ))

    def _maybe_auto_close_story(self, role: str, commit_summary: str) -> None:
        """M1 §7b: auto-close the role's active story if a COMMIT summary
        semantically matches the story goal AND a reviewer APPROVE follows.

        Heuristic: ≥50% non-stop-word overlap between summary and goal.
        Called from the runner's APPROVED branch (after the merge).
        """
        story_id = self._active_story_for(role)
        if story_id is None:
            return
        story = self.workspace.stories[story_id]
        if not _summary_matches_goal(commit_summary, story.goal):
            return
        story.closed = True
        story.closed_outcome = "shipped"
        story.closed_summary = commit_summary[:120]
        rendered = protocol.render_story_closed(
            story_id=story_id,
            outcome="shipped",
            summary=story.closed_summary,
        )
        self.workspace.post_signal(Signal(
            type=SignalType.PROTOCOL,
            summary=f"STORY_CLOSED {story.name} (shipped)",
            from_agent="runner",
            detail=rendered,
        ))

    # ── M1 side-task dispatch ──────────────────────────────────────────────

    def _dispatch_peer_response(
        self,
        target_role: str,
        asker_role: str,
        ask_id: str,
        question: str,
        context_paths: list[str],
    ) -> None:
        """Spawn `target_role` on a side task to answer `asker_role`'s ASK.

        On completion, posts an AGENT_MESSAGE with summary `ANSWER ...` and
        a JSON detail payload; the resume subscriber routes it back to the
        asker via `scheduler.resume()`. Tech_lead has a specialised
        `run_respond` method; other roles fall through to a generic
        single-LLM-call answer prompt.
        """
        target_cfg = next(
            (c for c in self.project.agents if c.role == target_role),
            None,
        )
        if target_cfg is None:
            # Target not in project — fall back to tech_lead if configured.
            tech_cfg = next(
                (c for c in self.project.agents if c.role == "tech_lead"),
                None,
            )
            if tech_cfg is None:
                return
            target_cfg = tech_cfg
            target_role = "tech_lead"

        async def _run_target() -> None:
            try:
                if target_role not in self.agents:
                    self._build_handler_agent(target_cfg)
                target_agent = self.agents[target_role]
                target_agent.iteration = self.workspace.iteration

                # Tech_lead has a specialised run_respond; everyone else
                # uses a generic one-shot prompt.
                if hasattr(target_agent, "run_respond") and callable(
                    getattr(target_agent, "run_respond")
                ):
                    answer_text = await target_agent.run_respond(
                        workspace=self.workspace,
                        ask_id=ask_id,
                        asker_role=asker_role,
                        question=question,
                        context_paths=context_paths,
                    )
                else:
                    answer_text = await self._generic_peer_answer(
                        target_agent, asker_role, ask_id, question, context_paths,
                    )

                # Parse the ANSWER block and post as AGENT_MESSAGE.
                try:
                    block = protocol.extract_trailing(answer_text or "")
                except ValueError:
                    block = None
                answer_body = ""
                if block is not None and block.kind == "ANSWER":
                    answer_body = block.fields.get("answer", "") or block.fields.get(
                        "body", ""
                    )

                self.workspace.post_signal(Signal(
                    type=SignalType.AGENT_MESSAGE,
                    summary=f"ANSWER {target_role} → {asker_role}",
                    from_agent=target_role,
                    detail=json.dumps({
                        "ask_id": ask_id,
                        "from": target_role,
                        "to": asker_role,
                        "answer": answer_body,
                    }),
                ))
                if self.quip_engine is not None:
                    try:
                        await self.quip_engine.emit_quip(
                            persona=target_role,
                            text=answer_body[:80] or "(no answer)",
                            addressed_to=asker_role,
                            event_type="peer_answer",
                        )
                    except Exception:
                        pass
            except Exception:
                logging.getLogger(__name__).exception(
                    "peer-response dispatch for %s crashed", target_role,
                )

        try:
            task = asyncio.create_task(
                _run_target(), name=f"peer-respond:{target_role}",
            )
            self._escalation_tasks.append(task)
        except RuntimeError:
            pass

    async def _generic_peer_answer(
        self,
        agent: BaseAgent,
        asker_role: str,
        ask_id: str,
        question: str,
        context_paths: list[str],
    ) -> str:
        """Single-LLM-call ANSWER for agents without a specialised handler."""
        from core.llm import LLMMessage
        ctx_note = (
            f"\nContext files: {', '.join(context_paths)}" if context_paths else ""
        )
        prompt = (
            f"PEER QUESTION from {asker_role}:\n"
            f"ask_id: {ask_id}\n"
            f"question: {question}{ctx_note}\n\n"
            f"Answer in ≤12 words. End with:\n"
            f"[ANSWER]\n"
            f"  ask_id: {ask_id}\n"
            f"  to: {asker_role}\n"
            f"  answer: <your answer>"
        )
        try:
            response = await agent.llm([LLMMessage(role="user", content=prompt)])
            return response.text or ""
        except Exception:
            return ""

    def _dispatch_skip_confirmation(
        self,
        confirmer_role: str,
        requesting_role: str,
        skip_id: str,
        reason: str,
        evidence_paths: list[str],
    ) -> None:
        """Spawn `confirmer_role` to grant or deny a REQUEST_SKIP.

        Posts SKIP_GRANTED or SKIP_DENIED signal on completion; the resume
        subscriber routes it back via `scheduler.mark_sat_out()` /
        `scheduler.resume()`.
        """
        confirmer_cfg = next(
            (c for c in self.project.agents if c.role == confirmer_role),
            None,
        )
        if confirmer_cfg is None:
            return

        async def _run_confirmer() -> None:
            try:
                if confirmer_role not in self.agents:
                    self._build_handler_agent(confirmer_cfg)
                confirmer = self.agents[confirmer_role]
                confirmer.iteration = self.workspace.iteration

                if hasattr(confirmer, "run_confirm_skip") and callable(
                    getattr(confirmer, "run_confirm_skip")
                ):
                    verdict_text = await confirmer.run_confirm_skip(
                        workspace=self.workspace,
                        skip_id=skip_id,
                        requesting_role=requesting_role,
                        reason=reason,
                        evidence_paths=evidence_paths,
                    )
                else:
                    verdict_text = await self._generic_skip_decision(
                        confirmer, requesting_role, skip_id, reason, evidence_paths,
                    )

                try:
                    block = protocol.extract_trailing(verdict_text or "")
                except ValueError:
                    block = None

                if block is not None and block.kind == "SKIP_GRANTED":
                    note = block.fields.get("note", "")
                    self.workspace.post_signal(Signal(
                        type=SignalType.PROTOCOL,
                        summary=f"SKIP_GRANTED {requesting_role}",
                        from_agent=confirmer_role,
                        detail=f"skip_id={skip_id};note={note}",
                    ))
                    if self.quip_engine is not None:
                        try:
                            await self.quip_engine.emit_quip(
                                persona=confirmer_role,
                                text=f"Granted — {note or 'confirmed'}",
                                addressed_to=requesting_role,
                                event_type="skip_granted",
                            )
                        except Exception:
                            pass
                elif block is not None and block.kind == "SKIP_DENIED":
                    redirected = block.fields.get("redirected_task", "")
                    self.workspace.post_signal(Signal(
                        type=SignalType.PROTOCOL,
                        summary=f"SKIP_DENIED {requesting_role}",
                        from_agent=confirmer_role,
                        detail=f"skip_id={skip_id};redirected_task={redirected}",
                    ))
                    if self.quip_engine is not None:
                        try:
                            await self.quip_engine.emit_quip(
                                persona=confirmer_role,
                                text=f"Denied — {redirected[:50] or 'do the work'}",
                                addressed_to=requesting_role,
                                event_type="skip_denied",
                            )
                        except Exception:
                            pass
                else:
                    # No valid verdict — default-deny with a generic redirect.
                    self.workspace.post_signal(Signal(
                        type=SignalType.PROTOCOL,
                        summary=f"SKIP_DENIED {requesting_role}",
                        from_agent=confirmer_role,
                        detail=f"skip_id={skip_id};redirected_task=no clear verdict; do your work",
                    ))
            except Exception:
                logging.getLogger(__name__).exception(
                    "skip-confirmation dispatch crashed",
                )

        try:
            task = asyncio.create_task(
                _run_confirmer(), name=f"skip-confirm:{confirmer_role}",
            )
            self._escalation_tasks.append(task)
        except RuntimeError:
            pass

    async def _generic_skip_decision(
        self,
        agent: BaseAgent,
        requesting_role: str,
        skip_id: str,
        reason: str,
        evidence_paths: list[str],
    ) -> str:
        """Single-LLM-call skip verdict for confirmers without a specialised handler."""
        from core.llm import LLMMessage
        evidence_note = (
            f"\nEvidence files: {', '.join(evidence_paths)}" if evidence_paths else ""
        )
        prompt = (
            f"REQUEST_SKIP from {requesting_role}:\n"
            f"skip_id: {skip_id}\n"
            f"reason: {reason}{evidence_note}\n\n"
            f"Inspect the evidence. Emit [SKIP_GRANTED] (skip_id, note) "
            f"if no relevant work, else [SKIP_DENIED] (skip_id, redirected_task)."
        )
        try:
            response = await agent.llm([LLMMessage(role="user", content=prompt)])
            return response.text or ""
        except Exception:
            return ""

    def _maybe_escalate(self, target_role: str) -> None:
        """If `target_role` has crossed any threshold in
        `project.escalation_thresholds`, dispatch the matching handler.
        Each (handler, target) pair fires at most once per run."""
        count = self._rejection_counts.get(target_role, 0)
        for handler_role, threshold in self.project.escalation_thresholds.items():
            if threshold <= 0:
                continue  # 0 disables this handler entirely
            if count < threshold:
                continue
            already = self._escalations_fired.setdefault(handler_role, set())
            if target_role in already:
                continue
            already.add(target_role)
            self._dispatch_escalation(handler_role, target_role, count)

    def _dispatch_escalation(
        self,
        handler_role: str,
        target_role: str,
        rejection_count: int,
    ) -> None:
        """Post an ESCALATION signal and (if the handler is in the
        project config) spawn its agent on a side task. The handler's
        run() executes alongside the scheduler's pipeline."""
        handler_cfg = next(
            (c for c in self.project.agents if c.role == handler_role),
            None,
        )
        # Always post the ESCALATION signal so it's visible on the board.
        self.workspace.post_signal(Signal(
            type=SignalType.ESCALATION,
            summary=f"escalating {target_role} to {handler_role}",
            detail=(
                f"target_role={target_role}; rejection_count={rejection_count}; "
                f"handler={'configured' if handler_cfg else 'missing'}"
            ),
            from_agent="runner",
            iteration=self.workspace.iteration,
        ))
        if handler_cfg is None:
            # Threshold was crossed but the project doesn't include the
            # handler. We've logged it on the board; nothing to dispatch.
            return

        async def _run_handler() -> None:
            try:
                # Phase C.0.5 cost gate: HEAVY-tier escalation handlers
                # are exactly the kind of mid-run dispatch the user
                # wanted to confirm before spending tokens on. Gate is
                # ON by default; users can disable per-project via
                # approval_gates.heavy_tier_mid_run_dispatch.
                if self._is_gate_enabled("heavy_tier_mid_run_dispatch"):
                    granted = await self._request_approval(
                        kind="heavy_tier_mid_run_dispatch",
                        summary=(
                            f"Auto-dispatch {handler_role} for stuck "
                            f"{target_role} ({rejection_count} rejections)"
                        ),
                        triggered_by=handler_role,
                        context={
                            "target_role": target_role,
                            "rejection_count": rejection_count,
                            "handler_role": handler_role,
                        },
                    )
                    if not granted:
                        # User denied — APPROVAL_DENIED is already on
                        # the board (audit trail). Nothing else to do.
                        return

                if handler_role not in self.agents:
                    self._build_handler_agent(handler_cfg)
                handler_agent = self.agents[handler_role]
                handler_agent.iteration = self.workspace.iteration
                await handler_agent.signal(
                    self.workspace, SignalType.STARTED,
                    summary=f"started ({handler_role}) — escalation",
                )
                await self._drive_agent_run(handler_agent, handler_cfg)
                await handler_agent.signal(
                    self.workspace, SignalType.COMPLETE,
                    summary=f"{handler_role} done — escalation",
                )
            except Exception as e:
                logging.getLogger(__name__).exception(
                    "escalation handler %s crashed", handler_role,
                )
                self.workspace.post_signal(Signal(
                    type=SignalType.FAILED,
                    summary=f"{handler_role} failed: {e}",
                    from_agent="runner",
                ))

        try:
            task = asyncio.create_task(_run_handler(), name=f"escalation:{handler_role}")
            self._escalation_tasks.append(task)
        except RuntimeError:
            # No running loop (shouldn't happen during a real run, but
            # defensively swallow so escalation can't take down the run).
            pass

    def _build_handler_agent(self, cfg: AgentConfig) -> None:
        """Build a single agent + inject runtime handles. Mirrors what
        `_build_agents` does for one role, used to lazily build escalation
        handlers that the scheduler isn't running."""
        agent = self.agent_factory(cfg)
        agent.role = cfg.role
        agent.description = cfg.description
        agent.depends_on = list(cfg.depends_on)
        if cfg.system_prompt:
            agent.system_prompt = cfg.system_prompt
        agent.declared_local_tools = list(cfg.declared_local_tools)
        agent.declared_mcp_servers = list(cfg.declared_mcp_servers)
        agent._llm, agent._model = self._resolve_llm_for_tier(cfg.model_tier)
        agent._broker = self.broker
        agent._context_manager = self.context_manager
        agent._manifest = self.manifest_builder.build_for_agent(
            role=cfg.role,
            declared_local_tools=cfg.declared_local_tools,
            declared_mcp_servers=cfg.declared_mcp_servers,
        )
        agent._local_tools = self._instantiate_local_tools(cfg.role)
        agent._current_task = self.project.goal
        self.agents[cfg.role] = agent

    async def _await_escalation_tasks(self) -> None:
        """Wait for any in-flight escalation handlers to finish so their
        signals land before the run is declared done.

        Awaits each task sequentially — CLAUDE.md hard rule 2 forbids
        concurrent task aggregation outside the scheduler. Sequential
        wait is fine here: handlers are typically one side-task per
        (handler, target) pair and don't depend on each other, so the
        only "cost" is wall-clock vs parallel — acceptable for an
        at-most-handful side-channel.
        """
        for task in self._escalation_tasks:
            try:
                await task
            except Exception:
                # Handlers post their own FAILED signals; we don't
                # propagate exceptions from side tasks back into the
                # main run.
                pass

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
                personas={
                    cfg.role: self.agents[cfg.role].persona
                    for cfg in self.project.agents
                    if self.agents.get(cfg.role) and self.agents[cfg.role].persona is not None
                },
            )


# ── Agent factory ───────────────────────────────────────────────────────────


AgentFactory = Callable[[AgentConfig], BaseAgent]


def default_agent_factory(cfg: AgentConfig) -> BaseAgent:
    """Default factory: prompt-defined agent unless `agent_class_role_type`
    points at a registered subclass."""
    if cfg.agent_class_role_type:
        from agents.registry import get
        cls = get(cfg.agent_class_role_type)
        return cls(role=cfg.role)
    return BaseAgent(role=cfg.role)


# Forward-declared for the import order.
class QuipEngine:  # noqa: F811 — real class lives in core/quip_engine.py
    def set_voices(self, voices: list) -> None: ...
    async def on_run_complete(self, workspace: Workspace, result: SchedulerResult) -> None: ...
    async def on_agent_complete(self, role: str, workspace: Workspace) -> None: ...
    async def on_review_verdict(self, role: str, verdict: str, workspace: Workspace) -> None: ...
