"""Compile a persona-backed role into an opencode agent system prompt (plan §5.11).

Reuses the layers `BaseAgent.effective_system_prompt()` builds (identity, character,
stake framing, teammate brief, manifesto, project rules, project context, memory,
base prompt, CoT primer, positive framing) and drops the layers opencode or the
harness now owns (repo map, tool catalog, protocol voice, autonomy rules).

The prompt is built when a role's server starts, so memory recall is per spawn.
Memory text only ever goes into the prompt; the board gets `digest()` and
`included()`.
"""

from __future__ import annotations

from pathlib import Path

import hashlib
import re
from dataclasses import dataclass, fields

from agents.base import COT_PRIMER, POSITIVE_FRAMING, BaseAgent
from agents.persona import Persona

# Substrings (case-sensitive) that mark legacy protocol / tool instructions which
# opencode's own tool list and the brief's "How to work" replace.
LEGACY_PROTOCOL_MARKERS: tuple[str, ...] = (
    "[COMMIT]", "[VERIFY]", "[ASK]", "[ANSWER]", "[DELTA]", "[ACTION]",
    "fs-read", "fs-list", "fs-write", "ask-peer", "board-message",
    "assign-task", "decompose-task", "closes: task:",
)

_BLANK_RUNS = re.compile(r"\n{3,}")


@dataclass
class PromptLayers:
    """The prompt layers in §5.11 order. Empty strings are omitted when rendered."""

    identity: str = ""
    character: str = ""
    stake: str = ""
    teammate_brief: str = ""
    manifesto: str = ""
    project_rules: str = ""
    project_context: str = ""
    memory: str = ""
    base: str = ""
    cot: str = ""
    framing: str = POSITIVE_FRAMING

    def included(self) -> list[str]:
        """Names of the non-empty layers, in prompt order."""
        return [f.name for f in fields(self) if getattr(self, f.name).strip()]

    def render(self) -> str:
        """Join the non-empty layers with blank lines. The framing line is always last."""
        parts = [getattr(self, f.name).strip() for f in fields(self) if f.name != "framing"]
        parts = [p for p in parts if p]
        parts.append(POSITIVE_FRAMING)
        return "\n\n".join(parts)

    def digest(self) -> str:
        """First 16 hex chars of sha256 over `render()`."""
        return hashlib.sha256(self.render().encode("utf-8")).hexdigest()[:16]


def strip_legacy_protocol(text: str) -> str:
    """Drop lines that teach the legacy protocol or tools; collapse 3+ blank lines to 2."""
    kept = [ln for ln in text.splitlines()
            if not any(marker in ln for marker in LEGACY_PROTOCOL_MARKERS)]
    return _BLANK_RUNS.sub("\n\n", "\n".join(kept)).strip()


async def build_agent_prompt(*, role: str, description: str, base_prompt: str,
                             persona: Persona | None, project_id: str | None,
                             persona_id: str | None, goal: str, task_hint: str = "",
                             teammate_brief: str = "", project_context: str = "",
                             projects_root: Path | None = None) -> PromptLayers:
    """Build the layered system prompt for one role's opencode agent.

    Roles without a persona get identity, base prompt, CoT primer and framing only
    (plus the project-wide manifesto, rules and context when they exist).
    """
    layers = PromptLayers()
    if persona is not None:
        layers.identity = persona.identity_layer()
        layers.character = persona.character_layer()
        layers.stake = persona.stake_layer()
    else:
        layers.identity = f"You are the {role} on a small software team. {description}".strip()
    if teammate_brief.strip():
        layers.teammate_brief = "YOUR TEAMMATES:\n\n" + teammate_brief.strip()
    if project_id:
        manifesto = BaseAgent.load_manifesto(project_id, projects_root).strip()
        if manifesto:
            layers.manifesto = "OFFICE RULES (read before every task):\n\n" + manifesto
        rules = BaseAgent.load_project_rules(project_id, persona_id, projects_root).strip()
        if rules:
            layers.project_rules = "PROJECT RULES (this project specifically):\n\n" + rules
    if project_context.strip():
        layers.project_context = (
            "PROJECT CONTEXT (the team's shared understanding of where this project is — "
            "read it, work in service of it, don't repeat what's already done):\n\n"
            + project_context.strip())
    if persona is not None and persona.has_memory():
        recall = await persona.recall(f"{goal}\n{description}\n{task_hint}".strip())
        layers.memory = recall.render_for_prompt()
    layers.base = strip_legacy_protocol(base_prompt)
    layers.cot = COT_PRIMER
    return layers
