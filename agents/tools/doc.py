"""Document parsing tools — `doc:*`.

DESIGN §31. Read-only ingestion of human-authored formats. Files are
referenced via FileWorkspace paths (working/ or inputs/). All tools return
plain text or structured data; agents work from the result.

Stage 4 ships:
  - `doc:markdown` — passthrough text + simple ATX heading extraction
  - `doc:html`     — basic HTML→text extraction (no JS execution)

`doc:pdf`, `doc:docx`, `doc:xlsx`, `doc:image` are listed in §31 but
require optional dependencies (pypdf, python-docx, openpyxl, vision API).
They land in Stage 5.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from agents.tools.base import BaseTool
from core.file_workspace import FileWorkspace, PathSafetyError


def _err(msg: str) -> dict[str, Any]:
    return {"error": msg}


# ── Markdown ───────────────────────────────────────────────────────────────


class DocMarkdown(BaseTool):
    """Read a markdown file and return its text plus extracted headings.

    The agent gets the raw text (so it can quote precisely) plus a
    `headings` list (for navigation) — useful for large specs in
    `inputs/`.
    """

    name = "doc:markdown"
    description = "Read a markdown file and return raw text + extracted heading outline."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "from": {"type": "string", "enum": ["working", "inputs"], "description": "default working"},
        },
        "required": ["path"],
    }

    def __init__(self, files: FileWorkspace):
        self.files = files

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        source = arguments.get("from", "working")
        if not path or not isinstance(path, str):
            return _err("missing required argument: path")
        try:
            if source == "inputs":
                text = await self.files.read_input(path)
            else:
                text = await self.files.read(path)
        except PathSafetyError as e:
            return _err(f"path safety: {e}")
        except FileNotFoundError:
            return _err(f"not found: {path}")
        except OSError as e:
            return _err(f"read failed: {e}")

        return {"ok": True, "path": path, "from": source,
                "text": text, "headings": _extract_headings(text)}


_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _extract_headings(md: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in _ATX_HEADING.finditer(md):
        out.append({"level": len(m.group(1)), "text": m.group(2).strip()})
    return out


# ── HTML ───────────────────────────────────────────────────────────────────


class _TextExtractor(HTMLParser):
    """Strip tags and accumulate visible text. Skips <script> / <style>."""

    SKIP_TAGS = {"script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped)

    def get_text(self) -> str:
        return "\n".join(self._parts)


class DocHtml(BaseTool):
    """Strip tags from an HTML file and return the visible text."""

    name = "doc:html"
    description = "Read an HTML file and return its visible text (no scripts/styles)."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "from": {"type": "string", "enum": ["working", "inputs"]},
        },
        "required": ["path"],
    }

    def __init__(self, files: FileWorkspace):
        self.files = files

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path")
        source = arguments.get("from", "working")
        if not path or not isinstance(path, str):
            return _err("missing required argument: path")
        try:
            if source == "inputs":
                html = await self.files.read_input(path)
            else:
                html = await self.files.read(path)
        except PathSafetyError as e:
            return _err(f"path safety: {e}")
        except FileNotFoundError:
            return _err(f"not found: {path}")
        except OSError as e:
            return _err(f"read failed: {e}")

        extractor = _TextExtractor()
        extractor.feed(html)
        return {"ok": True, "path": path, "from": source, "text": extractor.get_text()}
