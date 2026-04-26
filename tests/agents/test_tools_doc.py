"""Tests for agents/tools/doc.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.tools.doc import DocHtml, DocMarkdown
from core.file_workspace import FileWorkspace


@pytest.fixture
def fw(tmp_path: Path) -> FileWorkspace:
    return FileWorkspace(tmp_path / "ws")


# ── doc:markdown ───────────────────────────────────────────────────────────


async def test_doc_markdown_extracts_headings(fw: FileWorkspace):
    md = "# Title\nSome text.\n\n## Section A\nMore.\n\n### Sub\nDetail.\n"
    await fw.write("spec.md", md)
    res = await DocMarkdown(fw).run({"path": "spec.md"})
    assert res["ok"] is True
    assert res["text"] == md
    levels = [h["level"] for h in res["headings"]]
    titles = [h["text"] for h in res["headings"]]
    assert levels == [1, 2, 3]
    assert titles == ["Title", "Section A", "Sub"]


async def test_doc_markdown_from_inputs(fw: FileWorkspace):
    (fw.inputs / "req.md").write_text("# Requirements\nText", encoding="utf-8")
    res = await DocMarkdown(fw).run({"path": "req.md", "from": "inputs"})
    assert res["ok"] is True
    assert "Requirements" in [h["text"] for h in res["headings"]]


async def test_doc_markdown_missing(fw: FileWorkspace):
    res = await DocMarkdown(fw).run({"path": "nope.md"})
    assert "error" in res


async def test_doc_markdown_path_safety(fw: FileWorkspace):
    res = await DocMarkdown(fw).run({"path": "../escape.md"})
    assert "error" in res


# ── doc:html ───────────────────────────────────────────────────────────────


async def test_doc_html_strips_tags_and_skips_scripts(fw: FileWorkspace):
    html = """<html>
      <head><style>body{}</style><script>alert(1)</script></head>
      <body><h1>Hello</h1><p>world</p></body>
    </html>"""
    await fw.write("page.html", html)
    res = await DocHtml(fw).run({"path": "page.html"})
    assert res["ok"] is True
    text = res["text"]
    assert "Hello" in text
    assert "world" in text
    assert "alert(1)" not in text
    assert "body{}" not in text


async def test_doc_html_missing(fw: FileWorkspace):
    res = await DocHtml(fw).run({"path": "nope.html"})
    assert "error" in res
