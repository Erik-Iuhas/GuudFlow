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
        self.log_path = Path(log_path)
        self._voices: list[AgentVoice] = []
        self._quips: list[Quip] = []
        self._subscribers: list[Callable[[Quip], None]] = []

    def set_voices(self, voices: list[AgentVoice]) -> None:
        """Called by the runner after _build_agents() so the engine knows
        each team member's name and character."""
        self._voices = voices

    def subscribe(self, callback: Callable[[Quip], None]) -> Callable[[], None]:
        """Register a callback for new quips. Returns an unsubscribe fn."""
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

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
        for v in self._voices:
            if v.role == role:
                return v
        return self._fallback_voice(role)

    def _random_voice(self) -> AgentVoice:
        if self._voices:
            return random.choice(self._voices)
        return self._fallback_voice("system")

    def _fallback_voice(self, role: str) -> AgentVoice:
        return AgentVoice(
            role=role,
            name=role.capitalize(),
            character="You are a focused engineer on a software team.",
        )

    async def _generate(self, voice: AgentVoice, context: str, event: str) -> Quip:
        system = (
            f"{voice.character}\n\n"
            "React to what just happened in ONE punchy sentence — in character, "
            "no quotation marks, no preamble. Speak as yourself."
        )
        try:
            response = await self.llm.complete(
                system=system,
                messages=[LLMMessage(role="user", content=context)],
                tools=None,
                max_tokens=self.MAX_TOKENS,
                model=self.FAST_TIER_MODEL,
            )
            text = response.text.strip() or "(no comment)"
        except Exception as e:
            text = f"(quip failed: {e})"
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
