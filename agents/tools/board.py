"""Board tools — agent-to-agent messaging on the shared workspace board.

Agents use `board-message` to direct a question or note to another agent
role without blocking the scheduler. The recipient reads pending messages
injected into their task prompt at turn start.

`assign-task` (M3.5) is the planner-only counterpart: it puts a typed
work item with acceptance criteria on another agent's desk.
"""

from __future__ import annotations

import json
from typing import Any

from agents.tools.base import BaseTool
from core.signals import Signal, SignalType
from core.workspace import Task, TaskRef


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
    priority = 120
    when_to_use = "post a directed message to a peer agent on the board"
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


_REF_KINDS: frozenset[str] = frozenset({"file", "commit", "task", "story"})


class AssignTask(BaseTool):
    """Place a typed work item on another agent's desk (DESIGN §38.12).

    Only planning archetypes (tech_lead, architect, requirements_writer,
    meta) hold the `assign-task` RBAC scope. Specialists and reviewers
    calling this tool will see a permission denial at the manifest layer.

    The acceptance criteria are the contract: reviewers will check the
    assignee's commit against them. Spell out the verifiable bits.

    Examples:
      - architect → developer: goal "build the JWT issuer", acceptance
        ["rs256 signing", "key rotation", "rate-limit 1000 rpm"],
        out_of_scope ["oauth2 social"], references
        [{"kind": "file", "value": "docs/auth.md", "note": "design"}]
      - tech_lead → reviewer: goal "re-review developer's third attempt",
        acceptance ["focus on the cache invalidation in store.py"]
    """

    name = "assign-task"
    priority = 120
    when_to_use = "hand a structured task with acceptance criteria to another agent"
    description = (
        "Put a typed work item on another agent's desk. The task carries "
        "a goal, acceptance criteria the reviewer will check against, "
        "optional out-of-scope exclusions, and source-of-truth references "
        "(files, commits, parent tasks, stories). Returns the task_id."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "assignee": {
                "type": "string",
                "description": "Role of the agent receiving the task (e.g. 'developer').",
            },
            "goal": {
                "type": "string",
                "description": "One sentence: what to accomplish.",
            },
            "acceptance": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Named, verifiable assertions. Reviewers check these "
                    "explicitly. Be specific."
                ),
            },
            "out_of_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional explicit exclusions to catch drift early.",
            },
            "parent_task": {
                "type": "string",
                "description": (
                    "Optional task_id of the parent. Subtasks cap at depth 2."
                ),
            },
            "story": {
                "type": "string",
                "description": (
                    "Optional story_id. Defaults to the planner's active story."
                ),
            },
            "references": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["file", "commit", "task", "story"]},
                        "value": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["kind", "value"],
                },
                "description": (
                    "Source-of-truth pointers. Reviewers cross-check the "
                    "assignee's diff against these."
                ),
            },
            "subtasks": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Track 7 — optional ordered list of {goal, acceptance} "
                    "chunks. ONLY use when the chunks are INDEPENDENTLY "
                    "committable (each could ship alone). For sequential / "
                    "coupled work just send one task and let the assignee "
                    "chain tool calls. Each subtask's context window is "
                    "isolated — its tool history gets stashed as a summary "
                    "before the next subtask starts. That's a feature for "
                    "unrelated work, a lobotomy for code that builds on prior "
                    "subtasks. When present the runner creates parent + "
                    "children atomically; assignee gets the parent "
                    "IN_PROGRESS and the children PENDING in order."
                ),
                "items": {
                    "type": "object",
                    "required": ["goal"],
                    "properties": {
                        "goal": {"type": "string"},
                        "acceptance": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
        },
        "required": ["assignee", "goal"],
    }

    def __init__(self, from_role: str, workspace: Any, runner: Any | None = None):
        self._from_role = from_role
        self._workspace = workspace
        # Optional runner handle so the tool can read the planner's
        # active story when the caller doesn't pass one explicitly.
        self._runner = runner

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        assignee = arguments.get("assignee")
        goal = arguments.get("goal")
        if not assignee or not isinstance(assignee, str):
            return _err("missing required argument: assignee")
        if not goal or not isinstance(goal, str):
            return _err("missing required argument: goal")

        roster = set(getattr(self._workspace, "agent_roles", None) or set())
        if roster and assignee not in roster:
            return _err(
                f"unknown role {assignee!r}. The team is {sorted(roster)}. "
                f"Reassign to one of these — or do the work yourself with "
                f"fs-write instead of delegating."
            )

        acceptance_raw = arguments.get("acceptance") or []
        if not isinstance(acceptance_raw, list):
            return _err("acceptance must be a list of strings")
        acceptance = [str(a).strip() for a in acceptance_raw if str(a).strip()]

        out_of_scope_raw = arguments.get("out_of_scope") or []
        if not isinstance(out_of_scope_raw, list):
            return _err("out_of_scope must be a list of strings")
        out_of_scope = [str(o).strip() for o in out_of_scope_raw if str(o).strip()]

        parent_task = arguments.get("parent_task") or None
        if parent_task is not None and not isinstance(parent_task, str):
            return _err("parent_task must be a string task_id")

        story = arguments.get("story") or None
        if story is None:
            # Default to the planner's active story if known.
            story = self._workspace.active_story.get(self._from_role)

        refs_raw = arguments.get("references") or []
        if not isinstance(refs_raw, list):
            return _err("references must be a list of objects")
        references: list[TaskRef] = []
        for ref in refs_raw:
            if not isinstance(ref, dict):
                return _err("each reference must be an object with kind/value/note")
            kind = str(ref.get("kind") or "")
            value = str(ref.get("value") or "")
            note = str(ref.get("note") or "")
            if kind not in _REF_KINDS:
                return _err(
                    f"reference kind must be one of {sorted(_REF_KINDS)}, got {kind!r}"
                )
            if not value:
                return _err("reference.value is required")
            references.append(TaskRef(kind=kind, value=value, note=note))

        # Track 7 — optional `subtasks` list for atomic parent + children.
        # Validate every entry up front so we don't half-commit a malformed
        # batch to the desk.
        subtasks_raw = arguments.get("subtasks")
        normalised_subtasks: list[dict[str, Any]] = []
        if subtasks_raw is not None:
            if not isinstance(subtasks_raw, list) or not subtasks_raw:
                return _err("subtasks must be a non-empty list when provided")
            for i, spec in enumerate(subtasks_raw):
                if not isinstance(spec, dict):
                    return _err(f"subtasks[{i}] must be an object")
                sub_goal = spec.get("goal")
                if not sub_goal or not isinstance(sub_goal, str):
                    return _err(f"subtasks[{i}].goal is required")
                sub_acc_raw = spec.get("acceptance") or []
                if not isinstance(sub_acc_raw, list):
                    return _err(f"subtasks[{i}].acceptance must be a list")
                sub_acc = [str(a).strip() for a in sub_acc_raw if str(a).strip()]
                normalised_subtasks.append({
                    "goal": sub_goal.strip(),
                    "acceptance": sub_acc,
                })

        iteration = getattr(self._workspace, "iteration", 0)
        task = Task(
            task_id="",  # workspace.add_task auto-generates if blank
            story_id=str(story) if story else "",
            parent_task_id=parent_task,
            assigned_to=assignee,
            created_by=self._from_role,
            created_at_iter=iteration,
            goal=goal.strip(),
            acceptance_criteria=acceptance,
            out_of_scope=out_of_scope,
            references=references,
        )
        try:
            self._workspace.add_task(task)
        except ValueError as e:
            return _err(str(e))

        # Create children under the just-created parent. Same iteration,
        # same story, all assigned to the assignee. If add_task fails on
        # any (e.g. depth-cap because parent_task is itself depth-3),
        # surface the error — parent already landed, but children are a
        # best-effort follow-on.
        subtask_ids: list[str] = []
        for spec in normalised_subtasks:
            child = Task(
                task_id="",
                story_id=task.story_id,
                parent_task_id=task.task_id,
                assigned_to=assignee,
                created_by=self._from_role,
                created_at_iter=iteration,
                goal=spec["goal"],
                acceptance_criteria=spec["acceptance"],
            )
            try:
                self._workspace.add_task(child)
            except ValueError as e:
                return _err(
                    f"created parent {task.task_id} but failed on subtasks[{len(subtask_ids)}]: {e}"
                )
            subtask_ids.append(child.task_id)

        # Mirror the structured ASSIGN_TASK protocol message onto the
        # board so reviewers and the studio see the same wire format
        # the design specifies (DESIGN §38.12 §2.1).
        from core.protocol import TaskRefField, render_assign_task
        rendered = render_assign_task(
            assignee, task.task_id,
            goal=task.goal,
            acceptance=task.acceptance_criteria,
            out_of_scope=task.out_of_scope,
            parent_task=task.parent_task_id,
            story=task.story_id or None,
            references=[
                TaskRefField(r.kind, r.value, r.note) for r in task.references
            ],
        )
        self._workspace.post_signal(Signal(
            type=SignalType.PROTOCOL,
            summary=f"ASSIGN_TASK {assignee} task:{task.task_id}: {goal[:60]}",
            from_agent=self._from_role,
            iteration=iteration,
            detail=rendered,
        ))

        warnings: list[str] = []
        if not acceptance:
            warnings.append(
                "no acceptance criteria — reviewer has nothing to check against"
            )

        result: dict[str, Any] = {
            "ok": True,
            "task_id": task.task_id,
            "assignee": assignee,
            "warnings": warnings,
        }
        # Track 7 — surface child ids when subtasks were created.
        if subtask_ids:
            result["subtask_ids"] = subtask_ids
        return result
