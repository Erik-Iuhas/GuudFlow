"""Text rendering for CLI output.

Plain ASCII tables, lightweight ANSI colors gated on TTY detection,
and helpers for the recurring shapes (signal rows, agent rosters,
run summaries). Kept dependency-free so the CLI stays portable.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence


# ── ANSI helpers ────────────────────────────────────────────────────────────


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("GUUDFLOW_CLI_NO_COLOR"):
        return False
    return sys.stdout.isatty()


_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "grey": "\033[90m",
}


def color(text: str, *styles: str) -> str:
    if not _color_enabled() or not styles:
        return text
    prefix = "".join(_ANSI[s] for s in styles if s in _ANSI)
    return f"{prefix}{text}{_ANSI['reset']}"


def _strip_ansi(s: str) -> str:
    """Visible width — used for column padding so colored text aligns."""
    out: list[str] = []
    i = 0
    while i < len(s):
        if s[i] == "\033" and i + 1 < len(s) and s[i + 1] == "[":
            j = s.find("m", i + 2)
            if j < 0:
                break
            i = j + 1
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def _vis_len(s: str) -> int:
    return len(_strip_ansi(s))


# ── Table rendering ─────────────────────────────────────────────────────────


def render_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    max_widths: Sequence[int | None] | None = None,
) -> str:
    """Two-space-padded table. No box drawing — minimum noise.

    `max_widths` is per-column truncation; None means no cap.
    """
    rows_list = [[("" if c is None else str(c)) for c in r] for r in rows]
    if max_widths:
        for r in rows_list:
            for i, cap in enumerate(max_widths):
                if cap is not None and i < len(r) and _vis_len(r[i]) > cap:
                    r[i] = _truncate(r[i], cap)

    widths = [_vis_len(h) for h in headers]
    for r in rows_list:
        for i, cell in enumerate(r):
            if i >= len(widths):
                widths.append(_vis_len(cell))
            else:
                widths[i] = max(widths[i], _vis_len(cell))

    out: list[str] = []
    header_line = "  ".join(
        _pad(color(h, "bold"), widths[i]) for i, h in enumerate(headers)
    )
    out.append(header_line)
    out.append(color("  ".join("-" * w for w in widths), "grey"))
    for r in rows_list:
        out.append("  ".join(_pad(c, widths[i]) for i, c in enumerate(r)))
    return "\n".join(out)


def _pad(s: str, width: int) -> str:
    pad = width - _vis_len(s)
    if pad <= 0:
        return s
    return s + (" " * pad)


def _truncate(s: str, width: int) -> str:
    if width <= 1 or _vis_len(s) <= width:
        return s
    # Strip ANSI for truncation, then re-emit plain.
    plain = _strip_ansi(s)
    return plain[: width - 1] + "…"


# ── Domain renderers ────────────────────────────────────────────────────────


def fmt_relative_time(ts: Any) -> str:
    """Accept ISO string, epoch seconds, or None — return a short
    relative form: '4s', '12m', '3h', '2d'."""
    if ts is None or ts == "":
        return "-"
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError, OSError):
        return str(ts)
    delta = datetime.now(tz=timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 0:
        return "now"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


_STATUS_STYLE = {
    "running": ("yellow",),
    "complete": ("green",),
    "failed": ("red",),
    "stale": ("red",),
    "stopped": ("magenta",),
    "max_iterations": ("magenta",),
    "stuck": ("red",),
    "queued": ("cyan",),
    "pending": ("cyan",),
}


def fmt_status(status: str | None) -> str:
    if not status:
        return color("-", "dim")
    styles = _STATUS_STYLE.get(status.lower(), ("dim",))
    return color(status, *styles)


_SIGNAL_STYLE = {
    "STARTED": ("cyan",),
    "THINKING": ("dim",),
    "COMPLETE": ("green",),
    "FAILED": ("red",),
    "BLOCKED": ("red",),
    "REJECTED": ("red",),
    "APPROVED": ("green",),
    "ESCALATION": ("magenta",),
    "HUMAN_NEEDED": ("yellow", "bold"),
    "APPROVAL_NEEDED": ("yellow", "bold"),
    "APPROVAL_GRANTED": ("green",),
    "APPROVAL_DENIED": ("red",),
    "USER_COMMENT": ("cyan",),
    "USER_COMMENT_PRIORITY": ("cyan", "bold"),
    "GIT_COMMIT": ("blue",),
    "GIT_MERGE": ("blue",),
    "GIT_CONFLICT": ("red",),
    "PROTOCOL": ("dim",),
    "PROTOCOL_PARSE_FAIL": ("red", "dim"),
    "AGENT_MESSAGE": ("magenta",),
    "TASK_ASSIGNED": ("blue",),
    "TASK_COMPLETED": ("green",),
}


def fmt_signal_type(sig_type: str) -> str:
    styles = _SIGNAL_STYLE.get(sig_type, ())
    return color(sig_type, *styles) if styles else sig_type


def render_signal_row(sig: dict[str, Any]) -> str:
    """One-line signal entry for the board feed."""
    when = fmt_relative_time(sig.get("timestamp"))
    sig_type = fmt_signal_type(sig.get("type", ""))
    actor = sig.get("from_agent") or ("user" if sig.get("from_user") else "")
    actor_str = color(f"[{actor}]", "dim") if actor else ""
    summary = sig.get("summary") or ""
    iter_no = sig.get("iteration")
    iter_str = color(f"i{iter_no}", "grey") if iter_no else ""
    parts = [color(f"{when:>4}", "grey"), iter_str, sig_type, actor_str, summary]
    return " ".join(p for p in parts if p)


def render_dashboard(payload: dict[str, Any]) -> str:
    """Top-level summary mirroring the studio Dashboard view."""
    out: list[str] = []

    active = payload.get("active_runs") or []
    out.append(color(f"Active runs ({len(active)})", "bold"))
    if not active:
        out.append("  " + color("none", "dim"))
    else:
        rows = [
            (
                r.get("run_id", ""),
                r.get("project_id", ""),
                fmt_status(r.get("status", "")),
                f"{r.get('elapsed_s', 0)}s",
                f"{(r.get('task_progress') or {}).get('closed', 0)}/"
                f"{(r.get('task_progress') or {}).get('total', 0)}",
                ",".join(a.get("role", "") for a in r.get("current_agents") or []),
            )
            for r in active
        ]
        out.append(
            render_table(
                ["run", "project", "status", "elapsed", "tasks", "agents"],
                rows,
                max_widths=[14, 18, 10, 8, 8, 40],
            )
        )

    pending = payload.get("pending_approvals") or []
    out.append("")
    out.append(color(f"Pending approvals ({len(pending)})", "bold"))
    if not pending:
        out.append("  " + color("none", "dim"))
    else:
        rows = [
            (a.get("run_id", ""), a.get("approval_id", ""), a.get("kind", ""), a.get("title", ""))
            for a in pending
        ]
        out.append(
            render_table(
                ["run", "approval", "kind", "title"],
                rows,
                max_widths=[14, 16, 14, 60],
            )
        )

    recent = payload.get("recent_completions") or []
    out.append("")
    out.append(color(f"Recent completions ({len(recent)})", "bold"))
    if not recent:
        out.append("  " + color("none", "dim"))
    else:
        rows = [
            (
                r.get("run_id", ""),
                r.get("project_id", ""),
                fmt_status(r.get("outcome", "")),
                f"{r.get('duration_s') or 0}s",
                fmt_relative_time(r.get("ended_at")),
            )
            for r in recent
        ]
        out.append(
            render_table(
                ["run", "project", "outcome", "duration", "ended"],
                rows,
                max_widths=[14, 18, 14, 10, 8],
            )
        )

    top = payload.get("top_personas") or []
    out.append("")
    out.append(color(f"Top personas ({len(top)})", "bold"))
    if not top:
        out.append("  " + color("none", "dim"))
    else:
        rows = [
            (
                p.get("persona_id", ""),
                p.get("name", ""),
                p.get("role", ""),
                str(p.get("recent_runs", 0)),
                f"{int((p.get('win_rate') or 0) * 100)}%",
            )
            for p in top
        ]
        out.append(
            render_table(
                ["persona", "name", "role", "runs", "win"],
                rows,
                max_widths=[18, 20, 18, 6, 6],
            )
        )

    return "\n".join(out)


def render_run_summary(meta: dict[str, Any]) -> str:
    """Header block for a run — used by `runs show` and as the
    sticky header above `runs board` / `runs watch`."""
    rows = [
        ("run_id", meta.get("run_id", "")),
        ("project", meta.get("project_id", "")),
        ("status", fmt_status(meta.get("status", ""))),
        ("started", fmt_relative_time(meta.get("created_at"))),
    ]
    if meta.get("ended_at"):
        rows.append(("ended", fmt_relative_time(meta.get("ended_at"))))
    if meta.get("duration_s") is not None:
        rows.append(("duration", f"{meta['duration_s']}s"))
    if meta.get("goal"):
        rows.append(("goal", _truncate(meta["goal"], 80)))
    width = max(len(r[0]) for r in rows)
    return "\n".join(f"{color(k.ljust(width), 'dim')}  {v}" for k, v in rows)
