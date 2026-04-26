"""Persona chat — DESIGN §37.

A thin shell over `Persona.recall()` + a single LLM call. Lets users
talk to a persona outside any active run to extract knowledge,
retrospect on past runs, or get pre-run consultation.

Constraints (from §37):
- Read-only by default. No memory mutation.
- No tools, no scheduler, no broker. Pure consultation surface.
- Audit-logged at the route layer (this module just builds the call).
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.persona import MemoryRecall, Persona
from core.llm import LLMClient, LLMMessage


CHAT_FRAMING = (
    "You are in chat mode — a colleague is asking you a question outside "
    "of any active run. Draw on your memory and experience to answer "
    "directly and conversationally. Cite specific past runs or patterns "
    "when relevant. If you don't have enough memory to answer, say so "
    "rather than fabricating details."
)


@dataclass
class ChatRecallSummary:
    """What memory was surfaced to answer this message — returned to the
    caller so the UI can show 'this answer drew on N patterns + N
    episodes' for transparency."""

    patterns_used: int
    anti_patterns_used: int
    episodes_used: int


@dataclass
class ChatResult:
    reply: str
    recall: ChatRecallSummary


def build_chat_system_prompt(persona: Persona, recall: MemoryRecall) -> str:
    """Compose the persona's identity + character + stake + memory +
    chat-mode framing into a single system prompt. Mirrors the §20 prompt
    layering used in run-time but swaps the task-specific layers for the
    chat framing."""
    parts: list[str] = [persona.identity_layer()]
    character = persona.character_layer()
    if character:
        parts.append(character)
    stake = persona.stake_layer()
    if stake:
        parts.append(stake)

    memory_block = recall.render_for_prompt() if recall else ""
    if memory_block:
        parts.append("Relevant memory from past runs:\n" + memory_block)

    parts.append(CHAT_FRAMING)
    return "\n\n".join(p for p in parts if p)


async def chat_with_persona(
    persona: Persona,
    llm: LLMClient,
    message: str,
    *,
    max_tokens: int = 1024,
    recall_limit: int = 5,
) -> ChatResult:
    """Recall memory against the message, build the prompt, call the
    LLM, return the reply + a recall summary.

    The model returned text-only; tools are intentionally not offered —
    chat mode is consultation, not execution."""
    recall = await persona.recall(message, limit=recall_limit)
    system = build_chat_system_prompt(persona, recall)
    response = await llm.complete(
        system=system,
        messages=[LLMMessage(role="user", content=message)],
        tools=None,
        max_tokens=max_tokens,
    )
    return ChatResult(
        reply=response.text,
        recall=ChatRecallSummary(
            patterns_used=len(recall.patterns),
            anti_patterns_used=len(recall.anti_patterns),
            episodes_used=len(recall.episodes),
        ),
    )
