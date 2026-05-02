"""Track 12 — sub-LLM dispatch primitive + opinionated wrappers.

Three tools that let an agent fire a one-shot LLM call to a (typically
cheaper) model:

- `llm-dispatch`     — generic; agent crafts the full prompt
- `summarise-subtask` — wrapper for closed-subtask summaries (Track 8)
- `protocol-cleanup` — wrapper for converting prose into a protocol block
                       (Track 9 will use this)

All three route through the same dispatch path. The differences are the
locked-in system prompt and the post-processing of the model's response.

Design constraints:

- Tools are stateless; per-agent state (LLM client, model resolver,
  metrics sink) is injected at construction by the runner.
- `max_tokens` is hard-bounded to MAX_OUTPUT_TOKENS server-side so an
  agent can't burn the budget on a single call.
- Token usage rolls up into the calling agent's
  `IterationMetrics.sub_llm_breakdown[use_case]` via the metrics sink
  callback — visible in the live run analytics without parsing per-call
  rows.
- Model selection: the resolver callback picks (LLMClient, model_id)
  for a given use_case. Resolver respects the project's
  `sub_llm_models` map first, then falls back to
  `ModelRegistry.cheapest_tool_capable()`.

See `~/.claude/...track_12_sub_llm_dispatch.md` (in the buff folder) for
full design + commit plan.
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from agents.tools.base import BaseTool
from core.llm import LLMMessage
from core.llm.client import LLMClient


# Hard cap on max_tokens for any single dispatch call. Even with the
# generic primitive, no single sub-LLM call can spend more than this.
MAX_OUTPUT_TOKENS: int = 1024


# Resolver: given a use_case name, return (LLMClient, model_id_string).
# Implemented in the runner using the project's `sub_llm_models` map +
# ModelRegistry fallback.
ModelResolver = Callable[[str], tuple[LLMClient, str]]


# Metrics sink: bumps the calling agent's IterationMetrics row for the
# given use_case. Implemented by the runner as a closure over
# `agent._current_metrics`. Signature:
#     sink(use_case, input_tokens, output_tokens)
MetricsSink = Callable[[str, int, int], None]


# ── System prompts (locked) ────────────────────────────────────────────────


SUMMARISE_SUBTASK_SYSTEM = (
    "You are a TaskCompletionRecord summariser.\n"
    "Given a closed subtask's goal and the agent's tool-call trace, "
    "produce a 1-3 sentence summary plus a bulleted list of deliverables.\n"
    "Output strict JSON: {\"summary\": str, \"deliverables\": [str, ...]}.\n"
    "No prose outside the JSON object."
)


PROTOCOL_CLEANUP_SYSTEM = (
    "You convert prose into AgentFlow protocol blocks.\n"
    "Given raw_response and expected_kind, output exactly one protocol "
    "block of that kind and nothing else. Schema reference:\n"
    "  [COMMIT]\n  summary: <≤12 words>\n  closes: task:<id>\n"
    "  [VERIFY]\n  result: PASS|FAIL\n  detail: <required if FAIL>\n"
    "Do not invent task ids. If the raw response cited a task id, "
    "preserve it; if not, omit the closes: line."
)


# ── Generic primitive ─────────────────────────────────────────────────────


class LlmDispatchTool(BaseTool):
    """Generic sub-LLM dispatch — agent crafts the system + user prompt.

    Use cases that aren't covered by `summarise-subtask` or
    `protocol-cleanup` go through here. Examples: second-opinion checks,
    classification, claim verification.

    Returns a dict with `text`, `model`, `input_tokens`, `output_tokens`.
    """

    name = "llm-dispatch"
    priority = 90
    when_to_use = (
        "to delegate a small task to a cheap model (cleanup, summary, "
        "second opinion). Returns a single text response."
    )
    description = (
        "Fire a one-shot LLM call with the given system + user prompt. "
        "Useful for offloading bookkeeping (summaries, format cleanup, "
        "quick classifications) to a cheaper model so the heavy model "
        "stays focused on the main reasoning loop. The model used is "
        "resolved per-use-case from the project's sub_llm_models config; "
        "defaults to the cheapest tool-capable model in the registry."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "required": ["system", "user"],
        "properties": {
            "system": {
                "type": "string",
                "description": "system prompt for the sub-call",
            },
            "user": {
                "type": "string",
                "description": "user message",
            },
            "max_tokens": {
                "type": "integer",
                "description": (
                    f"output cap, hard-bounded to {MAX_OUTPUT_TOKENS} "
                    "server-side"
                ),
                "default": 512,
            },
        },
    }

    # The use_case name passed to the resolver and metrics sink. Subclasses
    # override this to point at their own bucket (e.g. "summarise-subtask").
    USE_CASE: str = "llm-dispatch"

    def __init__(
        self,
        resolver: ModelResolver,
        metrics_sink: MetricsSink | None = None,
    ):
        self._resolver = resolver
        self._metrics_sink = metrics_sink

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        system = str(arguments.get("system") or "").strip()
        user = str(arguments.get("user") or "").strip()
        if not system or not user:
            return {"error": "both `system` and `user` are required"}

        try:
            max_tokens = int(arguments.get("max_tokens", 512))
        except (TypeError, ValueError):
            return {"error": "max_tokens must be an integer"}
        # Hard clamp — never let the agent spend more than MAX_OUTPUT_TOKENS
        # on a single dispatch.
        if max_tokens < 1:
            max_tokens = 1
        if max_tokens > MAX_OUTPUT_TOKENS:
            max_tokens = MAX_OUTPUT_TOKENS

        return await self._dispatch(system, user, max_tokens)

    async def _dispatch(
        self, system: str, user: str, max_tokens: int,
    ) -> dict[str, Any]:
        """Shared dispatch path for the generic tool and the wrappers.

        Resolves the model, makes the LLM call, records metrics, returns
        a result dict. Subclasses override `run()` to pre-fill prompts
        and post-process the response, then call this method.
        """
        try:
            client, model_id = self._resolver(self.USE_CASE)
        except Exception as e:
            return {"error": f"model resolver failed: {e}"}

        try:
            response = await client.complete(
                system=system,
                messages=[LLMMessage(role="user", content=user)],
                max_tokens=max_tokens,
                model=model_id,
            )
        except Exception as e:
            return {"error": f"LLM call failed: {e}"}

        text = (response.text or "").strip()
        in_tok = int(getattr(response, "input_tokens", 0) or 0)
        out_tok = int(getattr(response, "output_tokens", 0) or 0)

        if self._metrics_sink is not None:
            try:
                self._metrics_sink(self.USE_CASE, in_tok, out_tok)
            except Exception:
                # Metrics are best-effort; never fail a dispatch on them.
                pass

        return {
            "text": text,
            "model": model_id,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
        }


# ── Wrapper: summarise-subtask ────────────────────────────────────────────


class SummariseSubtaskTool(LlmDispatchTool):
    """Wrapper that produces a `TaskCompletionRecord`-shaped summary.

    Used by Track 8 (subtask context hot-swap) to produce the summary
    marker that replaces a closed subtask's verbose tool history. Agents
    can also call it manually to produce a record for any in-flight
    subtask.

    Returns `{summary: str, deliverables: list[str]}` plus the usual
    token counts. Falls back to a heuristic summary if the model
    returns invalid JSON.
    """

    name = "summarise-subtask"
    priority = 95
    when_to_use = (
        "after closing a subtask — produces a JSON summary the runner "
        "stashes in place of the subtask's raw tool history."
    )
    description = (
        "Summarise a closed subtask into a 1-3 sentence outcome statement "
        "plus a bulleted list of deliverables. Output is strict JSON "
        "matching the TaskCompletionRecord shape."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "required": ["subtask_goal", "tool_calls"],
        "properties": {
            "subtask_goal": {
                "type": "string",
                "description": "the closed subtask's goal field",
            },
            "tool_calls": {
                "type": "array",
                "description": (
                    "list of {name, arguments, result_summary} dicts "
                    "describing what the agent did"
                ),
                "items": {"type": "object"},
            },
            "max_tokens": {
                "type": "integer",
                "default": 200,
            },
        },
    }

    USE_CASE = "summarise-subtask"

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        goal = str(arguments.get("subtask_goal") or "").strip()
        tool_calls = arguments.get("tool_calls") or []
        if not goal:
            return {"error": "subtask_goal is required"}
        if not isinstance(tool_calls, list):
            return {"error": "tool_calls must be a list"}

        try:
            max_tokens = int(arguments.get("max_tokens", 200))
        except (TypeError, ValueError):
            max_tokens = 200
        if max_tokens > MAX_OUTPUT_TOKENS:
            max_tokens = MAX_OUTPUT_TOKENS

        # Render the trace as a compact bullet list for the user prompt.
        trace_lines: list[str] = [f"Subtask goal: {goal}", "", "Trace:"]
        for i, call in enumerate(tool_calls, start=1):
            if not isinstance(call, dict):
                continue
            cname = call.get("name", "?")
            cargs = call.get("arguments") or {}
            cres = call.get("result_summary") or call.get("result") or ""
            trace_lines.append(
                f"{i}. {cname}({json.dumps(cargs, default=str)[:120]}) "
                f"→ {str(cres)[:200]}"
            )

        user = "\n".join(trace_lines)
        result = await self._dispatch(SUMMARISE_SUBTASK_SYSTEM, user, max_tokens)
        if "error" in result:
            return result

        # Parse the JSON body. On failure, fall back to a heuristic so the
        # caller (often the runner) always gets a usable summary.
        text = result.get("text", "")
        summary = ""
        deliverables: list[str] = []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                summary = str(parsed.get("summary") or "").strip()
                raw_deliv = parsed.get("deliverables") or []
                if isinstance(raw_deliv, list):
                    deliverables = [str(d).strip() for d in raw_deliv if d]
        except (json.JSONDecodeError, TypeError):
            pass

        if not summary:
            # Best-effort fallback so the marker isn't empty.
            summary = (text.split("\n", 1)[0] or goal)[:160]

        return {
            "summary": summary,
            "deliverables": deliverables,
            "model": result.get("model", ""),
            "input_tokens": result.get("input_tokens", 0),
            "output_tokens": result.get("output_tokens", 0),
        }


# ── Wrapper: protocol-cleanup ─────────────────────────────────────────────


class ProtocolCleanupTool(LlmDispatchTool):
    """Wrapper that converts prose into a parseable protocol block.

    Track 9 (protocol cleaner sidecar) uses this as its execution
    primitive. Agents can call it directly when their last response
    failed protocol parse and they need a clean retry without
    re-reasoning.
    """

    name = "protocol-cleanup"
    priority = 95
    when_to_use = (
        "your last response had no valid protocol block — call to get a "
        "clean [COMMIT]/[VERIFY] block from your raw text."
    )
    description = (
        "Convert prose into a parseable AgentFlow protocol block of the "
        "given kind. Returns a single block (COMMIT, VERIFY, DELTA, "
        "ACTION, or STATUS) ready to append to the agent's response."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "required": ["raw_response", "expected_kind"],
        "properties": {
            "raw_response": {
                "type": "string",
                "description": "the prose to convert",
            },
            "expected_kind": {
                "type": "string",
                "enum": ["COMMIT", "VERIFY", "DELTA", "ACTION", "STATUS"],
            },
            "max_tokens": {
                "type": "integer",
                "default": 200,
            },
        },
    }

    USE_CASE = "protocol-cleanup"

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw = str(arguments.get("raw_response") or "").strip()
        kind = str(arguments.get("expected_kind") or "").strip().upper()
        if not raw:
            return {"error": "raw_response is required"}
        if kind not in {"COMMIT", "VERIFY", "DELTA", "ACTION", "STATUS"}:
            return {"error": f"expected_kind {kind!r} not supported"}

        try:
            max_tokens = int(arguments.get("max_tokens", 200))
        except (TypeError, ValueError):
            max_tokens = 200
        if max_tokens > MAX_OUTPUT_TOKENS:
            max_tokens = MAX_OUTPUT_TOKENS

        user = (
            f"expected_kind: {kind}\n\n"
            f"raw_response:\n{raw}"
        )

        result = await self._dispatch(PROTOCOL_CLEANUP_SYSTEM, user, max_tokens)
        if "error" in result:
            return result

        return {
            "protocol_block": result.get("text", ""),
            "model": result.get("model", ""),
            "input_tokens": result.get("input_tokens", 0),
            "output_tokens": result.get("output_tokens", 0),
        }
