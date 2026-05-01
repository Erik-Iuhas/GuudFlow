"""Track 1c — end-to-end: repo_map content visible in effective_system_prompt.

Verifies that a real workspace whose repo_map is built from actual source
files produces a system prompt that lists those files. This is the
regression guard for the gap diagnosed across run_75df8cf97a and
run_26d48d4328 where workspace.repo_map was built but never reached any
agent's prompt.
"""
from __future__ import annotations

from pathlib import Path

from agents.base import BaseAgent
from core.repo_map import build as build_repo_map
from core.workspace import Workspace


async def test_repo_map_content_reaches_effective_system_prompt(tmp_path: Path):
    """Build a repo_map from a real tmp_path tree and confirm the prompt
    shows those files under the CODEBASE STRUCTURE layer."""
    # Three known python files with distinct symbols.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        "def login(user, pw):\n    return True\n\n"
        "def logout(token):\n    pass\n"
    )
    (tmp_path / "src" / "db.py").write_text(
        "class Connection:\n    def query(self, sql):\n        return []\n"
    )
    (tmp_path / "src" / "main.py").write_text(
        "from src.auth import login\nfrom src.db import Connection\n\n"
        "def main():\n    pass\n"
    )

    # Real repo_map build (tree-sitter parses, PageRank ranks).
    workspace = Workspace(run_id="run-1c-it", goal="repo_map-in-prompt")
    workspace.repo_map = build_repo_map(tmp_path)
    assert workspace.repo_map.entries, "repo_map should have entries from built tree"

    # Drive a real BaseAgent.
    agent = BaseAgent(role="developer", system_prompt="MARKER_BASE_PROMPT")
    agent._workspace = workspace

    prompt = await agent.effective_system_prompt()

    # The CODEBASE STRUCTURE layer is present.
    assert "CODEBASE STRUCTURE" in prompt

    # All three filenames appear in the rendered map.
    # (paths are relative + posix-style per repo_map's normalisation)
    assert "src/auth.py" in prompt
    assert "src/db.py" in prompt
    assert "src/main.py" in prompt

    # And the layer ordering is correct: structure before base prompt.
    assert prompt.index("CODEBASE STRUCTURE") < prompt.index("MARKER_BASE_PROMPT")


async def test_repo_map_byte_stable_within_iteration(tmp_path):
    """Track 1d — within an iteration, repeated prompt renders are byte-identical
    so the system-prompt cache holds across LLM calls.

    Two renders, no rebuild between → identical output.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("def foo():\n    return 1\n")
    (tmp_path / "src" / "bar.py").write_text("def bar():\n    return 2\n")

    workspace = Workspace(run_id="r-1d", goal="cache-stability")
    workspace.repo_map = build_repo_map(tmp_path)

    agent = BaseAgent(role="developer", system_prompt="X")
    agent._workspace = workspace

    p1 = await agent.effective_system_prompt()
    p2 = await agent.effective_system_prompt()
    assert p1 == p2, "prompt must be byte-stable across calls within an iteration"


async def test_repo_map_task_relevance_reflects_current_task(tmp_path):
    """Track 1d — switching _current_task between renders changes which files
    surface in the RELEVANT section."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        "def login():\n    pass\n\ndef logout():\n    pass\n"
    )
    (tmp_path / "src" / "render.py").write_text(
        "def draw_frame():\n    pass\n\ndef shake():\n    pass\n"
    )

    workspace = Workspace(run_id="r-1d-t", goal="task-aware")
    workspace.repo_map = build_repo_map(tmp_path)

    agent = BaseAgent(role="developer", system_prompt="X")
    agent._workspace = workspace

    # Task A — login flow → should surface auth.py.
    agent._current_task = "implement login flow"
    p_a = await agent.effective_system_prompt()
    relevance_a = p_a[p_a.index("RELEVANT TO YOUR CURRENT TASK"):p_a.index("PROTOCOL VOICE")]
    assert "src/auth.py" in relevance_a

    # Task B — frame rendering → should surface render.py instead.
    agent._current_task = "polish draw_frame shake animation"
    p_b = await agent.effective_system_prompt()
    relevance_b = p_b[p_b.index("RELEVANT TO YOUR CURRENT TASK"):p_b.index("PROTOCOL VOICE")]
    assert "src/render.py" in relevance_b
    assert "src/auth.py" not in relevance_b
