"""Batch fs tools — collapse N sequential round-trips into one tool call.

Track 3e. Real-run analysis showed agents emit 18-30 sequential `fs-edit` /
`fs-write` calls when implementing or correcting a code skeleton. Each call
is its own LLM round-trip with full system-prompt re-send, conversation
history growth, and ~5-10s wall-clock latency.

These batch variants apply N entries in one tool call, reusing the per-entry
safety + claim/release machinery from the singular tools. Failures are
per-entry: an N-th entry failure does NOT roll back entries 1..N-1.
The agent sees a structured success/failure summary and can retry just the
failed ones.

DESIGN §31 hyphen naming. Per-entry FILE_CLAIM / FILE_RELEASE signals are
preserved on the board for forensic continuity (Track 3d.1 will collapse
them at render-time later).
"""
from __future__ import annotations

from typing import Any

from agents.tools.base import BaseTool
from agents.tools.fs import (
    ClaimEmitter,
    ReleaseEmitter,
    _err,
    _ok,
)
from core.file_workspace import FileWorkspace, PathSafetyError


_MAX_ENTRIES = 30

# Token-budget guards — every byte of `content` lives inside the tool
# arguments JSON, which counts against the model's output_tokens cap.
# At ~4 bytes/token, the defaults below keep a worst-case batch under
# ~3K output tokens (well below the 4096-default max_tokens), leaving
# headroom for surrounding response text.
_MAX_BYTES_PER_ENTRY = 4_000   # ~1K tokens — single moderate file
_MAX_BYTES_PER_BATCH = 12_000  # ~3K tokens — sum across all entries


def _summarize_paths(paths: list[str]) -> str:
    """Compact summary: 'src/{a, b, c}, tests/{x, y}' style.
    Falls back to comma-list if grouping doesn't help."""
    if not paths:
        return ""
    if len(paths) <= 3:
        return ", ".join(paths)
    by_dir: dict[str, list[str]] = {}
    for p in paths:
        head, _, tail = p.rpartition("/")
        by_dir.setdefault(head or ".", []).append(tail or p)
    chunks: list[str] = []
    for d, names in sorted(by_dir.items()):
        if d in (".", ""):
            chunks.append(", ".join(sorted(names)))
        elif len(names) == 1:
            chunks.append(f"{d}/{names[0]}")
        else:
            chunks.append(f"{d}/{{{', '.join(sorted(names))}}}")
    return ", ".join(chunks)


class FsWriteBatch(BaseTool):
    """Apply many file writes in one tool call.

    Each entry goes through `FileWorkspace.write` with the same path-safety
    and claim/release semantics as the singular `fs-write`. Per-entry failure
    does not abort the batch — successes from entries 1..N-1 are kept.
    """

    name = "fs-write-batch"
    priority = 100
    when_to_use = "≥ 2 SHORT files in one call (per-file ≤ ~1K tokens, total ≤ ~3K); larger files → fs-write"
    description = (
        "Write/overwrite many SHORT files in one tool call. Each file's content "
        "lives inside the arguments JSON which counts against output_tokens, so "
        f"per-entry contents must stay under {_MAX_BYTES_PER_ENTRY} bytes and "
        f"total batch under {_MAX_BYTES_PER_BATCH} bytes. Larger files MUST use "
        "individual fs-write calls. Per-entry failures are reported individually; "
        "successes are not rolled back."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "description": (
                    f"List of {{path, content}} entries (max {_MAX_ENTRIES})."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        "required": ["files"],
    }
    intent = "edit"

    def __init__(
        self,
        files: FileWorkspace,
        claim_emitter: ClaimEmitter | None = None,
        release_emitter: ReleaseEmitter | None = None,
    ):
        self.files = files
        self.claim_emitter = claim_emitter
        self.release_emitter = release_emitter

    def aci_doc(self) -> dict[str, str]:
        return {
            "purpose": "Apply many file writes in one tool call (Track 3e).",
            "args": "{files: [{path, content}, ...]}",
            "emits": (
                "{ok:true, written:N, failed:M, paths_summary, "
                "failures: [{path, error}, ...]}"
            ),
            "errors": "empty list | exceeds 30 entries | each entry fails individually",
            "example": (
                'fs-write-batch({"files": ['
                '{"path": "src/a.py", "content": "..."}, '
                '{"path": "src/b.py", "content": "..."}'
                "]})"
            ),
            "notes": (
                "Per-entry failures don't roll back successes. Hard cap of 30 "
                "entries — split into multiple calls if you need more."
            ),
        }

    async def _claim(self, path: str) -> None:
        if self.claim_emitter is not None:
            await self.claim_emitter(self.intent, path)

    async def _release(self, path: str, outcome: str) -> None:
        if self.release_emitter is not None:
            await self.release_emitter(path, outcome, None)

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        entries = arguments.get("files")
        if not isinstance(entries, list) or len(entries) == 0:
            return _err("missing/empty argument: files (non-empty list required)")
        if len(entries) > _MAX_ENTRIES:
            return _err(
                f"too many entries: {len(entries)} (max {_MAX_ENTRIES}; split into multiple calls)"
            )

        # Pre-flight token-budget check. Reject the entire batch if any
        # entry is too big OR the total payload would blow output_tokens.
        # This prevents the LLM from wasting an LLM call on a request that
        # Anthropic would truncate at max_tokens (with empty arguments
        # returned), and points it at the right tool instead.
        oversized = [
            (e.get("path", f"<entry {i}>"), len((e.get("content") or "").encode("utf-8")))
            for i, e in enumerate(entries)
            if isinstance(e, dict)
            and isinstance(e.get("content"), str)
            and len(e["content"].encode("utf-8")) > _MAX_BYTES_PER_ENTRY
        ]
        if oversized:
            offending = "; ".join(f"{p}={n}B" for p, n in oversized[:5])
            return _err(
                f"entries exceed per-file cap of {_MAX_BYTES_PER_ENTRY} bytes "
                f"(~{_MAX_BYTES_PER_ENTRY//4} tokens): {offending}. "
                "Use individual fs-write calls for files this large — "
                "batch arguments share the model's output token budget."
            )
        total_payload = sum(
            len((e.get("content") or "").encode("utf-8"))
            for e in entries if isinstance(e, dict) and isinstance(e.get("content"), str)
        )
        if total_payload > _MAX_BYTES_PER_BATCH:
            return _err(
                f"total batch payload {total_payload} bytes exceeds cap of "
                f"{_MAX_BYTES_PER_BATCH} bytes (~{_MAX_BYTES_PER_BATCH//4} tokens). "
                "Split into smaller batches OR use individual fs-write for the "
                "larger files — batch arguments share the model's output token budget."
            )

        successes: list[str] = []
        failures: list[dict[str, str]] = []
        total_bytes = 0

        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                failures.append({"path": f"<entry {idx}>", "error": "entry not a dict"})
                continue
            path = entry.get("path")
            content = entry.get("content")
            if not path or not isinstance(path, str):
                failures.append({"path": f"<entry {idx}>", "error": "missing path"})
                continue
            if content is None or not isinstance(content, str):
                failures.append({"path": path, "error": "missing/invalid content"})
                continue

            await self._claim(path)
            try:
                await self.files.write(path, content)
            except PathSafetyError as e:
                await self._release(path, "aborted")
                failures.append({"path": path, "error": f"path safety: {e}"})
                continue
            except OSError as e:
                await self._release(path, "aborted")
                failures.append({"path": path, "error": f"write failed: {e}"})
                continue
            await self._release(path, "written")
            successes.append(path)
            total_bytes += len(content.encode("utf-8"))

        return _ok(
            written=len(successes),
            failed=len(failures),
            total_bytes=total_bytes,
            paths_summary=_summarize_paths(successes),
            failures=failures,
        )


class FsEditBatch(BaseTool):
    """Apply many find/replace edits in one tool call.

    Each entry goes through the same matching + safety logic as `fs-edit`:
    exact match by default (errors on zero or multiple matches unless
    `replace_all=true`). Per-entry failure does not abort the batch.
    """

    name = "fs-edit-batch"
    priority = 100
    when_to_use = "≥ 2 SHORT find/replace edits (per-entry ≤ ~1K tokens, total ≤ ~3K); larger → fs-edit"
    description = (
        "Surgical find/replace on many files in one tool call. Each entry "
        "follows fs-edit semantics (exact match unless replace_all=true). "
        f"Per-entry find+replace text capped at {_MAX_BYTES_PER_ENTRY} bytes, "
        f"total batch payload at {_MAX_BYTES_PER_BATCH} bytes. Per-entry "
        "failures are reported individually; successes are not rolled back."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "description": (
                    f"List of {{path, find, replace, replace_all?}} entries "
                    f"(max {_MAX_ENTRIES})."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "find": {"type": "string"},
                        "replace": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                    "required": ["path", "find", "replace"],
                },
            },
        },
        "required": ["edits"],
    }
    intent = "edit"

    def __init__(
        self,
        files: FileWorkspace,
        claim_emitter: ClaimEmitter | None = None,
        release_emitter: ReleaseEmitter | None = None,
    ):
        self.files = files
        self.claim_emitter = claim_emitter
        self.release_emitter = release_emitter

    def aci_doc(self) -> dict[str, str]:
        return {
            "purpose": "Apply many find/replace edits in one tool call (Track 3e).",
            "args": "{edits: [{path, find, replace, replace_all?}, ...]}",
            "emits": (
                "{ok:true, edited:N, failed:M, paths_summary, "
                "failures: [{path, error}, ...]}"
            ),
            "errors": (
                "empty list | exceeds 30 entries | per-entry: SEARCH_NOT_FOUND, "
                "MULTIPLE_MATCHES, path safety, not found"
            ),
            "example": (
                'fs-edit-batch({"edits": ['
                '{"path": "src/a.py", "find": "X", "replace": "Y"}, '
                '{"path": "src/b.py", "find": "old", "replace": "new", "replace_all": true}'
                "]})"
            ),
            "notes": (
                "Whitespace exact. Per-entry failures don't roll back successes. "
                "Hard cap of 30 entries."
            ),
        }

    async def _claim(self, path: str) -> None:
        if self.claim_emitter is not None:
            await self.claim_emitter(self.intent, path)

    async def _release(self, path: str, outcome: str) -> None:
        if self.release_emitter is not None:
            await self.release_emitter(path, outcome, None)

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        entries = arguments.get("edits")
        if not isinstance(entries, list) or len(entries) == 0:
            return _err("missing/empty argument: edits (non-empty list required)")
        if len(entries) > _MAX_ENTRIES:
            return _err(
                f"too many entries: {len(entries)} (max {_MAX_ENTRIES}; split into multiple calls)"
            )

        # Pre-flight token-budget check (same rationale as fs-write-batch).
        # find+replace text both live in the arguments JSON and count
        # against the model's output_tokens cap.
        def _entry_bytes(e: dict) -> int:
            f = e.get("find") if isinstance(e.get("find"), str) else ""
            r = e.get("replace") if isinstance(e.get("replace"), str) else ""
            return len((f + r).encode("utf-8"))
        oversized = [
            (e.get("path", f"<entry {i}>"), _entry_bytes(e))
            for i, e in enumerate(entries)
            if isinstance(e, dict) and _entry_bytes(e) > _MAX_BYTES_PER_ENTRY
        ]
        if oversized:
            offending = "; ".join(f"{p}={n}B" for p, n in oversized[:5])
            return _err(
                f"entries exceed per-entry cap of {_MAX_BYTES_PER_ENTRY} bytes "
                f"of find+replace text: {offending}. "
                "Use individual fs-edit calls for edits this large."
            )
        total_payload = sum(
            _entry_bytes(e) for e in entries if isinstance(e, dict)
        )
        if total_payload > _MAX_BYTES_PER_BATCH:
            return _err(
                f"total batch payload {total_payload} bytes exceeds cap of "
                f"{_MAX_BYTES_PER_BATCH} bytes. Split into smaller batches."
            )

        successes: list[str] = []
        failures: list[dict[str, str]] = []
        total_replacements = 0

        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                failures.append({"path": f"<entry {idx}>", "error": "entry not a dict"})
                continue
            path = entry.get("path")
            find = entry.get("find")
            replace = entry.get("replace")
            replace_all = bool(entry.get("replace_all", False))

            if not path or not isinstance(path, str):
                failures.append({"path": f"<entry {idx}>", "error": "missing path"})
                continue
            if find is None or not isinstance(find, str) or find == "":
                failures.append({"path": path, "error": "missing/empty find"})
                continue
            if replace is None or not isinstance(replace, str):
                failures.append({"path": path, "error": "missing replace"})
                continue

            try:
                content = await self.files.read(path)
            except PathSafetyError as e:
                failures.append({"path": path, "error": f"path safety: {e}"})
                continue
            except FileNotFoundError:
                failures.append({"path": path, "error": "not found"})
                continue
            except OSError as e:
                failures.append({"path": path, "error": f"read failed: {e}"})
                continue

            count = content.count(find)
            if count == 0:
                from core.edit_blocks import _closest_matches
                close = _closest_matches(find, content, top_n=3)
                hint = (" (closest: " + " | ".join(close) + ")") if close else ""
                failures.append({"path": path, "error": f"SEARCH_NOT_FOUND{hint}"})
                continue
            if not replace_all and count > 1:
                failures.append({
                    "path": path,
                    "error": f"MULTIPLE_MATCHES: {count} occurrences (set replace_all=true or use a more specific find)",
                })
                continue

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
                failures.append({"path": path, "error": f"write failed: {e}"})
                continue
            await self._release(path, "written")
            successes.append(path)
            total_replacements += replacements

        return _ok(
            edited=len(successes),
            failed=len(failures),
            total_replacements=total_replacements,
            paths_summary=_summarize_paths(successes),
            failures=failures,
        )
