"""The "office state" a quip is written from (plan §11.5): who is where, and what just
happened between people. Built from facts already on the board; no model call.

Quips stay a side channel (DESIGN §23): this text goes only into the quip generator's prompt,
never into an agent's context. It holds numbers and events, never opinions, so a quip can
tease about something true ("third first-try pass in a row") but has nothing to invent.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from core.signals import Signal, SignalType
from core.workspace import Workspace

MAX_INTERACTIONS = 3
BANNED_OPENERS = ("rolling up my sleeves", "cracking my knuckles", "grabbing coffee",
                  "i'm ready to", "time to dive in", "diving into")


def _detail(sig: Signal) -> dict[str, Any]:  # Any: signal detail JSON
    try:
        data = json.loads(sig.detail or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def standing(workspace: Workspace, names: Mapping[str, str], roles: Sequence[str]) -> list[str]:
    """One line per teammate: cards done, first-try passes, sent back, spend so far."""
    cards = list(workspace.tasks.values())
    sent_back: dict[str, int] = {}
    spend: dict[str, float] = {}
    for sig in workspace.board:
        if sig.type is SignalType.REJECTED:
            for author in _detail(sig).get("authors") or []:
                sent_back[str(author)] = sent_back.get(str(author), 0) + 1
        elif sig.type is SignalType.AGENT_RESULT and sig.from_agent:
            cost = _detail(sig).get("cost")
            if isinstance(cost, (int, float)):
                spend[sig.from_agent] = float(cost)      # a running total per role
    top = max(spend.values(), default=0.0)
    lines = []
    for role in roles:
        mine = [c for c in cards if c.assigned_to == role]
        done = [c for c in mine if getattr(c, "column", "") == "done"]
        first_try = sum(1 for c in done if not getattr(c, "review_round", 0))
        bits = [f"{len(done)} of {len(mine)} card(s) done" if mine else "no cards"]
        if done:
            bits.append(f"{first_try} passed review first try")
        if sent_back.get(role):
            bits.append(f"sent back {sent_back[role]} time(s)")
        if role in spend:
            bits.append(f"${spend[role]:.2f} spent" + (" (the most so far)"
                                                      if spend[role] == top and top > 0 else ""))
        lines.append(f"- {names.get(role, role)} ({role}): {', '.join(bits)}")
    return lines


def interactions(workspace: Workspace, names: Mapping[str, str], speaker: str) -> list[str]:
    """The last few things that happened between the speaker and someone else."""
    who = lambda r: names.get(r, r)  # noqa: E731 — local shorthand
    out: list[str] = []
    for sig in reversed(workspace.board):
        d = _detail(sig)
        line = None
        if sig.type is SignalType.REJECTED:
            authors = [str(a) for a in d.get("authors") or []]
            if sig.from_agent == speaker or speaker in authors:
                line = (f"{who(sig.from_agent or '?')} sent {d.get('task_id', 'a card')} back to "
                        f"{', '.join(who(a) for a in authors)} (round {d.get('round', '?')})")
        elif sig.type is SignalType.OVERSEER_INTERVENTION and (d.get("role") == speaker
                                                               or d.get("author_role") == speaker):
            line = f"the overseer stepped in on {who(speaker)}'s work ({d.get('type', 'note')})"
        elif sig.type in (SignalType.RUN_LIMITS_CHANGED, SignalType.BUDGET_WARNING,
                          SignalType.TIME_LIMIT_REACHED):
            line = sig.summary
        if line and line not in out:
            out.append(line)
        if len(out) >= MAX_INTERACTIONS:
            break
    return list(reversed(out))


def history(personas_root: Path | None, hired: Mapping[str, str], names: Mapping[str, str],
            roles: Sequence[str]) -> list[str]:
    """At most one line per teammate from their persona's record across projects."""
    if personas_root is None:
        return []
    out = []
    for role in roles:
        persona = hired.get(role)
        path = personas_root / persona / "reputation.json" if persona else None
        try:
            rep = json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else {}
        except (OSError, ValueError):
            continue
        prod = rep.get("production") or {}
        total = int(prod.get("total_runs") or 0)
        if total:
            out.append(f"- {names.get(role, role)}: {total} earlier session(s), "
                       f"{int(prod.get('completed_runs') or 0)} completed")
    return out


def office_state(workspace: Workspace, names: Mapping[str, str], speaker: str,
                 roles: Sequence[str], personas_root: Path | None = None) -> str:
    """The block the quip generator gets (empty sections are left out)."""
    parts = []
    team = [r for r in roles if r != speaker]
    lines = standing(workspace, names, [speaker] + team)
    if lines:
        parts.append("Where everyone stands this session:\n" + "\n".join(lines))
    recent = interactions(workspace, names, speaker)
    if recent:
        parts.append("What just happened around you:\n" + "\n".join(f"- {r}" for r in recent))
    past = history(personas_root, workspace.hired_personas, names, team)
    if past:
        parts.append("Your teammates' track records:\n" + "\n".join(past))
    return "\n\n".join(parts)
