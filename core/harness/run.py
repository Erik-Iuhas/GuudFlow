"""Run a project on the opencode harness (plan §3, §4.1).

`OpencodeRun` reuses what `core.runner.AgentRunner` already set up for the run
(workspace + board persisters, git repo, quip engine, run directory) and
replaces only the agent loop: each role is one opencode server working in its
own git clone, scheduled by the unchanged `core.scheduler.Scheduler`.

Lifecycle of one spawn (`execute_turn`):
  sync clone with main → ensure server → open result slot → post STARTED and
  CAPABILITIES → drive the turn → (one retry if no result) → collect the branch
  → post AGENT_RESULT + COMPLETE / BLOCKED / FAILED.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from core.harness.brief import BriefInputs, ReviewIssue, UpstreamResult, build_brief, retry_brief
from core.harness.capabilities import (
    Capabilities,
    CompiledCapabilities,
    compile_capabilities,
    defaults_for,
    gate_vision,
    infer_role_type,
)
from core.harness.config_gen import ProviderBinding, agent_entry, build_config
from core.harness.event_bridge import EventBridge
from core.harness.git_roles import RoleClones
from core.harness.mcp_server import McpIdentity, McpServer, McpTool, RunningHttpServer, ToolResult, serve_app
from core.harness.opencode_client import OpencodeClient
from core.harness.approvals import PermissionGate
from core.harness.opencode_runner import EventHook, PermissionHook, PermissionReply, TurnStats, drive_turn
from core.harness.result_inbox import ResultInbox
from core.harness.results import AgentResult, ReviewResult
from core.harness.server import OpencodeServer
from core.llm.registry import ModelRegistry
from core.scheduler import AgentSpec, Scheduler, SchedulerResult
from core.signals import Signal, SignalType
from core.workspace import AgentState, Artifact, Workspace

if TYPE_CHECKING:
    from core.runner import AgentConfig, AgentRunner

log = logging.getLogger(__name__)

BindingFactory = Callable[["AgentConfig"], ProviderBinding]
RolePermissionHook = Callable[[str, dict[str, Any]], Awaitable[PermissionReply]]


class TurnFailed(RuntimeError):
    """A role's turn ended without an acceptable outcome. The scheduler marks it failed."""


@dataclass
class HarnessSettings:
    """Knobs for an opencode run. Defaults suit a real run; tests override them."""

    production: bool = field(default_factory=lambda: os.environ.get("GUUDFLOW_ENV") == "production")
    turn_timeout: float = 1200.0
    steps: int = 60
    binding_factory: BindingFactory | None = None
    extra_providers: dict[str, dict[str, Any]] | None = None
    # None → the run's PermissionGate (approval cards in Studio, allow-list, sticky
    # "always"). Tests pass a hook taking (role, ask).
    on_permission: RolePermissionHook | None = None
    on_event: Callable[[str, dict[str, Any]], None] | None = None
    keep_servers: bool = False  # tests: keep servers between spawns of the same run
    max_parallel: int = 3       # concurrent roles (each sandbox costs ~1 GB, plan §5.6)
    max_spawns_per_role: int = 12


@dataclass
class _Role:
    cfg: "AgentConfig"
    role_type: str
    requested: Capabilities
    compiled: CompiledCapabilities
    binding: ProviderBinding
    server: OpencodeServer | None = None
    client: OpencodeClient | None = None
    token: str = ""


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
        for tool in self._git_tools():
            self.mcp.register(tool)
        self.clones = RoleClones(runner.git, runner.run_root / "roles")
        allow = runner.project.approval_gates.get("tool_permission_allow") or {}
        self.gate = PermissionGate(self.workspace, allow_list={
            str(k): [str(p) for p in v] for k, v in allow.items() if isinstance(v, list)})
        self.results: dict[str, BaseModel] = {}
        self._delivered_notes: set[str] = set()
        self._roles: dict[str, _Role] = {}
        self._http: RunningHttpServer | None = None
        self._registry: ModelRegistry | None = runner._model_registry  # noqa: SLF001

    # ── lifecycle ──────────────────────────────────────────────────────────
    async def run(self) -> SchedulerResult:
        """Run every role through the scheduler and tear everything down after."""
        for cfg in self.runner.project.agents:
            self._roles[cfg.role] = self._prepare_role(cfg)
        self._http = await serve_app(self.mcp.app())
        try:
            specs = [AgentSpec(role=cfg.role, runner=self._runner_for(cfg),
                               depends_on=list(cfg.depends_on),
                               max_review_rounds=cfg.max_review_rounds)
                     for cfg in self.runner.project.agents]
            scheduler = Scheduler(specs, self.workspace, scope="pipeline",
                                  max_iterations=self.settings.max_spawns_per_role,
                                  transition_rules=False,
                                  max_parallel=self.settings.max_parallel)
            self.runner._scheduler = scheduler  # noqa: SLF001 — lets force_stop() reach it
            return await scheduler.run()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Stop every opencode server and the MCP server; revoke tokens."""
        for role in self._roles.values():
            await self._stop_server(role)
        self.mcp.revoke_run(self.run_id)
        self.inbox.close_run(self.run_id)
        if self._http is not None:
            await self._http.stop()
            self._http = None

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
        # P1/P2: no sandbox manager yet, so every role runs on the host (host_dev).
        # compile_capabilities refuses host_dev in production, which is the gate.
        effective = replace(requested, sandbox="host_dev")
        compiled = compile_capabilities(effective, role_type, production=self.settings.production)
        return _Role(cfg=cfg, role_type=role_type, requested=requested, compiled=compiled,
                     binding=binding)

    def _binding(self, cfg: "AgentConfig") -> ProviderBinding:
        if self.settings.binding_factory is not None:
            return self.settings.binding_factory(cfg)
        registry = self._registry or ModelRegistry(Path("data/models.json"))
        model = registry.get(cfg.model_id) if cfg.model_id else None
        model = model or registry.resolve(cfg.model_tier)
        provider = registry.provider(model.provider)
        if provider is None:
            raise TurnFailed(f"no provider config for {model.provider!r}")
        # host_dev only: the provider key is handed to this one opencode process
        # explicitly. Container mode (P3) routes through the gateway instead.
        return ProviderBinding(provider_key=model.provider, model=model, client=provider.client,
                               base_url=provider.base_url or None,
                               api_key=os.environ.get(provider.api_key_env) or None)

    async def _ensure_server(self, role: _Role, workdir: Path) -> OpencodeClient:
        if role.client is not None:
            return role.client
        assert self._http is not None
        identity = McpIdentity(self.run_id, role.cfg.role,
                               role.compiled.mcp_tools | {"submit_result", "changes", "history"})
        role.token = self.mcp.issue_token(identity)
        agent = agent_entry(role.compiled, model_ref=role.binding.model_ref,
                            prompt=self._system_prompt(role), description=role.cfg.description,
                            steps=self.settings.steps)
        config = build_config(role=role.cfg.role, agent=agent, binding=role.binding,
                              mcp_url=self._http.url + "/", mcp_token=role.token,
                              extra_providers=self.settings.extra_providers)
        home = self.runner.run_root / "opencode_home" / role.cfg.role
        role.server = OpencodeServer(config=config, home=home, workdir=workdir)
        await role.server.start()
        role.client = OpencodeClient(role.server.base_url, str(workdir),
                                     password=role.server.password,
                                     timeout=self.settings.turn_timeout + 60)
        return role.client

    async def _stop_server(self, role: _Role) -> None:
        if role.client is not None:
            await role.client.aclose()
            role.client = None
        if role.server is not None:
            await role.server.stop()
            role.server = None
        if role.token:
            self.mcp.revoke(role.token)
            role.token = ""

    def _system_prompt(self, role: _Role) -> str:
        base = role.cfg.system_prompt.strip()
        persona = f"You are the {role.cfg.role} on a small software team."
        return f"{persona}\n\n{base}".strip()

    # ── one spawn ──────────────────────────────────────────────────────────
    async def execute_turn(self, role_name: str) -> None:
        """Run one turn for `role_name` (the scheduler's runner callable)."""
        role = self._roles[role_name]
        cfg = role.cfg
        workdir = self.clones.ensure(role_name, self._author(cfg))
        self._link_tool_dir(role_name, workdir)
        if not self.clones.sync_from_main(role_name):
            self._post(SignalType.FAILED, role_name, "could not merge main into the branch")
            raise TurnFailed(f"{role_name}: merge conflict syncing with main")
        client = await self._ensure_server(role, workdir)
        model: type[BaseModel] = ReviewResult if role.role_type == "reviewer" else AgentResult
        self.inbox.expect(self.run_id, role_name, model)
        self._post(SignalType.STARTED, role_name, f"{role_name} started")
        self._post_capabilities(role)

        session = await client.create_session(title=f"{self.run_id}:{role_name}")
        on_event = self._event_hook(role_name)
        on_permission = self._permission_hook(role_name)
        stats = await drive_turn(client, session["id"], self._brief(role), agent=role_name,
                                 on_event=on_event, on_permission=on_permission,
                                 timeout=self.settings.turn_timeout)
        result = self.inbox.take(self.run_id, role_name)
        if result is None and not stats.timed_out:
            _, last_error = self.inbox.status(self.run_id, role_name)
            more = await drive_turn(client, session["id"], retry_brief(last_error),
                                    agent=role_name, on_event=on_event,
                                    on_permission=on_permission, timeout=300)
            _merge_stats(stats, more)
            result = self.inbox.take(self.run_id, role_name)
        if not self.settings.keep_servers:
            await self._stop_server(role)
        await self._finish(role, result, stats)

    async def _finish(self, role: _Role, result: BaseModel | None, stats: TurnStats) -> None:
        name = role.cfg.role
        if result is None:
            _, err = self.inbox.status(self.run_id, name)
            reason = err or "; ".join(stats.errors) or "no result was submitted"
            self._post(SignalType.FAILED, name, f"{name} finished without a valid result",
                       reason)
            raise TurnFailed(f"{name}: {reason}")
        summary = getattr(result, "summary", "") or ""
        collected = self.clones.collect(name, summary)
        self.results[name] = result
        self._post(SignalType.AGENT_RESULT, name, _result_line(name, result),
                   json.dumps({"result": result.model_dump(), "cost": stats.cost,
                               "tokens": stats.tokens, "tool_calls": stats.tool_calls,
                               "commits": collected.new_commits,
                               "files": collected.changed_files}))
        for to_role, note in getattr(result, "notes_for", {}).items():
            self._post_note(name, to_role, note)
        if not collected.merged:
            self._post(SignalType.FAILED, name, f"{name}'s branch was not merged",
                       collected.problem)
            raise TurnFailed(f"{name}: {collected.problem}")
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
        self.workspace.completed_agents.append(name)
        self.workspace.agent_states[name] = AgentState.COMPLETE
        self._post(SignalType.COMPLETE, name, f"{name} complete", summary or None)

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
        """Escalation path (the overseer replaces this in P4, plan §11)."""
        self._post(SignalType.ESCALATION, role, summary, detail)

    # ── briefs, notes, signals ─────────────────────────────────────────────
    def _brief(self, role: _Role) -> str:
        cfg = role.cfg
        upstream = tuple(
            UpstreamResult(role=dep, summary=getattr(self.results[dep], "summary", ""),
                           files_changed=tuple(getattr(self.results[dep], "files_changed", ())))
            for dep in cfg.depends_on if dep in self.results)
        issues: tuple[ReviewIssue, ...] = ()
        review_round = 0
        needs: list[tuple[str, str]] = []
        for item in self._scheduler().drain_pending_input(cfg.role):
            if isinstance(item, dict) and item.get("kind") == "review":
                issues += tuple(ReviewIssue(str(i["file"]), str(i["detail"]))
                                for i in item.get("issues", []))
                review_round = self._scheduler().review_rounds(str(item["reviewer"]), cfg.role)
            elif isinstance(item, dict) and item.get("kind") == "need":
                needs.append((str(item["from"]), str(item["need"])))
        notes = self._take_notes(cfg.role) + tuple(needs)
        research = self.clones.path(cfg.role) / "research" / "notes.md"
        return build_brief(BriefInputs(
            goal=self.runner.project.goal, role=cfg.role, description=cfg.description,
            is_reviewer=role.role_type == "reviewer", upstream=upstream, notes=notes,
            review_issues=issues if role.role_type != "reviewer" else (),
            review_round=review_round,
            research_notes=(research.read_text(encoding="utf-8", errors="replace")
                            if role.role_type != "researcher" and research.is_file() else None),
        ))

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
        self._post(SignalType.AGENT_MESSAGE, from_role, f"note → {to_role}",
                   json.dumps({"from": from_role, "to": to_role, "message": text}))

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
                    "branch: a file summary and the full diff (optionally for one path).",
                    {**empty, "properties": {"path": {"type": "string"}}}, changes),
            McpTool("history", "Recent commits on your branch.",
                    {**empty, "properties": {"limit": {"type": "integer"}}}, history),
        ]

    def _post_note_tool(self) -> McpTool:
        async def handler(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
            to_role, text = str(args.get("to_role", "")), str(args.get("text", "")).strip()
            if to_role not in self._roles or to_role == identity.role:
                return ToolResult(f"unknown role {to_role!r}; roles: {', '.join(self._roles)}",
                                  is_error=True)
            if not text:
                return ToolResult("text must not be empty", is_error=True)
            self._post_note(identity.role, to_role, text)
            return ToolResult(f"Note posted for {to_role}; they will see it on their next turn.")

        return McpTool(
            name="post_note",
            description="Leave a note for another role. They read it at the start of their "
                        "next turn. Nobody answers mid-turn, so do not wait for a reply.",
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
        return (cfg.persona_id or cfg.role).split("-")[0].capitalize()


def _result_line(role: str, result: BaseModel) -> str:
    if isinstance(result, ReviewResult):
        return f"{role}: review {result.verdict} ({len(result.issues)} issue(s))"
    if isinstance(result, AgentResult):
        return f"{role}: {result.status} — {result.summary[:120]}"
    return f"{role}: result submitted"


def _merge_stats(into: TurnStats, more: TurnStats) -> None:
    into.cost += more.cost
    for key, value in more.tokens.items():
        into.tokens[key] = into.tokens.get(key, 0) + value
    into.tool_calls += more.tool_calls
    into.permission_asks += more.permission_asks
    into.errors += more.errors
    into.timed_out = into.timed_out or more.timed_out


def cleanup_role_homes(run_root: Path) -> None:
    """Remove per-role opencode homes (logs and caches) after a run."""
    shutil.rmtree(run_root / "opencode_home", ignore_errors=True)
