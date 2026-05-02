"""Tool output compression — token-reduction layer at the call_tool boundary.

Track 3c. Each compressor is a pure function of the tool's *result dict*:
inspect a known field (e.g. `diff`, `commits`, `stdout`), apply text
strategies (filter / group / truncate / dedupe), and return a modified
dict alongside before/after token counts.

Inspired by RTK (https://github.com/rtk-ai/rtk). RTK is a generic shell
proxy — they have to operate on raw bytes because they're tool-agnostic.
We have a single tool-call chokepoint (`BaseAgent.call_tool`), so our
compressors can be **structure-aware**: each one knows which field of
the result dict carries the bulky text.

Stateless and deterministic. No LLM calls. A buggy compressor must
never break a tool call — `compress()` catches exceptions and falls
back to identity output (mirrors the persister-error rule from
`core/workspace.py`).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from core.repo_map import _approx_tokens


# ── Public API ──────────────────────────────────────────────────────────────


@dataclass
class CompressedResult:
    """Output of `compress(tool_name, result)`.

    `result` preserves the original type (usually dict) so downstream code
    that consumes specific keys still works. Token counts are computed
    against the JSON-serialised forms — that's what the LLM actually
    receives as `tool_result` content.
    """

    result: Any
    original_tokens: int
    compressed_tokens: int
    strategies_applied: list[str] = field(default_factory=list)

    @property
    def tokens_saved(self) -> int:
        return max(0, self.original_tokens - self.compressed_tokens)


CompressorFn = Callable[[dict[str, Any]], "CompressedResult"]


_REGISTRY: dict[str, CompressorFn] = {}


def register(tool_name: str, fn: CompressorFn) -> None:
    """Register a compressor for a specific tool name. Called at module load."""
    _REGISTRY[tool_name] = fn


def _serialise_tokens(obj: Any) -> int:
    """Token cost the LLM will pay for this result — measured on its JSON form.

    Bulletproof: never raises. Some payloads have repr() methods that themselves
    raise (deliberately broken test fixtures, or genuinely buggy result objects).
    Returning 0 for unmeasurable input is acceptable — better than breaking the
    tool call.
    """
    if isinstance(obj, str):
        try:
            return _approx_tokens(obj)
        except Exception:
            return 0
    try:
        return _approx_tokens(json.dumps(obj, default=str))
    except Exception:
        try:
            return _approx_tokens(repr(obj))
        except Exception:
            return 0


def _identity(result: Any) -> CompressedResult:
    n = _serialise_tokens(result)
    return CompressedResult(
        result=result,
        original_tokens=n,
        compressed_tokens=n,
        strategies_applied=[],
    )


def _safe_identity(result: Any) -> CompressedResult:
    """Identity that cannot raise even on pathological inputs."""
    try:
        return _identity(result)
    except Exception:
        return CompressedResult(
            result=result,
            original_tokens=0,
            compressed_tokens=0,
            strategies_applied=[],
        )


def compress(tool_name: str, result: Any) -> CompressedResult:
    """Apply the registered compressor for `tool_name`, or identity if none.

    Failures fall through to identity — never raises. A buggy compressor
    cannot break a tool call. Same rule as workspace persisters: an
    auxiliary cannot break the primary path.
    """
    try:
        fn = _REGISTRY.get(tool_name)
        if fn is None or not isinstance(result, dict):
            return _safe_identity(result)
        try:
            return fn(result)
        except Exception:
            return _safe_identity(result)
    except Exception:
        return _safe_identity(result)


# ── Shared text strategies ─────────────────────────────────────────────────


_BLANK_RUN_RE = re.compile(r"\n{3,}")


def _squash_blank_runs(text: str) -> tuple[str, bool]:
    """Collapse runs of 3+ blank lines into a single blank line."""
    new_text = _BLANK_RUN_RE.sub("\n\n", text)
    return new_text, new_text != text


def _strip_trailing_whitespace(text: str) -> tuple[str, bool]:
    """Strip trailing whitespace on each line. Catches log noise."""
    lines = text.split("\n")
    stripped = [ln.rstrip() for ln in lines]
    new_text = "\n".join(stripped)
    return new_text, new_text != text


def _dedupe_repeated_lines(text: str, threshold: int = 5) -> tuple[str, bool]:
    """Collapse N+ consecutive identical lines into `<line>  × N`."""
    lines = text.split("\n")
    if len(lines) < threshold:
        return text, False
    out: list[str] = []
    i = 0
    hit = False
    while i < len(lines):
        cur = lines[i]
        run = 1
        while i + run < len(lines) and lines[i + run] == cur:
            run += 1
        if run >= threshold and cur.strip():
            out.append(f"{cur}  × {run}")
            hit = True
        else:
            out.extend([cur] * run)
        i += run
    return "\n".join(out), hit


# ── code-exec compressor ───────────────────────────────────────────────────


_PYTEST_BANNER_PATTERNS = [
    re.compile(r"^=+ test session starts =+\s*$", re.MULTILINE),
    # "platform linux -- Python 3.11.3, pytest-8.0.0, pluggy-1.4.0"
    re.compile(r"^platform \S+ -- Python .+pytest-.+$", re.MULTILINE),
    re.compile(r"^rootdir: .+$", re.MULTILINE),
    re.compile(r"^configfile: .+$", re.MULTILINE),
    re.compile(r"^plugins: .+$", re.MULTILINE),
    re.compile(r"^cachedir: .+$", re.MULTILINE),
    re.compile(r"^asyncio: mode=.+$", re.MULTILINE),
    re.compile(r"^collecting \.\.\..*$", re.MULTILINE),
    re.compile(r"^collected \d+ items?\s*$", re.MULTILINE),
]


_NPM_NOISE_PATTERNS = [
    re.compile(r"^npm WARN .+$", re.MULTILINE),
    re.compile(r"^npm notice .+$", re.MULTILINE),
    re.compile(r"^npm http .+$", re.MULTILINE),
]


_PIP_NOISE_PATTERNS = [
    re.compile(r"^Looking in indexes: .+$", re.MULTILINE),
    re.compile(r"^Requirement already satisfied: .+$", re.MULTILINE),
    re.compile(r"^Collecting .+$", re.MULTILINE),
    re.compile(r"^Downloading .+\.whl.*$", re.MULTILINE),
]


def _strip_pytest_banners(text: str) -> tuple[str, bool]:
    """Remove pytest's session-starts banner block. Keep failure summary intact."""
    new_text = text
    hit = False
    for pat in _PYTEST_BANNER_PATTERNS:
        replaced = pat.sub("", new_text)
        if replaced != new_text:
            hit = True
        new_text = replaced
    return new_text, hit


def _strip_npm_noise(text: str) -> tuple[str, bool]:
    """Drop npm WARN/notice/http lines that flood install output."""
    new_text = text
    hit = False
    for pat in _NPM_NOISE_PATTERNS:
        replaced = pat.sub("", new_text)
        if replaced != new_text:
            hit = True
        new_text = replaced
    return new_text, hit


def _strip_pip_noise(text: str) -> tuple[str, bool]:
    """Drop pip resolver chatter."""
    new_text = text
    hit = False
    for pat in _PIP_NOISE_PATTERNS:
        replaced = pat.sub("", new_text)
        if replaced != new_text:
            hit = True
        new_text = replaced
    return new_text, hit


def _compress_code_exec_text(text: str) -> tuple[str, list[str]]:
    """Apply the code-exec text strategies. Order matters; documented in this fn."""
    strategies: list[str] = []

    text, hit = _strip_pytest_banners(text)
    if hit:
        strategies.append("pytest_banner")

    text, hit = _strip_npm_noise(text)
    if hit:
        strategies.append("npm_noise")

    text, hit = _strip_pip_noise(text)
    if hit:
        strategies.append("pip_noise")

    text, hit = _dedupe_repeated_lines(text)
    if hit:
        strategies.append("repeated_lines")

    text, hit = _strip_trailing_whitespace(text)
    if hit:
        strategies.append("trailing_ws")

    text, hit = _squash_blank_runs(text)
    if hit:
        strategies.append("blank_runs")

    return text, strategies


def compress_code_exec(result: dict[str, Any]) -> CompressedResult:
    """Compress code-exec result. Targets `stdout` and `stderr` fields if present.

    Strategy order (locked — don't reorder casually):
      1. Strip pytest session-starts banners (keeps failure summary intact)
      2. Strip npm install chatter
      3. Strip pip resolver chatter
      4. Dedupe consecutive identical lines (5+ → "line × N")
      5. Strip trailing whitespace per line
      6. Collapse 3+ blank lines into one
    """
    original_tokens = _serialise_tokens(result)
    new_result = dict(result)
    all_strategies: list[str] = []

    for field_name in ("stdout", "stderr", "output", "text"):
        val = new_result.get(field_name)
        if isinstance(val, str) and val:
            compressed_text, strategies = _compress_code_exec_text(val)
            new_result[field_name] = compressed_text
            for s in strategies:
                if s not in all_strategies:
                    all_strategies.append(s)

    return CompressedResult(
        result=new_result,
        original_tokens=original_tokens,
        compressed_tokens=_serialise_tokens(new_result),
        strategies_applied=all_strategies,
    )


register("code-exec", compress_code_exec)


# ── git-log compressor ─────────────────────────────────────────────────────


_GIT_TRAILER_PATTERNS = [
    re.compile(r"^Signed-off-by:.*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^Co-Authored-By:.*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^Reviewed-by:.*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^Acked-by:.*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^Tested-by:.*$", re.MULTILINE | re.IGNORECASE),
]


def _strip_commit_trailers(text: str) -> tuple[str, bool]:
    new_text = text
    hit = False
    for pat in _GIT_TRAILER_PATTERNS:
        replaced = pat.sub("", new_text)
        if replaced != new_text:
            hit = True
        new_text = replaced
    return new_text, hit


def compress_git_log(result: dict[str, Any]) -> CompressedResult:
    """Compress git-log result. Targets `commits` list and any free-text `log` field.

    Strategies:
      1. Strip Signed-off-by / Co-Authored-By / Reviewed-by trailers from messages.
      2. If > 20 commits, keep first 10 + last 5 with "... N commits omitted ..." marker.
    """
    original_tokens = _serialise_tokens(result)
    new_result = dict(result)
    strategies: list[str] = []

    # Commit list compression.
    commits = new_result.get("commits")
    if isinstance(commits, list) and commits:
        cleaned: list[dict[str, Any]] = []
        any_trailer_stripped = False
        for c in commits:
            if isinstance(c, dict):
                msg = c.get("message")
                if isinstance(msg, str):
                    new_msg, hit = _strip_commit_trailers(msg)
                    if hit:
                        any_trailer_stripped = True
                        c = {**c, "message": new_msg.rstrip("\n")}
            cleaned.append(c)
        if any_trailer_stripped:
            strategies.append("commit_trailers")

        # Truncate long histories.
        if len(cleaned) > 20:
            head = cleaned[:10]
            tail = cleaned[-5:]
            omitted = len(cleaned) - 15
            new_result["commits"] = head + [
                {"_omitted": f"{omitted} commits omitted (use git-log --limit to scope)"}
            ] + tail
            strategies.append("history_truncated")
        else:
            new_result["commits"] = cleaned

    # Plain-text `log` field (some git-log variants).
    log_text = new_result.get("log")
    if isinstance(log_text, str) and log_text:
        new_text, hit = _strip_commit_trailers(log_text)
        if hit and "commit_trailers" not in strategies:
            strategies.append("commit_trailers")
        new_result["log"] = new_text

    return CompressedResult(
        result=new_result,
        original_tokens=original_tokens,
        compressed_tokens=_serialise_tokens(new_result),
        strategies_applied=strategies,
    )


register("git-log", compress_git_log)


# ── git-diff compressor ────────────────────────────────────────────────────


_DIFF_INDEX_RE = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+( \d+)?\s*$", re.MULTILINE)
_GIT_DIFF_HEADER_RE = re.compile(
    r"^diff --git a/(.+) b/.+$", re.MULTILINE
)


def _strip_diff_index_lines(text: str) -> tuple[str, bool]:
    """Drop `index abc..def 100644` lines that the LLM doesn't need."""
    new_text = _DIFF_INDEX_RE.sub("", text)
    return new_text, new_text != text


def _truncate_oversize_diff(text: str, max_tokens: int = 5000) -> tuple[str, bool]:
    """If the diff is huge, truncate with a marker telling the agent to scope."""
    if _approx_tokens(text) <= max_tokens:
        return text, False
    # Keep the first 3000 tokens worth of text (~12000 chars).
    keep_chars = max_tokens * 3
    truncated = text[:keep_chars]
    marker = (
        f"\n\n... <diff truncated at ~{max_tokens} tokens; "
        "re-run git-diff with a `path` filter to scope> ...\n"
    )
    return truncated + marker, True


def compress_git_diff(result: dict[str, Any]) -> CompressedResult:
    """Compress git-diff result. Targets the `diff` field.

    Strategies:
      1. Drop `index abc..def` lines (LLM doesn't need git-internal hashes).
      2. Strip trailing whitespace.
      3. Squash 3+ blank lines into one.
      4. Truncate diffs > 5000 tokens with a "scope this" marker.
    """
    original_tokens = _serialise_tokens(result)
    new_result = dict(result)
    strategies: list[str] = []

    diff = new_result.get("diff")
    if isinstance(diff, str) and diff:
        new_diff, hit = _strip_diff_index_lines(diff)
        if hit:
            strategies.append("diff_index_lines")
        new_diff, hit = _strip_trailing_whitespace(new_diff)
        if hit:
            strategies.append("trailing_ws")
        new_diff, hit = _squash_blank_runs(new_diff)
        if hit:
            strategies.append("blank_runs")
        new_diff, hit = _truncate_oversize_diff(new_diff)
        if hit:
            strategies.append("oversize_truncate")
        new_result["diff"] = new_diff

    return CompressedResult(
        result=new_result,
        original_tokens=original_tokens,
        compressed_tokens=_serialise_tokens(new_result),
        strategies_applied=strategies,
    )


register("git-diff", compress_git_diff)


# ── git-changed-files compressor ───────────────────────────────────────────


def _group_files_by_dir(files: list[str]) -> str:
    """Render a `files` list as `src/{a, b, c}, tests/{x}` style. Caps at top 20 dirs."""
    if not files:
        return ""
    by_dir: dict[str, list[str]] = {}
    for p in files:
        head, _, tail = p.rpartition("/")
        by_dir.setdefault(head or ".", []).append(tail or p)

    sorted_dirs = sorted(by_dir.items(), key=lambda kv: -len(kv[1]))
    overflow = ""
    if len(sorted_dirs) > 20:
        kept = sorted_dirs[:20]
        omitted = len(sorted_dirs) - 20
        overflow = f", and {omitted} more directories"
        sorted_dirs = kept

    chunks: list[str] = []
    for d, names in sorted(sorted_dirs, key=lambda kv: kv[0]):
        if d in (".", ""):
            chunks.extend(sorted(names))
        elif len(names) == 1:
            chunks.append(f"{d}/{names[0]}")
        else:
            chunks.append(f"{d}/{{{', '.join(sorted(names))}}}")
    return ", ".join(chunks) + overflow


def compress_git_changed_files(result: dict[str, Any]) -> CompressedResult:
    """Compress git-changed-files. Replaces `files` list with a directory-grouped
    `summary` string while keeping the original list in `files_full` for any
    consumer that needs it.

    Strategies:
      1. Group by directory: `src/{a, b, c}` instead of three separate lines.
      2. Cap at top 20 directories by file count.
    """
    original_tokens = _serialise_tokens(result)
    new_result = dict(result)
    strategies: list[str] = []

    files = new_result.get("files")
    if isinstance(files, list) and len(files) > 5:
        # Only worth grouping if there are enough entries to benefit.
        summary = _group_files_by_dir([f for f in files if isinstance(f, str)])
        if summary:
            new_result["summary"] = summary
            new_result["files_full_count"] = len(files)
            # Drop the per-file list to save tokens. Agents that need the full
            # list can grep the workspace or run with a path filter.
            new_result.pop("files", None)
            strategies.append("dir_grouping")

    return CompressedResult(
        result=new_result,
        original_tokens=original_tokens,
        compressed_tokens=_serialise_tokens(new_result),
        strategies_applied=strategies,
    )


register("git-changed-files", compress_git_changed_files)
