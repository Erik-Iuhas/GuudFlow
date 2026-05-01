"""Story lifecycle tools — `story-complete` and `story-open`.

Planners, architects, and requirements writers use these to manage the
story units that scope work. Story scoping is git-based: each story
captures a `base_ref` at open time, and agents diff docs against that
ref via `fs-diff` to see what's new for their story.

`story-complete`: mark a story as shipped.

`story-open`: open a new story mid-run (for planners who discover the work
needs a new scope during execution, rather than waiting for the next proposal).
"""

from __future__ import annotations

from typing import Any

from agents.tools.base import BaseTool


def _err(msg: str) -> dict[str, Any]:
    return {"error": msg}


def _ok(**kwargs: Any) -> dict[str, Any]:
    return {"ok": True, **kwargs}


class StoryComplete(BaseTool):
    """Mark a story as shipped.

    Call this when the work for a story is genuinely done, not just paused.
    The story's `base_ref` is preserved so reviewers can still diff what
    shipped via `fs-diff`.

    Use `story-open` for the reverse — starting a new story mid-run.
    """

    name = "story-complete"
    input_schema = {
        "type": "object",
        "properties": {
            "story_name": {
                "type": "string",
                "description": (
                    "The story slug to close (e.g. 'auth-flow'). "
                    "Use fs-list to see which stories own which docs."
                ),
            },
            "summary": {
                "type": "string",
                "description": "One sentence describing what was shipped.",
            },
        },
        "required": ["story_name"],
    }

    def __init__(self, role: str, workspace: Any, runner: Any = None):
        self._role = role
        self._workspace = workspace
        self._runner = runner

    def aci_doc(self) -> dict[str, str]:
        return {
            "purpose": "Mark a story as shipped.",
            "args": "{story_name:string, summary?:string}",
            "emits": "{ok:true, story_name, story_id}",
            "errors": "story not found | already closed",
            "example": 'story-complete({"story_name": "auth-flow", "summary": "JWT auth implemented and reviewed."})',
        }

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        story_name = str(arguments.get("story_name") or "").strip()
        summary = str(arguments.get("summary") or "").strip()
        if not story_name:
            return _err("missing required argument: story_name")

        # Find the story by name.
        story_id: str | None = None
        for sid, s in self._workspace.stories.items():
            if s.name == story_name:
                story_id = sid
                break
        if story_id is None:
            known = [s.name for s in self._workspace.stories.values()]
            return _err(
                f"story {story_name!r} not found. Known stories: {known or ['(none)']}"
            )

        story = self._workspace.stories[story_id]
        if story.closed:
            return _err(
                f"story {story_name!r} is already closed (outcome={story.closed_outcome!r})"
            )

        story.closed = True
        story.closed_outcome = "shipped"
        story.closed_summary = summary[:120] if summary else f"completed by {self._role}"

        if self._runner is not None:
            from core.protocol import render_story_closed
            from core.signals import Signal, SignalType
            rendered = render_story_closed(
                story_id=story_id,
                outcome="shipped",
                summary=story.closed_summary,
            )
            self._workspace.post_signal(Signal(
                type=SignalType.PROTOCOL,
                summary=f"STORY_CLOSED {story_name} (shipped)",
                from_agent=self._role,
                detail=rendered,
            ))

        return _ok(
            story_name=story_name,
            story_id=story_id,
        )


class StoryOpen(BaseTool):
    """Open a new story mid-run.

    Use when you discover during execution that a distinct scope of work
    needs its own story — for example a planner who reads the source and
    finds a third subsystem not covered by the seeded stories.

    New stories become the active story for the owner_role (if specified)
    or adopt-on-first-use for unowned roles.
    """

    name = "story-open"
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Story slug — lowercase, hyphens, max 40 chars."
                ),
            },
            "goal": {
                "type": "string",
                "description": "One sentence: what does done look like for this story?",
            },
            "owner_role": {
                "type": "string",
                "description": (
                    "Role that owns this story (it becomes their active story). "
                    "Omit for team-wide stories."
                ),
            },
        },
        "required": ["name", "goal"],
    }

    def __init__(self, role: str, workspace: Any, runner: Any = None):
        self._role = role
        self._workspace = workspace
        self._runner = runner

    def aci_doc(self) -> dict[str, str]:
        return {
            "purpose": "Open a new story mid-run to scope a newly-discovered work unit.",
            "args": "{name:string, goal:string, owner_role?:string}",
            "emits": "{ok:true, story_id, name, goal}",
            "errors": "duplicate name | missing name/goal",
            "example": 'story-open({"name": "perf-fixes", "goal": "Reduce load time below 200ms.", "owner_role": "backend_dev"})',
        }

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        import uuid as _uuid
        from core.workspace import StoryState

        name = str(arguments.get("name") or "").strip().lower().replace(" ", "-")
        goal = str(arguments.get("goal") or "").strip()
        owner_role = str(arguments.get("owner_role") or "").strip() or None

        if not name:
            return _err("missing required argument: name")
        if not goal:
            return _err("missing required argument: goal")
        if len(name) > 40:
            return _err(f"name too long ({len(name)} chars, max 40)")

        # Reject duplicate names.
        for s in self._workspace.stories.values():
            if s.name == name and not s.closed:
                return _err(f"story {name!r} already exists and is open")

        story_id = _uuid.uuid4().hex
        iteration = getattr(self._workspace, "iteration", 0)
        base_ref: str | None = None
        if self._runner is not None:
            base_ref = self._runner._story_base_ref()
        self._workspace.stories[story_id] = StoryState(
            story_id=story_id,
            name=name,
            goal=goal,
            owner_role=owner_role,
            opened_at_iteration=iteration,
            base_ref=base_ref,
        )

        if owner_role:
            self._workspace.active_story[owner_role] = story_id
        else:
            # Team-wide: assign to any role without a more-specific story.
            for role, current_sid in list(self._workspace.active_story.items()):
                current = self._workspace.stories.get(current_sid)
                if current is None or current.closed:
                    self._workspace.active_story[role] = story_id

        if self._runner is not None:
            from core.protocol import render_story_opened
            from core.signals import Signal, SignalType
            rendered = render_story_opened(
                story_id=story_id,
                name=name,
                goal=goal,
                owner_role=owner_role,
                parent_story_id=None,
            )
            self._workspace.post_signal(Signal(
                type=SignalType.PROTOCOL,
                summary=f"STORY_OPENED {name}",
                from_agent=self._role,
                detail=rendered,
            ))

        return _ok(story_id=story_id, name=name, goal=goal)
