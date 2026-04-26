"""Text tools — `text:diff`, `text:estimate_tokens`.

Both are pure / read-only. `text:diff` produces a unified diff between
two strings or two files (read via FileWorkspace). `text:estimate_tokens`
returns an approximate token count for a string or a file — char-count
divided by 4, matching the heuristic in `core/repo_map.py` and
`core/context_manager.py`.
"""

from __future__ import annotations

import difflib
from typing import Any

from agents.tools.base import BaseTool
from core.file_workspace import FileWorkspace, PathSafetyError


def _err(msg: str) -> dict[str, Any]:
    return {"error": msg}


# ── text:diff ───────────────────────────────────────────────────────────────


class TextDiff(BaseTool):
    """Unified diff between two strings or two files.

    Caller provides either `a` and `b` (raw strings) or `a_path` and
    `b_path` (paths read via FileWorkspace). Mixing is allowed (`a` raw
    + `b_path` from disk). Returns the unified diff text plus a
    `has_changes` flag.
    """

    name = "text-diff"
    description = "Unified diff between two strings or two files. Pass `a`/`b` for raw text or `a_path`/`b_path` for files."
    input_schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "string"},
            "a_path": {"type": "string"},
            "b_path": {"type": "string"},
            "a_label": {"type": "string", "description": "label for a in the diff header"},
            "b_label": {"type": "string", "description": "label for b in the diff header"},
            "context_lines": {"type": "integer", "description": "default 3"},
            "from": {"type": "string", "enum": ["working", "inputs"], "description": "default working"},
        },
    }

    def __init__(self, files: FileWorkspace | None = None):
        self.files = files

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        a_text = arguments.get("a")
        b_text = arguments.get("b")
        a_path = arguments.get("a_path")
        b_path = arguments.get("b_path")
        source = arguments.get("from", "working")
        context = int(arguments.get("context_lines", 3))

        if a_text is None and a_path is None:
            return _err("must provide `a` or `a_path`")
        if b_text is None and b_path is None:
            return _err("must provide `b` or `b_path`")

        if a_path is not None:
            if self.files is None:
                return _err("a_path given but no FileWorkspace available")
            a_text, err = await self._read(a_path, source)
            if err is not None:
                return err
        if b_path is not None:
            if self.files is None:
                return _err("b_path given but no FileWorkspace available")
            b_text, err = await self._read(b_path, source)
            if err is not None:
                return err

        a_label = arguments.get("a_label") or (a_path or "a")
        b_label = arguments.get("b_label") or (b_path or "b")

        a_lines = (a_text or "").splitlines(keepends=True)
        b_lines = (b_text or "").splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(
            a_lines, b_lines,
            fromfile=a_label, tofile=b_label,
            n=context,
        ))
        diff_text = "".join(diff_lines)
        return {
            "ok": True,
            "diff": diff_text,
            "has_changes": bool(diff_lines),
            "a_lines": len(a_lines),
            "b_lines": len(b_lines),
        }

    async def _read(self, path: str, source: str) -> tuple[str | None, dict | None]:
        try:
            if source == "inputs":
                text = await self.files.read_input(path)  # type: ignore[union-attr]
            else:
                text = await self.files.read(path)  # type: ignore[union-attr]
        except PathSafetyError as e:
            return None, _err(f"path safety: {e}")
        except FileNotFoundError:
            return None, _err(f"not found: {path}")
        except OSError as e:
            return None, _err(f"read failed: {e}")
        return text, None


# ── text:estimate_tokens ────────────────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    """Approximation: chars/4. Matches context_manager and repo_map."""
    return max(1, len(text) // 4) if text else 0


class TextEstimateTokens(BaseTool):
    """Approximate token count for a string or a file. Useful for agents
    deciding "should I read this whole file?" before pulling it in.

    Uses char-count / 4 — the same heuristic context_manager uses for
    its budget tracking, so estimates here line up with the budget.
    """

    name = "text-estimate_tokens"
    description = "Estimate token count for a string or a file path. Approximation: chars/4 (same heuristic as context_manager)."
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "path": {"type": "string"},
            "from": {"type": "string", "enum": ["working", "inputs"], "description": "default working"},
        },
    }

    def __init__(self, files: FileWorkspace | None = None):
        self.files = files

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        text = arguments.get("text")
        path = arguments.get("path")
        source = arguments.get("from", "working")

        if text is None and not path:
            return _err("must provide `text` or `path`")
        if text is not None and path:
            return _err("provide either `text` or `path`, not both")

        if path:
            if self.files is None:
                return _err("path given but no FileWorkspace available")
            try:
                if source == "inputs":
                    content = await self.files.read_input(path)
                else:
                    content = await self.files.read(path)
            except PathSafetyError as e:
                return _err(f"path safety: {e}")
            except FileNotFoundError:
                return _err(f"not found: {path}")
            except OSError as e:
                return _err(f"read failed: {e}")
            return {
                "ok": True,
                "path": path,
                "chars": len(content),
                "estimated_tokens": _estimate_tokens(content),
            }

        return {
            "ok": True,
            "chars": len(text),  # type: ignore[arg-type]
            "estimated_tokens": _estimate_tokens(text),  # type: ignore[arg-type]
        }
