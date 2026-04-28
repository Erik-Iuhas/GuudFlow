"""SEARCH/REPLACE edit block parser — Aider-style fenced diff format.

M1 spec §6. Agents emit DELTA messages whose body is a sequence of these
blocks. The parser validates structure; the applier is atomic: all blocks
succeed or none are applied.

Grammar (BNF-ish):
    delta_body      ::= block (NEWLINE block)*
    block           ::= search_block | delete_file_block
    search_block    ::= "<<<<<<< SEARCH " path NEWLINE
                        content NEWLINE
                        "=======" NEWLINE
                        content NEWLINE
                        ">>>>>>> REPLACE" NEWLINE
    delete_file_blk ::= "<<<<<<< DELETE_FILE " path NEWLINE
                        ">>>>>>> END" NEWLINE
    path            ::= relative path under working/, no ".." or absolute

Semantics:
- SEARCH empty → create file  (error if file already exists)
- REPLACE empty → delete matched lines (not the file)
- DELETE_FILE block → delete the entire file
- Multiple blocks per DELTA → applied in order, atomic (all-or-none)
- Whitespace is exact-match (tabs vs spaces, trailing whitespace)
- Match must be unique; MULTIPLE_MATCHES error if found >1 time
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from core.file_workspace import FileWorkspace


OpKind = Literal["edit", "create", "delete_content", "delete_file"]


class ParseError(ValueError):
    """Raised when the edit-block body is structurally malformed."""


class ApplyError(ValueError):
    """Raised when a block cannot be applied (not matched, file locked, etc.)."""


@dataclass
class EditBlock:
    path: str                   # relative path under working/
    search: str                 # empty for create
    replace: str                # empty for delete-content
    op: OpKind

    def __post_init__(self) -> None:
        if ".." in Path(self.path).parts or Path(self.path).is_absolute():
            raise ParseError(f"unsafe path in edit block: {self.path!r}")


@dataclass
class BlockResult:
    path: str
    op: OpKind
    ok: bool
    error: str = ""


@dataclass
class ApplyResult:
    ok: bool
    results: list[BlockResult] = field(default_factory=list)
    error: str = ""


# ── Parser ───────────────────────────────────────────────────────────────────

_SEARCH_START = "<<<<<<< SEARCH "
_REPLACE_DIVIDER = "======="
_REPLACE_END = ">>>>>>> REPLACE"
_DELETE_FILE_START = "<<<<<<< DELETE_FILE "
_DELETE_FILE_END = ">>>>>>> END"


def parse(body: str) -> list[EditBlock]:
    """Parse a DELTA body into EditBlocks.

    Raises ParseError with a description on malformed input.
    """
    blocks: list[EditBlock] = []
    lines = body.splitlines(keepends=True)
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip("\n\r")

        if stripped.startswith(_DELETE_FILE_START):
            path = stripped[len(_DELETE_FILE_START):].strip()
            i += 1
            if i >= len(lines) or lines[i].rstrip("\n\r") != _DELETE_FILE_END:
                raise ParseError(
                    f"DELETE_FILE block for {path!r} missing '>>>>>>> END'"
                )
            blocks.append(EditBlock(path=path, search="", replace="", op="delete_file"))
            i += 1

        elif stripped.startswith(_SEARCH_START):
            path = stripped[len(_SEARCH_START):].strip()
            i += 1

            # Collect SEARCH content up to "======="
            search_lines: list[str] = []
            while i < len(lines) and lines[i].rstrip("\n\r") != _REPLACE_DIVIDER:
                search_lines.append(lines[i])
                i += 1
            if i >= len(lines):
                raise ParseError(
                    f"SEARCH block for {path!r} missing '=======' divider"
                )
            i += 1  # consume "======="

            # Collect REPLACE content up to ">>>>>>> REPLACE"
            replace_lines: list[str] = []
            while i < len(lines) and lines[i].rstrip("\n\r") != _REPLACE_END:
                replace_lines.append(lines[i])
                i += 1
            if i >= len(lines):
                raise ParseError(
                    f"REPLACE block for {path!r} missing '>>>>>>> REPLACE'"
                )
            i += 1  # consume ">>>>>>> REPLACE"

            search_content = "".join(search_lines)
            replace_content = "".join(replace_lines)

            if not search_content:
                op: OpKind = "create"
            elif not replace_content:
                op = "delete_content"
            else:
                op = "edit"

            blocks.append(EditBlock(
                path=path,
                search=search_content,
                replace=replace_content,
                op=op,
            ))

        else:
            # skip blank lines between blocks
            if stripped:
                raise ParseError(
                    f"unexpected line in DELTA body at index {i}: {stripped!r}"
                )
            i += 1

    return blocks


# ── Applier ──────────────────────────────────────────────────────────────────

def apply(blocks: list[EditBlock], workspace: "FileWorkspace") -> ApplyResult:
    """Apply edit blocks atomically via FileWorkspace.

    All-or-nothing: if any block fails, no changes are written.
    Returns ApplyResult with per-block status.
    """
    # --- validation pass (read-only): check every block can be applied --------
    staged: list[tuple[EditBlock, str | None]] = []  # (block, new_content | None=delete)
    results: list[BlockResult] = []

    for block in blocks:
        try:
            new_content = _plan_block(block, workspace)
            staged.append((block, new_content))
            results.append(BlockResult(path=block.path, op=block.op, ok=True))
        except ApplyError as e:
            results.append(BlockResult(path=block.path, op=block.op, ok=False, error=str(e)))

    if any(not r.ok for r in results):
        return ApplyResult(ok=False, results=results, error="one or more blocks failed validation")

    # --- write pass -----------------------------------------------------------
    for block, new_content in staged:
        try:
            if block.op == "delete_file":
                workspace.delete(block.path)
            elif block.op == "create":
                workspace.write(block.path, block.replace)
            else:
                workspace.write(block.path, new_content or "")  # type: ignore[arg-type]
        except Exception as e:
            # Write failure after validation: partial state. Surface it.
            return ApplyResult(ok=False, results=results, error=f"write failed: {e}")

    return ApplyResult(ok=True, results=results)


def _plan_block(block: EditBlock, workspace: "FileWorkspace") -> str | None:
    """Return the post-application file content (or None for delete_file).

    Raises ApplyError on any constraint violation.
    """
    if block.op == "delete_file":
        if not workspace.exists(block.path):
            raise ApplyError(f"FILE_NOT_FOUND: {block.path}")
        return None

    if block.op == "create":
        if workspace.exists(block.path):
            raise ApplyError(f"FILE_EXISTS: {block.path}")
        return block.replace

    # edit or delete_content — need to find the search content in the file
    if not workspace.exists(block.path):
        raise ApplyError(f"FILE_NOT_FOUND: {block.path}")

    current = workspace.read(block.path)
    count = current.count(block.search)
    if count == 0:
        close = _closest_matches(block.search, current)
        hint = "\nClosest matches in file:\n" + "\n".join(close) if close else ""
        raise ApplyError(f"SEARCH_NOT_FOUND: {block.path}{hint}")
    if count > 1:
        raise ApplyError(f"MULTIPLE_MATCHES: {block.search!r} found {count} times in {block.path}")

    return current.replace(block.search, block.replace, 1)


def _closest_matches(search: str, file_content: str, top_n: int = 3) -> list[str]:
    """Return up to `top_n` snippets from the file closest to the search string.

    Uses difflib sequence matching over a window of lines equal to the search
    line-count. Returns formatted snippet strings for the retry hint.
    """
    search_lines = search.splitlines()
    win = max(len(search_lines), 1)
    file_lines = file_content.splitlines()
    candidates: list[tuple[float, str]] = []

    for start in range(0, max(1, len(file_lines) - win + 1)):
        chunk_lines = file_lines[start:start + win]
        chunk = "\n".join(chunk_lines)
        ratio = difflib.SequenceMatcher(None, search, chunk).ratio()
        lineno = start + 1
        candidates.append((ratio, f"  Line {lineno}-{lineno + win - 1}: {chunk[:120]!r}"))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [c[1] for c in candidates[:top_n]]
