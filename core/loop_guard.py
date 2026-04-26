"""LoopGuard — detect "stuck" agents and break them out of unproductive loops.

The "staring at a door that doesn't open" problem: an LLM keeps making the
same broken tool call, or alternates between two failing approaches, or
just spins past a sensible time budget. Iteration caps catch the count;
this catches the *shape* of the stuck-ness and surfaces it back to the LLM
with an explicit nudge.

Three signals it watches for:
  1. **Wall-clock budget exceeded** — the agent has been working past its
     time allotment. Hard abort.
  2. **Same call, same error, repeated** — the LLM has called `X(args)` and
     gotten the same error N times in a row. Inject a strong nudge: "the
     arguments are not the problem; try something else."
  3. **Alternating cycle** — the LLM cycles between two failing calls. Same
     nudge framing: "pick a different approach entirely."

The runner consults LoopGuard after each tool result and either appends a
nudge to the next LLM turn or aborts the agent's attempt. The scheduler
respects existing per-role iteration caps independently.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoopGuardConfig:
    """Per-agent-attempt loop-detection thresholds."""

    max_wall_seconds: float = 600.0      # 10 min default per agent attempt
    max_repeat_identical: int = 3        # same (name, args, error) → nudge
    max_repeat_error_class: int = 5      # same error class across any args → nudge
    history_window: int = 12             # sliding history for cycle detection
    cycle_window: int = 6                # last N calls to scan for alternation
    cycle_distinct_threshold: int = 2    # ≤ this many distinct calls in window = cycle


@dataclass
class _CallRecord:
    name: str
    args_hash: str
    is_error: bool
    error_class: str
    result_summary: str


@dataclass
class LoopGuardVerdict:
    """What the runner should do after a tool result.

    `abort` is hard — return from the agent's tool-use loop with the given
    reason as the final response. `nudge` is soft — append it as a user
    message before the next LLM call. Both can be set; abort dominates.
    """

    abort: bool = False
    nudge: str | None = None
    reason: str = ""


class LoopGuard:
    """One instance per agent attempt. The runner constructs it before
    entering the tool-use loop and calls `record()` after each tool
    result."""

    def __init__(self, config: LoopGuardConfig | None = None):
        self.config = config or LoopGuardConfig()
        self.started_at = time.monotonic()
        self._history: list[_CallRecord] = []

    # ── Time ────────────────────────────────────────────────────────────────

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    def deadline_exceeded(self) -> bool:
        return self.elapsed_seconds > self.config.max_wall_seconds

    # ── Recording ───────────────────────────────────────────────────────────

    def record(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> LoopGuardVerdict:
        """Note one tool call and return the runner's verdict."""
        record = _build_record(tool_name, arguments, result)
        self._history.append(record)
        if len(self._history) > self.config.history_window:
            self._history = self._history[-self.config.history_window:]

        # Wall-clock first — abort beats nudge.
        if self.deadline_exceeded():
            return LoopGuardVerdict(
                abort=True,
                reason=(
                    f"wall-clock deadline of {self.config.max_wall_seconds:.0f}s "
                    f"exceeded; ending attempt"
                ),
            )

        # Same call + same error, N times in a row.
        repeat_n = self.config.max_repeat_identical
        if len(self._history) >= repeat_n:
            tail = self._history[-repeat_n:]
            if all(
                r.name == record.name
                and r.args_hash == record.args_hash
                and r.is_error
                and r.error_class == record.error_class
                for r in tail
            ):
                return LoopGuardVerdict(
                    nudge=(
                        f"You have called `{record.name}` with the same arguments "
                        f"{repeat_n} times in a row and gotten the same error each "
                        f"time ({record.result_summary}). The arguments are not the "
                        "problem on retry. Either change your approach, try a "
                        "different tool, or summarise what you can complete without "
                        "this call. Do NOT call it again with the same arguments."
                    ),
                    reason=f"repeated identical call: {record.name}",
                )

        # Same error class, any args, N times across the window.
        if record.is_error:
            class_count = sum(
                1 for r in self._history
                if r.is_error and r.error_class == record.error_class
            )
            if class_count >= self.config.max_repeat_error_class:
                return LoopGuardVerdict(
                    nudge=(
                        f"You have hit `{record.error_class}` errors "
                        f"{class_count} times in this attempt. Stop trying to "
                        "work around it the same way. Either complete what you "
                        "can without the failing capability, or end the attempt "
                        "and explain what you need from the user."
                    ),
                    reason=f"repeated error class: {record.error_class}",
                )

        # Alternating cycle — last N calls span only K distinct sigs and all error.
        cw = self.config.cycle_window
        if len(self._history) >= cw:
            tail = self._history[-cw:]
            distinct_sigs = {(r.name, r.args_hash) for r in tail}
            if (
                len(distinct_sigs) <= self.config.cycle_distinct_threshold
                and all(r.is_error for r in tail)
            ):
                return LoopGuardVerdict(
                    nudge=(
                        "You are cycling between a small set of failing tool "
                        "calls. Both/all have failed multiple times. Pick a "
                        "different approach entirely — different tool, different "
                        "input, or end the attempt."
                    ),
                    reason="alternating failure cycle",
                )

        return LoopGuardVerdict()


# ── Helpers ────────────────────────────────────────────────────────────────


def _build_record(
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> _CallRecord:
    is_error = bool(result.get("error")) or result.get("ok") is False
    error_class = _classify_error(result) if is_error else ""

    try:
        args_canon = json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args_canon = str(sorted(arguments.items()))
    args_hash = hashlib.sha1(args_canon.encode("utf-8")).hexdigest()[:10]

    summary = str(result.get("error") or "ok")
    if len(summary) > 100:
        summary = summary[:97] + "..."

    return _CallRecord(
        name=tool_name,
        args_hash=args_hash,
        is_error=is_error,
        error_class=error_class,
        result_summary=summary,
    )


def _classify_error(result: dict[str, Any]) -> str:
    """Crude classifier — first colon-separated prefix of the error string,
    or 'error' as a fallback. Stable enough for repetition detection.
    """
    err = str(result.get("error") or "").strip()
    if not err:
        return "error"
    if ":" in err:
        return err.split(":", 1)[0].strip().lower() or "error"
    # Multi-word errors without colon — take first 3 words.
    words = err.split()
    if not words:
        return "error"
    return " ".join(words[:3]).lower()
