"""Track 8 — recall-subtask tool.

Re-hydrates a closed subtask's stashed message history so the agent
can read the original tool calls + results in detail. Pairs with
Track 8's hot-swap pass: when a subtask closes, the runner stashes its
message span and replaces it with a one-line `[SUBTASK CLOSED — id=X]`
marker. This tool splices the stashed messages back into the live
history at the marker's position.

Read-class tool — cheap, no token spend beyond the splice. Baseline
RBAC scope: every agent gets it without explicit grant. Inert in
practice unless a subtask was previously stashed in the same run.
"""
from __future__ import annotations

from typing import Any

from agents.tools.base import BaseTool


def _err(detail: str) -> dict[str, Any]:
    return {"error": detail}


class RecallSubtask(BaseTool):
    """Re-hydrate a stashed subtask's full tool-call history."""

    name = "recall-subtask"
    priority = 50  # read-class, near git-log / fs-read
    when_to_use = (
        "you closed subtask X earlier and now need its tool details — "
        "splices the stashed span back into your context."
    )
    description = (
        "Re-hydrate a previously-closed subtask's message history. The "
        "marker `[SUBTASK CLOSED — id=X]` left in the live history is "
        "replaced with the original assistant + tool_result blocks "
        "produced while X was active. Inert (returns error) if the "
        "subtask wasn't stashed in this run."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "required": ["subtask_id"],
        "properties": {
            "subtask_id": {
                "type": "string",
                "description": "task_id of the previously-closed subtask",
            },
        },
    }

    def __init__(self, role: str, runner: Any):
        # Role identifies which agent's metrics row to bump on success.
        # Runner provides access to _current_agent_messages (the live
        # history we splice into) and _stashed_subtask_messages (the
        # source of the splice).
        self._role = role
        self._runner = runner

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        subtask_id = arguments.get("subtask_id")
        if not subtask_id or not isinstance(subtask_id, str):
            return _err("subtask_id is required")

        runner = self._runner
        stashed = runner._stashed_subtask_messages.get(subtask_id)
        if not stashed:
            return _err(
                f"no stash found for subtask {subtask_id!r} — "
                "either it never closed, never lived in this run, or "
                "was already restored."
            )

        # Find the marker position by scanning the live message list for
        # a user message whose content starts with the canonical sentinel
        # `[SUBTASK CLOSED — id=<subtask_id>`. Restrict to user-role
        # messages so a model's prose mentioning the marker text doesn't
        # cause a false positive.
        live = runner._current_agent_messages
        marker_idx: int | None = None
        sentinel = f"[SUBTASK CLOSED — id={subtask_id}"
        for i, msg in enumerate(live):
            if msg.role != "user":
                continue
            content = msg.content
            text = content if isinstance(content, str) else ""
            if text.startswith(sentinel):
                marker_idx = i
                break

        if marker_idx is None:
            # Stash exists but the marker is gone — splice at the end as
            # a best-effort fallback so the agent at least sees the
            # restored detail.
            live.extend(stashed)
            restored = len(stashed)
        else:
            # Replace the single marker with the original span.
            live[marker_idx:marker_idx + 1] = stashed
            restored = len(stashed)

        # Track 8 — stash is consumed on recall (matches the user's
        # mental model: "I asked for it back, it's back"). A subsequent
        # close re-stashes from scratch.
        runner._stashed_subtask_messages.pop(subtask_id, None)

        # Bump the iteration metric so the dashboard can show
        # restore activity. Fail silently if there's no agent or no
        # active metrics row.
        agent = (runner.agents or {}).get(self._role)
        if agent is not None:
            m = getattr(agent, "_current_metrics", None)
            if m is not None:
                m.subtask_messages_restored += restored

        return {"ok": True, "restored_messages": restored, "subtask_id": subtask_id}
