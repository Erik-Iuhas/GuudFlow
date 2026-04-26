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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from agents.base import BaseAgent, ToolNotAvailable
from agents.tools.base import BaseTool
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
from agents.tools.git import GitDiff, GitLog, GitRead, GitStatus
from core.context_manager import ContextManager
from core.credentials_broker import CredentialsBroker
from core.file_workspace import FileWorkspace
from core.git_workspace import GitWorkspace
from core.llm import LLMClient, LLMMessage, ToolCall
from core.loop_guard import LoopGuard, LoopGuardConfig
from core.repo_map import build as build_repo_map, update_incremental
from core.scheduler import AgentSpec, Scheduler, SchedulerResult, SchedulerStatus
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


class AgentRunner:
    """One run. Owns the workspace, scheduler, agents, broker, LLM."""

    def __init__(
        self,
        project: ProjectConfig,
        run_id: str,
        run_root: Path | str,
        llm: LLMClient,
        broker: CredentialsBroker | None = None,
        screenshot_callback: ScreenshotCallback | None = None,
        memory_writer: BaseAgent | None = None,
        quip_engine: "QuipEngine | None" = None,
        agent_factory: "AgentFactory | None" = None,
        max_wall_seconds: float | None = 1800.0,  # 30 min run-level budget
        docker_manager: "DockerManager | None" = None,
        rubric_scorer: BaseAgent | None = None,
        training_scenarios_root: Path | None = None,
    ):
        self.project = project
        self.run_id = run_id
        self.run_root = Path(run_root)
        self.llm = llm
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

        # Escalation tracking (DESIGN §23, Phase C.3). _rejection_counts
        # accumulates REJECTED signals per upstream role; once a role
        # crosses an escalation threshold, the corresponding handler in
        # `project.escalation_thresholds` (e.g. tech_lead) is dispatched.
        # `_escalations_fired[handler] = {roles already escalated for}`
        # so each (handler, role) pair fires at most once per run.
        self._rejection_counts: dict[str, int] = {}
        self._escalations_fired: dict[str, set[str]] = {}
        self._escalation_tasks: list[asyncio.Task] = []

        # Set up workspace state.
        self.workspace = Workspace(
            run_id=run_id,
            goal=project.goal,
            max_iterations=project.max_iterations,
        )
        # Subscribe to the board so we can count REJECTED signals and
        # fire escalations when thresholds are crossed.
        self.workspace.subscribe(self._on_signal_for_escalation)
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
            GitDiff, GitLog, GitRead, GitStatus,
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
        for cls in (
            CodeExec, CodeOutline,
            DataCsv, DataJson, DataXml, DataYaml,
            DocHtml, DocMarkdown,
            MathEval,
            TextDiff, TextEstimateTokens,
            TimeDiff, TimeNow,
            WebFetch, WebSearch,
        ):
            self._local_registry[cls.name] = LocalToolEntry(
                name=cls.name,
                description=cls.description,
                input_schema=getattr(cls, "input_schema", {}) or {},
            )

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
            GitDiff.name: GitDiff(self.git),
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
            agent.system_prompt = cfg.system_prompt
            agent.declared_local_tools = list(cfg.declared_local_tools)
            agent.declared_mcp_servers = list(cfg.declared_mcp_servers)
            # Inject runtime handles.
            agent._llm = self.llm
            agent._model = TIER_TO_MODEL.get(cfg.model_tier, TIER_TO_MODEL["solid"])
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

    # ── Main run loop ───────────────────────────────────────────────────────

    async def run(self) -> SchedulerResult:
        self._build_agents()

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
        result = await scheduler.run()

        # Wait for any in-flight escalation handlers (Phase C.3) so
        # their signals land before memory_writer / quip_engine fire.
        await self._await_escalation_tasks()

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

            try:
                artifact_text = await self._drive_agent_run(agent, cfg)
            except Exception as e:
                workspace.agent_states[cfg.role] = AgentState.FAILED
                await agent.signal(
                    workspace, SignalType.FAILED,
                    summary=f"{cfg.role} crashed",
                    detail=str(e),
                )
                raise

            # Auto-commit any uncommitted changes on the agent's branch.
            commit_hash = await self._auto_commit(cfg.role, workspace)

            # Record the artifact.
            workspace.artifacts[cfg.role] = Artifact(
                agent_role=cfg.role,
                type="text",
                content=artifact_text or "",
                iteration=workspace.iteration,
            )
            workspace.completed_agents.append(cfg.role)
            workspace.agent_states[cfg.role] = AgentState.COMPLETE

            # Reviewer signal type: parse the artifact text. Anything
            # starting with "REJECTED" → REJECTED signal (no merge);
            # otherwise APPROVED. Non-reviewers always get COMPLETE.
            if cfg.is_reviewer:
                if (artifact_text or "").lstrip().upper().startswith("REJECTED"):
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
            # On APPROVED only: merge upstream agent branches into main.
            # REJECTED leaves the branches alone for the next iteration.
            if cfg.is_reviewer and final_signal is SignalType.APPROVED:
                for dep_role in cfg.depends_on:
                    branch = workspace.git.agent_branches.get(dep_role)
                    if branch:
                        ok = self.git.merge(branch, into="main")
                        if ok:
                            workspace.git.merged.append(branch)

        return runner

    async def _drive_agent_run(self, agent: BaseAgent, cfg: AgentConfig) -> str:
        """Run the agent's tool-use loop.

        For agents that override `run()`, defer to it. Otherwise, drive a
        generic LLM tool-use loop until the model returns end_turn.
        """
        # Subclasses can implement run() directly. Detect by checking
        # whether run is the BaseAgent default (which raises).
        cls_run = type(agent).run
        if cls_run is not BaseAgent.run:
            return await agent.run(self.workspace)

        # Generic prompt-defined agent: drive a tool-use loop.
        return await self._generic_tool_use_loop(agent, cfg)

    async def _generic_tool_use_loop(self, agent: BaseAgent, cfg: AgentConfig) -> str:
        task_prompt = (
            f"Project goal: {self.project.goal}\n\n"
            f"Your role: {cfg.role}. {cfg.description}\n\n"
            f"Use the available tools to complete your work, then return a final "
            f"response describing what you did."
        )
        messages: list[LLMMessage] = [LLMMessage(role="user", content=task_prompt)]
        guard = LoopGuard(cfg.loop_guard)
        nudges: list[str] = []  # accumulated for the next user turn

        for iteration in range(cfg.max_tool_iterations):
            if guard.deadline_exceeded():
                return f"(loop guard: wall-clock budget exhausted after {guard.elapsed_seconds:.0f}s)"

            response = await agent.llm(messages)

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
                for tc in response.tool_calls:
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

            # end_turn or anything else — return the text.
            return response.text or ""

        return "(max tool iterations reached)"

    async def _auto_commit(self, role: str, workspace: Workspace) -> str | None:
        """Stage and commit any uncommitted changes on the agent's branch."""
        try:
            branch = self.git.agent_branch(role)
            workspace.git.agent_branches[role] = branch
        except Exception:
            return None

        status = self.git.status()
        if status.clean:
            return None

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
        agent.system_prompt = cfg.system_prompt
        agent.declared_local_tools = list(cfg.declared_local_tools)
        agent.declared_mcp_servers = list(cfg.declared_mcp_servers)
        agent._llm = self.llm
        agent._model = TIER_TO_MODEL.get(cfg.model_tier, TIER_TO_MODEL["solid"])
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
    async def on_run_complete(self, workspace: Workspace, result: SchedulerResult) -> None:
        ...
