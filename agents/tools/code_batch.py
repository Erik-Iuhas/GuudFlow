"""Batch code-outline — outline many files in one tool call.

Track 3e. When an agent wants structural detail across several files
during a navigation pass, this avoids N sequential round-trips.
"""
from __future__ import annotations

from typing import Any

from agents.tools.base import BaseTool
from agents.tools.code import CodeOutline
from core.file_workspace import FileWorkspace


_MAX_PATHS = 10


class CodeOutlineBatch(BaseTool):
    """Outline many source files in one tool call.

    Each path is delegated to the singular `code-outline` per-file logic.
    Per-path failures (file not found, unsupported language, parse errors)
    are reported individually; successes are kept.
    """

    name = "code-outline-batch"
    priority = 22
    when_to_use = "PREFER over code-outline when navigating ≥ 3 files at once"
    description = (
        "Extract structural outlines for many files in one tool call. Use "
        "during navigation passes when you want signatures across several "
        "files — one batch call replaces N sequential code-outline round-trips."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "description": (
                    f"List of file paths relative to working/ (max {_MAX_PATHS})."
                ),
                "items": {"type": "string"},
            },
        },
        "required": ["paths"],
    }

    def __init__(self, files: FileWorkspace):
        self.files = files
        self._delegate = CodeOutline(files)

    def aci_doc(self) -> dict[str, str]:
        return {
            "purpose": "Outline many source files in one tool call (Track 3e).",
            "args": "{paths: [string, ...]}",
            "emits": (
                "{ok:true, outlined:N, failed:M, "
                "outlines: [{path, ...}, ...], failures: [{path, error}, ...]}"
            ),
            "errors": "empty list | exceeds 10 paths | per-path failures",
            "example": (
                'code-outline-batch({"paths": ["src/a.py", "src/b.py", "src/c.py"]})'
            ),
            "notes": "Hard cap of 10 paths per call.",
        }

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        paths = arguments.get("paths")
        if not isinstance(paths, list) or len(paths) == 0:
            return {"error": "missing/empty argument: paths (non-empty list required)"}
        if len(paths) > _MAX_PATHS:
            return {
                "error": (
                    f"too many paths: {len(paths)} (max {_MAX_PATHS}; "
                    "split into multiple calls)"
                )
            }

        outlines: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []

        for idx, p in enumerate(paths):
            if not isinstance(p, str) or not p:
                failures.append({"path": f"<entry {idx}>", "error": "invalid path"})
                continue
            result = await self._delegate.run({"path": p})
            if "error" in result:
                failures.append({"path": p, "error": str(result["error"])})
            else:
                outlines.append(result)

        return {
            "ok": True,
            "outlined": len(outlines),
            "failed": len(failures),
            "outlines": outlines,
            "failures": failures,
        }
