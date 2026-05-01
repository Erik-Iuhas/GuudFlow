"""Story-tag convention for project docs.

Markdown files may declare a `story:` frontmatter key. The convention is:

    ---
    story: ghost-ai
    ---

    # Ghost AI design

This module reads the tag without a YAML dependency — frontmatter is
parsed by a tiny line scanner. Files without frontmatter, or without
the `story:` key, return `None` (untagged / shared).

Story scoping itself is git-based: each story records a `base_ref` at
open time, and agents diff docs against that ref via `fs-diff` to see
what's new for their story. This module exists only to advertise
whole-file ownership in `fs-list` output so agents can prefer their
own story's docs on continuation runs.
"""

from __future__ import annotations

from pathlib import Path


_DOC_EXTS: frozenset[str] = frozenset({".md", ".markdown"})
_MAX_HEAD_BYTES = 1024  # plenty for a frontmatter block


def parse_story_tag(content: str) -> str | None:
    """Return the `story:` frontmatter value, or None if absent.

    Accepts either the full file content or just the head — only the
    leading frontmatter block (between two `---` delimiters at the
    start of the file) is examined.
    """
    if not content:
        return None
    head = content[:_MAX_HEAD_BYTES]
    lines = head.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return None
        if stripped.lower().startswith("story:"):
            value = stripped.split(":", 1)[1].strip()
            value = value.strip('"').strip("'")
            return value or None
    return None


def read_story_tag(path: Path) -> str | None:
    """Read just enough of `path` to extract its story tag."""
    if path.suffix.lower() not in _DOC_EXTS:
        return None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            head = f.read(_MAX_HEAD_BYTES)
    except OSError:
        return None
    return parse_story_tag(head)
