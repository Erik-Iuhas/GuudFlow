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
from typing import Any, Awaitable, Callable, Literal

from agents.base import BaseAgent, ToolNotAvailable
from agents.tools.base import BaseTool
from agents.tools.board import AssignTask, BoardMessage
from agents.tools.decompose_task import DecomposeTask
from agents.tools.code import CodeExec, CodeOutline
from agents.tools.code_batch import CodeOutlineBatch
from agents.tools.fs_batch import FsEditBatch, FsWriteBatch
from agents.tools.llm_dispatch import (
    LlmDispatchTool, ProtocolCleanupTool, SummariseSubtaskTool,
)
from agents.tools.data import DataCsv, DataJson, DataXml, DataYaml
from agents.tools.desk_push import DeskPushTool
from agents.tools.desk_search import DeskSearchTool
from agents.tools.doc import DocHtml, DocMarkdown
from agents.tools.fs import (
    FsCreate,
    FsDelete,
    FsDiff,
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
from agents.tools.story import StoryComplete, StoryOpen
from agents.tools.math_tool import MathEval
from agents.tools.text import TextDiff, TextEstimateTokens
from agents.tools.time_tool import TimeDiff, TimeNow
from agents.tools.web import WebFetch, WebSearch
from core import protocol
from core.context_manager import ContextManager
from core.credentials_broker import CredentialsBroker
from core.file_workspace import FileWorkspace
from core.git_workspace import GitWorkspace
from core.llm import LLMClient, LLMMessage, ToolCall
from core.task_summarizer import TaskSummarizer
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
    IterationMetrics,
    Task,
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
    max_tool_iterations: int = 30  # cap on tool-use loop iterations within one run() call (extendable on user approval)
    # LoopGuard config — per-attempt wall-clock + repetition detection.
    # None means use LoopGuardConfig defaults.
    loop_guard: LoopGuardConfig | None = None
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


# All built-in tool classes registered in _build_local_registry.
# Used by /api/tools to enumerate the catalog without constructing a runner.
LOCAL_TOOL_CLASSES: tuple[type[BaseTool], ...] = (
    FsRead, FsList, FsExists, FsReadRange, FsGrep,
    FsCreate, FsWrite, FsEdit, FsDelete, FsRename,
    FsWriteBatch, FsEditBatch,                # Track 3e — batch variants
    GitDiff, GitChangedFiles, GitLog, GitRead, GitStatus,
    BoardMessage, AssignTask, DecomposeTask,  # Track 7 — decompose-task
    CodeExec, CodeOutline, CodeOutlineBatch,  # Track 3e — batch variant
    DataCsv, DataJson, DataXml, DataYaml,
    DocHtml, DocMarkdown,
    MathEval,
    TextDiff, TextEstimateTokens,
    TimeDiff, TimeNow,
    WebFetch, WebSearch,
    DeskSearchTool, DeskPushTool,
    LlmDispatchTool, SummariseSubtaskTool, ProtocolCleanupTool,  # Track 12
)


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
        task_summarizer: TaskSummarizer | None = None,
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
        self.task_summarizer = task_summarizer
        self.quip_engine = quip_engine
        # M5.5: messages list for the currently-running agent invocation.
        # Set at the start of _generic_tool_use_loop so compaction can
        # access it from _handle_task_closures.
        self._current_agent_messages: list[LLMMessage] = []
        self.agent_factory = agent_factory or default_agent_factory
        self.max_wall_seconds = max_wall_seconds
        # Track 1d — repo_map dirty flag. Set on GIT_COMMIT, consumed at
        # agent-transition boundaries. Defers full PageRank rebuilds out of
        # the agent's hot path so within-iteration commits don't bust the
        # system-prompt cache for the active agent.
        self._repo_map_dirty: bool = False
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
        # M5: consecutive-fail redispatch (D1–D4 in m5-backend-pass.md).
        # _consecutive_fails resets on APPROVED; _pending_feedback carries
        # the reviewer's reason into the next iteration's context.
        self._consecutive_fails: dict[str, int] = {}
        self._pending_feedback: "dict[str, RejectedFeedback]" = {}
        self._scheduler: Scheduler | None = None  # set in run() after creation
        # M1: track outstanding interrupts so signal subscribers can resume.
        # Maps role → InterruptToken. Cleared by _resume_token_for_role().
        self._open_interrupts: dict[str, "InterruptToken"] = {}
        # Track 2a: question-respawn tracking. Completed agents are re-queued
        # when another agent sends them an AGENT_MESSAGE (question). Capped at
        # 3 respawns per role per run to prevent ping-pong loops.
        self._question_respawn_counts: dict[str, int] = {}
        self._question_mode_roles: set[str] = set()
        self._agent_turns_persister: "Callable[[dict], None] | None" = None
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
            # M5 D4: rebuild consecutive_fails from the board so resume
            # picks up where the previous run left off.
            self._rebuild_consecutive_fails_from_board()
        else:
            self.workspace = Workspace(
                run_id=run_id,
                goal=project.goal,
                max_iterations=project.max_iterations,
            )
        # Subscribe to the board so we can count REJECTED signals and
        # fire escalations when thresholds are crossed.
        self.workspace.subscribe(self._on_signal_for_escalation)
        # M5: consecutive-fail redispatch. Parallel to _on_signal_for_escalation;
        # handles the "bounce back to author once, then escalate" pattern.
        self.workspace.subscribe(self._on_signal_for_rejection_redispatch)
        self.workspace.subscribe(self._on_signal_for_approval)
        # Track 2a: re-queue completed agents that receive an AGENT_MESSAGE.
        self.workspace.subscribe(self._on_signal_for_question_respawn)
        # M1: subscribe so ANSWER / SKIP_GRANTED / SKIP_DENIED / HUMAN_RESPONDED
        # routes back to scheduler.resume() and unparks the asker.
        self.workspace.subscribe(self._on_signal_for_resume)
        # M2: subscribe so DESK_HANDOFF refreshes target's DeskIndex and fires quip.
        self.workspace.subscribe(self._on_desk_handoff)
        # M3.5: subscribe so USER_COMMENT lands on a planner's desk
        # when the team has one hired (DESIGN §38.12 §4.5).
        self.workspace.subscribe(self._on_user_comment_routing)
        # Phase G-prime.0 — incremental board persistence so a crashed
        # mid-run leaves a complete trail on disk for the resume loader.
        # board.jsonl co-exists with the end-of-run consolidated
        # board.json; both readers and writers tolerate either source.
        self.workspace.set_board_persister(self._build_board_persister())
        # M3.5 chunk 7 — task mutation persister so resume can restore
        # the per-agent desks without replaying every TASK_* signal.
        self.workspace.set_task_persister(self._build_task_persister())
        # Track 7 — synthetic root task per agent role. Created BEFORE
        # the task persister fires for any other task so the resume
        # replay sees roots first; idempotent on resumed runs.
        self._create_synthetic_root_tasks()
        # Phase G-prime.1 — artifact persistence. When a completed
        # agent's artifact is set via workspace.set_artifact, persist
        # the full dict so a resumed run can read the agent's output
        # without re-running it.
        self.workspace.set_artifact_persister(
            self._build_artifact_persister()
        )
        # Track 1 — iteration metrics and context log persisters.
        self.workspace._iteration_metrics_persister = (
            self._build_iteration_metrics_persister()
        )
        self.workspace._context_log_persister = (
            self._build_context_log_persister()
        )
        self._agent_turns_persister = self._build_agent_turns_persister()
        # Run-level deadline — checked by the scheduler.
        if max_wall_seconds is not None:
            import time as _time
            self.workspace.deadline_at = _time.time() + max_wall_seconds
        # Track 4.3 — per-project `local_path_in_place` source repo: agents
        # work directly in the user's directory instead of a fresh copy under
        # `<run>/workspace/working/`. Inputs/deliverables stay under run_root
        # for run isolation. Resolved here from `<project_dir>/inputs/source_repo.meta.json`
        # so the choice is per-project (the Source repo selection on the
        # Inputs tab), not a global setting.
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

    def _build_local_registry(self) -> None:
        """Catalogue every built-in tool we know how to instantiate.

        The catalog drives manifest filtering. Actual instances are created
        per-agent in `_instantiate_local_tools`."""
        for cls in LOCAL_TOOL_CLASSES:
            self._local_registry[cls.name] = LocalToolEntry(
                name=cls.name,
                description=cls.description,
                input_schema=getattr(cls, "input_schema", {}) or {},
                # Track 3d.4 — propagate manifest-driven guidance from the class.
                priority=getattr(cls, "priority", 100),
                when_to_use=getattr(cls, "when_to_use", ""),
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

        # Track 12 — sub-LLM dispatch resolver + metrics sink. Resolver
        # picks (LLMClient, model_id) for a use_case, honoring the
        # project's `sub_llm_models` map first and falling back to the
        # registry's `cheapest_tool_capable()`. Sink rolls per-use-case
        # token usage onto the calling agent's `_current_metrics` row.
        def _sub_llm_resolver(use_case: str) -> "tuple[LLMClient, str]":
            registry = getattr(self, "_model_registry", None)
            # Resolution order (Track 12):
            #   1. Per-agent override on this role's AgentConfig.
            #   2. Project-wide sub_llm_models[use_case].
            #   3. Registry's cheapest_tool_capable() fallback.
            #   4. Agent's own LLM client (no-registry dev mode).
            cfg = next((c for c in self.project.agents if c.role == role), None)
            agent_override = (
                (getattr(cfg, "sub_llm_models_override", None) or {}).get(use_case)
                if cfg is not None else None
            )
            project_override = (self.project.sub_llm_models or {}).get(use_case)
            override_id = agent_override or project_override
            if override_id and registry is not None:
                m = registry.get(override_id)
                if m is not None:
                    return registry.get_client(m), m.id
            if registry is not None:
                m = registry.cheapest_tool_capable()
                return registry.get_client(m), m.id
            # No registry — single-LLM dev mode. Reuse the agent's own
            # client + the agent's resolved model id.
            agent = self.agents.get(role)
            if agent is not None and getattr(agent, "_llm", None):
                return agent._llm, getattr(agent, "_model", "")
            raise RuntimeError(
                "sub-LLM dispatch needs either a ModelRegistry or an "
                "agent-level LLM client; neither is available."
            )

        def _sub_llm_metrics_sink(use_case: str, in_tok: int, out_tok: int) -> None:
            agent = self.agents.get(role)
            if agent is None:
                return
            m = getattr(agent, "_current_metrics", None)
            if m is None:
                return
            bucket = m.sub_llm_breakdown.setdefault(
                use_case, {"calls": 0, "input_tokens": 0, "output_tokens": 0},
            )
            bucket["calls"] += 1
            bucket["input_tokens"] += in_tok
            bucket["output_tokens"] += out_tok

        # code:exec runner — production mode + a docker_manager routes
        # through the sandbox container; dev mode (or production without
        # a docker_manager) uses the hardened host runner. The host
        # runner refuses production+host_runner combos via its own mode
        # check, so a misconfigured production deploy fails loudly.
        in_production = os.environ.get("GUUDFLOW_MODE") == "production"
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
            FsList.name: FsList(self.files, workspace=self.workspace, role=role, git=self.git),
            FsExists.name: FsExists(self.files),
            FsReadRange.name: FsReadRange(self.files),
            FsGrep.name: FsGrep(self.files),
            FsDiff.name: FsDiff(self.files, workspace=self.workspace, role=role, git=self.git),
            FsCreate.name: FsCreate(self.files, claim_emitter, release_emitter),
            FsWrite.name: FsWrite(self.files, claim_emitter, release_emitter),
            FsEdit.name: FsEdit(self.files, claim_emitter, release_emitter),
            FsDelete.name: FsDelete(self.files, claim_emitter, release_emitter),
            FsRename.name: FsRename(self.files, claim_emitter, release_emitter),
            # Track 3e — batch variants share scope/safety with their singular counterparts.
            FsWriteBatch.name: FsWriteBatch(self.files, claim_emitter, release_emitter),
            FsEditBatch.name: FsEditBatch(self.files, claim_emitter, release_emitter),
            BoardMessage.name: BoardMessage(role, self.workspace),
            AssignTask.name: AssignTask(role, self.workspace, runner=self),
            DecomposeTask.name: DecomposeTask(role, self.workspace),
            # Story lifecycle — available to all agents (no RBAC gate).
            # Any agent that writes .md files may open or close a story.
            StoryComplete.name: StoryComplete(role, self.workspace, runner=self),
            StoryOpen.name: StoryOpen(role, self.workspace, runner=self),
            GitDiff.name: GitDiff(self.git),
            GitChangedFiles.name: GitChangedFiles(self.git),
            GitLog.name: GitLog(self.git),
            GitRead.name: GitRead(self.git),
            GitStatus.name: GitStatus(self.git),
            CodeExec.name: code_exec_tool,
            CodeOutline.name: CodeOutline(self.files),
            CodeOutlineBatch.name: CodeOutlineBatch(self.files),  # Track 3e
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
            # Track 12 — sub-LLM dispatch primitive + opinionated wrappers.
            # All three share the same resolver / metrics sink; the use_case
            # tag is fixed per subclass (LlmDispatchTool.USE_CASE) so the
            # resolver picks the right model and the sink credits the right
            # bucket in `IterationMetrics.sub_llm_breakdown`.
            LlmDispatchTool.name: LlmDispatchTool(
                resolver=_sub_llm_resolver,
                metrics_sink=_sub_llm_metrics_sink,
            ),
            SummariseSubtaskTool.name: SummariseSubtaskTool(
                resolver=_sub_llm_resolver,
                metrics_sink=_sub_llm_metrics_sink,
            ),
            ProtocolCleanupTool.name: ProtocolCleanupTool(
                resolver=_sub_llm_resolver,
                metrics_sink=_sub_llm_metrics_sink,
            ),
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

    def _create_synthetic_root_tasks(self) -> None:
        """Track 7 — create one synthetic root Task per agent role.

        Acts as the default parent for `decompose-task` calls that don't
        specify one. Idempotent — skips if `root:{role}` already exists
        (resumed runs). Filtered from `desk_for()` by default so it
        doesn't pollute the Kanban or trip M3.5's "synthetic task when
        desk empty" check.

        **Silent infrastructure** — bypasses `add_task` / `mutate_task`
        so no TASK_ASSIGNED or TASK_STARTED signals land on the board.
        Same pattern as M3.5's `_ensure_synthetic_task`. The agent
        doesn't need to "see" the root via signals; it interacts with
        it implicitly through `decompose-task`'s default-parent
        resolution and Layer 4.7's active-subtask render.
        """
        ws = self.workspace
        iteration = ws.iteration
        for cfg in self.project.agents:
            root_id = f"root:{cfg.role}"
            if root_id in ws.tasks:
                continue  # resumed run already has it
            task = Task(
                task_id=root_id,
                story_id="",  # root isn't tied to a story
                assigned_to=cfg.role,
                created_by="runner",
                created_at_iter=iteration,
                goal=self.project.goal or f"({cfg.role} run scope)",
                acceptance_criteria=[],
                status="IN_PROGRESS",
                started_at_iter=iteration,
            )
            # Bypass add_task / mutate_task — direct write so the board
            # stays quiet and the persister doesn't fire for plumbing.
            ws.tasks[root_id] = task
            ws.task_queues.setdefault(cfg.role, []).append(root_id)
            # Seed active_subtask manually (mutate_task would have done
            # this; we're skipping that path for silence).
            ws.active_subtask[cfg.role] = root_id

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

    def _build_iteration_metrics_persister(self):
        """Return a callable that appends each IterationMetrics row to
        iteration_metrics.jsonl. One append per row = crash-safe."""
        from dataclasses import asdict
        try:
            self.run_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        path = self.run_root / "iteration_metrics.jsonl"

        def _persist(m) -> None:
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(m), default=str) + "\n")
            except OSError:
                pass

        return _persist

    def _build_context_log_persister(self):
        """Return a callable that appends each ContextSnapshot row to
        context_log.jsonl. One append per row = crash-safe."""
        from dataclasses import asdict
        try:
            self.run_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        path = self.run_root / "context_log.jsonl"

        def _persist(s) -> None:
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(s), default=str) + "\n")
            except OSError:
                pass

        return _persist

    def _build_agent_turns_persister(self):
        """Return a callable that appends a full agent turn record to
        agent_turns.jsonl — one line per completed agent invocation.

        Each record contains: role, iteration, model, all messages (prompt,
        tool calls, tool results, final response), and the final output text.
        Crash-safe: each append is independent.
        """
        try:
            self.run_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        path = self.run_root / "agent_turns.jsonl"

        def _persist(record: dict) -> None:
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, default=str) + "\n")
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
        # Track 1d — defer repo_map rebuild to next agent-transition.
        # Within an iteration, multiple commits do NOT each rebuild the map;
        # the system-prompt cache stays byte-stable for the active agent.
        # The deferred rebuild fires inside _drive_agent_run pre-spawn.
        if signal_type is SignalType.GIT_COMMIT:
            self._repo_map_dirty = True

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
        # Build reverse adjacency once: role -> set of roles that directly
        # depend on it. Used to compute transitive blockers below.
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
                # Skip auto-fill for any role that transitively depends on us —
                # filling in the reverse direction would create a cycle and
                # the scheduler would hit STUCK on tick 1. Direct + transitive
                # both matter: a → b → us means depending on a still cycles.
                blockers = _transitive_dependents(cfg.role)
                cfg.depends_on = [
                    r for r in non_final_roles
                    if r != cfg.role and r not in blockers
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
            # Track 1c — workspace handle so effective_system_prompt() can
            # inject Layer 4.5 (codebase structure from repo_map) per §28.
            agent._workspace = self.workspace
            agent._manifest = self.manifest_builder.build_for_agent(
                role=cfg.role,
                declared_local_tools=cfg.declared_local_tools,
                declared_mcp_servers=cfg.declared_mcp_servers,
                excluded_baseline=cfg.excluded_baseline_tools,
            )
            agent._local_tools = self._instantiate_local_tools(cfg.role)
            # On a continuation run, the agent's "task" used for context
            # retrieval (token-overlap scoring against repo files, persona
            # memory recall) is the USER FEEDBACK — not the original goal.
            # Without this, every continuation re-loads files matching the
            # whole project description and re-does prior work.
            agent._current_task = (
                self._continuation_task_hint() or self.project.goal
            )
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

            # Continuation brief — surfaces prior-run feedback to agents
            # at runtime (not just at proposal time). Without this, devs
            # rebuild from scratch instead of addressing review comments.
            agent._continuation_brief = self._build_continuation_brief()

            # M2: build DeskIndex (persona-default + project-overlay) and inject.
            agent._desk_index = self._build_desk_index(cfg)

            self.agents[cfg.role] = agent

    def _continuation_task_hint(self) -> str:
        """Concatenate every prior-run feedback.md into a short task hint.

        Returned string is what gets used as `agent._current_task` so
        relevance scoring (file retrieval, memory recall) targets the
        actual feedback this run is supposed to address rather than the
        full original project goal.
        """
        inputs_dir = self.run_root / "workspace" / "inputs"
        if not inputs_dir.is_dir():
            return ""
        chunks: list[str] = []
        for entry in sorted(inputs_dir.iterdir()):
            if not entry.is_dir() or not entry.name.startswith("from_run_"):
                continue
            fb_path = entry / "feedback.md"
            if not fb_path.is_file():
                continue
            try:
                text = fb_path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if text:
                chunks.append(text)
        return "\n".join(chunks).strip()

    def _build_continuation_brief(self) -> str:
        """Read inputs/from_run_*/feedback.md across all attached prior
        runs and assemble a runtime brief. Empty string when no prior
        runs are attached — the layer is skipped in that case.

        The brief is injected into every agent's effective system prompt
        so devs (not just the meta-agent at proposal time) know they are
        on a continuation and what feedback to address.
        """
        inputs_dir = self.run_root / "workspace" / "inputs"
        if not inputs_dir.is_dir():
            return ""

        feedbacks: list[tuple[str, str]] = []  # (run_id, feedback_text)
        prior_run_ids: list[str] = []
        for entry in sorted(inputs_dir.iterdir()):
            if not entry.is_dir() or not entry.name.startswith("from_run_"):
                continue
            prior_run_id = entry.name[len("from_run_"):]
            prior_run_ids.append(prior_run_id)
            fb_path = entry / "feedback.md"
            if fb_path.is_file():
                try:
                    text = fb_path.read_text(encoding="utf-8").strip()
                except OSError:
                    text = ""
                if text:
                    feedbacks.append((prior_run_id, text))

        if not prior_run_ids:
            return ""

        lines: list[str] = [
            "## CONTINUATION RUN — read this before any other work",
            "",
            "This is NOT a fresh start. A prior run produced deliverables "
            "that were seeded into `working/`. Your job is to extend / "
            "fix that work, NOT redo it from scratch.",
            "",
            "Prior run(s) attached:",
        ]
        for pid in prior_run_ids:
            lines.append(
                f"- `inputs/from_run_{pid}/deliverables/` — what shipped last time"
            )

        if feedbacks:
            lines.append("")
            lines.append("USER FEEDBACK (must be addressed this run):")
            lines.append("")
            for pid, text in feedbacks:
                lines.append(f"From review of `{pid}`:")
                lines.append(f"> {text}")
                lines.append("")

        lines.append(
            "Before you write code: `fs-list` and `fs-read` what's already "
            "in `working/`. Identify the deltas the feedback asks for. "
            "Edit existing files where possible — only add new files when "
            "the change genuinely needs a new module."
        )
        lines.append("")
        lines.append("STORY-SCOPED DOCS (agile workflow):")
        lines.append(
            "- `fs-list` shows `.md` files with their `story:` frontmatter "
            "(whole-file ownership) AND a `changed_in_story` flag when the "
            "file has changed since your active story opened."
        )
        lines.append(
            "- `fs-diff` returns a unified diff of a doc against your "
            "story's baseline ref — the cheapest way to see what's new for "
            "your story in a shared doc without re-reading the whole file."
        )
        lines.append(
            "- `fs-read` returns the full file content; pair it with `fs-diff` "
            "when you need both the prose and the delta."
        )
        lines.append("")
        lines.append("AUTHORING PATTERN — for architects / tech leads:")
        lines.append(
            "When a doc is wholly about one story, add `story: <name>` "
            "frontmatter so `fs-list` advertises ownership. For shared "
            "docs that grow over time, just write — git history attributes "
            "the changes to the active story automatically."
        )
        lines.append("")
        lines.append("    ---")
        lines.append("    story: ghost-ai")
        lines.append("    ---")
        lines.append("")
        lines.append("    # Ghost AI design")
        lines.append("    ...")
        lines.append("")
        lines.append(
            "Story names are short kebab-case nouns drawn from feedback "
            "or the run goal."
        )
        lines.append("")
        lines.append("FOLLOWER PATTERN — for specialists / devs:")
        lines.append(
            "Use `fs-list` to find docs flagged `changed_in_story: true`, "
            "then `fs-diff` on those to read just the new context. Use "
            "`fs-read` when you need the full doc."
        )
        return "\n".join(lines).strip()

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
        """Apply `partial` to the live agent for `role` so changes take effect
        on the next iteration.

        Handles `declared_local_tools` and any other AgentConfig field that
        the settings panel may send. Tool changes rebuild the agent's manifest.
        """
        cfg = next((c for c in self.project.agents if c.role == role), None)
        if cfg is None:
            raise KeyError(f"unknown role {role!r}")

        agent = self.agents.get(role)
        if agent is None:
            raise KeyError(f"agent {role!r} not yet instantiated")

        # Apply supported fields.
        allowed = {
            "declared_local_tools",
            "model_tier",
            "description",
            "max_tool_iterations",
            "max_protocol_retries",
        }
        for key, value in partial.items():
            if key not in allowed:
                continue
            if hasattr(cfg, key):
                setattr(cfg, key, value)

        # Rebuild tool manifest if tools changed.
        if "declared_local_tools" in partial:
            from core.tool_manifest import ToolManifestBuilder, RoleRBAC
            rbac_entry = self.project.rbac.get(role)
            if rbac_entry is None:
                from core.tool_manifest import RoleRBAC as _RoleRBAC
                rbac_entry = _RoleRBAC(allowed_scopes=cfg.declared_local_tools or [])
            builder = ToolManifestBuilder(
                declared_local_tools=cfg.declared_local_tools or [],
                role_rbac=rbac_entry,
                credentials_broker=None,
            )
            agent._manifest = builder.build()

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

        # Give GitWorkspace the persona display names so each commit carries
        # the right author (e.g. "Sarah (developer)" not "guudflow").
        git_personas: dict[str, str] = {}
        for role, agent in self.agents.items():
            git_personas[role] = (
                agent.persona.profile.name if agent.persona is not None else role
            ) or role
        self.git._hired_personas = git_personas

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

        # User-initiated Stop: skip post-run hooks (memory writer, land-on-
        # main, quip flourish) so the run terminates promptly. Without this
        # the FE keeps showing "running" while we burn LLM tokens on a run
        # the user already abandoned.
        if result.status is SchedulerStatus.STOPPED:
            self._close_remaining_stories()
            return result

        # Wait for any in-flight escalation handlers (Phase C.3) so
        # their signals land before memory_writer / quip_engine fire.
        await self._await_escalation_tasks()

        # Land-on-main pass: ensure the leaf agent's branch is on main.
        # With the pre-merge chain fixed, reviewer→APPROVED usually handles
        # this already. This is a safety net for reviewer-less pipelines and
        # any race where APPROVED fired before all upstream commits landed.
        # TODO(remote-mode): for runs against an upstream remote, push a
        # feature branch and open a PR here instead of merging locally.
        if self.git is not None:
            await self._land_leaf_on_main()

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

    def _ensure_synthetic_task(self, role: str, cfg: "AgentConfig") -> None:
        """Create an implicit IN_PROGRESS task for `role` if its desk is empty.

        Called at agent dispatch time so the Kanban always shows a card even
        when no planner called assign-task. Marked created_by="scheduler" so
        it's distinguishable from human/planner tasks. The caller is
        responsible for closing it on agent completion.
        """
        from core.workspace import Task

        desk = self.workspace.desk_for(role)
        if desk:
            return  # agent already has explicit tasks — don't synthesise

        goal = cfg.description or self.project.goal
        story_id = self.workspace.active_story.get(role, "")
        task = Task(
            task_id=_uuid.uuid4().hex,
            story_id=story_id,
            assigned_to=role,
            created_by="scheduler",
            created_at_iter=self.workspace.iteration,
            goal=goal,
            status="IN_PROGRESS",
            started_at_iter=self.workspace.iteration,
        )
        # Bypass add_task so we don't emit TASK_ASSIGNED for a synthetic task;
        # append directly.
        self.workspace.tasks[task.task_id] = task
        self.workspace.task_queues.setdefault(role, []).append(task.task_id)

    def _close_synthetic_task(self, role: str) -> None:
        """Mark any open synthetic task for `role` as COMPLETE.

        Called when the agent completes so the Kanban card moves to Done.
        Only closes tasks with created_by="scheduler" to avoid interfering
        with real planner-assigned tasks.
        """
        for task in self.workspace.desk_for(role):
            if task.created_by == "scheduler" and task.status == "IN_PROGRESS":
                task.status = "COMPLETE"
                task.completed_at_iter = self.workspace.iteration
                break

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
            # Skip the gate if no upstream agent has completed yet — a
            # final-reviewer that runs first (e.g. an architect with empty
            # depends_on) would otherwise hang the run on tick 0 awaiting
            # approval for "validating finished work" that hasn't happened.
            if (
                cfg.is_final_reviewer
                and self._is_gate_enabled("heavy_tier_mid_run_dispatch")
                and len(workspace.completed_agents) > 0
            ):
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

            # Synthesise an implicit IN_PROGRESS task so the Kanban always
            # shows a card even when no planner called assign-task.
            self._ensure_synthetic_task(cfg.role, cfg)

            # Quip: agent reacts to receiving the baton (handoff moment).
            if self.quip_engine is not None:
                try:
                    await self.quip_engine.on_agent_started(cfg.role, workspace)
                except Exception:
                    pass

            # Pre-merge upstream branches into this agent's own branch so
            # its working tree contains all upstream work before it runs.
            # Applies to every agent with depends_on (not only reviewers):
            # integration_dev needs dev branches merged in before it can
            # integrate files; reviewers need them for git-diff context.
            # Agents with no depends_on are a no-op. Conflicts post a
            # GIT_CONFLICT signal and fail the iteration cleanly.
            all_clean, conflicted = await self._premerge_upstream_deps(cfg)
            if not all_clean:
                workspace.agent_states[cfg.role] = AgentState.FAILED
                await agent.signal(
                    workspace, SignalType.FAILED,
                    summary=f"{cfg.role}: pre-run merge conflict",
                    detail=(
                        f"Could not auto-merge {', '.join(conflicted)} into "
                        f"agent/{cfg.role}. Resolve manually or split the "
                        f"upstream work."
                    ),
                )
                return

            # Track 1: initialise per-iteration metrics before the run.
            agent._last_commit_was_noop = False  # reset from any prior run
            agent._current_metrics = IterationMetrics(
                run_id=workspace.run_id,
                role=cfg.role,
                iteration=agent.iteration,
                model=agent._model,
                started_at=_time.time(),
            )
            try:
                drive_result = await self._drive_agent_run(agent, cfg)
            except Exception as e:
                workspace.agent_states[cfg.role] = AgentState.FAILED
                await agent.signal(
                    workspace, SignalType.FAILED,
                    summary=f"{cfg.role} crashed",
                    detail=str(e),
                )
                if agent._current_metrics is not None:
                    agent._current_metrics.failed = True
                raise
            finally:
                _m = agent._current_metrics
                if _m is not None:
                    _m.ended_at = _time.time()
                    _m.elapsed_seconds = _m.ended_at - _m.started_at
                    _m.completed = not _m.failed
                    _m.no_op = agent._last_commit_was_noop  # Track 2b
                    # Track 3d.2: aggregate ContextSnapshots recorded during
                    # this iteration into the metrics row. Agents that skip
                    # get_context() entirely will show 0 here; their file I/O
                    # is captured separately in direct_file_read_tokens.
                    for snap in workspace.context_log:
                        if snap.role == cfg.role and snap.iteration == agent.iteration:
                            _m.repo_map_tokens += snap.repo_map_tokens
                            _m.git_diff_tokens += snap.git_diff_tokens
                            _m.relevant_files_tokens += sum(
                                t for _, t in snap.relevant_files
                            )
                            _m.context_tokens_total += snap.used_tokens
                    workspace.add_iteration_metrics(_m)
                    agent._current_metrics = None
                # Log the full turn — messages + output — to agent_turns.jsonl.
                if self._agent_turns_persister is not None:
                    try:
                        def _serialise_msg(msg: LLMMessage) -> dict:
                            tc = None
                            if msg.tool_calls:
                                tc = [{"id": c.id, "name": c.name, "arguments": c.arguments}
                                      for c in msg.tool_calls]
                            return {"role": msg.role, "content": msg.content, "tool_calls": tc}

                        turn_record: dict = {
                            "run_id": workspace.run_id,
                            "role": cfg.role,
                            "iteration": agent.iteration,
                            "model": agent._model,
                            "started_at": _m.started_at if _m else None,
                            "ended_at": _m.ended_at if _m else None,
                            "elapsed_seconds": _m.elapsed_seconds if _m else None,
                            "input_tokens": _m.input_tokens if _m else None,
                            "output_tokens": _m.output_tokens if _m else None,
                            "llm_calls": _m.llm_calls if _m else None,
                            "tools_called": _m.tools_called if _m else [],
                            "completed": _m.completed if _m else False,
                            "failed": _m.failed if _m else True,
                            "messages": [_serialise_msg(msg) for msg in self._current_agent_messages],
                            "final_output": (
                                {"kind": drive_result.kind, "fields": drive_result.fields}
                                if isinstance(drive_result, protocol.CompactBlock)
                                else str(drive_result) if drive_result else None
                            ),
                        }
                        self._agent_turns_persister(turn_record)
                    except Exception:
                        pass

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

            # Track 2b — enforce no-op rationale when the branch is clean.
            # An agent that produces no commits must state (≥ 10 chars) why
            # in its CompactBlock summary, so downstream agents and reviewers
            # see structured context instead of a silent COMPLETE.
            # Exemption: reviewers are never expected to commit — their output
            # is a [VERIFY] block, not a [COMMIT] block.
            if commit_hash is None and not cfg.is_reviewer:
                rationale = ""
                if (
                    isinstance(drive_result, protocol.CompactBlock)
                    and drive_result.kind == "COMMIT"
                ):
                    rationale = drive_result.fields.get("summary", "").strip()
                if len(rationale) < 10:
                    workspace.agent_states[cfg.role] = AgentState.FAILED
                    await agent.signal(
                        workspace, SignalType.FAILED,
                        summary=f"{cfg.role}: no-op completion missing rationale",
                        detail=(
                            "Agent produced no git commits and did not explain why. "
                            "The COMMIT block summary must state what was verified and "
                            "why no change was needed (at least 10 characters)."
                        ),
                    )
                    return
                # Post a synthetic no-op COMMIT protocol signal so downstream
                # agents and reviewers can read the rationale as typed context.
                _no_op_raw = protocol.render_commit(
                    cfg.role, "no-op", [], ["no-op-verified"],
                    no_op_reason=rationale,
                )
                workspace.post_signal(Signal(
                    type=SignalType.PROTOCOL,
                    summary=agent._namespace("COMMIT (no-op)"),
                    detail=_no_op_raw,
                    from_agent=cfg.role,
                    iteration=workspace.iteration,
                ))

            # M3.5 — process the COMMIT's optional `closes: task:<id>`
            # trailer (DESIGN §38.12 §4.3). Validates assignee + state,
            # mutates each closed task to COMPLETE, cascades parent
            # closure, and posts TASK_OUT_OF_SCOPE warnings on
            # invariant violations. The COMMIT itself is not blocked.
            await self._handle_task_closures(cfg.role, drive_result, commit_hash)

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
            # Close any synthetic task so the Kanban card moves to Done.
            self._close_synthetic_task(cfg.role)

            workspace.completed_agents.append(cfg.role)
            workspace.agent_states[cfg.role] = AgentState.COMPLETE
            self._question_mode_roles.discard(cfg.role)

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

            # On APPROVED: merge the reviewer's branch (which already
            # carries all upstream work via _premerge_upstream_for_reviewer)
            # into main. One merge commit per APPROVED, not one per dep.
            # REJECTED leaves the branches alone for the next iteration.
            if cfg.is_reviewer and final_signal is SignalType.APPROVED:
                reviewer_branch = workspace.git.agent_branches.get(cfg.role)
                if reviewer_branch:
                    pr_mode = getattr(self.project, "pr_mode", "manual")
                    if pr_mode == "auto":
                        merge_target = "main"
                    else:
                        merge_target = self._integration_branch_name()
                        try:
                            from git import Repo
                            repo = Repo(self.git.working_dir)
                            if merge_target not in [h.name for h in repo.heads]:
                                repo.create_head(merge_target, "main")
                        except Exception:
                            pass
                    ok = self.git.merge(reviewer_branch, into=merge_target)
                    if ok:
                        workspace.git.merged.append(reviewer_branch)
                # M1 §7b: auto-close each dep's active story if its
                # latest commit summary matches the story goal. Story
                # closure is per-dep regardless of how branches merged.
                for dep_role in cfg.depends_on:
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

    async def _handle_task_closures(
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

        M5.5: synthesizes a TaskCompletionRecord for each closed task
        and compacts the agent's current message history in-place.
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

        # M5.5: extract commit metadata for the summarizer.
        commit_subject = drive_result.fields.get("summary", "").strip()
        diff_stat = drive_result.fields.get("diff_stat", "").strip()

        def _warn(summary: str, detail: str) -> None:
            ws.post_signal(Signal(
                type=SignalType.TASK_OUT_OF_SCOPE,
                summary=summary,
                from_agent=role,
                iteration=iteration,
                detail=detail,
            ))

        # If the working tree was clean, _auto_commit returned None.
        # The agent emitted closes: anyway — surface it so the
        # reviewer sees that nothing actually shipped against
        # the named tasks.
        if commit_hash is None:
            _warn(
                f"{role} commit closes tasks but produced no git commit",
                f"closes={raw_closes};commit_hash=None",
            )

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

            # M5.5 C1: synthesize completion record before mutating task.
            completion_record_dict: dict | None = None
            if self.task_summarizer is not None and commit_hash:
                try:
                    task_refs = [r.value for r in (task.references or [])]
                    record = await self.task_summarizer.summarize(
                        task_id=tid,
                        task_goal=task.goal,
                        task_acceptance_criteria=task.acceptance_criteria or [],
                        task_references=task_refs,
                        messages=self._current_agent_messages,
                        commit_sha=commit_hash,
                        commit_subject=commit_subject,
                        diff_stat=diff_stat,
                    )
                    completion_record_dict = record.to_dict()
                    # M5.5 C2: compact agent messages after synthesis.
                    self._compact_messages_after_close(
                        self._current_agent_messages, tid, commit_hash, record
                    )
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "TaskSummarizer failed for task %s: %s", tid, exc
                    )

            ws.mutate_task(
                tid, "COMPLETE",
                iteration=iteration,
                closed_by_commit=commit_hash or "",
                completion_summary=completion_record_dict,
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

    def _compact_messages_after_close(
        self,
        messages: list[LLMMessage],
        task_id: str,
        sha: str,
        record: "TaskSummarizer | Any",
    ) -> None:
        """M5.5 C2: replace task's verbose message history with a single marker.

        Heuristic: walk backwards from the end to find where this task's work
        began (the message after the most recent prior `closes: task:*` marker,
        or the first message mentioning task_id, or fallback to last 2 messages).
        Mutates ``messages`` in-place.
        """
        from core.task_summarizer import TaskCompletionRecord

        summary = record.summary if hasattr(record, "summary") else str(record)
        degraded = summary == "(summary unavailable)"
        # Use the task goal as fallback text on degraded records.
        marker_text = summary if not degraded else f"[task {task_id}] work completed"

        marker = LLMMessage(
            role="user",
            content=f"[runner] Task {task_id} closed at {sha[:7]}. Summary: {marker_text}",
        )

        if not messages:
            return

        found_start = False
        i_start = 0

        # Search for the start of this task's work: the first message after
        # the most recent prior "[runner] Task ... closed at" marker.
        for i in range(len(messages) - 1, -1, -1):
            content = messages[i].content
            text = content if isinstance(content, str) else ""
            if "[runner] Task" in text and "closed at" in text:
                i_start = i + 1
                found_start = True
                break

        # Fallback: if no prior marker found, look for first mention of task_id.
        if not found_start:
            for i, msg in enumerate(messages):
                content = msg.content
                text = content if isinstance(content, str) else str(content)
                if task_id in text:
                    i_start = i
                    found_start = True
                    break

        # Safety fallback: task_id never mentioned and no prior marker.
        if not found_start:
            i_start = max(0, len(messages) - 2)

        # Replace everything from i_start to end with the marker.
        messages[i_start:] = [marker]

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
        from core.protocol import render_task_completion_summary

        desk = self.workspace.desk_for(role)
        if not desk:
            return ""

        # M5.5 C3: split desk into active/completed; completed tasks with
        # completion_summary render as compact summary lines.
        completed_tasks = [t for t in desk if t.status == "COMPLETE"]
        active_desk = [t for t in desk if t.status != "COMPLETE"]

        # Group active tasks: top-level first, each followed by its children.
        # BLOCKED/CANCELLED tasks render at the bottom with a divider.
        top_level = [t for t in active_desk if t.parent_task_id is None]
        children_by_parent: dict[str, list] = {}
        for t in active_desk:
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

        # Children whose parent is no longer on the active desk (e.g. completed
        # parents whose children are still open) render under "Other".
        orphans = [
            t for t in active_desk
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

        # M5.5 C3: render completed tasks with summaries in a sub-section.
        if completed_tasks:
            out.append("")
            out.append("--- previously completed ---")
            for t in completed_tasks:
                if t.completion_summary:
                    out.append(render_task_completion_summary(t.completion_summary))
                else:
                    # Fallback: M3.5-style header for tasks closed pre-M5.5.
                    sha = t.closed_by_commit[:7] if t.closed_by_commit else ""
                    out.append(f"[task:{t.task_id}] COMPLETE — {t.goal}" +
                               (f" (commit {sha})" if sha else ""))
                out.append("")

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

    def _rebuild_repo_map_if_dirty(self) -> None:
        """Track 1d — consume the dirty flag and rebuild repo_map before the
        next agent runs. Called at agent-transition boundaries so within-
        iteration commits don't bust the system-prompt cache for the active
        agent (commits set the flag; the next spawn pays for the rebuild).
        """
        if not self._repo_map_dirty:
            return
        try:
            self.workspace.repo_map = build_repo_map(self.files.working)
        except Exception:
            pass
        finally:
            self._repo_map_dirty = False

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
        # Track 1d — pre-spawn: rebuild repo_map if any prior commits dirtied it.
        self._rebuild_repo_map_if_dirty()

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

        if cfg.is_reviewer:
            start_directive = (
                "START REVIEWING NOW. Your FIRST tool call MUST be `git-changed-files` "
                "to see what changed. Then call `git-diff` on each changed path before "
                "forming a verdict. A FAIL result must cite the file and the specific "
                "defect — at least one full sentence naming what is wrong and why. "
                "Do not describe what you will do — do it. "
                "End with the required VERIFY_RESULT protocol block once your review is complete."
            )
        elif cfg.role in self._question_mode_roles:
            start_directive = (
                "You have been re-activated to answer a question from another agent. "
                "Read the most recent AGENT_MESSAGE signal directed at your role to find "
                "the question. Answer it concisely and completely using your tools as needed. "
                "Post your reply as an AGENT_MESSAGE signal, then end with a [COMMIT] block "
                "whose summary begins 'answered: ' followed by one sentence describing your answer."
            )
        else:
            start_directive = (
                "START WORKING NOW. Call `fs-list` or `git-changed-files` as your very "
                "first tool call to understand what already exists on disk. Then read, "
                "build, or review as your role requires. Do not describe what you will do — "
                "do it. End with the required protocol block once your work is complete. "
                "If you produce no file commits, your [COMMIT] summary MUST explain what "
                "you verified and why no change was needed (e.g. 'tests already cover this case'). "
                "A bare summary like 'done' or 'no changes' will fail."
            )
        task_prompt = (
            f"Project goal: {self.project.goal}\n\n"
            f"Your role: {cfg.role}. {cfg.description}{upstream_ctx}{messages_ctx}\n\n"
            f"{start_directive}"
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

        # M5: prepend reviewer rejection feedback if this is a re-dispatch.
        # Feedback is the absolute first thing the agent reads each re-run.
        pending_fb = self._pending_feedback.pop(cfg.role, None)
        if pending_fb is not None:
            from core.protocol import render_rejected_feedback
            fb_block = render_rejected_feedback(
                pending_fb.reviewer_name,
                pending_fb.reviewer_role,
                pending_fb.detail,
                pending_fb.attempt_number,
            )
            task_prompt = fb_block + "\n\n" + task_prompt

        messages: list[LLMMessage] = [LLMMessage(role="user", content=task_prompt)]
        # M5.5: expose messages to _handle_task_closures (compaction).
        self._current_agent_messages = messages
        guard = LoopGuard(cfg.loop_guard)
        nudges: list[str] = []  # accumulated for the next user turn
        protocol_retries_used = 0
        ask_enforcement_failures = 0  # track how many times we've caught non-ASK on empty upstream

        # Tool-iteration cap is extendable: when the agent hits the cap we
        # ask the user "keep going?" rather than silently returning a string
        # that the upstream code mistakes for a successful artifact.
        iteration = 0
        cap = cfg.max_tool_iterations
        TOOL_ITER_EXTENSION_BUMP = 15
        while True:
            if iteration >= cap:
                granted = await self._request_approval(
                    kind="tool_iteration_extension",
                    summary=(
                        f"{cfg.role} hit tool-call cap ({cap}) — grant +"
                        f"{TOOL_ITER_EXTENSION_BUMP} more?"
                    ),
                    triggered_by=cfg.role,
                    context={
                        "role": cfg.role,
                        "cap": cap,
                        "iterations_used": iteration,
                        "bump": TOOL_ITER_EXTENSION_BUMP,
                    },
                )
                if granted:
                    cap += TOOL_ITER_EXTENSION_BUMP
                    continue
                return protocol.CompactBlock(
                    kind="VERIFY",
                    fields={
                        "result": "FAIL",
                        "detail": (
                            f"agent {cfg.role!r} hit tool-iteration cap of "
                            f"{cap} and the user denied an extension"
                        ),
                    },
                )

            if guard.deadline_exceeded():
                return f"(loop guard: wall-clock budget exhausted after {guard.elapsed_seconds:.0f}s)"

            iteration += 1
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
                    # call of each iteration where nothing is yet
                    # IN_PROGRESS (DESIGN §38.12 §4.2). Retrying per
                    # iteration covers the case where iteration 0
                    # produces no tool calls (LLM emits text + a
                    # bare COMMIT block) — without this the desk
                    # would skip PENDING → COMPLETE and lose the
                    # TASK_STARTED audit step. _maybe_auto_start_task
                    # is a no-op if a task is already IN_PROGRESS,
                    # so calling it on every iteration is safe.
                    if tc_idx == 0 and not cfg.is_reviewer:
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

            # Protocol parse failure. On exhaustion, emit a synthetic VERIFY
            # FAIL rather than crashing — "the loop is the error handler"
            # (DESIGN rule #8). Otherwise post a PROTOCOL_PARSE_FAIL signal
            # and nudge with increasing specificity.
            if protocol_retries_used >= cfg.max_protocol_retries:
                detail = parse_error or "no [COMMIT]/[VERIFY]/[ACTION]/[STATUS] block found"
                return protocol.CompactBlock(
                    kind="VERIFY",
                    fields={
                        "result": "FAIL",
                        "detail": (
                            f"agent {cfg.role!r} failed to emit a valid protocol block "
                            f"after {cfg.max_protocol_retries} retries: {detail}"
                        ),
                    },
                )

            retry_num = protocol_retries_used + 1  # 1-indexed for display
            self.workspace.post_signal(Signal(
                type=SignalType.PROTOCOL_PARSE_FAIL,
                summary=f"{cfg.role} protocol retry ({retry_num}/{cfg.max_protocol_retries})",
                detail=parse_error or "no valid protocol block found at end of response",
                from_agent=cfg.role,
                iteration=self.workspace.iteration,
            ))
            protocol_retries_used += 1

            messages.append(LLMMessage(role="assistant", content=text))

            if protocol_retries_used == 1:
                # Tier 1: basic reminder.
                nudge_msg = (
                    "[PROTOCOL] Your last response did not end with a valid "
                    "compact protocol block. Re-emit your final answer ending "
                    "with one of [COMMIT] / [VERIFY] / [ACTION] / [ASK] / [STATUS] as "
                    "specified in the system prompt. Block must be the last "
                    "thing in your response, with required fields filled in."
                )
                if parse_error:
                    nudge_msg += f"\nParse error: {parse_error}"
            elif protocol_retries_used == 2:
                # Tier 2: show a concrete example block.
                example = (
                    "[VERIFY]\n  result: PASS"
                    if cfg.is_reviewer else
                    "[COMMIT]\n  summary: <one-line description of what you did>"
                )
                nudge_msg = (
                    "[PROTOCOL] Still no valid protocol block. "
                    f"End your response with EXACTLY this pattern (fill in the fields):\n\n{example}"
                )
                if parse_error:
                    nudge_msg += f"\n\nParse error from last attempt: {parse_error}"
            else:
                # Tier 3: minimal directive.
                minimal = (
                    "[VERIFY]\n  result: PASS"
                    if cfg.is_reviewer else
                    "[COMMIT]\n  summary: done"
                )
                nudge_msg = (
                    f"End your response with EXACTLY this text and nothing after it:\n\n{minimal}"
                )

            messages.append(LLMMessage(role="user", content=nudge_msg))
            continue

    def _integration_branch_name(self) -> str:
        """Track 4.2 — name of the branch leaf-work merges into when
        `pr_mode=manual`. Prefers the active story slug if any role has one
        open; otherwise uses a short run-id-based name.
        """
        ws = self.workspace
        active_story = getattr(ws, "active_story", None) or {}
        stories = getattr(ws, "stories", None) or {}
        for _role, story_id in active_story.items():
            story = stories.get(story_id) if stories else None
            if story is not None and not getattr(story, "closed", False):
                slug = (
                    str(story_id).lower().replace(" ", "-").replace("_", "-")
                )
                return f"flow/story-{slug}"
        return f"flow/run-{self.run_id[:10]}"

    def _suggest_pr_title(self) -> str:
        """One-line PR title suggestion derived from the project goal."""
        goal = (self.project.goal or "").strip().replace("\n", " ")
        if len(goal) > 60:
            goal = goal[:57].rstrip() + "..."
        return goal or f"AgentFlow run {self.run_id[:10]}"

    async def _emit_pr_ready_signal(
        self, integration_branch: str, leaf_branch: str
    ) -> None:
        """Post PR_READY with branch + paste-ready `gh pr create` command."""
        head_sha = ""
        try:
            from git import Repo
            repo = Repo(self.git.working_dir)
            if integration_branch in [h.name for h in repo.heads]:
                head_sha = repo.heads[integration_branch].commit.hexsha
        except Exception:
            pass

        title = self._suggest_pr_title()
        # Quote the title for shell safety; the user pastes this verbatim.
        gh_command = (
            f'gh pr create --base main --head {integration_branch} '
            f'--title "{title}"'
        )
        detail = json.dumps({
            "integration_branch": integration_branch,
            "base_branch": "main",
            "head_sha": head_sha,
            "leaf_branch": leaf_branch,
            "gh_command": gh_command,
            "title_suggestion": title,
        })
        self.workspace.post_signal(Signal(
            type=SignalType.PR_READY,
            summary=f"branch ready for PR: {integration_branch}",
            detail=detail,
            from_agent="runner",
        ))

    async def _land_leaf_on_main(self) -> None:
        """Merge the leaf agent's branch into the run's landing target.

        Landing target depends on `project.pr_mode`:
        - `auto` → `main` (today's behaviour; merges direct).
        - `manual` → an integration branch (`flow/story-<slug>` if a story is
          open, else `flow/run-<id>`); main is left untouched and a PR_READY
          signal is posted with a paste-ready `gh pr create` command.

        The leaf is the agent with is_final_reviewer=True, or — if none —
        the agent that no other agent depends on (a sink in the dep graph)
        with the most commits ahead of main. Skips silently if the target
        already contains the leaf's tip as an ancestor.
        """
        if self.git is None:
            return

        pr_mode = getattr(self.project, "pr_mode", "manual")
        target = "main" if pr_mode == "auto" else self._integration_branch_name()

        agents = self.project.agents

        # Prefer the designated final reviewer.
        leaf_cfg = next((c for c in agents if getattr(c, "is_final_reviewer", False)), None)

        if leaf_cfg is None:
            # Fall back: find sinks (no other agent depends_on them).
            depended_on = {r for c in agents for r in (c.depends_on or [])}
            sinks = [c for c in agents if c.role not in depended_on]
            if not sinks:
                return
            # Pick the sink whose branch is furthest ahead of main.
            from git import InvalidGitRepositoryError, NoSuchPathError, Repo
            try:
                repo = Repo(self.git.working_dir)
                main_sha = repo.heads["main"].commit.hexsha

                def commits_ahead(role: str) -> int:
                    branch = self.workspace.git.agent_branches.get(role, f"agent/{role}")
                    try:
                        return sum(1 for _ in repo.iter_commits(f"main..{branch}"))
                    except Exception:
                        return 0

                leaf_cfg = max(sinks, key=lambda c: commits_ahead(c.role))
            except (InvalidGitRepositoryError, NoSuchPathError, KeyError):
                return

        leaf_branch = self.workspace.git.agent_branches.get(
            leaf_cfg.role, f"agent/{leaf_cfg.role}"
        )

        # Check whether the target already contains the leaf's tip.
        try:
            from git import Repo
            repo = Repo(self.git.working_dir)
            target_ref = (
                repo.heads[target] if target in [h.name for h in repo.heads] else None
            )
            leaf_commit = repo.commit(leaf_branch)
            if target_ref is not None and (
                repo.is_ancestor(leaf_commit, target_ref.commit)
                or target_ref.commit == leaf_commit
            ):
                # Already landed on this target. Manual mode still posts a
                # PR_READY signal so the UI can surface the gh command.
                if pr_mode == "manual":
                    await self._emit_pr_ready_signal(target, leaf_branch)
                return
        except Exception:
            return

        async with self._git_lock:
            # In manual mode the integration branch may not exist yet — fork
            # it from main so it carries the same baseline as auto mode.
            if pr_mode == "manual" and target != "main":
                try:
                    from git import Repo
                    repo = Repo(self.git.working_dir)
                    if target not in [h.name for h in repo.heads]:
                        repo.create_head(target, "main")
                except Exception:
                    pass

            ok = self.git.merge(leaf_branch, into=target)
            if not ok:
                self.workspace.post_signal(Signal(
                    type=SignalType.GIT_CONFLICT,
                    summary=f"end-of-run merge conflict: {leaf_branch} → {target}",
                    detail=(
                        f"The final branch could not be merged into {target} automatically. "
                        "Resolve conflicts manually in the run workspace."
                    ),
                    from_agent="runner",
                ))
                return

            if pr_mode == "manual":
                await self._emit_pr_ready_signal(target, leaf_branch)

    async def _premerge_upstream_deps(
        self, cfg: AgentConfig,
    ) -> tuple[bool, list[str]]:
        """Before an agent runs, merge each upstream depends_on branch into
        the agent's own branch so its working tree contains all upstream work.

        This applies to all agents with depends_on, not just reviewers. A
        non-reviewer like integration_dev needs the dev branches merged in
        before it can see and integrate the files.

        Returns (all_clean, conflicted_branches). When a conflict happens the
        merge is already aborted by GitWorkspace.merge(); we just collect the
        names so the caller can decide what to do.

        Skips silently for agents with no depends_on and upstream deps that
        haven't produced a branch yet (e.g., they haven't committed).
        Serialized via _git_lock so concurrent agents don't race on checkout.
        """
        if not cfg.depends_on:
            return True, []

        conflicted: list[str] = []
        async with self._git_lock:
            # Ensure the agent's own branch exists (may not yet on iter 1
            # before the agent has done any work of their own).
            # Resolve which parent branches exist so we can fork from the
            # first one's tip (pipeline-as-git-history). Branches not yet
            # created (upstream hasn't committed) are excluded here and will
            # be merged in later once they land.
            resolved_parent_branches = [
                b for dep in cfg.depends_on
                if (b := self.workspace.git.agent_branches.get(dep))
            ]
            try:
                agent_branch = self.git.agent_branch(
                    cfg.role, parent_branches=resolved_parent_branches
                )
                self.workspace.git.agent_branches[cfg.role] = agent_branch
            except Exception:
                return True, []

            for dep_role in cfg.depends_on:
                dep_branch = self.workspace.git.agent_branches.get(dep_role)
                if not dep_branch:
                    continue  # upstream hasn't committed yet
                # Skip self-merges and already-merged tips.
                if dep_branch == agent_branch:
                    continue
                ok = self.git.merge(dep_branch, into=agent_branch)
                if not ok:
                    conflicted.append(dep_branch)

        return (len(conflicted) == 0), conflicted

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

    # ── M3.5: USER_COMMENT routing to planner desk (DESIGN §38.12 §4.5) ────

    # Priority order for picking which planner absorbs a user comment.
    # Meta is excluded — it runs at proposal time, not mid-run.
    _PLANNER_ROLE_TYPES: tuple[str, ...] = (
        "tech_lead", "architect", "requirements_writer",
    )

    def _pick_planner_for_user_comment(self) -> str | None:
        """Return the role name of the highest-priority on-standby
        planner, or None if no planner is hired or all hired planners
        are currently RUNNING. Priority: tech_lead > architect >
        requirements_writer."""
        by_role_type: dict[str, str] = {}
        for cfg in self.project.agents:
            if cfg.agent_class_role_type in self._PLANNER_ROLE_TYPES:
                by_role_type.setdefault(cfg.agent_class_role_type, cfg.role)

        for role_type in self._PLANNER_ROLE_TYPES:
            role = by_role_type.get(role_type)
            if role is None:
                continue
            state = self.workspace.agent_states.get(role)
            if state is AgentState.RUNNING:
                continue
            return role
        return None

    def _on_user_comment_routing(self, sig: Signal) -> None:
        """Subscriber. On USER_COMMENT (or _PRIORITY), add a synthetic
        task to the highest-priority on-standby planner's desk so the
        planner picks it up next iteration. If no planner is hired,
        do nothing — agents fall back to today's check_user_board()
        behaviour at step boundaries."""
        if sig.type not in (
            SignalType.USER_COMMENT, SignalType.USER_COMMENT_PRIORITY,
        ):
            return

        planner_role = self._pick_planner_for_user_comment()
        if planner_role is None:
            return

        # Avoid duplicate synthetic tasks for the same comment if the
        # subscriber fires twice (e.g. during resume replay).
        synthetic_id = f"comment_{sig.id.hex[:10]}"
        if synthetic_id in self.workspace.tasks:
            return

        active_story = self.workspace.active_story.get(planner_role, "")
        from core.workspace import Task as _Task, TaskRef as _TaskRef
        refs: list[_TaskRef] = []
        if active_story:
            refs.append(_TaskRef(
                kind="story", value=active_story, note="active story",
            ))

        text_preview = (sig.summary or "").strip().replace("\n", " ")
        if len(text_preview) > 120:
            text_preview = text_preview[:117] + "..."

        task = _Task(
            task_id=synthetic_id,
            story_id=active_story,
            assigned_to=planner_role,
            created_by="user",
            created_at_iter=self.workspace.iteration,
            goal=f"respond to user comment: {text_preview}",
            acceptance_criteria=[
                "answer the user directly, decompose into tasks for "
                "downstream agents, or post HUMAN_NEEDED if you can't proceed",
            ],
            references=refs,
        )
        try:
            self.workspace.add_task(task)
        except ValueError:
            # Subscriber must never raise — broken routing shouldn't
            # take the run down. The user_board fallback still works.
            return

        # Mark the absorbed comment as seen by the planner so the
        # planner's check_user_board() doesn't double-process it.
        sig.mark_seen_by(planner_role)

        # Nudge the planner to READY if they're idle. If they're
        # already RUNNING, the scheduler will pick the new desk
        # entry up on their next iteration.
        state = self.workspace.agent_states.get(planner_role)
        if state in (None, AgentState.PENDING, AgentState.COMPLETE):
            self.workspace.agent_states[planner_role] = AgentState.READY

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

    def _on_signal_for_rejection_redispatch(self, sig: Signal) -> None:
        """Workspace subscriber (M5.C1/C2). On REJECTED, either re-dispatch
        the original author with reviewer feedback (consecutive < threshold)
        or escalate to tech_lead (consecutive >= threshold).

        Runs in parallel with _on_signal_for_escalation — they handle
        different patterns (consecutive vs. cumulative).
        """
        from core.protocol import RejectedFeedback, render_rejected_feedback  # noqa: F401 (used by type alias)

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

        threshold = self.project.consecutive_fail_threshold
        for target in reviewer_cfg.depends_on:
            attempt = self._consecutive_fails.get(target, 0) + 1
            self._consecutive_fails[target] = attempt

            if attempt < threshold:
                # Bounce back to author with reviewer context.
                reviewer_name = self._persona_name(sig.from_agent)
                detail = sig.detail or sig.summary or ""
                feedback = RejectedFeedback(
                    reviewer_role=sig.from_agent,
                    reviewer_name=reviewer_name,
                    detail=detail,
                    attempt_number=attempt,
                )
                self._pending_feedback[target] = feedback

                # Reset target state so scheduler re-spawns it.
                if target in self.workspace.completed_agents:
                    self.workspace.completed_agents.remove(target)
                self.workspace.agent_states[target] = AgentState.READY

                # Reset the reviewer back to PENDING so it re-runs once the
                # author finishes its second pass. PENDING (not READY) keeps
                # the depends_on gate in place — scheduler won't spawn the
                # reviewer until the author's re-COMPLETE lands.
                reviewer_role = sig.from_agent
                if reviewer_role in self.workspace.completed_agents:
                    self.workspace.completed_agents.remove(reviewer_role)
                self.workspace.agent_states[reviewer_role] = AgentState.PENDING

                # Cascade: any agent downstream of the author or the reviewer
                # that already completed against the first-pass output is now
                # stale — reset it to PENDING so it re-runs after the chain
                # catches up. Without this the run deadlocks at the point
                # where the first downstream completed agent sits terminal.
                self._reset_downstream_completed(target)
                self._reset_downstream_completed(reviewer_role)

                # Wake the scheduler loop.
                if self._scheduler is not None:
                    self._scheduler.notify()
            else:
                # Two-strike (or more): escalate and reset counter.
                history = self._build_rejection_history(target)
                self._consecutive_fails[target] = 0
                self._dispatch_consecutive_fail_escalation(target, history)

    def _on_signal_for_approval(self, sig: Signal) -> None:
        """Workspace subscriber (M5.C1). Resets the consecutive-fail counter
        for the reviewed role(s) when a reviewer emits APPROVED."""
        if sig.type is not SignalType.APPROVED:
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
            self._consecutive_fails.pop(target, None)

    def _on_signal_for_question_respawn(self, sig: Signal) -> None:
        """Workspace subscriber (Track 2a). When an AGENT_MESSAGE targets a
        completed agent, re-queue that agent for one answering iteration.

        Capped at 3 respawns per role per run to prevent ping-pong loops.
        """
        if sig.type is not SignalType.AGENT_MESSAGE:
            return
        detail = sig.detail or ""
        try:
            parsed = json.loads(detail)
            target = parsed.get("to", "")
        except (json.JSONDecodeError, AttributeError):
            return
        if not target:
            return
        if target not in self.workspace.completed_agents:
            return
        count = self._question_respawn_counts.get(target, 0)
        if count >= 3:
            return
        self._question_respawn_counts[target] = count + 1
        self.workspace.completed_agents.remove(target)
        self.workspace.agent_states[target] = AgentState.READY
        self._question_mode_roles.add(target)
        if self._scheduler is not None:
            self._scheduler.notify()

    def _reset_downstream_completed(self, root_role: str) -> None:
        """Walk forward through depends_on edges from root_role; for every
        agent transitively downstream that is in completed_agents, remove it
        and set its state to PENDING.

        Called by the reject-loop redispatch (M5.C1) so that when an author is
        bounced back for a second pass, the reviewer and any docs/final agents
        that already completed against the first-pass output get re-queued.
        Idempotent; does not notify the scheduler (caller is responsible).
        """
        visited: set[str] = set()
        queue: list[str] = [root_role]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            for cfg in self.project.agents:
                if current not in cfg.depends_on:
                    continue
                if cfg.role in self.workspace.completed_agents:
                    self.workspace.completed_agents.remove(cfg.role)
                    self.workspace.agent_states[cfg.role] = AgentState.PENDING
                queue.append(cfg.role)

    def _build_rejection_history(self, target_role: str) -> "list[RejectedFeedback]":
        """Reconstruct the REJECTED history for target_role from the board.
        Used to give tech_lead full context when escalating after threshold."""
        from core.protocol import RejectedFeedback

        history: list[RejectedFeedback] = []
        attempt = 0
        for sig in self.workspace.board:
            if sig.type is not SignalType.REJECTED:
                continue
            reviewer_cfg = next(
                (c for c in self.project.agents if c.role == sig.from_agent),
                None,
            )
            if reviewer_cfg is None or target_role not in reviewer_cfg.depends_on:
                continue
            attempt += 1
            name = self._persona_name(sig.from_agent)
            detail = sig.detail or sig.summary or ""
            from core.protocol import _REJECTED_FEEDBACK_MAX_DETAIL
            if len(detail) > _REJECTED_FEEDBACK_MAX_DETAIL:
                detail = detail[:_REJECTED_FEEDBACK_MAX_DETAIL] + "…(truncated)"
            history.append(RejectedFeedback(
                reviewer_role=sig.from_agent,
                reviewer_name=name,
                detail=detail,
                attempt_number=attempt,
            ))
        return history

    def _dispatch_consecutive_fail_escalation(
        self,
        rejected_role: str,
        history: "list[RejectedFeedback]",
    ) -> None:
        """Dispatch the escalation target (or HUMAN_NEEDED if absent) after
        consecutive failures on rejected_role exceed the project threshold (M5.C2).
        Prefers cfg.escalates_to[0] on the rejected role; falls back to tech_lead."""
        rejected_cfg = next(
            (c for c in self.project.agents if c.role == rejected_role), None,
        )
        escalate_to_role = (
            rejected_cfg.escalates_to[0]
            if rejected_cfg and rejected_cfg.escalates_to
            else "tech_lead"
        )
        tech_cfg = next(
            (c for c in self.project.agents if c.role == escalate_to_role),
            None,
        )

        if tech_cfg is None:
            # No tech_lead on team — pause for human.
            rendered_history = "\n\n".join(
                f"Attempt {fb.attempt_number} — {fb.reviewer_name} ({fb.reviewer_role}):\n{fb.detail}"
                for fb in history
            )
            question = (
                f"{rejected_role} has been rejected {len(history)} time(s) in a row.\n\n"
                f"{rendered_history}\n\nPlease provide guidance or resolve the blocker."
            )
            from core.workspace import HumanRequest
            self.workspace.pending_human = HumanRequest(
                from_agent="runner",
                question=question,
                context=f"consecutive_failures on {rejected_role}",
                iteration=self.workspace.iteration,
            )
            self.workspace.post_signal(Signal(
                type=SignalType.HUMAN_NEEDED,
                summary=f"consecutive_failures on {rejected_role}",
                detail=rendered_history[:500],
                from_agent="runner",
                iteration=self.workspace.iteration,
            ))
            return

        # Guard: if the cumulative-threshold handler already claimed and dispatched
        # this escalation target for this role, be a no-op.
        if rejected_role in self._escalations_fired.get(escalate_to_role, set()):
            return
        self._escalations_fired.setdefault(escalate_to_role, set()).add(rejected_role)

        async def _run_escalation_review() -> None:
            try:
                if escalate_to_role not in self.agents:
                    self._build_handler_agent(tech_cfg)
                tl_agent = self.agents[escalate_to_role]
                tl_agent.iteration = self.workspace.iteration

                if hasattr(tl_agent, "run_escalation_review"):
                    await tl_agent.run_escalation_review(
                        workspace=self.workspace,
                        rejected_role=rejected_role,
                        history=history,
                    )
                else:
                    self._dispatch_escalation(escalate_to_role, rejected_role, len(history))
            except Exception:
                logging.getLogger(__name__).exception(
                    "consecutive-fail escalation for %s crashed", rejected_role,
                )

        self.workspace.post_signal(Signal(
            type=SignalType.ESCALATION,
            summary=f"consecutive_failures: escalating {rejected_role} to {escalate_to_role}",
            detail=f"target_role={rejected_role}; consecutive_fail_count={len(history)}",
            from_agent="runner",
            iteration=self.workspace.iteration,
        ))
        try:
            task = asyncio.create_task(
                _run_escalation_review(),
                name=f"consec-fail-escalation:{rejected_role}",
            )
            self._escalation_tasks.append(task)
        except RuntimeError:
            pass

    def _rebuild_consecutive_fails_from_board(self) -> None:
        """Replay REJECTED and APPROVED from the board to restore _consecutive_fails.

        Called once when a restored_workspace is injected (M5 D4). Walks in
        chronological order applying the same increment/reset semantics as the
        live subscriber, so resumed runs have the correct consecutive count.
        """
        for sig in self.workspace.board:
            if sig.type is SignalType.REJECTED and sig.from_agent:
                reviewer_cfg = next(
                    (c for c in self.project.agents if c.role == sig.from_agent),
                    None,
                )
                if reviewer_cfg is None:
                    continue
                for target in reviewer_cfg.depends_on:
                    self._consecutive_fails[target] = self._consecutive_fails.get(target, 0) + 1
            elif sig.type is SignalType.APPROVED and sig.from_agent:
                reviewer_cfg = next(
                    (c for c in self.project.agents if c.role == sig.from_agent),
                    None,
                )
                if reviewer_cfg is None:
                    continue
                for target in reviewer_cfg.depends_on:
                    self._consecutive_fails.pop(target, None)

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

            base_ref = self._story_base_ref()

            self.workspace.stories[story_id] = StoryState(
                story_id=story_id,
                name=name,
                goal=goal,
                owner_role=owner,
                parent_story_id=parent,
                opened_at_iteration=self.workspace.iteration,
                base_ref=base_ref,
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

    def _story_base_ref(self) -> str | None:
        """Capture the current HEAD SHA in the run's git workspace.

        Used as the diff baseline for a newly-opened story so agents can
        ask "what's new for my story in this doc?" via `fs-diff` without
        any in-file markup.
        """
        gw = getattr(self, "git", None)
        if gw is None:
            return None
        try:
            return gw.head_sha()
        except Exception:
            return None

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
        # Track 1c — see comment at primary spawn site.
        agent._workspace = self.workspace
        agent._manifest = self.manifest_builder.build_for_agent(
            role=cfg.role,
            declared_local_tools=cfg.declared_local_tools,
            declared_mcp_servers=cfg.declared_mcp_servers,
        )
        agent._local_tools = self._instantiate_local_tools(cfg.role)
        agent._current_task = (
            self._continuation_task_hint() or self.project.goal
        )
        agent._continuation_brief = self._build_continuation_brief()
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
    points at a registered subclass. Falls back to BaseAgent if the type is
    an archetype label (e.g. 'specialist') rather than a concrete registry entry."""
    if cfg.agent_class_role_type:
        from agents.registry import has, get
        if has(cfg.agent_class_role_type):
            cls = get(cfg.agent_class_role_type)
            return cls(role=cfg.role)
    return BaseAgent(role=cfg.role)


# Forward-declared for the import order.
class QuipEngine:  # noqa: F811 — real class lives in core/quip_engine.py
    def set_voices(self, voices: list) -> None: ...
    async def on_run_complete(self, workspace: Workspace, result: SchedulerResult) -> None: ...
    async def on_agent_complete(self, role: str, workspace: Workspace) -> None: ...
    async def on_review_verdict(self, role: str, verdict: str, workspace: Workspace) -> None: ...
