"""Tests for the runner's text-mode response normalizer.

`_normalize_text_mode_response()` turns `[TOOL <name>] {...} [/TOOL]`
blocks emitted as plain text into synthetic `ToolCall` objects so the
rest of the iteration loop processes them identically to a structured
response. Parse failures become synthetic ToolCalls against a sentinel
name (`__text_parse_error__`) which the runner intercepts before
call_tool() to surface a useful repair message back to the model.
"""
from __future__ import annotations

from core.llm import LLMResponse
from core.runner import (
    _TEXT_PARSE_ERROR_TOOL,
    _normalize_text_mode_response,
)


def _resp(text: str = "", tool_calls=None, stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=list(tool_calls or []),
        stop_reason=stop_reason,
    )


def test_no_tool_blocks_response_untouched() -> None:
    """A response with no [TOOL] markers must pass through — the runner
    falls through to the protocol-block branch which extracts [COMMIT]."""
    r = _resp(text="[COMMIT]\n  summary: x\n  files: x")
    _normalize_text_mode_response(r)
    assert r.tool_calls == []
    assert r.stop_reason == "end_turn"
    assert "[COMMIT]" in r.text


def test_one_block_synthesizes_tool_call() -> None:
    """One [TOOL fs-write] block → one ToolCall and stop_reason flips to
    tool_use so the existing tool-use branch fires."""
    r = _resp(text='[TOOL fs-write]\n{"path": "a", "content": "b"}\n[/TOOL]')
    _normalize_text_mode_response(r)
    assert len(r.tool_calls) == 1
    tc = r.tool_calls[0]
    assert tc.name == "fs-write"
    assert tc.arguments == {"path": "a", "content": "b"}
    assert tc.id.startswith("text_tc_")
    assert r.stop_reason == "tool_use"
    assert "[TOOL" not in r.text
    assert "[/TOOL]" not in r.text


def test_multiple_blocks_preserved_in_order() -> None:
    """Block order must match emission order — same as structured tool_calls."""
    r = _resp(text=(
        '[TOOL fs-read]\n{"path": "a"}\n[/TOOL]\n'
        '[TOOL fs-list]\n{"path": "."}\n[/TOOL]'
    ))
    _normalize_text_mode_response(r)
    assert [tc.name for tc in r.tool_calls] == ["fs-read", "fs-list"]


def test_parse_failure_routed_to_synthetic_tool() -> None:
    """Malformed JSON becomes a ToolCall against the parse-error sentinel,
    so the runner's call-tool branch can intercept it and surface the
    diagnostic back to the model."""
    r = _resp(text='[TOOL fs-write]\n{not json}\n[/TOOL]')
    _normalize_text_mode_response(r)
    assert len(r.tool_calls) == 1
    tc = r.tool_calls[0]
    assert tc.name == _TEXT_PARSE_ERROR_TOOL
    assert tc.id.startswith("text_pe_")
    assert "not valid JSON" in tc.arguments["reason"]
    assert tc.arguments["raw"].startswith("[TOOL fs-write]")
    assert r.stop_reason == "tool_use"


def test_mixed_success_and_failure() -> None:
    """One good block + one bad block → both surface, in source order."""
    r = _resp(text=(
        '[TOOL fs-read]\n{"path": "a"}\n[/TOOL]\n'
        '[TOOL fs-write]\n{bad}\n[/TOOL]'
    ))
    _normalize_text_mode_response(r)
    names = [tc.name for tc in r.tool_calls]
    assert names == ["fs-read", _TEXT_PARSE_ERROR_TOOL]


def test_unterminated_block_surfaced_as_repair_signal() -> None:
    """Half-emitted blocks (likely truncated mid-emission) become parse
    errors — the model gets a clean signal to re-emit the full block."""
    r = _resp(text='[TOOL fs-read]\n{"path": "x"}\n')
    _normalize_text_mode_response(r)
    assert len(r.tool_calls) == 1
    assert r.tool_calls[0].name == _TEXT_PARSE_ERROR_TOOL
    assert "unterminated" in r.tool_calls[0].arguments["reason"].lower()


def test_text_after_blocks_is_preserved_for_protocol_parser() -> None:
    """Closing protocol blocks must survive normalization so the existing
    extract_trailing() path picks them up. Required for [COMMIT] /
    [VERIFY] / [ACTION] semantics in text-mode runs."""
    r = _resp(text=(
        '[TOOL fs-write]\n{"path": "x", "content": "y"}\n[/TOOL]\n'
        "\n"
        "[COMMIT]\n  summary: wrote x\n  files: x"
    ))
    _normalize_text_mode_response(r)
    assert "[COMMIT]" in r.text
    assert "summary: wrote x" in r.text


def test_tool_format_for_via_runner_method() -> None:
    """`AgentRunner._tool_format_for` plumbs ModelInfo.tool_format through
    verbatim and falls back to "structured" when the registry is missing
    or the model is unknown — keeps every existing run unchanged when
    models.json has no `tool_format` field set."""
    from core.runner import AgentRunner

    class _Mi:
        tool_format = "text"

    class _Reg:
        def get(self, model_id):
            return _Mi() if model_id == "weak" else None

    class _Holder:
        _model_registry = _Reg()

    # Bind the unbound method to a stand-in holder. Avoids constructing a
    # full AgentRunner — the helper only touches `self._model_registry`.
    assert AgentRunner._tool_format_for(_Holder(), "weak") == "text"  # type: ignore[arg-type]
    assert AgentRunner._tool_format_for(_Holder(), "unknown") == "structured"  # type: ignore[arg-type]

    # Registry-absent path: empty holder.
    class _Empty:
        _model_registry = None

    assert AgentRunner._tool_format_for(_Empty(), "anything") == "structured"  # type: ignore[arg-type]
