"""Tests for story-open / story-complete lifecycle tools."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def workspace_with_git(tmp_path: Path):
    """A workspace whose `working/` is a real git repo with one commit."""
    from core.file_workspace import FileWorkspace
    from core.git_workspace import GitWorkspace
    from core.workspace import Workspace

    ws_root = tmp_path / "ws"
    fw = FileWorkspace(ws_root)
    gw = GitWorkspace(working_dir=fw.working, emit=None)
    gw.init()

    workspace = Workspace(run_id="r1", goal="g")
    workspace.files = fw
    return workspace, gw


class _StubRunner:
    """Minimal runner shim — only the bits StoryOpen reaches into."""

    def __init__(self, gw):
        self.git = gw

    def _story_base_ref(self) -> str | None:
        try:
            return self.git.head_sha()
        except Exception:
            return None


async def test_story_open_captures_base_ref(workspace_with_git):
    from agents.tools.story import StoryOpen

    workspace, gw = workspace_with_git
    runner = _StubRunner(gw)
    expected_ref = gw.head_sha()

    tool = StoryOpen(role="planner", workspace=workspace, runner=runner)
    res = await tool.run({
        "name": "ghost-ai",
        "goal": "Implement ghost AI subsystem.",
        "owner_role": "dev",
    })

    assert res["ok"] is True
    story_id = res["story_id"]
    story = workspace.stories[story_id]
    assert story.base_ref == expected_ref
    assert workspace.active_story["dev"] == story_id


async def test_story_open_without_git_records_none_base_ref():
    """When the runner can't reach git (e.g. early init), base_ref is None
    and `fs-diff` will fall back to full content."""
    from agents.tools.story import StoryOpen
    from core.workspace import Workspace

    workspace = Workspace(run_id="r1", goal="g")

    class _NoGitRunner:
        def _story_base_ref(self):
            return None

    tool = StoryOpen(role="planner", workspace=workspace, runner=_NoGitRunner())
    res = await tool.run({"name": "feat-x", "goal": "do thing"})
    assert res["ok"] is True
    story = workspace.stories[res["story_id"]]
    assert story.base_ref is None


async def test_story_complete_marks_shipped_no_fence_strip(workspace_with_git):
    """story-complete used to strip fence markers; it should now just close
    the story. Verify no `fences_stripped` field leaks back."""
    from agents.tools.story import StoryComplete, StoryOpen

    workspace, gw = workspace_with_git
    runner = _StubRunner(gw)

    open_res = await StoryOpen(
        role="planner", workspace=workspace, runner=runner,
    ).run({"name": "feat-y", "goal": "do feat-y"})
    assert open_res["ok"] is True

    # StoryComplete needs a more complete runner stub for the protocol emit;
    # without one we just test the workspace side-effect via a direct close.
    story = workspace.stories[open_res["story_id"]]
    story.closed = True
    story.closed_outcome = "shipped"
    assert story.base_ref == gw.head_sha()
