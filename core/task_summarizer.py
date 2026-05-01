"""Task completion record synthesis (M5.5).

Fires once per `closes: task:<id>` parse. Produces a ``TaskCompletionRecord``
stored on the Task and later re-injected into the desk block (C3).

Design decisions:
- D1: runner-driven, not agent-driven (agent never sees the compaction tool)
- D2: per-task trigger, not threshold-based
- D3: TaskCompletionRecord schema
- D4: FAST tier with structured output
- D5: fixed prompt, max_tokens=200, JSON-strict with one retry
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.llm import LLMClient, LLMMessage

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a task-summarization assistant. Given the recent agent activity that
closed a task, output a structured JSON record.

Rules:
- summary: ONE to THREE sentences, past tense, declarative. What was done. No
  marketing language, no apology, no preamble. Subject-verb-object.
- deliverables: BULLETS of concrete artifacts. File paths preferred. Verifiable
  ("Added X", "Modified Y", "Wrote N tests"). 1–6 items.
- references: file paths and/or commit refs touched. Plain strings.
- closed_by_commit: the git SHA you were told closed this task. Echo verbatim.

Output JSON ONLY. No prose. No code fences. The schema is:
{"summary": "...", "deliverables": ["...", ...], "references": ["...", ...], "closed_by_commit": "..."}\
"""

_DEGRADED_SUMMARY = "(summary unavailable)"


def _render_messages(messages: list[Any], max_turns: int = 20, max_chars: int = 800) -> str:
    """Render the last ``max_turns`` messages from the agent's conversation.

    Tool-use blocks summarise to ``[tool_use name] {args}``.
    Tool-result blocks summarise to ``[tool_result] {first 200 chars}``.
    Text is truncated to ``max_chars``.
    """
    from core.llm import LLMMessage  # local import to avoid circular

    tail = messages[-max_turns:]
    lines: list[str] = []
    for msg in tail:
        role = msg.role if hasattr(msg, "role") else "?"
        content = msg.content if hasattr(msg, "content") else ""

        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    parts.append(block.get("text", "")[:max_chars])
                elif btype == "tool_use":
                    args = json.dumps(block.get("input", {}))[:120]
                    parts.append(f"[tool_use {block.get('name', '')}] {args}")
                elif btype == "tool_result":
                    raw = str(block.get("content", ""))[:200]
                    parts.append(f"[tool_result] {raw}")
            lines.append(f"{role}: " + " | ".join(parts))
        else:
            lines.append(f"{role}: {str(content)[:max_chars]}")
    return "\n".join(lines)


@dataclass(slots=True)
class TaskCompletionRecord:
    task_id: str
    summary: str
    deliverables: list[str]
    references: list[str]
    closed_by_commit: str
    synthesized_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "summary": self.summary,
            "deliverables": self.deliverables,
            "references": self.references,
            "closed_by_commit": self.closed_by_commit,
            "synthesized_at": self.synthesized_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskCompletionRecord":
        return cls(
            task_id=d.get("task_id", ""),
            summary=d.get("summary", _DEGRADED_SUMMARY),
            deliverables=d.get("deliverables", []),
            references=d.get("references", []),
            closed_by_commit=d.get("closed_by_commit", ""),
            synthesized_at=d.get("synthesized_at", ""),
        )

    def is_degraded(self) -> bool:
        return self.summary == _DEGRADED_SUMMARY


def _degraded(task_id: str, task_goal: str, commit_sha: str, task_refs: list[str]) -> TaskCompletionRecord:
    return TaskCompletionRecord(
        task_id=task_id,
        summary=_DEGRADED_SUMMARY,
        deliverables=[f"Goal: {task_goal[:120]}"] if task_goal else [],
        references=task_refs[:],
        closed_by_commit=commit_sha,
    )


class TaskSummarizer:
    """Synthesizes a TaskCompletionRecord for each closed task.

    Instantiated by the runner factory (FAST tier), passed into AgentRunner
    via the ``task_summarizer`` kwarg, mirroring the MemoryWriter pattern.
    """

    def __init__(self, llm: "LLMClient", log_prefix: str = "") -> None:
        self._llm = llm
        self._prefix = log_prefix

    async def summarize(
        self,
        *,
        task_id: str,
        task_goal: str,
        task_acceptance_criteria: list[str],
        task_references: list[str],
        messages: list[Any],
        commit_sha: str,
        commit_subject: str,
        diff_stat: str = "",
    ) -> TaskCompletionRecord:
        """Call the LLM once to synthesize a completion record.

        On parse failure: retries once with the error appended.
        On second failure: returns a degraded record and logs WARNING.
        Never raises — degraded record keeps the run going.
        """
        from core.llm import LLMMessage

        criteria_text = "\n".join(f"  - {c}" for c in task_acceptance_criteria) if task_acceptance_criteria else "(none)"
        rendered = _render_messages(messages)

        user_content = (
            f"Task: {task_goal}\n"
            f"Acceptance criteria:\n{criteria_text}\n"
            f"Closed by commit: {commit_sha} — {commit_subject}\n"
            f"Diff stats: {diff_stat or '(not available)'}\n\n"
            f"Recent agent messages (most recent 20 turns, oldest first):\n{rendered}"
        )

        llm_messages = [LLMMessage(role="user", content=user_content)]

        last_response_text = ""
        for attempt in range(2):
            try:
                response = await self._llm.complete(
                    system=_SYSTEM_PROMPT,
                    messages=llm_messages,
                    max_tokens=200,
                )
                last_response_text = response.text or ""
                text = last_response_text.strip()
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    raise ValueError("root is not a dict")
                return TaskCompletionRecord(
                    task_id=task_id,
                    summary=str(parsed.get("summary", _DEGRADED_SUMMARY)),
                    deliverables=list(parsed.get("deliverables", [])),
                    references=list(parsed.get("references", [])),
                    closed_by_commit=str(parsed.get("closed_by_commit", commit_sha)),
                )
            except Exception as exc:
                if attempt == 0:
                    # Retry with the parse error appended.
                    err_msg = f"Parse error: {exc}. Output only valid JSON matching the schema."
                    llm_messages = llm_messages + [
                        LLMMessage(role="assistant", content=last_response_text),
                        LLMMessage(role="user", content=err_msg),
                    ]
                else:
                    logger.warning(
                        "%sTaskSummarizer: failed after retry for task %s — using degraded record. Error: %s",
                        self._prefix,
                        task_id,
                        exc,
                    )

        return _degraded(task_id, task_goal, commit_sha, task_references)
