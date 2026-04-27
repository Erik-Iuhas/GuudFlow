"""Git tools — `git:*`. Wraps core/git_workspace.GitWorkspace.

DESIGN §31, §19. Read tools (`git:diff`, `git:log`, `git:read`,
`git:status`) are baseline. `git:commit` is NOT exposed as a tool —
agents commit by completing a step (DESIGN §19).
"""

from __future__ import annotations

from typing import Any

from agents.tools.base import BaseTool
from core.git_workspace import GitWorkspace, GitWorkspaceError


def _err(msg: str) -> dict[str, Any]:
    return {"error": msg}


_DIFF_CHAR_LIMIT = 6000  # ~1500 tokens — safe for a single file diff


class GitDiff(BaseTool):
    name = "git-diff"
    description = (
        "Show the diff between two branches. "
        "Always pass `path` to diff a single file — never call without `path` "
        "on a large codebase, or you will exceed token limits. "
        "Use git-changed-files first to get the list of changed paths."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "branch_a": {"type": "string", "description": "branch with new changes"},
            "branch_b": {"type": "string", "description": "base branch (default 'main')"},
            "path": {"type": "string", "description": "restrict diff to this file path"},
        },
        "required": ["branch_a"],
    }

    def __init__(self, git: GitWorkspace):
        self.git = git

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        a = arguments.get("branch_a")
        b = arguments.get("branch_b", "main")
        path = arguments.get("path") or None
        if not a or not isinstance(a, str):
            return _err("missing required argument: branch_a")
        try:
            diff = self.git.diff(a, b, path=path)
        except GitWorkspaceError as e:
            return _err(str(e))
        except Exception as e:
            return _err(f"git diff failed: {e}")
        truncated = False
        if len(diff) > _DIFF_CHAR_LIMIT:
            diff = diff[:_DIFF_CHAR_LIMIT]
            truncated = True
        return {
            "ok": True,
            "branch_a": a,
            "branch_b": b,
            "path": path,
            "diff": diff,
            "truncated": truncated,
        }


class GitChangedFiles(BaseTool):
    name = "git-changed-files"
    description = "List file paths changed in branch_a vs branch_b. Use this before git-diff to review files one at a time."
    input_schema = {
        "type": "object",
        "properties": {
            "branch_a": {"type": "string", "description": "branch with new changes"},
            "branch_b": {"type": "string", "description": "base branch (default 'main')"},
        },
        "required": ["branch_a"],
    }

    def __init__(self, git: GitWorkspace):
        self.git = git

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        a = arguments.get("branch_a")
        b = arguments.get("branch_b", "main")
        if not a or not isinstance(a, str):
            return _err("missing required argument: branch_a")
        try:
            files = self.git.changed_files(a, b)
        except GitWorkspaceError as e:
            return _err(str(e))
        except Exception as e:
            return _err(f"git changed-files failed: {e}")
        return {"ok": True, "branch_a": a, "branch_b": b, "files": files, "count": len(files)}


class GitLog(BaseTool):
    name = "git-log"
    description = "List recent commits on a branch (default current HEAD)."
    input_schema = {
        "type": "object",
        "properties": {
            "branch": {"type": "string"},
            "limit": {"type": "integer"},
        },
    }

    def __init__(self, git: GitWorkspace):
        self.git = git

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        branch = arguments.get("branch")
        limit = arguments.get("limit", 10)
        try:
            entries = self.git.log(branch=branch, limit=int(limit))
        except GitWorkspaceError as e:
            return _err(str(e))
        except Exception as e:
            return _err(f"git log failed: {e}")
        return {
            "ok": True,
            "branch": branch,
            "commits": [
                {
                    "hash": e.hash,
                    "branch": e.branch,
                    "author": e.author,
                    "message": e.message,
                    "ts": e.timestamp.isoformat(),
                }
                for e in entries
            ],
        }


class GitStatus(BaseTool):
    name = "git-status"
    description = "Show uncommitted changes on the current branch (staged, unstaged, untracked)."
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, git: GitWorkspace):
        self.git = git

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self.git.status()
        except GitWorkspaceError as e:
            return _err(str(e))
        except Exception as e:
            return _err(f"git status failed: {e}")
        return {
            "ok": True,
            "branch": result.branch,
            "clean": result.clean,
            "entries": [
                {"path": e.path, "state": e.state, "staged": e.staged}
                for e in result.entries
            ],
        }


class GitRead(BaseTool):
    name = "git-read"
    description = "Read a file's contents at the tip of a specific branch (no checkout)."
    input_schema = {
        "type": "object",
        "properties": {
            "branch": {"type": "string"},
            "path": {"type": "string"},
        },
        "required": ["branch", "path"],
    }

    def __init__(self, git: GitWorkspace):
        self.git = git

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        branch = arguments.get("branch")
        path = arguments.get("path")
        if not branch or not isinstance(branch, str):
            return _err("missing required argument: branch")
        if not path or not isinstance(path, str):
            return _err("missing required argument: path")
        try:
            content = self.git.read_at(branch, path)
        except FileNotFoundError as e:
            return _err(str(e))
        except GitWorkspaceError as e:
            return _err(str(e))
        except Exception as e:
            return _err(f"git read failed: {e}")
        return {"ok": True, "branch": branch, "path": path, "content": content}
