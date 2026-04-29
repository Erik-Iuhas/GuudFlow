"""QuipEngine — secondary LLM call producing short, in-character commentary.

DESIGN §23: quip board is a SEPARATE log from the main signal board.
Agents never read it. The runner triggers it on significant events
(agent complete, approval/rejection, end of run). FAST tier always —
quips are flavour, not reasoning.

Each quip is spoken in the voice of a specific agent persona. The
runner calls set_voices() after _build_agents() so the engine knows
who is on the team and what their character is.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.llm import LLMClient, LLMMessage
from core.scheduler import SchedulerResult
from core.workspace import Workspace


@dataclass
class AgentVoice:
    role: str       # "developer", "reviewer" — used to select speaker
    name: str       # display name shown in the quip feed
    character: str  # character description injected into the system prompt


@dataclass
class Quip:
    ts: str
    persona: str  # display name of the agent that "said" it
    role: str     # agent role
    text: str
    event: str    # "agent_complete" | "approved" | "rejected" | "run_complete"


class QuipEngine:
    """One cheap LLM call per event, written to quip_log.json on disk.

    The quip log is JSON (a list, atomic-rewrite). Constructor takes the
    output path; the runner sets voices via set_voices() after _build_agents().
    """

    FAST_TIER_MODEL = "claude-haiku-4-5-20251001"
    MAX_TOKENS = 80  # quips are short by design

    def __init__(self, llm: LLMClient, log_path: Path | str):
        self.llm = llm
        print(
            f"[QUIP_DEBUG] __init__ llm={type(llm).__name__} id={id(llm)} log_path={log_path}",
            flush=True,
        )
        self.log_path = Path(log_path)
        self._voices: list[AgentVoice] = []
        self._quips: list[Quip] = []
        self._subscribers: list[Callable[[Quip], None]] = []
        self._workspace: Workspace | None = None
        self._personas_root: Path | None = None

    def set_voices(self, voices: list[AgentVoice]) -> None:
        """Called by the runner after _build_agents() so the engine knows
        each team member's name and character."""
        self._voices = voices

    def set_workspace(self, workspace: Workspace, personas_root: Path | str | None = None) -> None:
        """Give the engine access to hired_personas for voice fallback."""
        self._workspace = workspace
        if personas_root is not None:
            self._personas_root = Path(personas_root)

    def subscribe(self, callback: Callable[[Quip], None]) -> Callable[[], None]:
        """Register a callback for new quips. Returns an unsubscribe fn."""
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    async def emit_quip(
        self,
        persona: str,
        text: str,
        addressed_to: str | None = None,
        event_type: str = "system",
    ) -> None:
        """Emit a pre-formed quip text (no LLM call).

        Used for ASK/ANSWER events where the runner already knows what
        the quip should say (M1 spec §12).  `persona` is the role string;
        `addressed_to` renders a "→ <name>" annotation in the feed.
        """
        voice = self._voice_for(persona)
        full_text = text
        if addressed_to:
            target_voice = self._voice_for(addressed_to)
            full_text = f"{text}  → {target_voice.name}"
        quip = Quip(
            ts=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            persona=voice.name,
            role=voice.role,
            text=full_text,
            event=event_type,
        )
        self._save(quip)

    # ── Public event hooks ──────────────────────────────────────────────────

    async def on_agent_started(self, role: str, workspace: Workspace) -> None:
        """An agent reacts to another agent starting — handoff moment."""
        voice = self._voice_for(role)
        n_done = len(workspace.completed_agents)
        n_total = len(self._voices) or (n_done + 1)
        context = (
            f"You just started your work. "
            f"{n_done} of ~{n_total} agents have completed so far. "
            "React briefly to being handed the baton — in character."
        )
        quip = await self._generate(voice, context, event="agent_started")
        self._save(quip)

    async def on_agent_complete(self, role: str, workspace: Workspace) -> None:
        """The completing agent reacts to finishing their work."""
        voice = self._voice_for(role)
        n_done = len(workspace.completed_agents)
        n_total = n_done + len([
            s for s in workspace.agent_states.values()
            if s.value not in ("complete", "failed")
        ])
        context = (
            f"You just finished your work. "
            f"{n_done} of ~{n_total or n_done} agents complete so far."
        )
        quip = await self._generate(voice, context, event="agent_complete")
        self._save(quip)

    async def on_review_verdict(
        self, role: str, verdict: str, workspace: Workspace
    ) -> None:
        """The reviewer reacts to their own APPROVED / REJECTED verdict."""
        voice = self._voice_for(role)
        reviewed = ", ".join(workspace.completed_agents[:-1]) or "the work"
        if verdict == "APPROVED":
            context = f"You just approved {reviewed}. It passed your review."
        else:
            context = f"You just rejected {reviewed}. It did not meet your standard."
        quip = await self._generate(voice, context, event=verdict.lower())
        self._save(quip)

    async def on_run_complete(self, workspace: Workspace, result: SchedulerResult) -> None:
        """A randomly chosen team member reacts to the overall run outcome."""
        from core.signals import SignalType
        voice = self._random_voice()
        status = result.status.value
        done = ", ".join(result.completed) or "(none)"
        failed = ", ".join(result.failed) or "none"

        # Check the board for reviewer verdicts — the scheduler reports
        # COMPLETE when all agents ran, regardless of whether a reviewer
        # posted REJECTED. The quip must reflect the actual verdict.
        rejected_signals = [
            s for s in workspace.board
            if s.type == SignalType.REJECTED
        ]
        approved_signals = [
            s for s in workspace.board
            if s.type == SignalType.APPROVED
        ]

        if rejected_signals:
            rejecting_agents = ", ".join(
                s.from_agent for s in rejected_signals if s.from_agent
            ) or "a reviewer"
            context = (
                f"The run just finished with status: {status}, but "
                f"the work was REJECTED by {rejecting_agents}. "
                f"The deliverables did NOT pass review — this is a failure, not a success."
            )
        elif approved_signals:
            context = (
                f"The run just finished with status: {status}. "
                f"The work was APPROVED. Completed: {done}."
            )
        else:
            context = (
                f"The run just finished with status: {status}. "
                f"Completed agents: {done}. Failed: {failed}."
            )
        quip = await self._generate(voice, context, event="run_complete")
        self._save(quip)

    async def on_iteration_complete(self, workspace: Workspace, iteration: int) -> None:
        """A randomly chosen agent reacts to an iteration boundary."""
        voice = self._random_voice()
        context = (
            f"Iteration {iteration} just finished. "
            f"{len(workspace.completed_agents)} agents complete so far."
        )
        quip = await self._generate(voice, context, event="iteration_complete")
        self._save(quip)

    async def on_event(self, summary: str, event: str = "system") -> None:
        """Generic hook — picks a random voice."""
        voice = self._random_voice()
        quip = await self._generate(voice, summary, event=event)
        self._save(quip)

    # ── Internals ───────────────────────────────────────────────────────────

    def _voice_for(self, role: str) -> AgentVoice:
        # 1. Check the pre-built voices list (populated at run start)
        for v in self._voices:
            if v.role == role:
                return v

        # 2. Try to synthesise from persona profile (M1 fix — DESIGN §23)
        #    Covers escalation side-tasks, peer-respond tasks, and any role
        #    added after set_voices() was called.
        if self._workspace is not None and self._personas_root is not None:
            persona_id = self._workspace.hired_personas.get(role)
            if persona_id:
                voice = self._synthesise_from_persona(role, persona_id)
                if voice is not None:
                    self._voices.append(voice)  # cache for run
                    return voice

        # 3. System fallback — reserved for genuine system events only.
        return self._fallback_voice(role)

    def _synthesise_from_persona(self, role: str, persona_id: str) -> AgentVoice | None:
        """Load persona profile from disk and build an AgentVoice.

        Returns None on any I/O failure — caller falls back to system voice.
        """
        try:
            import json as _json
            profile_path = self._personas_root / persona_id / "profile.json"  # type: ignore[operator]
            if not profile_path.exists():
                return None
            data = _json.loads(profile_path.read_text(encoding="utf-8"))
            name = data.get("name") or role.capitalize()
            character = data.get("character") or f"You are {name}, a specialist on a software team."
            return AgentVoice(role=role, name=name, character=character)
        except Exception:
            return None

    def _random_voice(self) -> AgentVoice:
        if self._voices:
            return random.choice(self._voices)
        return self._fallback_voice("system")

    def _fallback_voice(self, role: str) -> AgentVoice:
        """System voice — only for genuine runner/system events."""
        return AgentVoice(
            role=role,
            name="System" if role == "system" else role.capitalize(),
            character="You are a focused engineer on a software team.",
        )

    async def _generate(self, voice: AgentVoice, context: str, event: str) -> Quip:
        system = (
            f"{voice.character}\n\n"
            "React to what just happened in ONE punchy sentence — in character, "
            "no quotation marks, no preamble. Speak as yourself."
        )
        try:
            print(
                f"[QUIP_DEBUG] _generate self.llm={type(self.llm).__name__} id={id(self.llm)} engine_id={id(self)}",
                flush=True,
            )
            response = await self.llm.complete(
                system=system,
                messages=[LLMMessage(role="user", content=context)],
                tools=None,
                max_tokens=self.MAX_TOKENS,
                model=self.FAST_TIER_MODEL,
            )
            text = response.text.strip() or "(no comment)"
        except Exception as e:
            import traceback as _tb
            text = f"(quip failed: {e})"
            print(f"[QUIP_DEBUG] generate failed: {e}\n{_tb.format_exc()}", flush=True)
        return Quip(
            ts=datetime.now(timezone.utc).isoformat(),
            persona=voice.name,
            role=voice.role,
            text=text,
            event=event,
        )

    def _save(self, quip: Quip) -> None:
        self._quips.append(quip)
        self._persist()
        self._notify(quip)

    def _persist(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(
            json.dumps([q.__dict__ for q in self._quips], indent=2),
            encoding="utf-8",
        )

    def _notify(self, quip: Quip) -> None:
        for sub in list(self._subscribers):
            try:
                sub(quip)
            except Exception:
                continue

    def quips(self) -> list[Quip]:
        return list(self._quips)
