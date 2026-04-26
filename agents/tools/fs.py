"""Filesystem tools — `fs:*`.

DESIGN §31. All paths scoped to `workspace/working/` via `FileWorkspace`.
Path safety is enforced by `FileWorkspace._safe_path` (DESIGN rule 62).

Read tools (`fs:read`, `fs:list`, `fs:exists`) are baseline — every agent
gets them without explicit RBAC scope. Write tools (`fs:write`, `fs:edit`,
`fs:create`, `fs:delete`, `fs:rename`) require explicit `fs:*` scope and
auto-emit `FILE_CLAIM` / `FILE_RELEASE` signals (DESIGN §32) when called.

For Stage 4 the auto-claim logic is implemented INSIDE the tools — they
take an optional `claim_emitter` callback (set by BaseAgent at instantiation).
If unset, claims are silently skipped (useful for tests of the tools in
isolation). Stage 5's runner will wire up real claim/release emitters.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from agents.tools.base import BaseTool
from core.file_workspace import FileWorkspace, PathSafetyError


ClaimEmitter = Callable[[str, str], Awaitable[None]]
"""(intent, path) → None.  intent ∈ {"edit", "create", "delete", "rename"}.
The emitter posts a FILE_CLAIM. The release is fired by the tool itself
on success/failure."""

ReleaseEmitter = Callable[[str, str, str | None], Awaitable[None]]
"""(path, outcome, commit_hash) → None.  outcome ∈ {"committed", "aborted"}.
"committed" is fired even before a real git commit — claim release is
about coordination, not git state. The runner can lift the outcome to
"committed" on actual commit later."""


def _err(msg: str) -> dict[str, Any]:
    return {"error": msg}


def _ok(**kwargs: Any) -> dict[str, Any]:
    return {"ok": True, **kwargs}


# ── Read tools (baseline) ───────────────────────────────────────────────────


class FsRead(BaseTool):
    name = "fs-read"
    description = "Read the full contents of a file under working/ as text (UTF-8)."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "path relative to working/"}},
        "required": ["path"],
    }

    def __init__(self, files: FileWorkspace):
        self.files = files

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        if not path or not isinstance(path, str):
            return _err("missing required argument: path")
        try:
            content = await self.files.read(path)
        except PathSafetyError as e:
            return _err(f"path safety: {e}")
        except FileNotFoundError:
            return _err(f"not found: {path}")
        except OSError as e:
            return _err(f"read failed: {e}")
        return _ok(path=path, content=content, size=len(content))


class FsList(BaseTool):
    name = "fs-list"
    description = "List files under a working/ subpath. Optional recursive flag."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "subpath, '' for working root"},
            "recursive": {"type": "boolean", "description": "recurse into subdirs"},
        },
    }

    def __init__(self, files: FileWorkspace):
        self.files = files

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path") or ""
        recursive = bool(arguments.get("recursive", False))
        try:
            paths = await self.files.list(path, recursive=recursive)
        except PathSafetyError as e:
            return _err(f"path safety: {e}")
        except OSError as e:
            return _err(f"list failed: {e}")
        return _ok(path=path, recursive=recursive, files=paths, count=len(paths))


class FsExists(BaseTool):
    name = "fs-exists"
    description = "Check whether a working/ path exists."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def __init__(self, files: FileWorkspace):
        self.files = files

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        if not path or not isinstance(path, str):
            return _err("missing required argument: path")
        return _ok(path=path, exists=await self.files.exists(path))


class FsReadRange(BaseTool):
    """Read a specific line range from a file.

    Cheaper than `fs:read` for large files when you know which section
    you want. Lines are 1-indexed inclusive on both sides. End line is
    clamped to the file's actual line count if it overshoots.
    """

    name = "fs-read_range"
    description = "Read a specific line range from a file (1-indexed, inclusive). Use after fs-exists or code-outline to read just the section you need."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "description": "1-indexed inclusive"},
            "end_line": {"type": "integer", "description": "1-indexed inclusive; clamped to file length"},
        },
        "required": ["path", "start_line", "end_line"],
    }

    def __init__(self, files: FileWorkspace):
        self.files = files

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        start = arguments.get("start_line")
        end = arguments.get("end_line")
        if not path or not isinstance(path, str):
            return _err("missing required argument: path")
        if not isinstance(start, int) or not isinstance(end, int):
            return _err("start_line and end_line must be integers")
        if start < 1:
            return _err("start_line must be >= 1")
        if end < start:
            return _err("end_line must be >= start_line")

        try:
            text = await self.files.read(path)
        except PathSafetyError as e:
            return _err(f"path safety: {e}")
        except FileNotFoundError:
            return _err(f"not found: {path}")
        except OSError as e:
            return _err(f"read failed: {e}")

        lines = text.splitlines(keepends=True)
        total = len(lines)
        end_clamped = min(end, total)
        if start > total:
            return _err(f"start_line {start} exceeds file length {total}")

        slice_ = lines[start - 1:end_clamped]
        content = "".join(slice_)
        return _ok(
            path=path,
            start_line=start,
            end_line=end_clamped,
            total_lines=total,
            content=content,
        )


class FsGrep(BaseTool):
    """Search files under working/ for matching text.

    Returns a list of `{path, line_number, line, before, after}` matches.
    Cheaper than reading each file when you're looking for a specific
    string or pattern. Uses literal text by default; pass `regex=true` for
    regex semantics.
    """

    name = "fs-grep"
    description = "Search file contents under working/ for a query. Returns matches with line numbers and surrounding context."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "regex": {"type": "boolean", "description": "treat query as regex (default false)"},
            "path_glob": {"type": "string", "description": "limit to files matching glob (e.g. '**/*.py'); default all text files"},
            "max_matches": {"type": "integer", "description": "default 50"},
            "context_lines": {"type": "integer", "description": "context lines on each side; default 2"},
            "case_sensitive": {"type": "boolean", "description": "default false"},
        },
        "required": ["query"],
    }

    # Skip these obviously-binary extensions to keep the tool fast and
    # avoid noise. Anything not matching is best-effort decoded as UTF-8.
    BINARY_EXTS = {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
        ".pdf", ".zip", ".tar", ".gz", ".bz2", ".7z",
        ".pyc", ".pyo", ".so", ".dll", ".exe", ".dylib",
        ".woff", ".woff2", ".ttf", ".eot", ".otf",
        ".mp3", ".mp4", ".webm", ".mov", ".wav",
    }

    def __init__(self, files: FileWorkspace):
        self.files = files

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        import fnmatch
        import re

        query = arguments.get("query")
        if not query or not isinstance(query, str):
            return _err("missing required argument: query")

        use_regex = bool(arguments.get("regex", False))
        path_glob = arguments.get("path_glob") or ""
        max_matches = int(arguments.get("max_matches", 50))
        context_lines = max(0, int(arguments.get("context_lines", 2)))
        case_sensitive = bool(arguments.get("case_sensitive", False))

        flags = 0 if case_sensitive else re.IGNORECASE
        if use_regex:
            try:
                pattern = re.compile(query, flags)
            except re.error as e:
                return _err(f"invalid regex: {e}")
        else:
            pattern = re.compile(re.escape(query), flags)

        try:
            all_paths = await self.files.list("", recursive=True)
        except OSError as e:
            return _err(f"list failed: {e}")

        if path_glob:
            all_paths = [p for p in all_paths if fnmatch.fnmatch(p, path_glob)]

        matches: list[dict[str, Any]] = []
        files_searched = 0
        truncated = False

        for rel_path in all_paths:
            if any(rel_path.lower().endswith(ext) for ext in self.BINARY_EXTS):
                continue
            try:
                content = await self.files.read(rel_path)
            except (PathSafetyError, FileNotFoundError, OSError, UnicodeDecodeError):
                continue
            files_searched += 1

            lines = content.splitlines()
            for idx, line in enumerate(lines):
                if not pattern.search(line):
                    continue
                lo = max(0, idx - context_lines)
                hi = min(len(lines), idx + context_lines + 1)
                matches.append({
                    "path": rel_path,
                    "line_number": idx + 1,
                    "line": line,
                    "before": lines[lo:idx],
                    "after": lines[idx + 1:hi],
                })
                if len(matches) >= max_matches:
                    truncated = True
                    break
            if truncated:
                break

        return _ok(
            query=query,
            regex=use_regex,
            matches=matches,
            count=len(matches),
            files_searched=files_searched,
            truncated=truncated,
        )


# ── Write tools (claim-aware) ───────────────────────────────────────────────


class _ClaimingTool(BaseTool):
    """Mixin for write tools — wraps the operation in claim/release."""

    intent: str = "edit"

    def __init__(
        self,
        files: FileWorkspace,
        claim_emitter: ClaimEmitter | None = None,
        release_emitter: ReleaseEmitter | None = None,
    ):
        self.files = files
        self.claim_emitter = claim_emitter
        self.release_emitter = release_emitter

    async def _claim(self, path: str) -> None:
        if self.claim_emitter is not None:
            await self.claim_emitter(self.intent, path)

    async def _release(self, path: str, outcome: str = "committed", commit_hash: str | None = None) -> None:
        if self.release_emitter is not None:
            await self.release_emitter(path, outcome, commit_hash)


class FsWrite(_ClaimingTool):
    name = "fs-write"
    description = "Write/overwrite a file under working/. Creates parent dirs as needed."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }
    intent = "edit"

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        content = arguments.get("content")
        if not path or not isinstance(path, str):
            return _err("missing required argument: path")
        if content is None or not isinstance(content, str):
            return _err("missing required argument: content (string)")
        await self._claim(path)
        try:
            await self.files.write(path, content)
        except PathSafetyError as e:
            await self._release(path, "aborted")
            return _err(f"path safety: {e}")
        except OSError as e:
            await self._release(path, "aborted")
            return _err(f"write failed: {e}")
        await self._release(path, "committed")
        return _ok(path=path, bytes_written=len(content.encode("utf-8")))


class FsCreate(_ClaimingTool):
    name = "fs-create"
    description = "Create a NEW file under working/. Errors if it already exists."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string", "description": "initial contents (default empty)"},
        },
        "required": ["path"],
    }
    intent = "create"

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        content = arguments.get("content") or ""
        if not path or not isinstance(path, str):
            return _err("missing required argument: path")
        try:
            if await self.files.exists(path):
                return _err(f"already exists: {path}")
        except PathSafetyError as e:
            return _err(f"path safety: {e}")
        await self._claim(path)
        try:
            await self.files.write(path, content)
        except PathSafetyError as e:
            await self._release(path, "aborted")
            return _err(f"path safety: {e}")
        except OSError as e:
            await self._release(path, "aborted")
            return _err(f"create failed: {e}")
        await self._release(path, "committed")
        return _ok(path=path, created=True, bytes_written=len(content.encode("utf-8")))


class FsEdit(_ClaimingTool):
    """Surgical edits — find/replace.

    Two modes:
      - exact match (default): one occurrence of `find` must exist; replaced
        once. Errors if zero or multiple matches.
      - replace_all=True: replaces every occurrence; returns count.
    """

    name = "fs-edit"
    description = "Surgical edit on an existing file: find/replace exact strings."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "find": {"type": "string"},
            "replace": {"type": "string"},
            "replace_all": {"type": "boolean"},
        },
        "required": ["path", "find", "replace"],
    }
    intent = "edit"

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        find = arguments.get("find")
        replace = arguments.get("replace")
        replace_all = bool(arguments.get("replace_all", False))
        if not path or not isinstance(path, str):
            return _err("missing required argument: path")
        if find is None or not isinstance(find, str) or find == "":
            return _err("missing/empty argument: find")
        if replace is None or not isinstance(replace, str):
            return _err("missing required argument: replace")

        try:
            content = await self.files.read(path)
        except PathSafetyError as e:
            return _err(f"path safety: {e}")
        except FileNotFoundError:
            return _err(f"not found: {path}")
        except OSError as e:
            return _err(f"read failed: {e}")

        count = content.count(find)
        if count == 0:
            return _err(f"find string not present in {path}")
        if not replace_all and count > 1:
            return _err(
                f"find string occurs {count} times in {path}; "
                "set replace_all=true or pass a more specific find string"
            )

        if replace_all:
            new_content = content.replace(find, replace)
            replacements = count
        else:
            new_content = content.replace(find, replace, 1)
            replacements = 1

        await self._claim(path)
        try:
            await self.files.write(path, new_content)
        except OSError as e:
            await self._release(path, "aborted")
            return _err(f"write failed: {e}")
        await self._release(path, "committed")
        return _ok(path=path, replacements=replacements)


class FsDelete(_ClaimingTool):
    name = "fs-delete"
    description = "Delete a file or directory under working/."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    intent = "delete"

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        if not path or not isinstance(path, str):
            return _err("missing required argument: path")
        try:
            if not await self.files.exists(path):
                return _err(f"not found: {path}")
        except PathSafetyError as e:
            return _err(f"path safety: {e}")
        await self._claim(path)
        try:
            await self.files.delete(path)
        except PathSafetyError as e:
            await self._release(path, "aborted")
            return _err(f"path safety: {e}")
        except OSError as e:
            await self._release(path, "aborted")
            return _err(f"delete failed: {e}")
        await self._release(path, "committed")
        return _ok(path=path, deleted=True)


class FsRename(_ClaimingTool):
    name = "fs-rename"
    description = "Rename or move a file under working/."
    input_schema = {
        "type": "object",
        "properties": {
            "from": {"type": "string"},
            "to": {"type": "string"},
        },
        "required": ["from", "to"],
    }
    intent = "rename"

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        src = arguments.get("from")
        dst = arguments.get("to")
        if not src or not isinstance(src, str):
            return _err("missing required argument: from")
        if not dst or not isinstance(dst, str):
            return _err("missing required argument: to")

        try:
            content = await self.files.read(src)
        except PathSafetyError as e:
            return _err(f"path safety on from: {e}")
        except FileNotFoundError:
            return _err(f"not found: {src}")
        except OSError as e:
            return _err(f"read failed: {e}")

        await self._claim(src)
        await self._claim(dst)
        try:
            await self.files.write(dst, content)
            await self.files.delete(src)
        except PathSafetyError as e:
            await self._release(src, "aborted")
            await self._release(dst, "aborted")
            return _err(f"path safety: {e}")
        except OSError as e:
            await self._release(src, "aborted")
            await self._release(dst, "aborted")
            return _err(f"rename failed: {e}")
        await self._release(src, "committed")
        await self._release(dst, "committed")
        return _ok(src=src, dst=dst)
