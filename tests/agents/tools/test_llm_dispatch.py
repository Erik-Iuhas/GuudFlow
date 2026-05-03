"""Track 12 — sub-LLM dispatch tools."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from agents.tools.llm_dispatch import (
    LlmDispatchTool,
    MAX_OUTPUT_TOKENS,
    PROTOCOL_CLEANUP_SYSTEM,
    ProtocolCleanupTool,
    SUMMARISE_SUBTASK_SYSTEM,
    SummariseSubtaskTool,
)
from core.llm import MockLLMClient, MockTurn
from core.llm.client import LLMResponse


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_resolver(client, model_id: str = "test-model"):
    def _resolve(use_case: str):
        return client, model_id
    return _resolve


def _make_sink():
    calls: list[tuple[str, int, int]] = []
    def _sink(use_case: str, in_tok: int, out_tok: int) -> None:
        calls.append((use_case, in_tok, out_tok))
    _sink.calls = calls  # type: ignore[attr-defined]
    return _sink


@dataclass
class _SpyClient:
    """Minimal spy client for tests that need explicit token counts and to
    assert call shape — MockLLMClient is fine for happy paths but doesn't
    expose per-call token attribution."""

    text: str = "ok"
    input_tokens: int = 0
    output_tokens: int = 0
    seen: list[dict] = None  # type: ignore[assignment]

    def __post_init__(self):
        self.seen = []

    async def complete(
        self, *, system, messages, max_tokens, model,
        tools=None, cache_system=False,
    ):
        self.seen.append({
            "system": system,
            "messages": messages,
            "max_tokens": max_tokens,
            "model": model,
        })
        return LLMResponse(
            text=self.text,
            tool_calls=[],
            stop_reason="end_turn",
            model=model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


# ── Generic LlmDispatchTool ────────────────────────────────────────────────


async def test_llm_dispatch_happy_path():
    client = _SpyClient(text="hello there", input_tokens=10, output_tokens=3)
    sink = _make_sink()
    tool = LlmDispatchTool(resolver=_make_resolver(client), metrics_sink=sink)

    result = await tool.run({
        "system": "You are a greeter.",
        "user": "Say hi.",
        "max_tokens": 50,
    })

    assert result["text"] == "hello there"
    assert result["model"] == "test-model"
    assert result["input_tokens"] == 10
    assert result["output_tokens"] == 3
    assert sink.calls == [("llm-dispatch", 10, 3)]
    # System + user landed in the LLM call as expected.
    assert client.seen[0]["system"] == "You are a greeter."
    assert client.seen[0]["messages"][0].content == "Say hi."


async def test_llm_dispatch_missing_inputs_returns_error():
    client = _SpyClient()
    tool = LlmDispatchTool(resolver=_make_resolver(client))
    res = await tool.run({"system": "", "user": "hi"})
    assert "error" in res
    res = await tool.run({"system": "x", "user": ""})
    assert "error" in res


async def test_llm_dispatch_clamps_max_tokens():
    """Even an over-large request is clamped to MAX_OUTPUT_TOKENS server-side."""
    client = _SpyClient()
    tool = LlmDispatchTool(resolver=_make_resolver(client))
    await tool.run({"system": "s", "user": "u", "max_tokens": 99999})
    assert client.seen[0]["max_tokens"] == MAX_OUTPUT_TOKENS


async def test_llm_dispatch_clamps_negative_max_tokens():
    client = _SpyClient()
    tool = LlmDispatchTool(resolver=_make_resolver(client))
    await tool.run({"system": "s", "user": "u", "max_tokens": -10})
    assert client.seen[0]["max_tokens"] == 1


async def test_llm_dispatch_resolver_failure_returned_as_error():
    def _bad_resolver(_uc: str):
        raise RuntimeError("no model")
    tool = LlmDispatchTool(resolver=_bad_resolver)
    res = await tool.run({"system": "s", "user": "u"})
    assert "error" in res
    assert "no model" in res["error"] or "resolver" in res["error"].lower()


async def test_llm_dispatch_sink_failure_does_not_break_call():
    client = _SpyClient(text="ok", input_tokens=1, output_tokens=1)

    def _bad_sink(*args, **kwargs):
        raise RuntimeError("sink boom")

    tool = LlmDispatchTool(resolver=_make_resolver(client), metrics_sink=_bad_sink)
    res = await tool.run({"system": "s", "user": "u"})
    assert res.get("text") == "ok"


# ── SummariseSubtaskTool ───────────────────────────────────────────────────


async def test_summarise_subtask_returns_record_shape():
    payload = '{"summary": "Implemented login.", "deliverables": ["routes/login.py"]}'
    client = _SpyClient(text=payload, input_tokens=20, output_tokens=10)
    sink = _make_sink()
    tool = SummariseSubtaskTool(resolver=_make_resolver(client), metrics_sink=sink)

    res = await tool.run({
        "subtask_goal": "build the login route",
        "tool_calls": [
            {"name": "fs-write", "arguments": {"path": "routes/login.py"}, "result_summary": "wrote 42 bytes"},
        ],
    })

    assert res["summary"] == "Implemented login."
    assert res["deliverables"] == ["routes/login.py"]
    assert sink.calls == [("summarise-subtask", 20, 10)]


async def test_summarise_subtask_uses_locked_system_prompt():
    client = _SpyClient(text='{"summary": "ok", "deliverables": []}')
    tool = SummariseSubtaskTool(resolver=_make_resolver(client))
    await tool.run({"subtask_goal": "g", "tool_calls": []})
    assert client.seen[0]["system"] == SUMMARISE_SUBTASK_SYSTEM


async def test_summarise_subtask_falls_back_on_invalid_json():
    """If the model returns prose, we extract a best-effort summary."""
    client = _SpyClient(text="not json at all")
    tool = SummariseSubtaskTool(resolver=_make_resolver(client))
    res = await tool.run({"subtask_goal": "task X", "tool_calls": []})
    assert res["summary"]  # non-empty
    assert res["deliverables"] == []


async def test_summarise_subtask_missing_goal_errors():
    client = _SpyClient()
    tool = SummariseSubtaskTool(resolver=_make_resolver(client))
    res = await tool.run({"subtask_goal": "", "tool_calls": []})
    assert "error" in res


# ── ProtocolCleanupTool ────────────────────────────────────────────────────


async def test_protocol_cleanup_returns_block():
    block = "[COMMIT]\n  summary: auth route done\n  closes: task:s1"
    client = _SpyClient(text=block, input_tokens=15, output_tokens=20)
    sink = _make_sink()
    tool = ProtocolCleanupTool(resolver=_make_resolver(client), metrics_sink=sink)

    # Pass s1 as a valid id so the closes: line survives the strip pass.
    res = await tool.run({
        "raw_response": "I have done the auth route. closes task s1.",
        "expected_kind": "COMMIT",
        "valid_task_ids": ["s1"],
    })
    assert res["protocol_block"] == block
    assert sink.calls == [("protocol-cleanup", 15, 20)]


async def test_protocol_cleanup_strips_fabricated_closes():
    """The cheap model fabricates `closes: task:implementation-complete`
    (real bug from logic_dev's run_ab6fcc9f4c). With no matching id in
    valid_task_ids, the closes line MUST be stripped before the runner
    accepts the recovered block."""
    fabricated = (
        "[COMMIT]\n"
        "  summary: implement core game logic\n"
        "  closes: task:implementation-complete"
    )
    client = _SpyClient(text=fabricated)
    tool = ProtocolCleanupTool(resolver=_make_resolver(client))
    res = await tool.run({
        "raw_response": "I've completed the implementation.",
        "expected_kind": "COMMIT",
        "valid_task_ids": ["abc123", "def456"],  # neither matches
    })
    assert "closes:" not in res["protocol_block"]
    assert "implement core game logic" in res["protocol_block"]


async def test_protocol_cleanup_strips_all_closes_when_no_valid_ids():
    """If the agent's desk is empty, NO closes: line can be valid —
    strip them all unconditionally."""
    block = "[COMMIT]\n  summary: tweaked layout\n  closes: task:any-id"
    client = _SpyClient(text=block)
    tool = ProtocolCleanupTool(resolver=_make_resolver(client))
    res = await tool.run({
        "raw_response": "tweaked layout",
        "expected_kind": "COMMIT",
        # valid_task_ids omitted entirely → empty set → strip all closes
    })
    assert "closes:" not in res["protocol_block"]


async def test_protocol_cleanup_preserves_real_task_ids():
    """When the cleanup model picks a real id (from prose that genuinely
    cited it), the closes: line survives."""
    block = (
        "[COMMIT]\n"
        "  summary: implemented player movement\n"
        "  closes: task:9e217ad00cf64ec"
    )
    client = _SpyClient(text=block)
    tool = ProtocolCleanupTool(resolver=_make_resolver(client))
    res = await tool.run({
        "raw_response": "Did task:9e217ad00cf64ec — player movement.",
        "expected_kind": "COMMIT",
        "valid_task_ids": ["9e217ad00cf64ec"],
    })
    assert "closes: task:9e217ad00cf64ec" in res["protocol_block"]


async def test_protocol_cleanup_uses_locked_system_prompt():
    client = _SpyClient(text="[COMMIT]\n  summary: ok")
    tool = ProtocolCleanupTool(resolver=_make_resolver(client))
    await tool.run({"raw_response": "x", "expected_kind": "COMMIT"})
    assert client.seen[0]["system"] == PROTOCOL_CLEANUP_SYSTEM


async def test_protocol_cleanup_rejects_unknown_kind():
    client = _SpyClient()
    tool = ProtocolCleanupTool(resolver=_make_resolver(client))
    res = await tool.run({"raw_response": "x", "expected_kind": "BANANA"})
    assert "error" in res


async def test_protocol_cleanup_emits_parseable_block():
    """The wrapped output round-trips through the protocol's compact-block extractor."""
    from core.protocol import extract_trailing

    block = "[COMMIT]\n  summary: auth route done"
    client = _SpyClient(text=block)
    tool = ProtocolCleanupTool(resolver=_make_resolver(client))
    res = await tool.run({"raw_response": "raw", "expected_kind": "COMMIT"})
    parsed = extract_trailing(res["protocol_block"])
    assert parsed is not None
    assert parsed.kind == "COMMIT"
    assert "summary" in parsed.fields


# ── USE_CASE wiring ────────────────────────────────────────────────────────


def test_each_tool_has_distinct_use_case():
    assert LlmDispatchTool.USE_CASE == "llm-dispatch"
    assert SummariseSubtaskTool.USE_CASE == "summarise-subtask"
    assert ProtocolCleanupTool.USE_CASE == "protocol-cleanup"


async def test_use_case_drives_resolver():
    """Resolver receives the tool's USE_CASE so per-use-case model picks work."""
    seen_use_cases: list[str] = []

    def _resolver(use_case: str):
        seen_use_cases.append(use_case)
        return _SpyClient(), "test"

    await LlmDispatchTool(resolver=_resolver).run({"system": "s", "user": "u"})
    await SummariseSubtaskTool(resolver=_resolver).run({"subtask_goal": "g", "tool_calls": []})
    await ProtocolCleanupTool(resolver=_resolver).run({"raw_response": "r", "expected_kind": "COMMIT"})

    assert seen_use_cases == ["llm-dispatch", "summarise-subtask", "protocol-cleanup"]


# ── Tool surface (RBAC / manifest) ─────────────────────────────────────────


def test_baseline_scopes_for_sub_llm_tools():
    """Most sub-LLM tools require explicit RBAC grant (token-spending,
    agent-callable). Track 9 promoted `protocol-cleanup` to baseline so
    the runner's parse-fail sidecar always has it available without
    project config — the cost is gated by the parse-fail trigger."""
    from core.tool_manifest import BASELINE_LOCAL_SCOPES
    assert "llm-dispatch" not in BASELINE_LOCAL_SCOPES
    assert "summarise-subtask" not in BASELINE_LOCAL_SCOPES
    # Track 9 — protocol-cleanup is baseline (sidecar consumer).
    assert "protocol-cleanup" in BASELINE_LOCAL_SCOPES


def test_tools_registered_in_local_tool_classes():
    """The runner's LOCAL_TOOL_CLASSES tuple includes all three."""
    from core.runner import LOCAL_TOOL_CLASSES
    names = {cls.name for cls in LOCAL_TOOL_CLASSES}
    assert "llm-dispatch" in names
    assert "summarise-subtask" in names
    assert "protocol-cleanup" in names
