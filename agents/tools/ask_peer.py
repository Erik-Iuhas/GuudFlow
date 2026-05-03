"""ask-peer tool — discoverable wrapper around the M1 [ASK] protocol.

The [ASK]/[ANSWER] handshake (DESIGN §M1) lets an agent ask another
agent a question without spinning up a full new task instance: the
asker suspends via WaitingForPeer, the responder is dispatched
off-loop with task type `respond`, posts an [ANSWER] signal, and the
scheduler resumes the asker. No Track 2a respawn cascade.

Problem: agents reach for `board-message` instead of emitting [ASK]
because board-message is a discoverable tool in the catalog while
[ASK] is a protocol block hidden inside the system prompt. Result:
question-shaped board-messages trigger Track 2a respawn cycles and
cost cascades (see run_dfcfd6eee3 — ~$9.50 burned on QA respawn loops).

Fix: this tool is a discoverable entry point for the [ASK] flow. It
returns a sentinel dict `{"_emit_protocol": "ASK", "fields": {...}}`
that the runner's tool-use loop intercepts and routes through the
SAME [ASK]-block handler that handles the in-prose protocol path.
No code duplication — the tool is purely a discoverability shim.
"""
from __future__ import annotations

from typing import Any

from agents.tools.base import BaseTool


class AskPeer(BaseTool):
    """Suspend the calling agent and route a question to a peer agent.

    Sentinel return: the runner's `_generic_tool_use_loop` recognises
    `_emit_protocol == "ASK"` in the result and synthesises a
    `protocol.CompactBlock(kind="ASK", fields=...)` that flows through
    the existing ASK-block handler in `_drive_agent_run` (creates
    WaitingForPeer interrupt + spawns `_dispatch_peer_response`).
    """

    name = "ask-peer"
    # Priority 110 sorts ABOVE board-message (200 after step 3 of the
    # peer-comms cleanup plan), so the catalog presents this tool first
    # for any agent considering inter-agent communication.
    priority = 110
    when_to_use = "ask a peer agent a question; suspends you until they ANSWER"
    description = (
        "Suspend yourself and route a question to another agent. They "
        "spawn briefly to answer (no full respawn). You resume with "
        "their ANSWER. Use this for any question expecting a real reply "
        "— not board-message, which is fire-and-forget and would "
        "trigger the recipient's full respawn cycle."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "required": ["to", "question"],
        "properties": {
            "to": {
                "type": "string",
                "description": "Role of the agent you want to ask.",
            },
            "question": {
                "type": "string",
                "description": "The question. Be specific — they only "
                               "answer the question you ask.",
            },
            "context_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional paths the answerer should read "
                               "before answering (e.g. the file your "
                               "question is about).",
            },
        },
    }

    def aci_doc(self) -> dict[str, str]:
        return {
            "purpose": (
                "Ask a peer agent a question via the [ASK]/[ANSWER] "
                "handshake. Suspends the asker until the answer arrives "
                "(no Track 2a respawn cascade)."
            ),
            "args": "{to:string, question:string, context_paths?:list[string]}",
            "emits": (
                "AGENT_MESSAGE signal (ASK form) on the board; runner "
                "creates WaitingForPeer interrupt; off-loop responder "
                "spawned."
            ),
            "errors": "missing required args (to, question)",
            "example": (
                'ask-peer({"to": "tech_lead", '
                '"question": "Should I use sqlite or postgres for state?"})'
            ),
            "notes": (
                "Prefer this over board-message for ANY question "
                "expecting a reply. board-message is fire-and-forget; "
                "this one suspends you until you have a real answer."
            ),
        }

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        to_role = arguments.get("to")
        question = arguments.get("question")
        if not to_role or not isinstance(to_role, str):
            return {"error": "missing required argument: to (string)"}
        if not question or not isinstance(question, str):
            return {"error": "missing required argument: question (string)"}

        context_paths = arguments.get("context_paths") or []
        if not isinstance(context_paths, list):
            return {"error": "context_paths must be a list of strings"}

        # Sentinel return — runner's tool-use loop intercepts and
        # routes through the existing [ASK]-block handler. The asker
        # suspends; the responder spawns off-loop. See
        # core/runner.py:_generic_tool_use_loop ASK-sentinel branch.
        return {
            "_emit_protocol": "ASK",
            "fields": {
                "to": to_role.strip(),
                "question": question.strip(),
                "context_paths": [str(p).strip() for p in context_paths if str(p).strip()],
            },
        }
