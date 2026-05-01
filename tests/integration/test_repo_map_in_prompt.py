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
