"""QuipEngine — secondary LLM call producing short, in-character commentary.

DESIGN §23: quip board is a SEPARATE log from the main signal board.
Agents never read it. The runner triggers it on significant events
(iteration boundaries, approvals, conflicts, end of run). FAST tier
always — quips are flavour, not reasoning.

Stage 5 ships a minimal implementation that writes to
`data/runs/{run_id}/quip_log.json`. The studio (Stage 8) renders it
alongside the BoardFeed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.llm import LLMClient, LLMMessage
from core.scheduler import SchedulerResult
from core.workspace import Workspace


@dataclass
class Quip:
    ts: str
    persona: str  # name of the persona that "said" the quip, or "system"
    text: str
    event: str  # "iteration_complete" | "approved" | "rejected" | "run_complete" | "conflict"


class QuipEngine:
    """One LLM call per event, writing to the quip log on disk.

    The quip log is JSON (a list, atomic-rewrite). Constructor takes the
    output path; Stage 5's runner sets this to `data/runs/{run_id}/quip_log.json`.
    """

    FAST_TIER_MODEL = "claude-haiku-4-5-20251001"
    MAX_TOKENS = 100  # quips are short by design

    def __init__(self, llm: LLMClient, log_path: Path | str, persona_name: str = "system"):
        self.llm = llm
        self.log_path = Path(log_path)
        self.persona_name = persona_name
        self._quips: list[Quip] = []

    async def on_run_complete(self, workspace: Workspace, result: SchedulerResult) -> None:
        """Generate a final quip summarising how the run went."""
        prompt = self._summarise_for_quip(workspace, result)
        quip = await self._generate(prompt, event="run_complete")
        self._append(quip)
        self._persist()

    async def on_iteration_complete(self, workspace: Workspace, iteration: int) -> None:
        prompt = (
            f"Iteration {iteration} just finished. "
            f"{len(workspace.completed_agents)} agents complete. "
            "Write one short comment in character."
        )
        quip = await self._generate(prompt, event="iteration_complete")
        self._append(quip)
        self._persist()

    async def on_event(self, summary: str, event: str = "system") -> None:
        prompt = f"{summary}\n\nWrite one short, in-character comment."
        quip = await self._generate(prompt, event=event)
        self._append(quip)
        self._persist()

    # ── Internals ───────────────────────────────────────────────────────────

    def _summarise_for_quip(self, workspace: Workspace, result: SchedulerResult) -> str:
        return (
            f"Run finished with status: {result.status.value}. "
            f"Completed: {', '.join(result.completed) or '(none)'}. "
            f"Failed: {', '.join(result.failed) or '(none)'}. "
            "Write one short in-character comment about how it went."
        )

    async def _generate(self, prompt: str, event: str) -> Quip:
        try:
            response = await self.llm.complete(
                system="You are a member of an engineering team. Drop a short, "
                       "punchy comment about what just happened — one sentence, "
                       "no quotation marks, no preamble. Stay in character.",
                messages=[LLMMessage(role="user", content=prompt)],
                tools=None,
                max_tokens=self.MAX_TOKENS,
                model=self.FAST_TIER_MODEL,
            )
            text = response.text.strip() or "(no comment)"
        except Exception as e:
            text = f"(quip failed: {e})"
        return Quip(
            ts=datetime.now(timezone.utc).isoformat(),
            persona=self.persona_name,
            text=text,
            event=event,
        )

    def _append(self, quip: Quip) -> None:
        self._quips.append(quip)

    def _persist(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(
            json.dumps([q.__dict__ for q in self._quips], indent=2),
            encoding="utf-8",
        )

    def quips(self) -> list[Quip]:
        return list(self._quips)
