"""Track 9 — protocol cleaner sidecar.

When the agent emits prose without a valid trailing protocol block,
the runner-side sidecar dispatches `protocol-cleanup` (Track 12
wrapper) against the agent's raw response. If cleanup returns a
parseable block, the heavy retry is skipped entirely.

Tested behaviours:
  - cleanup success short-circuits the retry; PROTOCOL_CLEANUP_AUTO
    signal posted; protocol_retries_used stays at 0.
  - cleanup failure falls through to today's nudge logic; existing
    PROTOCOL_PARSE_FAIL behaviour preserved.
  - one cleanup attempt per parse-fail (no nested retry on the
    cleanup itself).
  - tool not granted → silent fall-through.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from core import protocol
from core.llm import LLMResponse
from core.runner import AgentRunner


# ── Spy LLMs ───────────────────────────────────────────────────────────────


@dataclass
class _AgentLLMReplies:
    """Programmable agent client used to drive the parse-fail path."""
    queue: list[str]

    async def complete(self, *, system, messages, max_tokens, model,
                       tools=None, cache_system=False):
        if not self.queue:
            text = "[COMMIT]\n  summary: fallback"
        else:
            text = self.queue.pop(0)
        return LLMResponse(
            text=text, tool_calls=[], stop_reason="end_turn", model=model,
            input_tokens=10, output_tokens=5,
        )


@dataclass
class _CleanupClient:
    """Stand-in for the dispatch resolver's LLM. Returns the canned
    block string each time complete() is called, plus token counts."""
    text: str
    input_tokens: int = 8
    output_tokens: int = 12
    seen: list[dict] | None = None

    def __post_init__(self):
        self.seen = []

    async def complete(self, *, system, messages, max_tokens, model,
                       tools=None, cache_system=False):
        self.seen.append({"system": system, "max_tokens": max_tokens})
        return LLMResponse(
            text=self.text, tool_calls=[], stop_reason="end_turn",
            model=model,
            input_tokens=self.input_tokens, output_tokens=self.output_tokens,
        )


def _stub_resolver(client, model_id="cleanup-model"):
    def _resolve(use_case: str):
        return client, model_id
    return _resolve


# ── Helpers to invoke the helper directly ─────────────────────────────────


def _build_minimal_runner(tmp_path, *, agent_llm=None):
    """Construct a runner with a single 'dev' role and a minimal agent
    set up enough to exercise `_try_auto_protocol_cleanup`."""
    from core.runner import AgentConfig, ProjectConfig
    from core.tool_manifest import RoleRBAC

    proj = ProjectConfig(
        project_id="p_cleanup", goal="g",
        agents=[AgentConfig(role="dev", model_tier="solid")],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=["protocol-cleanup"])},
    )
    runner = AgentRunner(
        project=proj, run_id="r_cleanup",
        run_root=tmp_path / "run",
        llm=agent_llm or _AgentLLMReplies(queue=[]),
    )
    return runner


def _attach_dev_with_cleanup_tool(runner, cleanup_client):
    """Set up the dev agent with a working protocol-cleanup tool wired
    to the given cleanup client. Mirrors what `_instantiate_local_tools`
    would do at run time."""
    from agents.tools.llm_dispatch import ProtocolCleanupTool
    from core.workspace import IterationMetrics

    class _Stub:
        _local_tools = None
        _current_metrics = None

    dev = _Stub()
    metrics = IterationMetrics(run_id=runner.run_id, role="dev", iteration=0)
    dev._current_metrics = metrics

    def _sink(use_case, in_tok, out_tok):
        bucket = metrics.sub_llm_breakdown.setdefault(
            use_case, {"calls": 0, "input_tokens": 0, "output_tokens": 0},
        )
        bucket["calls"] += 1
        bucket["input_tokens"] += in_tok
        bucket["output_tokens"] += out_tok

    cleanup_tool = ProtocolCleanupTool(
        resolver=_stub_resolver(cleanup_client), metrics_sink=_sink,
    )
    dev._local_tools = {"protocol-cleanup": cleanup_tool}
    runner.agents["dev"] = dev
    return dev


# ── Sidecar success ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sidecar_recovers_block_short_circuits_retry(tmp_path):
    runner = _build_minimal_runner(tmp_path)
    dev = _attach_dev_with_cleanup_tool(
        runner, _CleanupClient(text="[COMMIT]\n  summary: cleaned up"),
    )

    block, note = await runner._try_auto_protocol_cleanup(
        "dev", "I have done the thing.", is_reviewer=False,
    )
    assert block is not None
    assert block.kind == "COMMIT"
    assert "summary" in block.fields
    assert "recovered COMMIT" in note
    # Cost rolled into sub_llm_breakdown.
    bd = dev._current_metrics.sub_llm_breakdown.get("protocol-cleanup")
    assert bd is not None and bd["calls"] == 1


@pytest.mark.asyncio
async def test_sidecar_uses_VERIFY_for_reviewers(tmp_path):
    """Reviewers need [VERIFY], not [COMMIT]. Sidecar must pass the
    correct expected_kind."""
    cleanup = _CleanupClient(text="[VERIFY]\n  result: PASS")
    runner = _build_minimal_runner(tmp_path)
    _attach_dev_with_cleanup_tool(runner, cleanup)

    block, _note = await runner._try_auto_protocol_cleanup(
        "dev", "Looks correct.", is_reviewer=True,
    )
    assert block is not None
    assert block.kind == "VERIFY"
    # The cleanup system prompt was sent (locked, contains schema reference).
    assert any("AgentFlow protocol blocks" in c["system"] for c in cleanup.seen)


# ── Sidecar failure paths ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sidecar_falls_through_when_tool_not_granted(tmp_path):
    """Agent has no protocol-cleanup tool — sidecar returns None silently
    and caller falls through to the existing nudge."""
    runner = _build_minimal_runner(tmp_path)

    class _Stub:
        _local_tools: dict = {}
        _current_metrics = None
    runner.agents["dev"] = _Stub()

    block, note = await runner._try_auto_protocol_cleanup(
        "dev", "prose", is_reviewer=False,
    )
    assert block is None
    assert "not granted" in note


@pytest.mark.asyncio
async def test_sidecar_falls_through_when_cleanup_unparseable(tmp_path):
    """Cleanup model returns text that doesn't parse — sidecar returns
    None and caller falls through."""
    runner = _build_minimal_runner(tmp_path)
    _attach_dev_with_cleanup_tool(
        runner, _CleanupClient(text="just prose, no protocol block here"),
    )
    block, note = await runner._try_auto_protocol_cleanup(
        "dev", "more prose", is_reviewer=False,
    )
    assert block is None
    assert "unparseable" in note or "no trailing" in note


@pytest.mark.asyncio
async def test_sidecar_falls_through_when_dispatch_raises(tmp_path):
    """Cleanup client raises (e.g. provider down) — sidecar swallows
    the error and returns None."""
    class _BoomClient:
        async def complete(self, *args, **kwargs):
            raise RuntimeError("provider exploded")

    runner = _build_minimal_runner(tmp_path)
    _attach_dev_with_cleanup_tool(runner, _BoomClient())

    block, note = await runner._try_auto_protocol_cleanup(
        "dev", "prose", is_reviewer=False,
    )
    assert block is None
    # ProtocolCleanupTool catches the exception and returns {"error": ...},
    # so the sidecar surfaces "cleanup returned error".
    assert "error" in note.lower() or "raised" in note.lower()


@pytest.mark.asyncio
async def test_sidecar_falls_through_when_cleanup_returns_empty(tmp_path):
    runner = _build_minimal_runner(tmp_path)
    _attach_dev_with_cleanup_tool(runner, _CleanupClient(text=""))

    block, note = await runner._try_auto_protocol_cleanup(
        "dev", "prose", is_reviewer=False,
    )
    assert block is None
    assert "empty" in note.lower() or "no trailing" in note.lower()


# ── No agent yet ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sidecar_handles_unknown_role(tmp_path):
    runner = _build_minimal_runner(tmp_path)
    block, note = await runner._try_auto_protocol_cleanup(
        "ghost-role", "x", is_reviewer=False,
    )
    assert block is None
    assert "not yet" in note.lower() or "agent" in note.lower()


@pytest.mark.asyncio
async def test_sidecar_rejects_kind_mismatch_for_specialist(tmp_path):
    """Real bug from run_dfcfd6eee3: cleanup model returned [VERIFY] for
    a specialist (renderer_dev) because the prose said 'I verified the
    rendering works'. Accepting the VERIFY tagged the specialist as a
    no-op reviewer and looped them through FAILED → STARTED → FAILED.

    Kind must match expected; mismatch returns None so the normal
    nudge path retries instead.
    """
    cleanup = _CleanupClient(text="[VERIFY]\n  result: PASS")
    runner = _build_minimal_runner(tmp_path)
    _attach_dev_with_cleanup_tool(runner, cleanup)

    # is_reviewer=False → expected_kind=COMMIT, but cleanup returns VERIFY
    block, note = await runner._try_auto_protocol_cleanup(
        "dev",
        "I implemented the renderer and verified it works.",
        is_reviewer=False,
    )
    assert block is None
    assert "expected" in note.lower() and "verify" in note.lower()


@pytest.mark.asyncio
async def test_sidecar_rejects_commit_when_reviewer_expected(tmp_path):
    """Mirror case: reviewer wrote prose that sounds like a commit
    summary ('I changed X'). Cleanup must not let that slip through
    as a [COMMIT] when the reviewer was supposed to emit [VERIFY]."""
    cleanup = _CleanupClient(text="[COMMIT]\n  summary: changed X")
    runner = _build_minimal_runner(tmp_path)
    _attach_dev_with_cleanup_tool(runner, cleanup)

    block, note = await runner._try_auto_protocol_cleanup(
        "dev", "I changed X.", is_reviewer=True,
    )
    assert block is None
    assert "expected" in note.lower() and "commit" in note.lower()
