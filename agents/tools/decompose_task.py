"""Track 7 — decompose-task tool.

Receiver-side decomposition: an agent breaks the work in front of them
into a list of named subtasks. The runner advances the agent through
the subtasks in order; cascade closure auto-closes the parent when all
children terminal.

Two flows the same tool covers:
  - **Self-decompose** (no `parent_task_id`) — attaches the new
    children to the caller's synthetic root (`root:{role}`). Used when
    the agent has no formal task and is working against the project
    goal directly.
  - **Receiver further-decompose** (explicit `parent_task_id`) — the
    parent must be assigned to the caller and have no existing
    children.

Pairs with the delegator-side `assign-task subtasks=[...]` extension
(Step 5) — that one creates parent + children atomically; this one
drills into an existing task. Same Task model, same cascade closure.
"""
from __future__ import annotations

from typing import Any

from agents.tools.base import BaseTool


def _err(detail: str) -> dict[str, Any]:
    return {"error": detail}


def _ok(**kwargs: Any) -> dict[str, Any]:
    return {"ok": True, **kwargs}


class DecomposeTask(BaseTool):
    """Break the agent's current scope into ordered subtasks."""

    name = "decompose-task"
    priority = 120  # coordination band, same as assign-task
    when_to_use = "ONLY for INDEPENDENTLY-committable subtasks; for sequential/coupled steps just chain tool calls in one turn"
    description = (
        "Decompose a task into INDEPENDENTLY-COMMITTABLE subtasks assigned to "
        "yourself. Each subtask runs in its own context window — when subtask N "
        "closes, its tool history is stashed and replaced with a summary marker "
        "before subtask N+1 starts. That's a feature for unrelated work in the "
        "same area; it's a LOBOTOMY for sequential code that builds on prior "
        "subtasks (state → entities → scene). \n\n"
        "RIGHT use: 'fix 5 unrelated lint warnings', 'write 4 independent doc "
        "files', 'add 3 separate test cases that don't share fixtures'.\n"
        "WRONG use: 'implement state → use state for entities → use entities "
        "for scene' — that's a single coherent unit, just chain fs-write calls "
        "within ONE iteration (you have 30 tool-iterations per turn).\n\n"
        "Without parent_task_id, attaches to your synthetic root. With "
        "parent_task_id, drills into a task already on your desk — the parent "
        "must be assigned to you and have no existing children."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "required": ["subtasks"],
        "properties": {
            "subtasks": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Ordered list of subtask specs. Runner activates them "
                    "FIFO as you close each one."
                ),
                "items": {
                    "type": "object",
                    "required": ["goal"],
                    "properties": {
                        "goal": {
                            "type": "string",
                            "description": "One sentence: what the subtask accomplishes.",
                        },
                        "acceptance": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Verifiable assertions for this subtask. "
                                "Optional but reviewers check them."
                            ),
                        },
                    },
                },
            },
            "parent_task_id": {
                "type": "string",
                "description": (
                    "Optional task on your desk to decompose. Defaults to "
                    "your run's synthetic root."
                ),
            },
        },
    }

    def __init__(self, from_role: str, workspace: Any):
        self._from_role = from_role
        self._workspace = workspace

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        subtasks_raw = arguments.get("subtasks")
        if not isinstance(subtasks_raw, list) or not subtasks_raw:
            return _err("subtasks must be a non-empty list")

        # Validate every subtask spec UP FRONT before mutating workspace.
        # Avoids partial-create state if subtask 3 of 4 is malformed.
        normalised: list[dict[str, Any]] = []
        for i, spec in enumerate(subtasks_raw):
            if not isinstance(spec, dict):
                return _err(f"subtasks[{i}] must be an object")
            goal = spec.get("goal")
            if not goal or not isinstance(goal, str):
                return _err(f"subtasks[{i}].goal is required")
            acceptance_raw = spec.get("acceptance") or []
            if not isinstance(acceptance_raw, list):
                return _err(f"subtasks[{i}].acceptance must be a list")
            acceptance = [str(a).strip() for a in acceptance_raw if str(a).strip()]
            normalised.append({"goal": goal.strip(), "acceptance": acceptance})

        # Resolve parent — explicit > synthetic root.
        parent_id = arguments.get("parent_task_id")
        ws = self._workspace
        if parent_id:
            if not isinstance(parent_id, str):
                return _err("parent_task_id must be a string task_id")
            parent = ws.tasks.get(parent_id)
            if parent is None:
                return _err(f"unknown parent_task_id {parent_id!r}")
            if parent.assigned_to != self._from_role:
                return _err(
                    f"parent_task_id {parent_id!r} is assigned to "
                    f"{parent.assigned_to!r}, not you ({self._from_role!r}). "
                    "You can only decompose your own tasks."
                )
            existing_children = ws.child_tasks(parent_id)
            if existing_children:
                return _err(
                    f"parent_task_id {parent_id!r} already has "
                    f"{len(existing_children)} children — cannot decompose "
                    "again. Close the existing children or pick a different parent."
                )
        else:
            parent_id = f"root:{self._from_role}"
            if parent_id not in ws.tasks:
                # Defensive — runner creates these at startup, but if
                # something skipped that path, surface clearly.
                return _err(
                    "no synthetic root for this role; the runner should "
                    "have created one at run start"
                )
            existing_children = ws.child_tasks(parent_id)
            if existing_children:
                return _err(
                    "your synthetic root already has children. Pass an "
                    "explicit parent_task_id to decompose a specific task."
                )

        # All validation passed — create children. Import here so the
        # module stays cheap to load when the tool isn't used.
        from core.workspace import Task

        iteration = ws.iteration
        story_id = ws.active_story.get(self._from_role, "") if ws.active_story else ""
        subtask_ids: list[str] = []
        for spec in normalised:
            t = Task(
                task_id="",  # workspace generates fresh uuid
                story_id=story_id,
                parent_task_id=parent_id,
                assigned_to=self._from_role,
                created_by=self._from_role,
                created_at_iter=iteration,
                goal=spec["goal"],
                acceptance_criteria=spec["acceptance"],
            )
            try:
                ws.add_task(t)
            except ValueError as e:
                # Surface depth-cap and similar add_task rejections as a
                # clean tool error (consistent with the up-front
                # validation cases above) rather than letting the
                # exception bubble out of the tool.
                return _err(
                    f"failed to add subtasks[{len(subtask_ids)}]: {e}"
                )
            subtask_ids.append(t.task_id)

        return _ok(
            parent_task_id=parent_id,
            subtask_ids=subtask_ids,
            count=len(subtask_ids),
        )
