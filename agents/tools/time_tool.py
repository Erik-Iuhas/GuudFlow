"""Time tools — `time:now`, `time:diff`.

Pure / read-only. UTC-only by design — no timezone math, no locale
parsing. Anything fancier (natural-language dates, named timezones)
goes through `code:exec` or a future MCP.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.tools.base import BaseTool


def _err(msg: str) -> dict[str, Any]:
    return {"error": msg}


def _parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 timestamp. Accepts 'Z' as +00:00."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ── time:now ───────────────────────────────────────────────────────────────


class TimeNow(BaseTool):
    name = "time-now"
    priority = 90
    when_to_use = "get the current UTC timestamp"
    description = "Current UTC timestamp. Returns ISO string and epoch (seconds + ms)."
    input_schema = {"type": "object", "properties": {}}

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            "ok": True,
            "iso": now.isoformat(),
            "epoch_seconds": int(now.timestamp()),
            "epoch_ms": int(now.timestamp() * 1000),
        }


# ── time:diff ──────────────────────────────────────────────────────────────


class TimeDiff(BaseTool):
    """Duration between two ISO-8601 timestamps (b - a).

    Returns `total_seconds` and a breakdown into days / hours / minutes /
    seconds. Negative durations are supported (b before a).
    """

    name = "time-diff"
    priority = 90
    when_to_use = "compute duration between two ISO timestamps"
    description = "Duration between two ISO timestamps (b - a). Returns total_seconds and a days/hours/minutes/seconds breakdown."
    input_schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string", "description": "ISO timestamp"},
            "b": {"type": "string", "description": "ISO timestamp"},
        },
        "required": ["a", "b"],
    }

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        a = arguments.get("a")
        b = arguments.get("b")
        if not isinstance(a, str) or not isinstance(b, str):
            return _err("`a` and `b` must be ISO-8601 strings")
        try:
            ta = _parse_iso(a)
            tb = _parse_iso(b)
        except ValueError as e:
            return _err(f"could not parse timestamp: {e}")

        # Treat naive datetimes as UTC.
        if ta.tzinfo is None:
            ta = ta.replace(tzinfo=timezone.utc)
        if tb.tzinfo is None:
            tb = tb.replace(tzinfo=timezone.utc)

        delta = tb - ta
        total = delta.total_seconds()
        sign = -1 if total < 0 else 1
        abs_total = abs(total)
        days, rem = divmod(int(abs_total), 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        return {
            "ok": True,
            "total_seconds": total,
            "days": sign * days,
            "hours": sign * hours,
            "minutes": sign * minutes,
            "seconds": sign * seconds,
            "negative": sign < 0,
        }
