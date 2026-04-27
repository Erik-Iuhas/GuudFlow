"""Board tools — agent-to-agent messaging on the shared workspace board.

Agents use `board-message` to direct a question or note to another agent
role without blocking the scheduler. The recipient reads pending messages
injected into their task prompt at turn start.
"""

from __future__ import annotations

import json
from typing import Any

from agents.tools.base import BaseTool
from core.signals import Signal, SignalType


def _err(msg: str) -> dict[str, Any]:
    return {"error": msg}


class BoardMessage(BaseTool):
    """Post a directed message to another agent on the shared board.

    Use this instead of asking the human when you need information from
    a peer agent. The recipient reads your message when they start their
    next turn. You can also check their reply by calling this tool and
    reading the board yourself (fs-list won't help — read the signals).

    Examples:
      - pygame_frontend_dev → game_designer: "Which input events should the
        frontend expose to the backend? The UML shows Event but not the method
        signatures."
      - bug_reviewer → pygame_backend_dev: "board.py line 42 — reveal() does
        not guard against negative coords. Was this intentional?"
    """

    name = "board-message"
    description = (
        "Send a directed message to another agent on the board. "
        "Use instead of asking the human when you need clarification "
        "from a peer. The recipient sees your message at turn start."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "The role of the agent you are messaging (e.g. 'game_designer')",
            },
            "message": {
                "type": "string",
                "description": "Your question or note to the other agent",
            },
        },
        "required": ["to", "message"],
    }

    def __init__(self, from_role: str, workspace: Any):
        self._from_role = from_role
        self._workspace = workspace

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        to = arguments.get("to")
        message = arguments.get("message")
        if not to or not isinstance(to, str):
            return _err("missing required argument: to")
        if not message or not isinstance(message, str):
            return _err("missing required argument: message")

        detail = json.dumps({"to": to, "from": self._from_role, "message": message})
        self._workspace.post_signal(Signal(
            type=SignalType.AGENT_MESSAGE,
            summary=f"{self._from_role} → {to}: {message[:80]}{'…' if len(message) > 80 else ''}",
            detail=detail,
            from_agent=self._from_role,
        ))
        return {"ok": True, "sent_to": to, "message": message}
