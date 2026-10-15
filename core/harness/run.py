"""Run a project on the opencode harness (plan §3, §4.1).

`OpencodeRun` reuses what `core.runner.AgentRunner` already set up for the run
(workspace + board persisters, git repo, quip engine, run directory) and
replaces only the agent loop: each role is one opencode server working in its
own git clone, scheduled by the unchanged `core.scheduler.Scheduler`.

Lifecycle of one spawn (`execute_turn`):
  sync clone with main → ensure server → open result slot → post STARTED and
  CAPABILITIES → drive the turn → (one retry if no result; one wrap-up if the
  turn timed out) → collect the branch (a merge conflict goes back to the agent
  in the same session) → post AGENT_RESULT + COMPLETE / BLOCKED / FAILED.

Git never dead-ends a role: a conflict found when syncing with main is handed
to the agent as its first job, a rejected branch is set aside (reset to main,
patch kept in `.guudflow/set_aside/`) and the next brief says so.
"""

from __future__ import annotations

import asyncio
import json
import re
import logging
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from core.contracts import Contract, ContractError, contracts_for_team, load_for_project, load_library, render_for_brief
from core.crew import Member, crew_names, label as crew_label, resolve_recipient, team_section
from core.harness.brief import (
    CONFLICT_HOWTO as _CONFLICT_HOWTO,
    BriefInputs,
    ReviewIssue,
    UpstreamResult,
    NOTEBOOK_LIMIT,
    build_brief,
    conflict_brief,
    notebook_brief,
    wrap_untrusted,
    retry_brief,
    wrap_up_brief,
)
from core.harness.capabilities import (
    Capabilities,
    CompiledCapabilities,
    compile_capabilities,
    shell_summary,
    defaults_for,
    gate_vision,
    infer_role_type,
    resolve_mcp_grants,
)
from core.harness.config_gen import ProviderBinding, agent_entry, build_config
from core.harness.event_bridge import EventBridge
from core.harness.git_roles import CollectResult, RoleClones
from core.harness.handoff import (changed_exports, host_facts, latest_per_card, render_handoffs,
                                  stored_handoffs)
from core.harness.session_policy import SessionLimits, SessionState, keep_session
from core.harness import mcp_proxy
from core.mcp_registry import McpRegistry
from core.harness.mcp_server import McpIdentity, McpServer, McpTool, RunningHttpServer, ToolResult, serve_app
from core.harness.opencode_client import OpencodeClient
from core.harness import mcp_browser, mcp_tools, mcp_visual, mcp_web
from core.harness.approvals import PermissionGate
from core.harness.artifacts import ArtifactRegistry
from core.harness.persona_prompt import PromptLayers, build_agent_prompt
from core.harness.opencode_runner import EventHook, PermissionHook, PermissionReply, TurnStats, drive_turn
from core.harness.result_inbox import ResultInbox
from core.harness.results import AgentResult, ReviewResult
from core.harness.server import OpencodeServer
from core import project_runs
from core.backlog import BacklogItem
from core.harness.spec_brief import SpecBrief
from core.harness.task_flow import TaskFlow, TurnPlan, backlog_text
from core.harness.detectors import DetectorConfig, RunView
from core.harness.overseer import OverseerCaps, OverseerResult, ProposeTeamChange
from core.harness.overseer_flow import OverseerFlow
from core.harness.retrospective import load_prior_carry_forward
from core.harness.visual_review import ImageBudget, capped
from core.harness.tasks import PlanResult, is_owner_check
from core.harness.tool_context import RunToolContext
from core.llm.registry import ModelInfo, ModelRegistry
from core.scheduler import AgentSpec, Scheduler, SchedulerResult
from core.signals import Signal, SignalType
from core.workspace import AgentState, Artifact, Workspace
from gateway.app import Gateway, Price, Upstream

if TYPE_CHECKING:
    from core.runner import AgentConfig, AgentRunner
    from worker.sandbox_manager import RunNetwork, Sandbox, SandboxManager

log = logging.getLogger(__name__)

BindingFactory = Callable[["AgentConfig"], ProviderBinding]
RolePermissionHook = Callable[[str, dict[str, Any]], Awaitable[PermissionReply]]
SandboxMode = Literal["auto", "host", "docker"]
SANDBOX_ENV = "GUUDFLOW_SANDBOX"


def sandbox_from_env() -> SandboxMode:
    """Where agents run by default: $GUUDFLOW_SANDBOX (auto | docker | host), else auto.

    `host` runs agents with no container, for machines without Docker (trusted work only;
    refused in production). `auto` uses Docker when the daemon is reachable.
    """
    value = os.environ.get(SANDBOX_ENV, "").strip().lower() or "auto"
    if value not in ("auto", "docker", "host"):
        log.warning("%s=%r is not auto, docker or host; using auto", SANDBOX_ENV, value)
        return "auto"
    return value  # type: ignore[return-value]

DEFAULT_PROVIDER_URLS = {"anthropic": "https://api.anthropic.com/v1"}


# Host secrets the web tools may read (search API keys); nothing else is exposed.
_TOOL_SECRETS = frozenset({"BRAVE_API_KEY", "TAVILY_API_KEY"})


DESK_PROPOSED_DIR = "proposed"   # persona_desks/<persona>/proposed/: from the meta proposal
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_SLUG_WORD = re.compile(r"[^a-z_-]")


def _comment_target(sig: Signal) -> str | None:
    """The one role a human comment is for (None: everyone)."""
    try:
        detail = json.loads(sig.detail or "{}")
    except ValueError:
        return None
    to = detail.get("to") if isinstance(detail, dict) else None
    return str(to) if to else None


def _config_key(cfg: "AgentConfig") -> str:
    """What a running server was configured from; a change means a restart."""
    return repr((cfg.model_tier, getattr(cfg, "model_id", None), getattr(cfg, "effort", ""),
                 cfg.description,
                 tuple(getattr(cfg, "declared_local_tools", None) or ()), cfg.system_prompt))


class TurnFailed(RuntimeError):
    """A role's turn ended without an acceptable outcome. The scheduler marks it failed."""


@dataclass
class HarnessSettings:
    """Knobs for an opencode run. Defaults suit a real run; tests override them."""

    production: bool = field(default_factory=lambda: os.environ.get("GUUDFLOW_ENV") == "production")
    # A turn is aborted after `turn_stall_timeout` seconds with no activity (plan §11.6);
    # `turn_timeout` is an optional absolute cap per turn, off by default.
    turn_timeout: float | None = None
    turn_stall_timeout: float = 600.0
    followup_timeout: float = 300.0   # retry / wrap-up / conflict follow-ups in the same session
    steps: int = 60
    binding_factory: BindingFactory | None = None
    extra_providers: dict[str, dict[str, Any]] | None = None
    # None → the run's PermissionGate (approval cards in Studio, allow-list, sticky
    # "always"). Tests pass a hook taking (role, ask).
    on_permission: RolePermissionHook | None = None
    on_event: Callable[[str, dict[str, Any]], None] | None = None
    keep_servers: bool = False  # tests: keep servers between spawns of the same run
    # A role keeps its opencode server between cards. Each card starts a fresh session and
    # reads handoffs from earlier cards (plan §11.1); the session is kept only when the next
    # card continues the last one, the cache is still warm and the context is small
    # (core/harness/session_policy.py). Also rotated after a failed or timed-out turn, when
    # work is set aside, or when the config changes. The overseer and reviewers start fresh.
    session_reuse: bool = True
    max_turns_per_session: int = 8
    session_keep_idle: float = 240.0
    session_keep_max_context: int = 60_000
    budget_warn_fraction: float = 0.8
    # At the end of a run each role that worked writes its notebook for the next session.
    notebooks: bool = True
    notebook_timeout: float = 120.0
    max_parallel: int = 3       # concurrent roles (each sandbox costs ~1 GB, plan §5.6)
    max_spawns_per_role: int = 12
    # "docker": every role in a hardened container (plan §5.3); "host": host_dev for
    # trusted local demos only (refused in production); "auto": docker when available.
    sandbox: SandboxMode = field(default_factory=sandbox_from_env)
    # Host-side tool overrides for tests (see each module's `tools()` signature).
    tool_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Task board (plan §4.6): "auto" drives the run by cards when the team has a
    # planner; "off" keeps plain role mode.
    task_board: Literal["auto", "off"] = "auto"
    # The overseer (plan §11): "auto" adds one to task-board runs that have none;
    # "on" always; "off" never (escalations then just sit on the board).
    overseer: Literal["auto", "on", "off"] = "auto"
    detectors: DetectorConfig = field(default_factory=DetectorConfig)
    overseer_caps: OverseerCaps = field(default_factory=OverseerCaps)
    watchdog_interval: float = 30.0


@dataclass
class _Role:
    cfg: "AgentConfig"
    role_type: str
    requested: Capabilities
    compiled: CompiledCapabilities
    binding: ProviderBinding
    server: OpencodeServer | None = None
    sandbox: "Sandbox | None" = None
    client: OpencodeClient | None = None
    token: str = ""
    prompt: PromptLayers | None = None  # layers of the prompt this role's server started with
    session_id: str | None = None       # the opencode session reused from card to card
    session_turns: int = 0
    config_key: str = ""                 # the config the server started with
    turns_done: int = 0                  # turns with an accepted result in this run
    last_turn_end: float = 0.0           # monotonic time the last turn in the session ended
    last_context: int = 0                # context size of the session's last model call
    last_card: str | None = None         # the card of the session's last turn


class OpencodeRun:
    """One project run on the opencode harness."""

    def __init__(self, runner: "AgentRunner", settings: HarnessSettings | None = None) -> None:
        self.runner = runner
        self.settings = settings or HarnessSettings()
        self.workspace: Workspace = runner.workspace
        self.run_id = runner.run_id
        self.mcp = McpServer()
        self.inbox = ResultInbox()
        self.mcp.register(self.inbox.tool())
        self.mcp.register(self._post_note_tool())
        self.mcp.register(self._notebook_tool())
        for tool in self._git_tools():
            self.mcp.register(tool)
        self.clones = RoleClones(runner.git, runner.run_root / "roles")
        allow = runner.project.approval_gates.get("tool_permission_allow") or {}
        self.gate = PermissionGate(self.workspace, allow_list={
            str(k): [str(p) for p in v] for k, v in allow.items() if isinstance(v, list)})
        self.results: dict[str, BaseModel] = {}
        self.flow: TaskFlow | None = None
        self.overseer: OverseerFlow | None = None
        self.agents: list["AgentConfig"] = list(runner.project.agents)
        self._started = 0.0
        self._carry_text = ""
        self._notebooks: dict[str, str] = {}
        # Per role: (kept the session, why, context tokens carried in) for the current turn.
        self._session_choice: dict[str, tuple[bool, str, int]] = {}
        self._turn_seq = 0
        self._budget_warned: float | None = None
        self._limits_seen: dict[str, str] = {}      # role -> last RUN_LIMITS_CHANGED briefed
        self._desks: dict[str, Any] = {}           # role -> DeskIndex (agents.persona_desk)
        self._desk_seen: dict[str, set[str]] = {}  # role -> desk notes already in its session
        self.images = ImageBudget()
        self._delivered_notes: set[str] = set()
        # Per role: what the next brief must say about git (set-aside work, conflicts).
        self._git_notes: dict[str, list[str]] = {}
        self.backlog: list[BacklogItem] = []
        self._roles: dict[str, _Role] = {}
        # role -> worker name (core/crew.py), set when the run starts.
        self.names: dict[str, str] = {}
        self.contracts: dict[str, Contract] = {}
        self._http: RunningHttpServer | None = None
        self._gw_http: RunningHttpServer | None = None
        self._registry: ModelRegistry | None = runner._model_registry  # noqa: SLF001
        self.mode: Literal["host", "docker"] = "host"
        self.sandboxes: "SandboxManager | None" = None
        self.network: "RunNetwork | None" = None
        self.gateway: Gateway | None = None
        self.artifacts = ArtifactRegistry(runner.run_root, self._artifact_post)
        # Third-party MCP servers (docs/MCP_PLAN.md): registry offers are read once per run.
        data_root = self._data_root()
        self._mcp_registry: McpRegistry | None = McpRegistry(data_root) if data_root else None
        self._mcp_offers: dict[str, frozenset[str]] = (
            self._mcp_registry.offers() if self._mcp_registry else {})
        self.mcp_proxy: mcp_proxy.McpProxy | None = None
        gates = runner.project.approval_gates
        self.tool_ctx = RunToolContext(
            run_id=self.run_id, workspace=self.workspace, run_root=runner.run_root,
            clone_for=self.clones.path, sandbox_host_for=self._sandbox_host,
            tool_dir_for=self.clones.tool_dir,
            settings={"package_allow": gates.get("package_allow") or {},
                      "web": gates.get("web") or {}},
            secret=lambda name: os.environ.get(name) if name in _TOOL_SECRETS else None)

    # ── lifecycle ──────────────────────────────────────────────────────────
    async def run(self) -> SchedulerResult:
        """Run every role through the scheduler and tear everything down after."""
        self.mode = await self._resolve_mode()
        self._started = time.time()
        # Authors first, so a reviewer can be given a different model from the work it
        # reviews (model diversity); the role order stays as configured.
        prepared: dict[str, _Role] = {}
        for cfg in sorted(self.agents, key=lambda c: bool(c.review_of)):
            prepared[cfg.role] = self._roles[cfg.role] = self._prepare_role(cfg)
        self._roles = {cfg.role: prepared[cfg.role] for cfg in self.agents}
        self.contracts = self._load_contracts()
        if self.settings.task_board == "auto":
            self.flow = TaskFlow.build(
                self.agents, {n: r.role_type for n, r in self._roles.items()},
                self.workspace, self._scheduler,
                post=lambda kind, role, summary, detail: self._post(kind, role, summary, detail),
                escalate=lambda role, summary, detail: self._escalate(role, summary, detail),
                planner_role=next((r for r, c in self.contracts.items()
                                   if c.may_change("session_plan")), None))
        self._setup_overseer()
        self._name_the_team()
        carried = self._load_carry_forward()
        self._notebooks = project_runs.load_notebooks(
            self._data_root(), self.runner.project.project_id, [c.role for c in self.agents])
        self._write_desk_seeds()
        # The project backlog items this run took (plan §13).
        self.backlog = project_runs.selection(self.runner.run_root)
        flow = self.flow
        if flow is not None:
            flow.backlog = list(self.backlog)
            flow.size_budget = self._size_budget()
            flow.board.review_min_size = str(
                self.runner.project.approval_gates.get("review_min_size") or "S")
            # The spec and the start mode (plan §4); None keeps briefs as they were.
            flow.spec = SpecBrief.load(self._data_root(), self.runner.project.project_id,
                                       getattr(self.runner.project, "start_mode", None))
            if flow.spec is not None:
                flow.backlog = self.backlog = flow.spec.resolve(self.backlog)
                methods = flow.spec.check_methods()
                flow.board.owner_check = lambda line, m=methods: is_owner_check(line, m)
            # The design and who owns it (role contracts plan §2.2, §4.2).
            self._setup_design(flow)
            flow.tool_dir_for = self.clones.tool_dir
            flow.ref_roots = [self.runner.run_root / "inputs", self.runner.git.working_dir]
            flow.run_root = self.runner.run_root
            flow.names = self.names
            flow.facts_for = lambda base, head: host_facts(
                self.clones.main_numstat(base, head),
                lambda path, rev=head: self.clones.main_file(rev, path))
            flow.exports_for = lambda base, head: changed_exports(
                self.clones.main_numstat(base, head),
                lambda path, rev=head: self.clones.main_file(rev, path),
                lambda path, rev=base: self.clones.main_file(rev, path))
            flow.vision_for = lambda r: (r in self._roles and
                                         self._roles[r].compiled.capabilities.vision == "on")
            self._unsubscribe_flow = self.workspace.subscribe(flow.on_signal)
        try:
            await self._start_services()
            specs = [AgentSpec(role=cfg.role, runner=self._runner_for(cfg),
                               depends_on=(flow.depends_on(cfg) if flow else list(cfg.depends_on))
                               if not self._is_overseer(cfg.role) else [],
                               max_review_rounds=cfg.max_review_rounds,
                               has_work=(lambda r=cfg.role: flow.has_work(r))
                               if flow and cfg.role in flow.card_roles else None)
                     for cfg in self.agents]
            if self.overseer is not None:
                # Event-driven: complete until a stall re-arms it (plan §11.1).
                self._mark_complete(self.overseer.overseer_role)
            if flow is not None and carried:
                # Continuation with open cards: pick up where the last run stopped
                # instead of re-planning from scratch (§11.4).
                self._mark_complete(flow.planner)
                self._post(SignalType.COMPLETE, flow.planner,
                           f"{flow.planner} complete: carried {carried} open card(s) forward",
                           None)
            scheduler = Scheduler(specs, self.workspace, scope="pipeline",
                                  max_iterations=self.settings.max_spawns_per_role,
                                  transition_rules=False,
                                  max_parallel=self.settings.max_parallel,
                                  watchdog=self._watchdog,
                                  watchdog_interval=self.settings.watchdog_interval)
            scheduler.drain_filter = self._may_start_while_draining
            self.runner._scheduler = scheduler  # noqa: SLF001 — lets force_stop() reach it
            if self.flow is not None:
                # Humans edit the board through the card API (S34): re-arm roles
                # that got a card back and let the scheduler look again.
                self.workspace.board_listeners.append(self.flow.settle)
            return await scheduler.run()
        finally:
            if self.flow is not None and self.flow.settle in self.workspace.board_listeners:
                self.workspace.board_listeners.remove(self.flow.settle)
            unsubscribe = getattr(self, "_unsubscribe_flow", None)
            if unsubscribe is not None:
                unsubscribe()
            await self.shutdown()

    def _load_contracts(self) -> dict[str, Contract]:
        """role → its contract (the library with the project's overrides; plan §2)."""
        try:
            library = load_for_project(self._data_root(), self.runner.project.project_id)
        except ContractError as e:
            log.warning("contracts: project overrides ignored: %s", e)
            library = load_library()
        return contracts_for_team(
            [(c.role, self._roles[c.role].role_type, c.contract or None) for c in self.agents],
            library)

    def _setup_design(self, flow: TaskFlow) -> None:
        """Give the task flow the project's design, and say whether the planner is a tech
        lead (its contract owns the design), whether its design changes apply at once (no
        human in the Lead seat) and whether the project is being adopted (plan §8)."""
        from core.design import Design
        from core.pm_prompts import Seats
        project = self.runner.project
        planner = self.contracts.get(flow.planner)
        flow.designer = bool(planner and planner.may_change("design"))
        flow.data_root, flow.project_id = self._data_root(), project.project_id
        flow.run_id = self.runner.run_id
        flow.design = Design.load(flow.data_root, flow.project_id)
        flow.design_auto = not Seats.from_project({"seats": getattr(project, "seats", {})}).lead
        flow.adopt = getattr(project, "start_mode", None) == "adopt"
        # The knowledge base (plan §4.3): facts generated from main (built now if the project
        # has none yet, e.g. when it is being adopted) and the last session's notebooks.
        from core.knowledge import build_facts, load_facts
        facts = load_facts(flow.data_root, flow.project_id)
        if facts is None and flow.data_root is not None and flow.project_id:
            try:
                facts = build_facts(flow.data_root, flow.project_id, check_command=str(
                    (project.approval_gates or {}).get("check_command") or ""))
            except Exception:  # noqa: BLE001 — facts are context, never a failure
                log.exception("building the knowledge facts failed")
        flow.facts = facts
        if flow.designer:
            flow.notebooks = dict(self._notebooks)

    def _contract_text(self, role: str) -> str | None:
        """The role's "Your role contract" section, with handoff targets named."""
        contract = self.contracts.get(role)
        if contract is None:
            return None
        by_contract: dict[str, list[str]] = {}
        for r, c in self.contracts.items():
            by_contract.setdefault(c.id, []).append(r)
        return render_for_brief(contract, by_contract, self.names)

    def _name_the_team(self) -> None:
        """Give every role a worker name (the overseer too) and tell the board who is who."""
        known = getattr(self.runner, "crew", {}) or {}
        self.names = crew_names(
            self.runner.project.project_id, [c.role for c in self.agents],
            explicit={c.role: known.get(c.role) or c.name for c in self.agents})
        members = [{"role": c.role, "name": self.names[c.role],
                    "role_type": self._roles[c.role].role_type,
                    "contract": self.contracts[c.role].id if c.role in self.contracts else None}
                   for c in self.agents]
        self._post(SignalType.TEAM, "runner",
                   "the team: " + ", ".join(crew_label(m["name"], m["role"]) for m in members),
                   json.dumps({"members": members}))

    def _label(self, role: str) -> str:
        """"Maya (builder)" for a role on this team."""
        return crew_label(self.names.get(role, role), role)

    def _setup_overseer(self) -> None:
        """Add the overseer role when the settings call for one (plan §11.1)."""
        mode = self.settings.overseer
        if mode == "off" or (mode == "auto" and self.flow is None):
            from core.harness import run_console
            run_console.note(self.runner.run_root, "runner", "no overseer on this run: " + (
                "the overseer setting is off" if mode == "off" else
                "the team has no role that plans sessions, so the run uses plain roles and "
                "the overseer (which watches task cards) is not started"), "warning")
            return
        name = next((n for n, r in self._roles.items() if r.role_type == "overseer"), None)
        if name is None:
            from core.runner import AgentConfig
            name = "overseer" if "overseer" not in self._roles else "run_overseer"
            cfg = AgentConfig(role=name, role_type="overseer", model_tier="heavy",
                              description=("Watch the whole run. When woken with a situation "
                                           "report, decide the smallest intervention that gets "
                                           "the work moving again. You change no files."))
            self.agents.append(cfg)
            self._roles[name] = self._prepare_role(cfg)
        self.overseer = OverseerFlow(
            workspace=self.workspace, overseer_role=name,
            roles={n: r.role_type for n, r in self._roles.items()},
            board=self.flow.board if self.flow else None, scheduler=self._scheduler,
            post=lambda kind, role, summary, detail: self._post(kind, role, summary, detail),
            clones=self.clones, main_dir=self.runner.git.working_dir,
            caps=self.settings.overseer_caps, detector_cfg=self.settings.detectors,
            on_team_change=self._draft_team_change)

    def _draft_team_change(self, iv: ProposeTeamChange) -> None:
        """Also put the overseer's team proposal in the project's open draft (plan §10.2).

        Best effort: the in-run approval request is the flow that matters, so a change that
        is not a valid team op (its shape is free-form) or an unreadable work.db is logged
        and skipped.
        """
        data_root, project_id = self._data_root(), self.runner.project.project_id
        if data_root is None or not project_id:
            return
        from core.plan_drafts import PlanDraftError, PlanDrafts
        change = {"reason": iv.rationale, **iv.change}
        try:
            PlanDrafts(data_root, project_id).add_team_change(change, by="overseer")
        except (PlanDraftError, OSError) as e:
            log.warning("overseer team change not added to the draft: %s", e)

    def _load_carry_forward(self) -> int:
        """Read the carry-forward of the run(s) this one continues; returns cards carried.

        Its text goes into every brief, so files seeded from the previous run are
        attributed ("previous run, commit X") and nobody distrusts or redoes them.
        """
        docs = load_prior_carry_forward(self.runner.run_root)
        if not docs:
            # An ongoing project: brief everyone on where the last accepted run left
            # the code. Its open work lives on the backlog, not on this board.
            data_root = self._data_root()
            if data_root is not None and self.runner.project.project_id:
                self._carry_text = project_runs.last_accepted_text(
                    data_root, self.runner.project.project_id)
            return 0
        self._carry_text = "\n\n".join(str(d.get("text") or "") for d in docs).strip()
        if self.flow is None:
            return 0
        owners = self.flow.owners()
        carried = 0
        for doc in docs:
            for old in doc.get("open_cards") or []:
                owner = old.get("owner") if old.get("owner") in owners else (owners or [""])[0]
                acceptance = [str(a) for a in old.get("acceptance") or []] or [
                    str(old.get("title") or "finish the carried card")]
                card = self.flow.board.add_card(
                    owner=owner, title=str(old.get("title") or "carried card")[:120],
                    acceptance=acceptance, created_by="carry_forward",
                    kind=str(old.get("kind") or "code"),
                    serves=[str(x) for x in old.get("serves") or []])
                for entry in old.get("thread") or []:
                    if isinstance(entry, dict) and entry.get("kind") and entry.get("text"):
                        card.thread.append({**entry, "task_id": card.task_id})
                self.flow.board.note(card.task_id, "note", "carry_forward",
                                     f"carried from run {doc.get('run_id')} "
                                     f"(card {old.get('task_id')}, was {old.get('column')})")
                carried += 1
        return carried

    def _size_budget(self) -> tuple[float | None, float | None]:
        """(session budget, measured average finished-card cost) for the planner's sizing."""
        raw = self.runner.project.approval_gates.get("token_budget_usd")
        budget = float(raw) if isinstance(raw, (int, float)) and raw else None
        data_root, project_id = self._data_root(), self.runner.project.project_id
        avg = (project_runs.average_card_cost(data_root, project_id)
               if data_root is not None and project_id else None)
        return budget, avg

    def _data_root(self) -> Path | None:
        root = self.runner.run_root
        return root.parent.parent if root.parent.name == "runs" else None

    def _is_overseer(self, role: str) -> bool:
        return self.overseer is not None and role == self.overseer.overseer_role

    def _run_view(self) -> RunView:
        scheduler = self._scheduler()
        budget = self.runner.project.approval_gates.get("token_budget_usd")
        return RunView(
            signals=self.workspace.board,
            cards=self.flow.board.cards() if self.flow else [],
            role_states={r: (s.value if isinstance(s, AgentState) else str(s))
                         for r, s in self.workspace.agent_states.items()
                         if not self._is_overseer(r)},
            running=[r for r in self._roles if scheduler.is_running(r)],
            spent_usd=self.gateway.spend(self.run_id) if self.gateway else 0.0,
            budget_usd=float(budget) if isinstance(budget, (int, float)) else None,
            started_at=self._started,
            deadline_at=self.workspace.deadline_at,
            deadline_mode=self.workspace.deadline_mode)

    def _watchdog(self) -> None:
        """Scheduler hook: the budget warning, then the stall detectors (wake the overseer
        when needed)."""
        self._budget_watch()
        if self.overseer is None:
            return
        name = self.overseer.overseer_role
        if self.overseer.check(self._run_view()) and name in self.workspace.completed_agents:
            self._scheduler().rearm(name)

    def _mark_complete(self, name: str) -> None:
        if name not in self.workspace.completed_agents:
            self.workspace.completed_agents.append(name)
        self.workspace.agent_states[name] = AgentState.COMPLETE

    async def shutdown(self) -> None:
        """Stop every opencode server/sandbox, the MCP server and gateway; revoke tokens."""
        await self._write_notebooks()
        # One sandbox that fails to stop must not skip revoking the run's
        # credentials below, nor fail a run whose work is already done.
        for role in self._roles.values():
            try:
                await self._stop_server(role)
            except Exception:  # noqa: BLE001 - cleanup; logged, never silent
                log.exception("could not stop the sandbox for %s in %s", role.cfg.role, self.run_id)
        self.mcp.revoke_run(self.run_id)
        if self.mcp_proxy is not None:
            try:
                await self.mcp_proxy.aclose()
            except Exception:  # noqa: BLE001 - cleanup; logged, never silent
                log.exception("could not close third-party MCP connections for %s", self.run_id)
            self.mcp_proxy = None
        self.inbox.close_run(self.run_id)
        if self.gateway is not None:
            self.gateway.revoke_run(self.run_id)
        for server in (self._http, self._gw_http):
            if server is not None:
                await server.stop()
        self._http = self._gw_http = None
        if self.gateway is not None:
            await self.gateway.aclose()
        if self.sandboxes is not None and self.network is not None:
            await asyncio.to_thread(self.sandboxes.remove_host_port_rules, self.network)
            await self.sandboxes.stop_run(self.run_id)
            self.network = None

    async def _resolve_mode(self) -> Literal["host", "docker"]:
        """Pick host or docker. Production never runs agents on the host."""
        wanted = self.settings.sandbox
        if wanted in ("auto", "docker"):
            from worker.sandbox_manager import SandboxManager
            if SandboxManager.available():
                self.sandboxes = SandboxManager()
                await self.sandboxes.ensure_image()
                return "docker"
            if wanted == "docker" or self.settings.production:
                raise TurnFailed("Docker is required for sandboxed agents but is not available")
        if self.settings.production:
            raise TurnFailed("sandbox 'host_dev' is refused in production")
        return "host"

    async def _start_services(self) -> None:
        """MCP server + LLM gateway, bound where the agents can reach them (plan §5.4)."""
        bind = "127.0.0.1"
        if self.mode == "docker":
            assert self.sandboxes is not None
            self.network = await self.sandboxes.create_run_network(self.run_id)
            bind = self.network.gateway_ip
        for tool in self._host_tools():
            self.mcp.register(tool)
        for tool in self._third_party_tools():
            self.mcp.register(tool)
        self._http = await serve_app(self.mcp.app(), host=bind)
        self.gateway = Gateway(self._upstreams(), self._price,
                               cost_log=self.runner.run_root / "llm_costs.jsonl")
        budget = self.runner.project.approval_gates.get("token_budget_usd")
        if isinstance(budget, (int, float)):
            self.gateway.set_run_budget(self.run_id, float(budget))
        self.workspace.controls["set_budget"] = self._set_budget
        self.workspace.controls["spend"] = lambda: self.gateway.spend(self.run_id)
        self.workspace.controls["wake"] = lambda: self._scheduler().notify()
        self._gw_http = await serve_app(self.gateway.app(), host=bind)
        if self.mode == "docker":
            assert self.sandboxes is not None and self.network is not None
            # Containers may reach the host ONLY on these two ports.
            await asyncio.to_thread(self.sandboxes.restrict_host_ports, self.network,
                                    [self._http.port, self._gw_http.port])

    def _set_budget(self, usd: float) -> None:
        """Raise (or set) the run's spend cap while it runs (API: PATCH /limits). Guidance the
        overseer gave because money was nearly gone is withdrawn (plan §11.4)."""
        self.gateway.set_run_budget(self.run_id, float(usd))
        self.runner.project.approval_gates["token_budget_usd"] = float(usd)
        if self.overseer is not None:
            self.overseer.withdraw_budget_guidance()

    def _budget_watch(self) -> None:
        """Warn once per budget when spend passes `budget_warn_fraction` of it (plan §11.4)."""
        budget = self.runner.project.approval_gates.get("token_budget_usd")
        if self.gateway is None or not isinstance(budget, (int, float)) or budget <= 0:
            return
        spent = self.gateway.spend(self.run_id)
        if spent < self.settings.budget_warn_fraction * budget or self._budget_warned == budget:
            return
        self._budget_warned = float(budget)
        self.workspace.post_signal(Signal(
            type=SignalType.BUDGET_WARNING, from_agent="system", priority="high",
            iteration=self.workspace.iteration,
            summary=(f"${spent:.2f} of the ${budget:.2f} session budget spent "
                     f"({spent / budget:.0%}); the session stops at the budget. Raise it?"),
            detail=json.dumps({"spent_usd": round(spent, 4), "budget_usd": float(budget)})))

    def _limits_section(self, role: str) -> tuple[tuple[str, str], ...]:
        """A limits change the role has not heard about yet (plan §11.4), once per change."""
        changes = [s for s in self.workspace.board if s.type is SignalType.RUN_LIMITS_CHANGED]
        if not changes or self._limits_seen.get(role) == changes[-1].id:
            return ()
        self._limits_seen[role] = changes[-1].id
        budget = self.runner.project.approval_gates.get("token_budget_usd")
        spent = self.gateway.spend(self.run_id) if self.gateway is not None else 0.0
        lines = [f"The owner changed this session's limits: {changes[-1].summary.split(': ', 1)[-1]}."]
        if isinstance(budget, (int, float)):
            lines.append(f"Budget now ${budget:.2f}; ${spent:.2f} spent so far.")
        if self.workspace.deadline_at is None:
            lines.append("There is no time limit now.")
        lines.append("Earlier guidance that money or time was nearly gone no longer applies; "
                     "work at the normal pace, and keep cards complete and tested.")
        return (("Session limits changed", " ".join(lines)),)

    def _host_tools(self) -> list[McpTool]:
        overrides = self.settings.tool_overrides
        browser = [capped(t, self.images)  # image caps per turn / per run (§5.10)
                   for t in mcp_browser.tools(self.tool_ctx, **overrides.get("mcp_browser", {}))]
        return [*mcp_tools.tools(self.tool_ctx, **overrides.get("mcp_tools", {})),
                *mcp_web.tools(self.tool_ctx, **overrides.get("mcp_web", {})),
                *browser, *mcp_visual.tools(self.tool_ctx)]

    def _third_party_tools(self) -> list[McpTool]:
        """Proxied third-party tools that at least one role is granted (docs/MCP_PLAN.md)."""
        if self._mcp_registry is None:
            return []
        granted = {t for r in self._roles.values() for t in r.compiled.mcp_tools
                   if t in self._mcp_offers}
        if not granted:
            return []
        self.mcp_proxy = mcp_proxy.McpProxy(self._mcp_registry)
        return mcp_proxy.tools(self.tool_ctx, self.mcp_proxy, granted)

    def _upstreams(self) -> dict[str, Upstream]:
        upstreams: dict[str, Upstream] = {}
        for role in self._roles.values():
            b = role.binding
            kind = "anthropic" if b.client == "anthropic" else "openai_compatible"
            base = b.base_url or DEFAULT_PROVIDER_URLS.get(b.provider_key, "")
            upstreams[b.provider_key] = Upstream(name=b.provider_key, kind=kind,  # type: ignore[arg-type]
                                                 base_url=base.rstrip("/"),
                                                 api_key=b.api_key or "")
        return upstreams

    def _price(self, model_id: str) -> Price | None:
        for role in self._roles.values():
            m = role.binding.model
            if m.id == model_id:
                return Price(m.cost_per_1k_input, m.cost_per_1k_output, m.cache_read_per_1k,
                             m.cache_write_price, m.long_context_threshold,
                             m.long_input_per_1k, m.long_output_per_1k)
        return None

    def _sandbox_host(self, role: str) -> str:
        r = self._roles.get(role)
        return r.sandbox.ip if r is not None and r.sandbox is not None else "127.0.0.1"

    def _artifact_post(self, kind: SignalType, role: str, summary: str,
                       detail: Any = None, **_: Any) -> None:
        self.tool_ctx.post(kind, role, summary, detail)

    def _runner_for(self, cfg: "AgentConfig") -> Callable[[Workspace], Any]:
        async def run_role(workspace: Workspace) -> None:
            await self.execute_turn(cfg.role)
        return run_role

    # ── role setup ─────────────────────────────────────────────────────────
    def _prepare_role(self, cfg: "AgentConfig") -> _Role:
        role_type = infer_role_type(cfg.role, is_reviewer=cfg.is_reviewer, declared=cfg.role_type)
        binding = self._binding(cfg)
        requested = gate_vision(Capabilities.from_dict(cfg.capabilities, base=defaults_for(role_type)),
                                binding.model.supports_images)
        # Host mode runs every role as host_dev (refused in production by
        # compile_capabilities); docker mode keeps the requested sandbox level.
        effective = (replace(requested, sandbox="host_dev") if self.mode == "host"
                     else replace(requested, sandbox="standard")
                     if requested.sandbox == "host_dev" else requested)
        extra = resolve_mcp_grants(cfg.mcp_tools, role_type, self._mcp_offers)
        compiled = compile_capabilities(effective, role_type, production=self.settings.production,
                                        extra_mcp_tools=extra)
        return _Role(cfg=cfg, role_type=role_type, requested=requested, compiled=compiled,
                     binding=binding)

    def _binding(self, cfg: "AgentConfig") -> ProviderBinding:
        if self.settings.binding_factory is not None:
            return self.settings.binding_factory(cfg)
        registry = self._registry or ModelRegistry.for_data_root(None)
        model = registry.get(cfg.model_id) if cfg.model_id else None
        # A tier with several models spreads roles across them; a reviewer avoids the
        # models of the roles it reviews when the tier has another one.
        avoid = [self._roles[r].binding.model.id for r in cfg.review_of if r in self._roles]
        model = model or registry.resolve(cfg.model_tier,
                                          seat=f"{self.runner.project.project_id}:{cfg.role}",
                                          avoid=avoid)
        provider = registry.provider(model.provider)
        if provider is None:
            raise TurnFailed(f"no provider config for {model.provider!r}")
        # The real key stays on the host: it is only given to the gateway upstream
        # (see _upstreams); opencode gets a per-role virtual key.
        return ProviderBinding(provider_key=model.provider, model=model, client=provider.client,
                               base_url=provider.base_url or None,
                               api_key=registry.api_key(model.provider),
                               working_context=self._working_context(model.working_context))

    @staticmethod
    def _turn_variant(role: _Role, plan: TurnPlan | None) -> str | None:
        """The effort variant for a card whose item asks for one, fitted to the role's model;
        None keeps the role's own effort (the server's default options)."""
        if plan is None or not plan.effort:
            return None
        return role.binding.model.pick_effort(plan.effort)

    def _effort(self, cfg: "AgentConfig", model: ModelInfo) -> str | None:
        """The role's effort level, else the project's `effort` gate, else the model's default,
        fitted to the levels the model accepts (None: the provider decides)."""
        gate = (self.runner.project.approval_gates or {}).get("effort")
        return model.pick_effort(cfg.effort or (str(gate) if gate else None))

    def _working_context(self, model_cap: int) -> int:
        """The project's `working_context_tokens` gate can only lower the model's working
        context (never raise it past what the install allows)."""
        raw = self.runner.project.approval_gates.get("working_context_tokens")
        try:
            want = int(raw) if raw not in (None, "") else 0
        except (TypeError, ValueError):
            log.warning("ignoring working_context_tokens %r: not a whole number", raw)
            want = 0
        return min(model_cap, want) if want > 0 else model_cap

    async def _ensure_server(self, role: _Role, workdir: Path,
                             task_hint: str = "") -> OpencodeClient:
        if role.client is not None and role.config_key == _config_key(role.cfg):
            return role.client
        if role.client is not None:
            # The team was edited mid-run: the change applies from this spawn.
            await self._stop_server(role)
        role.config_key = _config_key(role.cfg)
        assert self._http is not None
        assert self._gw_http is not None and self.gateway is not None
        name = role.cfg.role
        identity = McpIdentity(self.run_id, name, role.compiled.mcp_tools
                               | {"submit_result", "changes", "history", "post_note",
                                  "notebook"})
        role.token = self.mcp.issue_token(identity)
        virtual_key = self.gateway.issue_key(self.run_id, name, [role.binding.model.id],
                                             budget_usd=self._role_budget(role))
        # Effort is read here, not when the role was built, so an edit applies from this spawn.
        via_gateway = replace(role.binding,
                              base_url=f"{self._gw_http.url}/{role.binding.provider_key}",
                              api_key=virtual_key,
                              effort=self._effort(role.cfg, role.binding.model))
        role.prompt = await self._system_prompt(role, task_hint)
        agent = agent_entry(role.compiled, model_ref=via_gateway.model_ref,
                            prompt=role.prompt.render(), description=role.cfg.description,
                            steps=self.settings.steps)
        config = build_config(role=name, agent=agent, binding=via_gateway,
                              mcp_url=self._http.url + "/", mcp_token=role.token,
                              extra_providers=self.settings.extra_providers)
        # The event stream's read timeout must outlast the stall window (and any turn cap).
        timeout = max(self.settings.turn_timeout or 0.0, self.settings.turn_stall_timeout) + 60
        if self.mode == "docker":
            from worker.sandbox_manager import SandboxSpec
            assert self.sandboxes is not None and self.network is not None
            import secrets as _secrets
            password = _secrets.token_urlsafe(24)
            inputs = self.runner.run_root / "inputs"
            role.sandbox = await self.sandboxes.start(SandboxSpec(
                run_id=self.run_id, role=name, workdir=workdir,
                inputs_dir=inputs if inputs.is_dir() else None,
                profile=role.compiled.container, config=config, password=password,
                network=self.network, tool_dir=self.clones.tool_dir(name)))
            role.client = OpencodeClient(role.sandbox.base_url, "/work", password=password,
                                         timeout=timeout)
            return role.client
        home = self.runner.run_root / "opencode_home" / name
        role.server = OpencodeServer(config=config, home=home, workdir=workdir)
        await role.server.start()
        role.client = OpencodeClient(role.server.base_url, str(workdir),
                                     password=role.server.password, timeout=timeout)
        return role.client

    def _role_budget(self, role: _Role) -> float | None:
        budgets = self.runner.project.approval_gates.get("role_budget_usd") or {}
        value = budgets.get(role.cfg.role) if isinstance(budgets, dict) else None
        return float(value) if isinstance(value, (int, float)) else None

    async def _stop_server(self, role: _Role) -> None:
        role.session_id = None
        role.session_turns = 0
        if role.client is not None:
            await role.client.aclose()
            role.client = None
        if role.server is not None:
            await role.server.stop()
            role.server = None
        if role.sandbox is not None and self.sandboxes is not None:
            await self.sandboxes.stop(role.sandbox)
            role.sandbox = None
        if role.token:
            self.mcp.revoke(role.token)
            role.token = ""

    async def _system_prompt(self, role: _Role, task_hint: str = "") -> PromptLayers:
        """Compile the role's persona and project layers into its agent prompt (plan §5.11)."""
        cfg = role.cfg
        runner = self.runner
        persona = None
        if cfg.persona_id and runner.personas_root is not None:
            try:
                from agents.persona import Persona
                persona = Persona.load(cfg.persona_id, runner.personas_root)
            except Exception as exc:  # noqa: BLE001 — a bad persona must not take the run down
                log.warning("%s: could not load persona %r: %s", cfg.role, cfg.persona_id, exc)
        brief = ""
        builder = getattr(runner, "_build_teammate_brief", None)
        if builder is not None:
            try:
                brief = builder(cfg.role)
            except Exception as exc:  # noqa: BLE001 — the brief is optional context
                log.warning("%s: teammate brief failed: %s", cfg.role, exc)
        # Servers start per spawn, so memory recall can target this turn's card
        # (with keep_servers the first card's hint stays; tests only).
        return await build_agent_prompt(
            role=cfg.role, description=cfg.description, base_prompt=cfg.system_prompt,
            persona=persona, project_id=getattr(runner.project, "project_id", None),
            persona_id=cfg.persona_id, goal=runner.project.goal, task_hint=task_hint,
            teammate_brief=brief, project_context=getattr(runner, "_project_context", ""),
            projects_root=(self._data_root() / "projects") if self._data_root() else None)

    # ── one spawn ──────────────────────────────────────────────────────────
    async def execute_turn(self, role_name: str) -> None:
        """Run one turn for `role_name` (the scheduler's runner callable)."""
        role = self._roles[role_name]
        cfg = role.cfg
        workdir = self.clones.ensure(role_name, self._author(cfg))
        if self.mode == "host":
            self._link_tool_dir(role_name, workdir)
        if not self.clones.sync_from_main(role_name, keep_conflicts=True):
            self._sync_conflict(role_name)
        if self._is_overseer(role_name):
            assert self.overseer is not None
            plan: TurnPlan | None = self.overseer.begin_turn(
                self._run_view(), {r: _result_line(r, res) for r, res in self.results.items()})
        else:
            plan = self.flow.begin_turn(role_name) if self.flow is not None else None
        if plan is not None and plan.skip:
            assert self.flow is not None
            self.flow.end_turn(role_name)
            self.flow.settle(current=role_name)
            return
        try:
            await self._run_turn(role, workdir, plan)
        finally:
            if self.flow is not None:
                self.flow.end_turn(role_name)

    def _sync_conflict(self, role_name: str) -> None:
        """Main could not be merged cleanly into the role's branch at turn start.

        The markers stay in the worktree and resolving them becomes the first item
        of the brief. If git left nothing to resolve, the old work is set aside.
        """
        files = self.clones.conflicted_files(role_name)
        if files:
            self._git_notes.setdefault(role_name, []).append(
                "Your branch had unmerged work that conflicts with what teammates merged "
                f"since. These files contain git conflict markers: {', '.join(files)}. "
                + _CONFLICT_HOWTO)
            self._post(SignalType.GIT_CONFLICT, role_name,
                       f"{role_name}: resolve conflicts with main ({len(files)} file(s))",
                       "\n".join(files))
            return
        patch = self.clones.set_aside(role_name, "could not merge main into the branch")
        self._note_set_aside(role_name, "it could not be merged with main", patch)

    def _note_set_aside(self, role_name: str, why: str, patch: str | None) -> None:
        if patch is None:
            return
        if role_name in self._roles:
            # The folder no longer matches what the session remembers: start fresh.
            self._roles[role_name].session_id = None
        self._git_notes.setdefault(role_name, []).append(
            f"Your previous changes were set aside because {why}. Your folder now matches "
            f"main. The dropped changes are in `.guudflow/{patch}` (read-only): reuse "
            "whatever is still valid, and avoid what caused the problem.")

    async def _run_turn(self, role: _Role, workdir: Path, plan: TurnPlan | None) -> None:
        role_name = role.cfg.role
        hint = (plan.task_text or "")[:600] if plan is not None else ""
        client = await self._ensure_server(role, workdir, hint)
        model: type[BaseModel] = (plan.model if plan is not None else
                                  ReviewResult if role.role_type == "reviewer" else AgentResult)
        self.inbox.expect(self.run_id, role_name, model, plan.check if plan else None)
        variant = self._turn_variant(role, plan)
        self._post(SignalType.STARTED, role_name,
                   f"{role_name} started" + (f" on {plan.card_id}" if plan and plan.card_id else "")
                   + (f" at {variant} effort" if variant else ""))
        self._post_capabilities(role)

        self.images.new_turn(role_name)
        reuse = self._reuses_session(role)
        card_id = plan.card_id if plan is not None else None
        continuing, why = keep_session(
            SessionState(has_session=role.session_id is not None, turns=role.session_turns,
                         idle_seconds=time.monotonic() - role.last_turn_end,
                         context_tokens=role.last_context,
                         continues_card=(self.flow.continues(role.last_card, card_id)
                                         if self.flow is not None else True)),
            SessionLimits(enabled=reuse, max_turns=self.settings.max_turns_per_session,
                          max_idle_seconds=self.settings.session_keep_idle,
                          max_context_tokens=self.settings.session_keep_max_context),
            reviewer=role.role_type == "reviewer")
        self._session_choice[role_name] = (continuing, why, role.last_context if continuing else 0)
        if self.gateway is not None:
            self._turn_seq += 1
            self.gateway.label(self.run_id, role_name, card=card_id,
                               session="kept" if continuing else "fresh",
                               carried_tokens=role.last_context if continuing else 0,
                               turn=self._turn_seq)
        on_event = self._event_hook(role_name)
        on_permission = self._permission_hook(role_name)
        kept = False
        try:
            if not continuing:
                session = await client.create_session(title=f"{self.run_id}:{role_name}")
                role.session_id, role.session_turns = str(session["id"]), 0
                self._desk_seen.pop(role_name, None)
            session_id = str(role.session_id)
            role.session_turns += 1
            stats = await drive_turn(client, session_id,
                                     self._brief(role, plan, continuation=continuing),
                                     agent=role_name, on_event=on_event,
                                     on_permission=on_permission,
                                     timeout=self.settings.turn_timeout,
                                     stall=self.settings.turn_stall_timeout,
                                     variant=self._turn_variant(role, plan))
            kept = await self._turn_body(role, client, session_id, stats, plan,
                                         on_event, on_permission)
            role.last_context = stats.last_context
        finally:
            role.last_turn_end, role.last_card = time.monotonic(), card_id
            if not (kept and reuse) and not self.settings.keep_servers:
                await self._stop_server(role)

    def _reuses_session(self, role: _Role) -> bool:
        """Does this role keep its server and session between cards?"""
        return self.settings.session_reuse and not self._is_overseer(role.cfg.role)

    async def _turn_body(self, role: _Role, client: OpencodeClient, session_id: str,
                         stats: TurnStats, plan: TurnPlan | None,
                         on_event: EventHook, on_permission: PermissionHook) -> bool:
        """Follow-ups and finishing for one turn. True when the session is worth keeping."""
        role_name = role.cfg.role
        session = {"id": session_id}
        result = self.inbox.take(self.run_id, role_name)
        if result is None:
            # One follow-up in the same session: after a validation miss, the
            # rejection; after a timeout, a short "wrap up now" (salvages the work).
            _, last_error = self.inbox.status(self.run_id, role_name)
            text = wrap_up_brief(stats.errors[-1]) if stats.timed_out else retry_brief(last_error)
            more = await drive_turn(client, session["id"], text, agent=role_name,
                                    on_event=on_event, on_permission=on_permission,
                                    timeout=self.settings.followup_timeout,
                                    variant=self._turn_variant(role, plan))
            _merge_stats(stats, more)
            result = self.inbox.take(self.run_id, role_name)

        async def resolve(files: list[str]) -> BaseModel | None:
            """Hand a merge conflict back to the agent in the same session."""
            more = await drive_turn(client, session["id"], conflict_brief(files),
                                    agent=role_name, on_event=on_event,
                                    on_permission=on_permission,
                                    timeout=self.settings.followup_timeout,
                                    variant=self._turn_variant(role, plan))
            _merge_stats(stats, more)
            return self.inbox.take(self.run_id, role_name)

        await self._finish(role, result, stats, plan, resolve=resolve)
        if result is not None:
            role.turns_done += 1
        # A timed-out or result-less turn starts the next card in a fresh session.
        return result is not None and not stats.timed_out

    async def _finish(self, role: _Role, result: BaseModel | None, stats: TurnStats,
                      plan: TurnPlan | None = None,
                      resolve: Callable[[list[str]], Awaitable[BaseModel | None]] | None = None,
                      ) -> None:
        name = role.cfg.role
        card_id = plan.card_id if plan is not None else None
        if self._is_overseer(name):
            assert self.overseer is not None
            if isinstance(result, OverseerResult):
                self.overseer.apply(result)
            else:
                _, err = self.inbox.status(self.run_id, name)
                self.overseer.failed(err or "; ".join(stats.errors) or "no result was submitted")
            self._mark_complete(name)
            if self.flow is not None:
                self.flow.settle(current=name)
            return
        if result is None:
            _, err = self.inbox.status(self.run_id, name)
            reason = err or "; ".join(stats.errors) or "no result was submitted"
            if card_id is not None and self.flow is not None:
                self.flow.card_problem(name, card_id, f"no valid result: {reason}")
                self.flow.settle(current=name)
                return
            self._post(SignalType.FAILED, name, f"{name} finished without a valid result",
                       reason)
            raise TurnFailed(f"{name}: {reason}")
        summary = getattr(result, "summary", "") or ""
        collected = self.clones.collect(name, summary)
        if collected.conflict and resolve is not None:
            collected, result = await self._resolve_conflict(name, collected, result, resolve)
            summary = getattr(result, "summary", "") or ""
        self.artifacts.scan(name, self.clones.path(name), collected.changed_files,
                            tool_dir=self.clones.tool_dir(name))
        if self.gateway is not None:
            stats.cost = self.gateway.spend(self.run_id, name)
        self.results[name] = result
        self._post(SignalType.AGENT_RESULT, name, _result_line(name, result),
                   json.dumps({"result": result.model_dump(), "cost": stats.cost,
                               "tokens": stats.tokens, "tool_calls": stats.tool_calls,
                               "commits": collected.new_commits,
                               "files": collected.changed_files}))
        for to, note in getattr(result, "notes_for", {}).items():
            # Notes may be addressed by a teammate's name or role.
            self._post_note(name, resolve_recipient(to, self.names) or to, note)
        if not collected.merged:
            if collected.set_aside:
                self._note_set_aside(name, str(collected.problem), collected.set_aside)
            if card_id is not None and self.flow is not None:
                where = (f" (changes set aside in .guudflow/{collected.set_aside})"
                         if collected.set_aside else "")
                self.flow.card_problem(name, card_id,
                                       f"branch not merged: {collected.problem}{where}")
                self.flow.settle(current=name)
                return
            self._post(SignalType.FAILED, name, f"{name}'s branch was not merged",
                       collected.problem)
            raise TurnFailed(f"{name}: {collected.problem}")
        if card_id is not None and self.flow is not None:
            self.flow.apply_result(name, card_id, result,
                                   collected.new_commits[0] if collected.new_commits else "",
                                   (collected.base, collected.head))
            self.flow.settle(current=name)
            return
        self.workspace.set_artifact(name, Artifact(agent_role=name, type="agent_result",
                                                   content=result.model_dump(),
                                                   iteration=self._spawns(name)))
        if isinstance(result, AgentResult) and result.status == "blocked":
            assert result.blocked_on is not None
            self._handle_blocked(name, result.blocked_on.role, result.blocked_on.need)
            return
        if isinstance(result, ReviewResult) and result.verdict == "fail":
            if self._handle_review_fail(role, result):
                return
        if isinstance(result, AgentResult) and result.status == "failed":
            self._post(SignalType.FAILED, name, f"{name} reported failure", result.summary)
            raise TurnFailed(f"{name}: {result.summary}")
        if isinstance(result, PlanResult) and self.flow is not None:
            self.flow.plan_accepted(result)
            summary = f"planned {len(result.tasks)} card(s). {result.notes}".strip()
        self.workspace.completed_agents.append(name)
        self.workspace.agent_states[name] = AgentState.COMPLETE
        self._post(SignalType.COMPLETE, name, f"{name} complete", summary or None)
        if self.flow is not None:
            self.flow.settle(current=name)

    async def _resolve_conflict(
            self, name: str, collected: CollectResult, result: BaseModel,
            resolve: Callable[[list[str]], Awaitable[BaseModel | None]],
    ) -> tuple[CollectResult, BaseModel]:
        """Main moved under the role: let the agent merge, once, then collect again.

        Unresolvable → the work is set aside (patch kept) so the role's next turn
        starts from main instead of hitting the same conflict forever.
        """
        files = self.clones.start_conflict_resolution(name)
        if files:
            self._post(SignalType.GIT_CONFLICT, name,
                       f"{name}: main moved; resolving {len(files)} conflicted file(s)",
                       "\n".join(files))
            again = await resolve(files)
            if again is not None:
                result = again
        after = self.clones.collect(name, getattr(result, "summary", "") or "")
        if after.conflict:
            after.problem = f"merge conflict not resolved: {after.problem}"
            after.set_aside = self.clones.set_aside(name, after.problem)
        return after, result

    # ── review edges and blocking (plan §4.4, §4.5) ────────────────────────
    def _scheduler(self) -> Scheduler:
        scheduler = self.runner._scheduler  # noqa: SLF001
        assert isinstance(scheduler, Scheduler)
        return scheduler

    def _handle_blocked(self, name: str, on_role: str, need: str) -> None:
        outcome = self._scheduler().block(name, on_role, {"kind": "need", "from": name,
                                                           "need": need})
        if outcome.action == "unknown_role":
            self._post(SignalType.FAILED, name, f"{name} is blocked on unknown role {on_role!r}",
                       need)
            raise TurnFailed(f"{name}: blocked on unknown role {on_role!r}")
        if outcome.action == "cycle":
            self._post(SignalType.HUMAN_NEEDED, name,
                       f"blocked-on cycle: {' → '.join(outcome.cycle + (name,))}", need)
            return
        self._post(SignalType.BLOCKED, name, f"{name} is blocked on {on_role}", need)

    def _handle_review_fail(self, role: _Role, result: ReviewResult) -> bool:
        """Send work back to the reviewed roles. False when rounds are used up."""
        name = role.cfg.role
        authors = role.cfg.review_of or [d for d in role.cfg.depends_on
                                         if d in self._roles
                                         and self._roles[d].role_type != "reviewer"]
        issues = [{"file": i.file, "detail": i.detail} for i in result.issues]
        payload = {"kind": "review", "reviewer": name, "issues": issues}
        if authors and self._scheduler().request_rework(name, authors, payload):
            rounds = max(self._scheduler().review_rounds(name, a) for a in authors)
            self._post(SignalType.REJECTED, name,
                       f"{name} sent work back to {', '.join(authors)} (round {rounds})",
                       json.dumps({"authors": authors, "issues": issues, "round": rounds}))
            return True
        self._escalate(name, f"review rounds exhausted for {', '.join(authors) or name}",
                       json.dumps({"issues": issues}))
        return False

    def _escalate(self, role: str, summary: str, detail: str | None = None) -> None:
        """Escalate: the overseer's `escalation` detector picks this up (plan §11.2)."""
        self._post(SignalType.ESCALATION, role, summary, detail)

    # ── briefs, notes, signals ─────────────────────────────────────────────
    def _brief(self, role: _Role, plan: TurnPlan | None = None, *,
               continuation: bool = False) -> str:
        cfg = role.cfg
        upstream = tuple(
            UpstreamResult(role=dep, summary=getattr(self.results[dep], "summary", ""),
                           files_changed=tuple(getattr(self.results[dep], "files_changed", ())))
            for dep in cfg.depends_on if dep in self.results)
        issues: tuple[ReviewIssue, ...] = ()
        review_round = 0
        review_from: str | None = None
        needs: list[tuple[str, str]] = []
        for item in self._scheduler().drain_pending_input(cfg.role):
            if isinstance(item, dict) and item.get("kind") == "review":
                issues += tuple(ReviewIssue(str(i["file"]), str(i["detail"]))
                                for i in item.get("issues", []))
                review_round = self._scheduler().review_rounds(str(item["reviewer"]), cfg.role)
                review_from = str(item["reviewer"])
            elif isinstance(item, dict) and item.get("kind") == "need":
                needs.append((str(item["from"]), str(item["need"])))
        notes = self._take_notes(cfg.role) + tuple(needs)
        git_notes = self._git_notes.pop(cfg.role, [])
        research = self.clones.path(cfg.role) / "research" / "notes.md"
        return build_brief(BriefInputs(
            goal=self.runner.project.goal, role=cfg.role, description=cfg.description,
            is_reviewer=role.role_type == "reviewer", upstream=upstream, notes=notes,
            review_issues=issues if role.role_type != "reviewer" else (),
            review_round=review_round,
            research_notes=(research.read_text(encoding="utf-8", errors="replace")
                            if role.role_type != "researcher" and research.is_file() else None),
            task=plan.task_text if plan is not None else None,
            carry_forward=self._carry_text or None,
            git_notes=tuple(git_notes),
            human_comments=self._take_human_comments(cfg.role),
            continuation=continuation,
            notebook=self._notebooks.get(cfg.role),
            contract=self._contract_text(cfg.role),
            names=self.names, review_from=review_from,
            team=team_section(cfg.role, [
                Member(c.role, self.names.get(c.role, c.role), self._roles[c.role].role_type,
                       c.description) for c in self.agents if c.role in self._roles]),
            extra_sections=self._input_sections(cfg.role, plan, continuation)
            + (plan.extra_sections if plan is not None else ())
            + ((("This run's backlog items", backlog_text(self.backlog)),)
               if self.backlog and self.flow is None else ())
            + (self.overseer.brief_addenda(cfg.role) if self.overseer is not None
               and not self._is_overseer(cfg.role) else ())
            + self._limits_section(cfg.role),
        ))

    def _input_sections(self, role: str, plan: TurnPlan | None,
                        continuation: bool) -> tuple[tuple[str, str], ...]:
        """What the human provided that this role should see: uploaded input files (first
        brief of a session) and the persona's desk notes that this card triggers."""
        sections: list[tuple[str, str]] = []
        if not continuation and not self._is_overseer(role):
            listing = self._inputs_listing()
            if listing:
                sections.append(("Files the human provided", listing))
        desk = self._desk_notes(role, plan.task_text if plan is not None and plan.task_text
                                else self._roles[role].cfg.description)
        if desk:
            sections.append(("Your desk notes", desk))
        compiled = getattr(self._roles.get(role), "compiled", None)
        # Host mode: the role runs with the host's PATH, so the host says what is installed.
        installed = (lambda prog: shutil.which(prog) is not None) if self.mode == "host" else None
        shell = (shell_summary(compiled.permission, installed)
                 if compiled is not None and not self._is_overseer(role) else None)
        if shell:
            sections.append(("Shell commands", shell))
        return tuple(sections)

    def _inputs_listing(self) -> str:
        """The run's input files, read-only for every role (DESIGN §33). '' if none."""
        root = self.runner.run_root / "inputs"
        if not root.is_dir():
            return ""
        files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                       if p.is_file() and not p.is_symlink()
                       and not any(part.startswith(".") for part in p.relative_to(root).parts))
        if not files:
            return ""
        where = "/inputs" if self.mode == "docker" else str(root)
        shown = "\n".join(f"- `{where}/{f}`" for f in files[:40])
        more = f"\n- … and {len(files) - 40} more" if len(files) > 40 else ""
        first = (f" Read `{where}/README.md` first." if "README.md" in files else "")
        return (f"The human gave this run these files (read-only).{first}\n\n{shown}{more}")

    def _desk_index(self, role: str) -> Any:
        """The persona's desk (persona layer + this project's overlay), or None."""
        if role in self._desks:
            return self._desks[role]
        cfg = self._roles[role].cfg
        index = None
        root = self.runner.personas_root
        if cfg.persona_id and root is not None:
            try:
                from agents.persona_desk import DeskIndex
                data_root, pid = self._data_root(), self.runner.project.project_id
                overlay = (data_root / "projects" / pid / "persona_desks" / cfg.persona_id
                           if data_root is not None and pid else None)
                index = DeskIndex.build(Path(root) / cfg.persona_id / "desk", overlay)
            except Exception as exc:  # noqa: BLE001 — desk notes are optional context
                log.warning("%s: could not load desk notes: %s", role, exc)
        self._desks[role] = index
        return index

    def _desk_notes(self, role: str, task: str) -> str:
        """Desk notes this card triggers, each once per session. '' if none."""
        index = self._desk_index(role)
        if index is None or index.is_empty():
            return ""
        seen = self._desk_seen.setdefault(role, set())
        parts: list[str] = []
        for desk_file, snippet in index.auto_inject(task, role):
            if desk_file.name in seen:
                continue
            seen.add(desk_file.name)
            body = f"**{desk_file.name}** ({desk_file.kind})\n{snippet}"
            # Notes the project manager proposed are agent-written: data, not orders.
            parts.append(wrap_untrusted(f"desk note {desk_file.name}", body)
                         if desk_file.source_path.parent.name == DESK_PROPOSED_DIR else body)
        return "\n\n".join(parts)

    def _write_desk_seeds(self) -> None:
        """Desk notes proposed with the team (meta proposal `desk_seeds`) become files in the
        project's persona desks, so `_desk_notes` finds them. Existing files are kept."""
        seeds = getattr(self.runner.project, "desk_seeds", None) or {}
        data_root, pid = self._data_root(), self.runner.project.project_id
        if not seeds or data_root is None or not pid:
            return
        for persona_id, items in seeds.items():
            if not _SLUG.fullmatch(str(persona_id)) or not isinstance(items, list):
                continue
            folder = (data_root / "projects" / pid / "persona_desks" / str(persona_id)
                      / DESK_PROPOSED_DIR)
            for seed in items:
                name = str(seed.get("name") or "") if isinstance(seed, dict) else ""
                if not _SLUG.fullmatch(name) or not seed.get("body"):
                    continue
                path = folder / f"{name}.md"
                if path.exists():
                    continue
                triggers = [str(t) for t in seed.get("triggers") or [] if str(t).strip()]
                if not triggers:
                    continue
                folder.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "---\n" + f"name: {name}\n" + f"triggers: {json.dumps(triggers)}\n"
                    + f"kind: {_SLUG_WORD.sub('', str(seed.get('kind') or 'convention'))}\n"
                    + f"priority: {_SLUG_WORD.sub('', str(seed.get('priority') or 'normal'))}\n"
                    + "---\n" + str(seed["body"]).strip() + "\n", encoding="utf-8")

    def _take_human_comments(self, role: str) -> tuple[str, ...]:
        """Comments the human posted on the run that this role has not seen yet.

        Each comment reaches every role once, in its next brief; priority comments
        come first. The role is recorded in `seen_by` (DESIGN §6).
        """
        fresh = [s for s in self.workspace.unseen_user_comments(role)
                 if s.type in (SignalType.USER_COMMENT, SignalType.USER_COMMENT_PRIORITY)
                 and _comment_target(s) in (None, role)]
        fresh.sort(key=lambda s: s.type is not SignalType.USER_COMMENT_PRIORITY)
        for sig in fresh:
            self.workspace.mark_seen(sig.id, role)
        return tuple(("(priority) " if sig.type is SignalType.USER_COMMENT_PRIORITY else "")
                     + sig.summary for sig in fresh)

    def _take_notes(self, role: str) -> tuple[tuple[str, str], ...]:
        notes: list[tuple[str, str]] = []
        for sig in self.workspace.board:
            if sig.type is not SignalType.AGENT_MESSAGE or str(sig.id) in self._delivered_notes:
                continue
            try:
                payload = json.loads(sig.detail or "{}")
            except ValueError:
                continue
            if payload.get("to") == role:
                self._delivered_notes.add(str(sig.id))
                notes.append((str(payload.get("from", "?")), str(payload.get("message", ""))))
        return tuple(notes)

    def _post_note(self, from_role: str, to_role: str, text: str) -> None:
        self._post(SignalType.AGENT_MESSAGE, from_role,
                   f"{self._label(from_role)} → {self._label(to_role)}: note",
                   json.dumps({"from": from_role, "to": to_role, "message": text}))

    def _notebook_tool(self) -> McpTool:
        async def handler(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
            text = str(args.get("text", "")).strip()
            if not text:
                return ToolResult("text must not be empty", is_error=True)
            if len(text) > NOTEBOOK_LIMIT:
                return ToolResult(f"keep it under {NOTEBOOK_LIMIT} characters "
                                  f"(got {len(text)})", is_error=True)
            folder = self.runner.run_root / project_runs.NOTEBOOKS_DIR
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{identity.role}.md").write_text(text + "\n", encoding="utf-8")
            self._post(SignalType.AGENT_MESSAGE, identity.role,
                       f"{self._label(identity.role)} wrote a notebook for the next session",
                       json.dumps({"kind": "notebook", "chars": len(text)}))
            return ToolResult("Notebook saved. It reaches your next session once this one "
                              "is accepted.")

        return McpTool("notebook", "Save your notebook for the next session (markdown). Use "
                       "it only when asked to write your notebook.",
                       {"type": "object", "additionalProperties": False,
                        "properties": {"text": {"type": "string"}}, "required": ["text"]},
                       handler)

    async def _write_notebooks(self) -> None:
        """End of run: each role that worked writes its notebook, one after another.

        Each writes in a fresh session from its handoffs for the run. Skipped when the
        human stopped the run. Never raises.
        """
        stop = self.workspace.pending_human
        if not self.settings.notebooks or (stop is not None and stop.from_agent == "system"):
            return
        for role in self._roles.values():
            name = role.cfg.role
            if self._is_overseer(name) or role.turns_done == 0:
                continue
            try:
                # A fresh session briefed with this run's handoffs (plan §11.1): resuming the
                # role's last session could re-send a large, cold context for a short note.
                client = role.client or await self._ensure_server(
                    role, self.clones.path(name), "notebook")
                session = await client.create_session(title=f"{self.run_id}:{name}:notebook")
                if self.gateway is not None:
                    self.gateway.label(self.run_id, name, card=None, session="notebook")
                await drive_turn(client, str(session["id"]),
                                 notebook_brief(self._notebooks.get(name),
                                                self._own_handoffs(name)), agent=name,
                                 on_event=self._event_hook(name),
                                 on_permission=self._permission_hook(name),
                                 timeout=self.settings.notebook_timeout)
            except Exception:  # noqa: BLE001 — a notebook is a convenience, never a failure
                log.exception("%s: writing the notebook failed in %s", name, self.run_id)

    def _may_start_while_draining(self, role: str) -> bool:
        """After a soft time limit (plan §11.6): the overseer, and reviews of cards that are
        already submitted, still start; new building does not."""
        if self._is_overseer(role):
            return True
        return (self.flow is not None and self._roles[role].role_type == "reviewer"
                and self.flow.board.next_review(role) is not None)

    def _own_handoffs(self, role: str) -> str | None:
        """The role's handoffs from this run, for its notebook (None when there are none)."""
        if self.flow is None:
            return None
        mine = [("your card", h) for h in latest_per_card(stored_handoffs(self.flow.board)).values()
                if h.author == role]
        return render_handoffs(mine, self.names) if mine else None

    def _link_tool_dir(self, role: str, workdir: Path) -> None:
        """Host mode: expose the host-only tool dir at <worktree>/.guudflow (a sandbox
        bind-mounts it read-only there instead)."""
        link = workdir / ".guudflow"
        if not link.exists() and not link.is_symlink():
            link.symlink_to(self.clones.tool_dir(role), target_is_directory=True)

    def _git_tools(self) -> list[McpTool]:
        """Read-only git views for agents, computed on the host (agents have no git)."""
        empty = {"type": "object", "additionalProperties": False, "properties": {}}

        async def changes(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
            task_id = args.get("task_id")
            if task_id is not None:
                if self.flow is None or not isinstance(task_id, str):
                    return ToolResult("there is no task board in this run", is_error=True)
                try:
                    ranges = self.flow.board.merge_ranges(task_id)
                except KeyError:
                    return ToolResult(f"unknown card {task_id!r}", is_error=True)
                return ToolResult(await asyncio.to_thread(self.clones.merged_diff, ranges))
            path = args.get("path")
            if path is not None and (not isinstance(path, str) or path.startswith("/")
                                     or ".." in Path(path).parts):
                return ToolResult("path must be relative to your folder", is_error=True)
            text = await asyncio.to_thread(self.clones.changes, identity.role, "main", path)
            return ToolResult(text)

        async def history(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
            limit = args.get("limit", 20)
            if not isinstance(limit, int) or isinstance(limit, bool):
                return ToolResult("limit must be an integer", is_error=True)
            return ToolResult(await asyncio.to_thread(self.clones.history, identity.role, limit))

        return [
            McpTool("changes", "Show what changed in your folder compared to the team's main "
                    "branch: a file summary and the full diff (optionally for one path). "
                    "With `task_id`, show exactly what that card's work merged instead.",
                    {**empty, "properties": {"path": {"type": "string"},
                                             "task_id": {"type": "string"}}}, changes),
            McpTool("history", "Recent commits on your branch.",
                    {**empty, "properties": {"limit": {"type": "integer"}}}, history),
        ]

    def _post_note_tool(self) -> McpTool:
        async def handler(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
            to, text = str(args.get("to_role", "")), str(args.get("text", "")).strip()
            to_role = resolve_recipient(to, self.names) or to
            if to_role not in self._roles or to_role == identity.role:
                team = ", ".join(self._label(r) for r in self._roles if r != identity.role)
                return ToolResult(f"unknown teammate {to!r}; the team: {team}", is_error=True)
            if not text:
                return ToolResult("text must not be empty", is_error=True)
            self._post_note(identity.role, to_role, text)
            return ToolResult(f"Note posted for {self._label(to_role)}; "
                              "they will see it on their next turn.")

        return McpTool(
            name="post_note",
            description="Leave a note for a teammate (by name or role). They read it at the "
                        "start of their next turn. Nobody answers mid-turn, so do not wait "
                        "for a reply.",
            input_schema={"type": "object", "additionalProperties": False,
                          "properties": {"to_role": {"type": "string"},
                                         "text": {"type": "string"}},
                          "required": ["to_role", "text"]},
            handler=handler)

    def _post_capabilities(self, role: _Role) -> None:
        detail = {"role_type": role.role_type, "requested": role.requested.to_dict(),
                  "effective": role.compiled.capabilities.to_dict(),
                  "digest": role.compiled.digest(), "mcp_tools": sorted(role.compiled.mcp_tools),
                  "model": role.binding.model_ref, "warnings": list(role.compiled.warnings)}
        if role.prompt is not None:
            # Hash and layer names only: memory text never goes on the board.
            detail["prompt"] = {"digest": role.prompt.digest(), "layers": role.prompt.included()}
        for warning in role.compiled.warnings:
            log.warning("%s: %s", role.cfg.role, warning)
        self._post(SignalType.CAPABILITIES, role.cfg.role,
                   f"{role.cfg.role}: sandbox {role.compiled.capabilities.sandbox}, "
                   f"shell {role.requested.shell}, web {role.requested.web}",
                   json.dumps(detail))

    def _permission_hook(self, role: str) -> PermissionHook:
        hook = self.settings.on_permission
        if hook is not None:
            return lambda ask: hook(role, ask)
        return lambda ask: self.gate.decide(role, ask)

    def _event_hook(self, role: str) -> EventHook:
        """Board signals via the event bridge, plus any extra hook from settings."""
        bridge = EventBridge(role, self.workspace.post_signal)
        extra = self.settings.on_event

        def on_event(event: dict[str, Any]) -> None:
            bridge.handle(event)
            if extra is not None:
                extra(role, event)
        return on_event

    def _post(self, kind: SignalType, role: str, summary: str, detail: str | None = None) -> None:
        self.workspace.post_signal(Signal(type=kind, summary=summary, from_agent=role,
                                          detail=detail, iteration=self._spawns(role)))

    def _spawns(self, role: str) -> int:
        scheduler = self.runner._scheduler  # noqa: SLF001
        return int(getattr(scheduler, "_spawn_count", {}).get(role, 0)) if scheduler else 0

    def _author(self, cfg: "AgentConfig") -> str:
        return self.names.get(cfg.role) or (cfg.persona_id or cfg.role).split("-")[0].capitalize()


def _result_line(role: str, result: BaseModel) -> str:
    if isinstance(result, ReviewResult):
        return f"{role}: review {result.verdict} ({len(result.issues)} issue(s))"
    if isinstance(result, AgentResult):
        card = f" {result.task_id}" if result.task_id else ""
        return f"{role}:{card} {result.status} — {result.summary[:120]}"
    if isinstance(result, PlanResult):
        return f"{role}: planned {len(result.tasks)} card(s)"
    return f"{role}: result submitted"


def _merge_stats(into: TurnStats, more: TurnStats) -> None:
    into.cost += more.cost
    for key, value in more.tokens.items():
        into.tokens[key] = into.tokens.get(key, 0) + value
    into.tool_calls += more.tool_calls
    into.permission_asks += more.permission_asks
    into.errors += more.errors
    into.timed_out = into.timed_out or more.timed_out
    into.last_context = more.last_context or into.last_context


def cleanup_role_homes(run_root: Path) -> None:
    """Remove per-role opencode homes (logs and caches) after a run."""
    shutil.rmtree(run_root / "opencode_home", ignore_errors=True)
