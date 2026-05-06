"""Tests for `core.text_tools.parse_text_tool_calls`.

The parser is the load-bearing piece of text-mode tool routing — every
weak-model run that doesn't use structured tool_calls flows through
here. Cover happy path, malformed JSON, missing closer, multiple blocks
per response, and the prose-only fast path.
"""
from __future__ import annotations

from core.text_tools import (
    ParsedToolCall,
    ParseFailure,
    parse_text_tool_calls,
)


def test_no_blocks_returns_text_unchanged() -> None:
    """Fast path: response with no [TOOL] markers should round-trip."""
    text = "Just a [COMMIT] block with summary: nothing"
    out = parse_text_tool_calls(text)
    assert out.text == text
    assert out.tool_calls == []
    assert out.failures == []


def test_single_well_formed_block() -> None:
    """One [TOOL] block parses to one ParsedToolCall and is stripped from text."""
    text = '[TOOL fs-read]\n{"path": "src/main.py"}\n[/TOOL]'
    out = parse_text_tool_calls(text)
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "fs-read"
    assert out.tool_calls[0].arguments == {"path": "src/main.py"}
    assert "[TOOL" not in out.text
    assert "[/TOOL]" not in out.text
    assert out.failures == []


def test_multiple_blocks_in_order() -> None:
    """Two blocks → two calls in the order they appeared."""
    text = (
        '[TOOL fs-read]\n{"path": "a.py"}\n[/TOOL]\n'
        "Reading the spec.\n"
        '[TOOL fs-write]\n{"path": "b.py", "content": "x"}\n[/TOOL]'
    )
    out = parse_text_tool_calls(text)
    assert [c.name for c in out.tool_calls] == ["fs-read", "fs-write"]
    assert out.tool_calls[1].arguments == {"path": "b.py", "content": "x"}
    # Prose between blocks is preserved.
    assert "Reading the spec." in out.text


def test_prose_around_blocks_preserved_for_protocol_parser() -> None:
    """The text returned must keep the trailing [COMMIT]/[VERIFY] block intact —
    the runner feeds this string through the existing protocol parser."""
    text = (
        "Reasoning ...\n"
        '[TOOL fs-write]\n{"path": "x", "content": "y"}\n[/TOOL]\n'
        "\n"
        "[COMMIT]\n  summary: wrote x\n  files: x"
    )
    out = parse_text_tool_calls(text)
    assert "[COMMIT]" in out.text
    assert "summary: wrote x" in out.text
    # Block markers are gone.
    assert "[TOOL" not in out.text


def test_invalid_json_body_records_failure() -> None:
    """Malformed JSON inside [TOOL] body is captured as a ParseFailure with
    a diagnostic reason — the runner relays this back to the model as a
    repair signal, just like a structured tool error."""
    text = "[TOOL fs-write]\n{not json}\n[/TOOL]"
    out = parse_text_tool_calls(text)
    assert out.tool_calls == []
    assert len(out.failures) == 1
    assert "not valid JSON" in out.failures[0].reason
    assert out.failures[0].raw.startswith("[TOOL fs-write]")


def test_unterminated_block_records_failure() -> None:
    """An opener with no closer is a ParseFailure; the runner can repair."""
    text = '[TOOL fs-read]\n{"path": "x"}\n'
    out = parse_text_tool_calls(text)
    assert out.tool_calls == []
    assert len(out.failures) == 1
    assert "unterminated" in out.failures[0].reason.lower()


def test_non_object_json_body_recorded_as_failure() -> None:
    """JSON arrays/scalars are valid JSON but not valid tool arguments —
    a tool always takes a dict of named args. Reject explicitly."""
    text = '[TOOL fs-read]\n["not", "an", "object"]\n[/TOOL]'
    out = parse_text_tool_calls(text)
    assert out.tool_calls == []
    assert len(out.failures) == 1
    assert "must be a JSON object" in out.failures[0].reason


def test_mixed_success_and_failure_in_one_response() -> None:
    """A response with one good block and one bad should preserve both:
    the good one runs, the bad one becomes a repair signal."""
    text = (
        '[TOOL fs-read]\n{"path": "a"}\n[/TOOL]\n'
        '[TOOL fs-write]\n{broken}\n[/TOOL]'
    )
    out = parse_text_tool_calls(text)
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "fs-read"
    assert len(out.failures) == 1


def test_whitespace_tolerance_around_markers() -> None:
    """Leading whitespace on marker lines is tolerated — models are sloppy
    about indentation and we shouldn't punish them for it."""
    text = (
        "  [TOOL fs-read]\n"
        '{"path": "x"}\n'
        "  [/TOOL]"
    )
    out = parse_text_tool_calls(text)
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].arguments == {"path": "x"}


def test_hyphenated_and_underscored_names() -> None:
    """Tool names with hyphens (fs-list) and underscores must parse — these
    are common in the codebase's manifest."""
    text = (
        '[TOOL fs-list]\n{"path": "."}\n[/TOOL]\n'
        '[TOOL desk_search]\n{"q": "x"}\n[/TOOL]'
    )
    out = parse_text_tool_calls(text)
    assert [c.name for c in out.tool_calls] == ["fs-list", "desk_search"]


def test_synthesized_calls_have_isolated_state() -> None:
    """Each ParsedToolCall is a fresh dataclass — mutating one's arguments
    must not affect another's. Guard against accidental reference sharing
    that would corrupt the runner's tool_results."""
    text = (
        '[TOOL t]\n{"x": 1}\n[/TOOL]\n'
        '[TOOL t]\n{"x": 2}\n[/TOOL]'
    )
    out = parse_text_tool_calls(text)
    out.tool_calls[0].arguments["x"] = 999
    assert out.tool_calls[1].arguments["x"] == 2


def test_dataclass_field_defaults() -> None:
    """ParsedToolCall and ParseFailure expose the documented fields."""
    pc = ParsedToolCall(name="t", arguments={}, raw="")
    assert pc.name == "t"
    pf = ParseFailure(raw="x", reason="why")
    assert pf.reason == "why"
