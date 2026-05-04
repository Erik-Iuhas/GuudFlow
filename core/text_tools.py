"""Parser for Aider-style `[TOOL ...]` text-format tool calls.

Used when an agent runs against a model with `tool_format == "text"` —
the LLM emits tool calls as plain-text blocks instead of structured
`tool_calls`. The runner parses them out of `LLMResponse.text` and
routes each through `BaseAgent.call_tool()` exactly as if it had come
back as a structured tool_use block.

Format the model is asked to emit (defined in
`BaseAgent._text_tool_format_primer`):

    [TOOL fs-write]
    {"path": "foo.py", "content": "print('hi')\\n"}
    [/TOOL]

Rules enforced by this parser:
  - opening marker `[TOOL <name>]` on its own line (whitespace tolerated)
  - closing marker `[/TOOL]` on its own line
  - body between markers parses as a JSON object
  - tool name is `[A-Za-z_][\w-]*`, ≥ 2 chars
  - any other text in the response (prose, protocol blocks, narration)
    is preserved as-is on `ParsedTextResponse.text` so the runner can
    still route the closing [COMMIT]/[VERIFY] block through the
    protocol parser

Lenient about: trailing whitespace, blank lines around blocks, prose
between blocks, multiple blocks per response. Strict about: name shape,
JSON well-formedness — bad blocks become ParseFailure entries instead
of crashing, so the runner can feed them back to the model as repair
prompts (Aider-style).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedToolCall:
    """One successfully parsed `[TOOL <name>]` block."""

    name: str
    arguments: dict[str, Any]
    raw: str  # the full block text including markers, for debugging


@dataclass
class ParseFailure:
    """A block that looked like a `[TOOL ...]` invocation but failed to parse.

    Surfaced so the runner can emit a tool_result with `is_error=True` and
    `content=reason` — the model gets the same repair signal it would get
    from a real tool execution error."""

    raw: str       # the malformed block (or just the opener if no closer)
    reason: str    # short human-readable diagnosis


@dataclass
class ParsedTextResponse:
    """Result of parsing a text-mode LLM response.

    `text` is everything OUTSIDE `[TOOL ...]` blocks — including prose,
    reasoning narration, and the closing protocol block. The runner
    feeds `text` through the existing protocol parser to extract
    [COMMIT]/[VERIFY]/[ACTION]/etc.

    `tool_calls` is the ordered list of successfully-parsed invocations
    (run them in order, just like a structured response with multiple
    tool_use blocks).

    `failures` is parse failures the runner should report back as error
    tool_results so the model can correct on the next turn."""

    text: str
    tool_calls: list[ParsedToolCall] = field(default_factory=list)
    failures: list[ParseFailure] = field(default_factory=list)


# Opener / closer regexes. Anchored to line starts via re.MULTILINE in
# the scanner. Whitespace around the name is tolerated; the name itself
# must match the conservative identifier shape we document in the primer.
_OPEN_RE = re.compile(
    r"^[ \t]*\[TOOL[ \t]+([A-Za-z_][\w-]*)[ \t]*\][ \t]*$",
    re.MULTILINE,
)
_CLOSE_RE = re.compile(r"^[ \t]*\[/TOOL\][ \t]*$", re.MULTILINE)


def parse_text_tool_calls(response_text: str) -> ParsedTextResponse:
    """Extract `[TOOL <name>]` blocks from a text-mode LLM response.

    Walks left-to-right. For each `[TOOL <name>]` opener we find the
    nearest subsequent `[/TOOL]` closer (no nesting allowed — agents
    don't need it and it would make the parser fragile). Body between
    markers is parsed as JSON; on success we emit a ParsedToolCall, on
    failure a ParseFailure. Either way the entire block (markers + body)
    is excised from the output `text`.

    Text outside any block is concatenated verbatim and returned as
    `text` — the runner runs that through the protocol parser to find
    [COMMIT]/[VERIFY]/etc. just like in structured mode.
    """
    if "[TOOL" not in response_text:
        # Fast path: no blocks, no allocation.
        return ParsedTextResponse(text=response_text)

    tool_calls: list[ParsedToolCall] = []
    failures: list[ParseFailure] = []
    text_parts: list[str] = []
    cursor = 0

    for opener in _OPEN_RE.finditer(response_text):
        open_start = opener.start()
        open_end = opener.end()
        name = opener.group(1)

        # Anything between previous block (or start) and this opener is
        # plain text — preserve it verbatim.
        text_parts.append(response_text[cursor:open_start])

        # Find the next closer AFTER the opener. If there isn't one, this
        # is a half-emitted block — record a failure and bail; everything
        # remaining stays as text.
        closer_match = _CLOSE_RE.search(response_text, pos=open_end)
        if closer_match is None:
            raw = response_text[open_start:]
            failures.append(ParseFailure(
                raw=raw,
                reason="unterminated [TOOL] block — missing [/TOOL] marker",
            ))
            cursor = len(response_text)
            break

        body = response_text[open_end:closer_match.start()]
        full_block = response_text[open_start:closer_match.end()]

        # Strict JSON parse — single object expected. Diagnostics include
        # the JSON error so the model can repair on the next turn.
        try:
            args = json.loads(body)
        except json.JSONDecodeError as exc:
            failures.append(ParseFailure(
                raw=full_block,
                reason=(
                    f"[TOOL {name}] body is not valid JSON: "
                    f"{exc.msg} at line {exc.lineno} col {exc.colno}"
                ),
            ))
            cursor = closer_match.end()
            continue

        if not isinstance(args, dict):
            failures.append(ParseFailure(
                raw=full_block,
                reason=(
                    f"[TOOL {name}] body must be a JSON object "
                    f"(got {type(args).__name__})"
                ),
            ))
            cursor = closer_match.end()
            continue

        tool_calls.append(ParsedToolCall(name=name, arguments=args, raw=full_block))
        cursor = closer_match.end()

    # Anything after the last block.
    text_parts.append(response_text[cursor:])

    return ParsedTextResponse(
        text="".join(text_parts),
        tool_calls=tool_calls,
        failures=failures,
    )
