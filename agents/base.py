"""BaseAgent — the contract every agent extends.

DESIGN §3.4, §18, §20, §26, §30, §31, §32, §34, §36.

This is the contract; concrete agents (and prompt-defined agents) live in
`agents/builtin/` and are wired in Stage 5. The runner constructs each
agent with its persona, project rules, manifest, and broker handle, then
calls `run(workspace)` from inside the scheduler.

Key responsibilities:
  - Hold persona memory and inject it into the effective system prompt
    using the layer ordering from §20 (now updated for §23/§31/§36).
  - Expose ONE tool entrypoint: `call_tool(name, arguments)`. Local tools
    run directly; broker-routed tools go through `request_credential()`.
  - Expose protocol emit helpers (`emit_commit`, `emit_verify`, etc.) and
    `read_protocol()` for inter-agent communication (§18).
  - Expose `recall()` for persona memory (§26 / §34).
  - Expose `claim_files()` / `release_files()` for advisory file
    coordination (§32).
  - Expose `request_credential()` / `invoke_mcp()` for broker-routed
    calls (§30).

Concrete agents override `run(workspace)`. Everything else is provided.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable
from uuid import UUID, uuid4

from agents.persona import MemoryRecall, Persona
from agents.tools.base import BaseTool, ToolError
from core import protocol
from core.context_manager import AgentContext, ContextManager
from core.credentials_broker import CredentialResult, CredentialsBroker
from core.llm import LLMClient, LLMMessage, LLMResponse
from core.protocol import FileChange, ProtocolMessage, VerifyResult
from core.repo_map import _approx_tokens
from core.signals import Signal, SignalType
from core.tool_manifest import ToolDescriptor, ToolManifest
from core.workspace import (
    AgentState,
    HumanRequest,
    IterationMetrics,
    PackageRequest,
    PendingScreenshot,
    Workspace,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ClaimAdvice:
    """Returned by `_pre_write_check`. Tells the agent how to proceed
    when another agent already has an active claim on a path."""

    advice: str  # "proceed" | "await_release" | "coordinate_via_ask"
    blocking_agent: str | None = None
    reason: str = ""


# ── ToolNotAvailable / ToolDenied ──────────────────────────────────────────


class ToolNotAvailable(Exception):
    """Raised when an agent tries to call a tool that isn't in its manifest.

    DESIGN rule 76. The LLM should never see a tool not in the manifest,
    so this is a defensive check — bypass would invalidate the
    manifest's purpose."""


class ToolDenied(Exception):
    """Raised when the broker denies a credentialed call."""

    def __init__(self, tool: str, reason: str | None):
        super().__init__(f"{tool}: {reason or 'denied'}")
        self.tool = tool
        self.reason = reason


# ── BaseAgent ──────────────────────────────────────────────────────────────


@dataclass
class BaseAgent:
    """Stage 4 contract. Stage 5 builtins extend this with role-specific
    `run()` implementations."""

    # ── Identity & config ──────────────────────────────────────────────────
    role: str
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    system_prompt: str = ""

    # Declared intent (DESIGN §31). Actual surface is `_manifest`.
    declared_local_tools: list[str] = field(default_factory=list)
    declared_mcp_servers: list[str] = field(default_factory=list)

    persona: Persona | None = None

    # ── Runtime (set by runner before run()) ───────────────────────────────
    _manifest: ToolManifest | None = None
    _local_tools: dict[str, BaseTool] = field(default_factory=dict)
    _broker: CredentialsBroker | None = None
    _context_manager: ContextManager | None = None
    _llm: LLMClient | None = None
    _model: str = "claude-sonnet-4-6"
    # M1 §9 — scheduler handle so ask_human / request_visual_feedback can
    # park via interrupt() instead of mutating workspace.pending_human.
    # Injected by the runner immediately after Scheduler construction.
    _scheduler: Any = None  # core.scheduler.Scheduler
    _project_rules: str = ""
    # M2 — manifesto (Layer 3.6). Set by runner from per-project manifesto.md.
    _manifesto: str = ""
    # Phase K.1 — project's roadmap.md (free-form markdown the user
    # owns). Empty / unset = layer is omitted from the effective
    # prompt; same observable behaviour as today's runs.
    _project_context: str = ""
    # Continuation brief — set by the runner when the run has prior-run
    # attachments (inputs/from_run_*/) with optional feedback.md. Surfaces
    # the user's review comments and the existence of prior deliverables
    # to every agent so devs don't redo work the prior run already shipped.
    _continuation_brief: str = ""
    _current_task: str = ""
    # M1 — teammate brief (Layer 3.5). Set by runner before each LLM call.
    _teammate_brief: str = ""
    # M2 — persona desk index. Set by runner from DeskIndex built for this agent.
    _desk_index: Any = None  # agents.persona_desk.DeskIndex

    # Per-instance current iteration (set by runner per attempt).
    iteration: int = 0

    # Track 1: set by the runner before each _drive_agent_run; cleared after.
    _current_metrics: IterationMetrics | None = field(default=None, repr=False)

    # ── M2: manifesto + project rules loaders ─────────────────────────────

    @staticmethod
    def load_manifesto(project_id: str) -> str:
        """Read per-project manifesto.md. Returns "" if missing (surfaces bug)."""
        from pathlib import Path
        path = Path(f"data/projects/{project_id}/manifesto.md")
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                return ""
        return ""

    @staticmethod
    def load_project_rules(project_id: str, persona_id: str | None) -> str:
        """Read shared + persona-specific project rules."""
        from pathlib import Path
        rules_dir = Path(f"data/projects/{project_id}/persona_rules")
        if not rules_dir.exists():
            return ""
        parts: list[str] = []
        shared = rules_dir / "_shared.md"
        if shared.exists():
            try:
                text = shared.read_text(encoding="utf-8").strip()
                if text:
                    parts.append(text)
            except OSError:
                pass
        if persona_id:
            personal = rules_dir / f"{persona_id}.md"
            if personal.exists():
                try:
                    text = personal.read_text(encoding="utf-8").strip()
                    if text:
                        parts.append(text)
                except OSError:
                    pass
        return "\n\n---\n\n".join(parts)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def run(self, workspace: Workspace) -> str:
        """Subclasses override this. Default raises so base instances
        can't accidentally run as no-ops."""
        raise NotImplementedError(
            f"{type(self).__name__}.run() not implemented for role {self.role}"
        )

    # ── Signal posting ─────────────────────────────────────────────────────

    async def signal(
        self,
        workspace: Workspace,
        type: SignalType,
        summary: str,
        detail: str | None = None,
        priority: str = "normal",
    ) -> Signal:
        """Post a signal to the board with this agent as `from_agent`."""
        sig = Signal(
            type=type,
            summary=self._namespace(summary),
            detail=detail,
            from_agent=self.role,
            priority=priority,  # type: ignore[arg-type]
            iteration=self.iteration,
        )
        workspace.post_signal(sig)
        return sig

    def _namespace(self, summary: str) -> str:
        """Prefix summaries with the persona's name when one is hired,
        per DESIGN §3.4 ("Aria: wrote 3 components")."""
        if self.persona is None:
            return summary
        return f"{self.persona.profile.name}: {summary}"

    # ── Hard pause: ask_human ──────────────────────────────────────────────

    async def ask_human(
        self,
        workspace: Workspace,
        question: str,
        context: str | None = None,
    ) -> None:
        """Park the agent for a human answer. Returns immediately — the
        runner re-enters `run()` for this role after HUMAN_RESPONDED.

        M1 §9: when a scheduler is injected, this routes through
        `scheduler.interrupt(role, "human", ...)` so the resume path is
        unified with peer/skip waits. Without a scheduler (legacy/test
        callers), falls back to setting `pending_human` directly.
        """
        workspace.pending_human = HumanRequest(
            from_agent=self.role,
            question=question,
            context=context,
            iteration=self.iteration,
        )
        if self._scheduler is not None:
            from core.scheduler import WaitingForHuman
            self._scheduler.interrupt(
                self.role, "human", WaitingForHuman(kind="human", question=question),
            )
        else:
            workspace.agent_states[self.role] = AgentState.WAITING_FOR_HUMAN
        await self.signal(
            workspace,
            SignalType.HUMAN_NEEDED,
            summary=question,
            detail=context,
        )

    # ── Soft check: check_user_board ───────────────────────────────────────

    async def check_user_board(self, workspace: Workspace) -> list[Signal]:
        """Read unread USER_COMMENT signals and mark them seen. Called at
        natural step boundaries inside `run()` (DESIGN §3.4)."""
        unseen = workspace.unseen_user_comments(self.role)
        for sig in unseen:
            workspace.mark_seen(sig.id, self.role)
        return unseen

    # ── Visual feedback ────────────────────────────────────────────────────

    async def request_visual_feedback(
        self,
        workspace: Workspace,
        file_path: str,
        viewport: str = "desktop",
        question: str | None = None,
    ) -> None:
        """Post SCREENSHOT_READY for the runner to render. Suspends the
        agent (the runner manages re-entry on USER_VISUAL_FEEDBACK).

        M1 §9: parks via `scheduler.interrupt(role, "human", kind="visual")`
        when a scheduler is injected, so the resume path is unified.
        """
        workspace.pending_screenshots.append(PendingScreenshot(
            from_agent=self.role,
            file_path=file_path,
            viewport=viewport,
            awaiting_feedback=True,
        ))
        if self._scheduler is not None:
            from core.scheduler import WaitingForHuman
            self._scheduler.interrupt(
                self.role, "human",
                WaitingForHuman(kind="visual", question=question or file_path),
            )
        await self.signal(
            workspace,
            SignalType.SCREENSHOT_READY,
            summary=f"screenshot ready: {file_path}",
            detail=question,
        )

    # ── Package install request ────────────────────────────────────────────

    async def request_package(
        self,
        workspace: Workspace,
        package: str,
        reason: str,
    ) -> None:
        """Route a package install through the IT agent (§21)."""
        workspace.pending_package_requests.append(PackageRequest(
            from_agent=self.role,
            package=package,
            reason=reason,
            iteration=self.iteration,
        ))
        await self.signal(
            workspace,
            SignalType.PACKAGE_REQUEST,
            summary=f"package requested: {package}",
            detail=reason,
        )

    # ── Protocol emit (DESIGN §18) ─────────────────────────────────────────

    async def emit_commit(
        self,
        workspace: Workspace,
        commit_hash: str,
        files: Iterable[FileChange],
        checkpoints: Iterable[str],
    ) -> Signal:
        raw = protocol.render_commit(self.role, commit_hash, files, checkpoints)
        return await self._emit_protocol(workspace, raw, "COMMIT", commit_hash)

    async def emit_verify(
        self,
        workspace: Workspace,
        commit_hash: str,
        results: Iterable[VerifyResult],
        action: tuple[str, str, str] | None = None,
    ) -> Signal:
        raw = protocol.render_verify(commit_hash, results, action)  # type: ignore[arg-type]
        return await self._emit_protocol(workspace, raw, "VERIFY_RESULT", commit_hash)

    async def emit_delta(
        self,
        workspace: Workspace,
        iteration: int,
        from_hash: str,
        to_hash: str,
        changes: Iterable[str],
        addressed: Iterable[str] = (),
    ) -> Signal:
        raw = protocol.render_delta(self.role, iteration, from_hash, to_hash, changes, addressed)
        return await self._emit_protocol(workspace, raw, "DELTA", to_hash)

    async def emit_action(
        self,
        workspace: Workspace,
        target_role: str,
        action: str,
        subject: str,
    ) -> Signal:
        raw = protocol.render_action(target_role, action, subject)  # type: ignore[arg-type]
        return await self._emit_protocol(workspace, raw, "ACTION", None)

    async def emit_ask(
        self,
        workspace: Workspace,
        question_id: str,
        to_role: str,
        question: str,
        context: str | None = None,
    ) -> Signal:
        raw = protocol.render_ask(question_id, self.role, to_role, question, context)
        return await self._emit_protocol(workspace, raw, "ASK", None)

    async def emit_answer(
        self,
        workspace: Workspace,
        question_id: str,
        from_role: str,
        answer: str,
    ) -> Signal:
        raw = protocol.render_answer(question_id, self.role, from_role, answer)
        return await self._emit_protocol(workspace, raw, "ANSWER", None)

    async def _emit_protocol(
        self,
        workspace: Workspace,
        raw: str,
        kind: str,
        commit_hash: str | None,
    ) -> Signal:
        sig = Signal(
            type=SignalType.PROTOCOL,
            summary=self._namespace(f"{kind} {commit_hash or ''}".strip()),
            detail=raw,
            from_agent=self.role,
            iteration=self.iteration,
        )
        workspace.post_signal(sig)
        return sig

    async def read_protocol(
        self,
        workspace: Workspace,
        types: Iterable[str] | None = None,
        only_unseen: bool = True,
    ) -> list[ProtocolMessage]:
        """Parse PROTOCOL signals on the board into structured messages.

        Filters by `types` (e.g. {"VERIFY_RESULT", "ACTION"}) and, by
        default, only returns signals this agent hasn't yet absorbed
        (marks them seen).
        """
        type_set = set(types) if types else None
        out: list[ProtocolMessage] = []
        for sig in workspace.signals_of_type(SignalType.PROTOCOL):
            if only_unseen and self.role in sig.seen_by:
                continue
            try:
                msg = protocol.parse(sig.detail or "")
            except ValueError:
                continue
            if type_set and msg.type not in type_set:
                continue
            out.append(msg)
            if only_unseen:
                workspace.mark_seen(sig.id, self.role)
        return out

    # ── File coordination (§32) ─────────────────────────────────────────────

    async def claim_files(
        self,
        workspace: Workspace,
        paths: Iterable[str],
        intent: str = "edit",
        reason: str = "",
        estimated_duration_s: float = 60.0,
    ) -> list[Signal]:
        """Post FILE_CLAIM for each path. Manual claims override auto-claims
        from write tools for the same agent on the same path (§32)."""
        sigs = []
        for path in paths:
            payload = json.dumps({
                "path": path,
                "intent": intent,
                "estimated_duration_s": estimated_duration_s,
                "reason": reason,
            })
            sig = Signal(
                type=SignalType.FILE_CLAIM,
                summary=self._namespace(f"claim: {path}"),
                detail=payload,
                from_agent=self.role,
                iteration=self.iteration,
            )
            workspace.post_signal(sig)
            sigs.append(sig)
        return sigs

    async def release_files(
        self,
        workspace: Workspace,
        paths: Iterable[str],
        outcome: str = "committed",
        commit_hash: str | None = None,
    ) -> list[Signal]:
        sigs = []
        for path in paths:
            payload = json.dumps({
                "path": path,
                "outcome": outcome,
                "commit_hash": commit_hash,
            })
            sig = Signal(
                type=SignalType.FILE_RELEASE,
                summary=self._namespace(f"release: {path}"),
                detail=payload,
                from_agent=self.role,
                iteration=self.iteration,
            )
            workspace.post_signal(sig)
            sigs.append(sig)
        return sigs

    def active_claims(self, workspace: Workspace, path: str | None = None) -> list[dict]:
        """Return active claims (not yet released) on the board, optionally
        filtered to one path. Defensive: only the latest claim/release per
        (agent, path) is considered."""
        latest: dict[tuple[str, str], tuple[str, Signal]] = {}
        for sig in workspace.board:
            if sig.type not in (SignalType.FILE_CLAIM, SignalType.FILE_RELEASE):
                continue
            try:
                payload = json.loads(sig.detail or "{}")
            except json.JSONDecodeError:
                continue
            sig_path = payload.get("path")
            if not sig_path or sig.from_agent is None:
                continue
            key = (sig.from_agent, sig_path)
            kind = "claim" if sig.type is SignalType.FILE_CLAIM else "release"
            latest[key] = (kind, sig)

        active: list[dict] = []
        for (agent, sig_path), (kind, sig) in latest.items():
            if kind != "claim":
                continue
            if path is not None and sig_path != path:
                continue
            try:
                payload = json.loads(sig.detail or "{}")
            except json.JSONDecodeError:
                payload = {}
            active.append({
                "from_agent": agent,
                "path": sig_path,
                "intent": payload.get("intent"),
                "reason": payload.get("reason"),
                "claimed_at": sig.timestamp.isoformat(),
            })
        return active

    def pre_write_check(self, workspace: Workspace, path: str) -> ClaimAdvice:
        """Decide what to do when about to write a path that another
        agent has actively claimed. Default-wait per §32 rule 79."""
        active = self.active_claims(workspace, path=path)
        others = [c for c in active if c["from_agent"] != self.role]
        if not others:
            return ClaimAdvice(advice="proceed")
        return ClaimAdvice(
            advice="await_release",
            blocking_agent=others[0]["from_agent"],
            reason=f"{others[0]['from_agent']} has an active claim on {path}",
        )

    # ── Tool dispatch (§31) ────────────────────────────────────────────────

    async def call_tool(
        self,
        workspace: Workspace,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """The single tool entrypoint. Local tools run directly; broker-
        routed tools go through `request_credential`. Manifest membership
        is enforced (DESIGN rule 76)."""
        if self._manifest is None:
            raise ToolError("agent not initialised — manifest is None")

        d: ToolDescriptor | None = self._manifest.find(name)
        if d is None:
            raise ToolNotAvailable(
                f"{name!r} not in manifest for role {self.role!r}"
            )

        m = self._current_metrics
        if m is not None:
            m.tools_called.append(name)

        if d.invocation == "local":
            tool = self._local_tools.get(name)
            if tool is None:
                raise ToolError(f"local tool {name!r} declared but not instantiated")
            try:
                return await tool.run(arguments)
            except Exception:
                raise

        # Broker-routed.
        result = await self.request_credential(
            scope=d.scope or name,
            operation=name,
            payload=arguments,
        )
        if not result.granted:
            raise ToolDenied(name, result.reason)
        return result.result or {}

    # ── Broker (§30) ───────────────────────────────────────────────────────

    async def request_credential(
        self,
        scope: str,
        operation: str,
        payload: dict[str, Any],
    ) -> CredentialResult:
        """Route a credentialed call through the broker. Agents NEVER hold
        credentials; they receive only the result of the call."""
        if self._broker is None:
            raise ToolError("agent has no broker handle")
        return await self._broker.request(
            role=self.role,
            scope=scope,
            operation=operation,
            payload=payload,
        )

    async def invoke_mcp(
        self,
        workspace: Workspace,
        server: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Sugar over `call_tool` for MCP tools."""
        return await self.call_tool(workspace, f"mcp-{server}-{tool}", arguments)

    # ── Context (§28) ──────────────────────────────────────────────────────

    async def get_context(
        self,
        workspace: Workspace,
        task_description: str,
        max_tokens: int | None = None,
        tier: str = "solid",
        include_diff: bool = True,
    ) -> AgentContext:
        if self._context_manager is None:
            raise ToolError("agent has no context_manager handle")
        return await self._context_manager.get_context(
            task=task_description,
            agent_role=self.role,
            max_tokens=max_tokens,
            tier=tier,
            include_diff=include_diff,
        )

    async def search_codebase(self, query: str, limit: int = 5) -> list:
        if self._context_manager is None:
            raise ToolError("agent has no context_manager handle")
        return await self._context_manager.search(query, limit)

    # ── Memory (§26 / §34) ──────────────────────────────────────────────────

    async def recall(self, task_description: str, limit: int = 5) -> MemoryRecall:
        if self.persona is None:
            return MemoryRecall()
        return await self.persona.recall(task_description, limit=limit)

    # ── LLM ─────────────────────────────────────────────────────────────────

    async def llm(
        self,
        messages: list[LLMMessage],
        max_tokens: int = 4096,
        teammate_brief: str = "",
    ) -> LLMResponse:
        """One LLM call with this agent's effective system prompt.

        The runner injects `_llm` and `_model` before calling `run()`.
        Tool schemas come from this agent's manifest.
        """
        if self._llm is None:
            raise ToolError("agent has no LLM client")

        # Use the runner-injected brief if caller doesn't supply one.
        effective_brief = teammate_brief or self._teammate_brief
        system = await self.effective_system_prompt(teammate_brief=effective_brief)
        tools = self._tool_schemas_for_llm()

        resp = await self._llm.complete(
            system=system,
            messages=messages,
            tools=tools or None,
            max_tokens=max_tokens,
            model=self._model,
            cache_system=True,
        )
        m = self._current_metrics
        if m is not None:
            m.input_tokens += resp.input_tokens
            m.output_tokens += resp.output_tokens
            m.cache_read_tokens += resp.cache_read_tokens
            m.cache_creation_tokens += resp.cache_creation_tokens
            m.llm_calls += 1
            if resp.model:
                m.model = resp.model
            m.final_message_tokens = _approx_tokens(resp.text)
        return resp

    def _tool_schemas_for_llm(self) -> list[dict[str, Any]]:
        """Convert manifest descriptors into the Anthropic tool schema."""
        if self._manifest is None:
            return []
        return [
            {
                "name": d.name,
                "description": d.description,
                "input_schema": d.input_schema or {"type": "object", "properties": {}},
            }
            for d in self._manifest.descriptors
        ]

    # ── Protocol output primer (§18) ───────────────────────────────────────

    def _protocol_output_primer(self) -> str:
        """Layer 6.5: instruct the LLM to end its turn with a short
        structured block. Verbose prose above the block is treated as
        internal reasoning and discarded — only the block reaches the
        board, so other agents pay tokens for structure, not essays.
        """
        kind = getattr(type(self), "archetype_kind", "") or ""
        if kind == "reviewer":
            specific = (
                "Your turn MUST end with a [VERIFY] block:\n"
                "    [VERIFY] PASS\n"
                "  or, on rejection (detail is required and must be specific):\n"
                "    [VERIFY] FAIL\n"
                "      detail: <one sentence naming the blocking issue>\n"
            )
        elif kind == "escalation_handler":
            specific = (
                "Your turn MUST end with an [ACTION] block:\n"
                "    [ACTION]\n"
                "      target: <role to act>\n"
                "      verb: FIX|REVISIT|CLARIFY|APPROVE|REJECT|SKIP\n"
                "      subject: <one short noun phrase>\n"
            )
        elif kind == "specialist":
            specific = (
                "Your turn MUST end with a [COMMIT] block:\n"
                "    [COMMIT]\n"
                "      summary: <one short sentence on what you produced>\n"
                "      files: <comma-separated paths or `(none)`>\n"
            )
        else:
            specific = (
                "Your turn MUST end with one of these compact blocks:\n"
                "    [COMMIT]    summary: ... ; files: ...\n"
                "    [VERIFY]    PASS  /  FAIL  detail: ...\n"
                "    [ACTION]    target: ... ; verb: ... ; subject: ...\n"
                "    [STATUS]    description: <one short sentence>\n"
            )
        return (
            "PROTOCOL OUTPUT — non-negotiable.\n\n"
            "Reason internally however you need; communicate externally "
            "in protocol. Anything you write before the trailing block is "
            "treated as internal reasoning and DROPPED before other "
            "agents see it. Do not summarise, do not editorialise, do "
            "not include emoji or markdown headings in the block.\n\n"
            + specific
            + "\nNo prose after the block. The block is the last thing "
            "in your response."
        )

    # ── Effective system prompt (§20 + §31 + §36) ──────────────────────────

    async def effective_system_prompt(
        self,
        teammate_brief: str = "",
    ) -> str:
        """Compose the layered system prompt per DESIGN §20 (updated).

        Layer order:
          1. identity
          2. character
          3. stake_framing
          3.5. teammate_brief (§23)
          3.6. manifesto (M2) — office-wide rules of engagement
          3.7. project_rules (§36)
          3.8. project_context (Phase K.1) — roadmap.md
          4. memory (§26 / §34) — recalled, not full dump
          5. base prompt + tool catalog from manifest (§31)
          6. CoT primer
          7. positive framing
        """
        parts: list[str] = []

        if self.persona is not None:
            identity = self.persona.identity_layer()
            character = self.persona.character_layer()
            stake = self.persona.stake_layer()
        else:
            identity = f"You are the {self.role} agent. {self.description}".strip()
            character = (
                "You are a professional specialist. Work entirely within "
                "your role — no meta-commentary, no narrating what you are "
                "about to do, no summaries of actions you already took. "
                "Your output is evidence. Speak in the concise, direct voice "
                "of your discipline."
            )
            stake = ""

        parts.append(identity)
        if character:
            parts.append(character)
        if stake:
            parts.append(stake)
        if teammate_brief:
            parts.append("YOUR TEAMMATES:\n\n" + teammate_brief)
        if self._manifesto:
            parts.append("OFFICE RULES (read before every task):\n\n" + self._manifesto)
        if self._project_rules:
            parts.append("PROJECT RULES (this project specifically):\n\n" + self._project_rules)
        if self._project_context:
            parts.append(
                "PROJECT CONTEXT (the team's shared understanding of "
                "where this project is — read it, work in service of "
                "it, don't repeat what's already done):\n\n"
                + self._project_context
            )
        if self._continuation_brief:
            parts.append(self._continuation_brief)

        if self.persona is not None and self.persona.has_memory() and self._current_task:
            recall = await self.persona.recall(self._current_task)
            rendered = recall.render_for_prompt()
            if rendered:
                parts.append(rendered)
            m = self._current_metrics
            if m is not None:
                m.recall_episodes_count = len(recall.episodes) if recall.episodes else 0
                m.recall_patterns_count = len(recall.patterns) if recall.patterns else 0

        if self.system_prompt:
            parts.append(self.system_prompt)
        if self._manifest is not None:
            tool_text = self._manifest.render_for_prompt()
            if tool_text:
                parts.append(tool_text)

        # Layer 5.5 — protocol voice (M1 spec §4, verbatim)
        parts.append(
            "PROTOCOL VOICE\n"
            "When you emit a protocol message (COMMIT, VERIFY_RESULT, DELTA, ACTION,\n"
            "ASK, ANSWER, STATUS, REQUEST_SKIP), you are writing for another agent, not a human.\n\n"
            "Rules:\n"
            "- summary, detail, body fields: ≤ 12 words each\n"
            "- imperative or declarative only (\"auth endpoint done\" not \"I have implemented\")\n"
            "- no marketing words: robust, comprehensive, seamless, production-ready\n"
            "- no apology: \"unfortunately\", \"I'm sorry\"\n"
            "- no preamble: \"I have now\", \"as requested\", \"let me\"\n"
            "- no restating the task back at the requester\n"
            "- specific over vague: \"POST /auth returns 500 on empty body\" not \"login broken\"\n\n"
            "Your reasoning stays in your scratchpad (between tool calls). Only the\n"
            "protocol message goes on the wire.\n\n"
            "GOOD: [COMMIT]\n  summary: auth endpoint done; 400/401/500 covered\n"
            "BAD:  [COMMIT]\n  summary: I have successfully implemented a robust auth endpoint\n\n"
            "GOOD: [VERIFY] FAIL\n  detail: login: POST /auth returns 500 on empty body\n"
            "BAD:  [VERIFY] FAIL\n  detail: Unfortunately tests indicate login may have issues\n\n"
            "If your upstream work is empty or vague, emit [ASK] targeting that role FIRST —\n"
            "before any [ACTION] or [COMMIT]. Do not invent requirements."
        )

        parts.append(
            "Before producing output, briefly consider edge cases and what "
            "a reviewer would flag. Then respond in the concise professional "
            "voice of your role. Do not explain what you did — the work "
            "is the evidence. One short statement of outcome is enough."
        )
        parts.append(
            "## AUTONOMY RULES — non-negotiable\n\n"
            "You are an **autonomous agent**, not a chatbot or assistant.\n\n"
            "- **Start working immediately.** Your first action must be a tool call, not prose.\n"
            "- **Never present options** to the human (no 'Option A / Option B', no 'would you like me to...').\n"
            "- **Never disclaim your capabilities.** If you have tools, use them. If a file exists, read it.\n"
            "- **Never ask the human for permission** to proceed. You already have it.\n"
            "- **Never say 'I can't'** — if you are stuck, use `board-message` to ask another agent "
            "on the board, then do your best work with what you already have.\n"
            "- **Never complete your turn with zero tool calls.** If you made no tool calls, "
            "you have not done your job. Call at least one tool before returning.\n"
            "- Read your upstream agents' committed files first. They are on disk. Use `fs-list` "
            "then `fs-read` to understand what has been built before you start.\n"
            "- Your output is code, files, and decisions — not plans, options, or questions."
        )
        parts.append(self._protocol_output_primer())
        parts.append("Make this something you are proud of.")

        return "\n\n".join(parts)
