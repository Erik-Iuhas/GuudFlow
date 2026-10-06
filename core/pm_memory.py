"""Jack's memory: episodes written from facts, no LLM call (project-flow plan §10.3).

Two events become episodes in Jack's `episodes.jsonl` (rule 16: only specifics, never prose
generated from nothing):

  record_session_outcome   a session was accepted or rejected
  record_draft_outcome     a plan draft was approved or discarded

Both update Jack's reputation (kept in `production.by_task_type["pm"]`, which the memory
writer's refresh leaves alone). Patterns are not made here: `distill()` runs the existing
memory writer's distillation (`_distill_patterns` and `_reconcile`) for Jack only, and the
API calls it as a best-effort background step after a decision.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from agents.persona import Episode, Persona
from core.json_store import read_json
from core.pm_persona import load_pm
from core.pm_prompts import Seats
from core.start_modes import normalise_mode

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
GOAL_CAP = 300
NOTE_CAP = 1000
OPS_SHOWN = 12

# First match wins, so the narrower kinds come first ("a web game" is a game).
_TYPES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in (
        ("game", r"\bgames?\b|\bplatformer\b|\bpuzzle\b"),
        ("cli", r"\bcli\b|command[- ]line|\bterminal\b"),
        ("library", r"\blibrary\b|\bsdk\b|\bpackage\b|\bmodule\b"),
        ("data", r"\bdata\b|\bdataset\b|\betl\b|\bpipeline\b|\bcsv\b|\banalytics\b"),
        ("docs", r"\bdocs?\b|documentation|\bguide\b|\btutorial\b"),
        ("api", r"\bapi\b|\bendpoint|\brest\b|\bbackend\b|\bservice\b|\bserver\b"),
        ("web", r"\bweb\b|\bwebsite\b|\bsite\b|\bbrowser\b|\bfrontend\b|\bpage\b|\bapp\b"),
    ))


def classify_project(goal: str) -> str:
    """web, api, cli, game, library, data, docs or other, from the goal's keywords."""
    for name, pattern in _TYPES:
        if pattern.search(goal or ""):
            return name
    return "other"


# ── reading what the episodes cite ──────────────────────────────────────────
def _project_doc(data_root: Path, project_id: str) -> Mapping[str, Any]:  # Any: project JSON
    projects = read_json(Path(data_root) / "projects.json", {})
    doc = projects.get(project_id) if isinstance(projects, dict) else None
    return doc if isinstance(doc, dict) else {}


def _roles(doc: Mapping[str, Any]) -> list[str]:  # Any: project JSON
    return [str(a["role"]) for a in doc.get("agents") or []
            if isinstance(a, Mapping) and a.get("role")]


def _context(data_root: Path, project_id: str) -> dict[str, Any]:  # Any: JSON facts
    """Goal, project type, start mode, seats and team roles of the project."""
    doc = _project_doc(data_root, project_id)
    goal = str(doc.get("goal") or "").strip()
    seats = Seats.from_project(doc)
    return {"goal": goal, "project_type": classify_project(goal),
            "start_mode": normalise_mode(doc.get("start_mode")),
            "lead": "human" if seats.lead else "empty", "reviewer": seats.reviewer,
            "roles": _roles(doc)}


def _spec_counts(data_root: Path, project_id: str) -> dict[str, int]:
    from core.spec import Spec
    from core.work_db import WorkDB
    spec = Spec(WorkDB(Path(data_root) / "projects" / project_id).read_spec_doc())
    return {"requirements": sum(1 for r in spec.requirements if r.status == "active"),
            "decisions": len(spec.decisions),
            "rules": sum(1 for r in spec.rules if r.status == "active")}


def _session_facts(data_root: Path, project_id: str, run_id: str) -> dict[str, Any]:  # Any: JSON
    """Items, review rounds, rule issues, checks and cost, read from the run's board."""
    from core import project_runs, spec_adherence
    facts: dict[str, Any] = {"items": [], "review_rounds": 0, "rule_unmet": 0,
                             "rule_fixed": 0, "checks_met": [], "checks_unmet": [],
                             "cost": None}
    try:
        facts["items"] = [i.id for i in spec_adherence.run_items(data_root, project_id, run_id)]
    except Exception:  # noqa: BLE001 — facts are best effort
        log.warning("pm memory: items of %s unreadable", run_id, exc_info=True)
    board = spec_adherence.read_board(Path(data_root) / "runs" / run_id)
    if board:
        found = spec_adherence.reviews(board)
        verdicts = spec_adherence.check_verdicts(found)
        summary = spec_adherence.adherence(board)
        facts.update(
            review_rounds=len(found), rule_unmet=summary["unmet"], rule_fixed=summary["fixed"],
            checks_met=sorted(k for k, met in verdicts.items() if met),
            checks_unmet=sorted(k for k, met in verdicts.items() if not met))
        _, costs = project_runs.card_results(_signals(board))
        if sum(costs.values()) > 0:
            facts["cost"] = round(sum(costs.values()), 4)
    return facts


def _signals(board: list[dict[str, Any]]) -> list[Any]:  # Any: Signal objects
    """Signal objects for `card_results` (which reads attributes, not rows)."""
    from core.signals import Signal, SignalType
    out: list[Any] = []
    for row in board:
        try:
            out.append(Signal(type=SignalType[str(row["type"])], summary="",
                              from_agent=str(row.get("from_agent") or ""),
                              detail=str(row.get("detail") or "")))
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ── writing ────────────────────────────────────────────────────────────────
def _short(items: list[str], limit: int = OPS_SHOWN) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" (+{len(items) - limit} more)" if len(items) > limit else "")


def _append(data_root: Path, build: Callable[[Persona], Episode],
            bump: Callable[[dict[str, int]], None]) -> Episode:
    """Add one episode to Jack and update his reputation counters."""
    from agents.builtin.memory_writer import MemoryWriter
    with _LOCK:
        jack = load_pm(data_root)
        episode = build(jack)
        jack.episodes.append(episode)
        counters = jack.reputation.production.by_task_type.setdefault("pm", {})
        bump(counters)
        prod = jack.reputation.production
        prod.completed_runs = counters.get("sessions_accepted", 0)
        prod.failed_runs = counters.get("sessions_rejected", 0)
        prod.total_runs = prod.completed_runs + prod.failed_runs
        jack.reputation.total_episodes = len(jack.episodes)
        MemoryWriter()._save(jack, episode.run_id)
    return episode


def record_session_outcome(data_root: Path | str, project_id: str, run_id: str,
                           accepted: bool, note: str = "") -> Episode | None:
    """A session was accepted or rejected. Never raises; returns the episode or None."""
    data_root = Path(data_root)
    try:
        ctx = _context(data_root, project_id)
        counts = _spec_counts(data_root, project_id)
        facts = _session_facts(data_root, project_id, run_id)
        outcome = "accepted" if accepted else "rejected"
        note = (note or "").strip()[:NOTE_CAP]
        roles: list[str] = ctx["roles"]
        mode = ctx["start_mode"]

        def build(_: Persona) -> Episode:
            checks = facts["checks_met"] + facts["checks_unmet"]
            worked, failed = [], []
            if accepted:
                worked.append(f"session {run_id} was accepted")
            else:
                failed.append(f"session {run_id} was rejected")
            if facts["checks_met"]:
                worked.append(f"checks met: {_short(facts['checks_met'])}")
            if facts["checks_unmet"]:
                failed.append(f"checks unmet: {_short(facts['checks_unmet'])}")
            if facts["rule_fixed"]:
                worked.append(f"{facts['rule_fixed']} rule issue(s) fixed in review")
            if facts["rule_unmet"]:
                failed.append(f"{facts['rule_unmet']} rule issue(s) still unmet")
            return Episode(
                episode_id=f"ep_{uuid4().hex[:10]}", run_id=run_id,
                iteration=facts["review_rounds"], project_type=ctx["project_type"],
                task_context=ctx["goal"][:GOAL_CAP],
                situation=(f"{ctx['project_type']} project in {mode} mode, lead {ctx['lead']}, "
                           f"reviewer {ctx['reviewer']}, team {_short(roles) or 'none'}; spec "
                           f"{counts['requirements']} requirements, {counts['decisions']} "
                           f"decisions, {counts['rules']} rules; session {run_id} took "
                           f"{len(facts['items'])} item(s): {_short(facts['items']) or 'none'}"),
                what_i_tried=(f"{facts['review_rounds']} review round(s); {len(checks)} "
                              f"check(s) judged; cost "
                              f"{'unknown' if facts['cost'] is None else '$%.2f' % facts['cost']}"),
                what_failed="; ".join(failed), what_worked="; ".join(worked),
                feedback_verbatim=note, iterations_to_resolve=facts["review_rounds"],
                tags=_tags(ctx, "session", outcome, roles),
                timestamp=datetime.now(timezone.utc).isoformat())

        def bump(c: dict[str, int]) -> None:
            key = "sessions_accepted" if accepted else "sessions_rejected"
            c[key] = c.get(key, 0) + 1

        return _append(data_root, build, bump)
    except Exception:  # noqa: BLE001 — memory must never fail a decision
        log.exception("recording session %s in the project manager's memory failed", run_id)
        return None


def record_draft_outcome(data_root: Path | str, project_id: str, draft: Any,  # Any: Draft
                         approved: bool) -> Episode | None:
    """A plan draft was approved or discarded. Never raises; returns the episode or None.

    `draft` is the open draft as the human saw it (its changes carry `accepted` and `by`).
    Drafts with no change proposed by the project manager teach nothing and are skipped.
    """
    data_root = Path(data_root)
    try:
        proposed = [c for c in draft.changes if c.by == "pm"]
        if not proposed:
            return None
        ctx = _context(data_root, project_id)
        accepted = [c for c in proposed if approved and c.accepted is True]
        rejected = [c for c in proposed if not (approved and c.accepted is True)]
        edited = any(c.by == "human" for c in draft.changes)
        outcome = "approved" if approved else "discarded"

        def label(c: Any) -> str:  # Any: DraftChange
            return f"{c.cid}:{c.op}"

        def build(_: Persona) -> Episode:
            tags = _tags(ctx, "draft", outcome, ctx["roles"])
            if edited:
                tags.append("human_edited")
            return Episode(
                episode_id=f"ep_{uuid4().hex[:10]}", run_id=str(draft.id),
                project_type=ctx["project_type"], task_context=ctx["goal"][:GOAL_CAP],
                situation=(f"{ctx['project_type']} project in {ctx['start_mode']} mode; draft "
                           f"{draft.id} on spec version {draft.base_version} proposed "
                           f"{len(proposed)} change(s): {_short([label(c) for c in proposed])}"),
                what_i_tried=f"proposed {len(proposed)} change(s)",
                what_failed=(f"{len(rejected)} change(s) not accepted: "
                             f"{_short([label(c) for c in rejected])}" if rejected else ""),
                what_worked=(f"{len(accepted)} change(s) accepted: "
                             f"{_short([label(c) for c in accepted])}" if accepted else ""),
                feedback_verbatim=("the human edited the draft before deciding" if edited
                                   else ""),
                tags=tags, timestamp=datetime.now(timezone.utc).isoformat())

        def bump(c: dict[str, int]) -> None:
            c["drafts_approved" if approved else "drafts_discarded"] = c.get(
                "drafts_approved" if approved else "drafts_discarded", 0) + 1
            if approved and not edited and not rejected:
                c["drafts_approved_untouched"] = c.get("drafts_approved_untouched", 0) + 1

        return _append(data_root, build, bump)
    except Exception:  # noqa: BLE001 — memory must never fail a plan action
        log.exception("recording draft %s in the project manager's memory failed",
                      getattr(draft, "id", "?"))
        return None


def _tags(ctx: Mapping[str, Any], kind: str, outcome: str,  # Any: context facts
          roles: list[str]) -> list[str]:
    tags = [kind, ctx["start_mode"], ctx["project_type"], outcome]
    if roles:
        tags.append("team:" + "+".join(sorted(roles)))
    return list(dict.fromkeys(tags))


# ── patterns ───────────────────────────────────────────────────────────────
async def distill(data_root: Path | str, make_llm: Callable[[], Any]) -> int:  # Any: LLMClient
    """Distil Jack's patterns with the existing memory writer; returns how many were added.

    `make_llm` is called only when Jack has a cluster of 3+ episodes sharing a theme, so no
    client is built otherwise. This is an LLM call: callers run it in the background and
    never let it block or fail a decision.
    """
    from agents.builtin.memory_writer import MemoryWriter, _build_episode_clusters
    jack = load_pm(data_root)
    if not _build_episode_clusters(jack):
        return 0
    writer = MemoryWriter(llm=make_llm())
    now = datetime.now(timezone.utc)
    before = len(jack.patterns)
    await writer._distill_patterns(jack, now)
    await writer._reconcile(jack, now)
    with _LOCK:
        # Re-read so episodes recorded while the model was thinking are not overwritten.
        fresh = load_pm(data_root)
        jack.episodes = fresh.episodes
        jack.reputation = fresh.reputation
        jack.reputation.active_patterns = sum(
            1 for p in jack.patterns if p.status in ("active", "emerging"))
        writer._save(jack, "")
    return len(jack.patterns) - before


def summary(data_root: Path | str) -> dict[str, Any]:  # Any: JSON for GET /api/pm
    """Jack's identity, memory counts and reputation, as `GET /api/pm` returns them."""
    jack = load_pm(data_root)
    rep = jack.reputation
    pm = rep.production.by_task_type.get("pm", {})
    return {
        "persona_id": jack.profile.persona_id, "name": jack.profile.name or "Jack",
        "specialty": jack.profile.specialty, "episodes": len(jack.episodes),
        "patterns": len(jack.patterns) + len(jack.anti_patterns),
        "reputation": {
            "sessions_accepted": int(pm.get("sessions_accepted", 0)),
            "sessions_rejected": int(pm.get("sessions_rejected", 0)),
            "drafts_approved": int(pm.get("drafts_approved", 0)),
            "drafts_approved_untouched": int(pm.get("drafts_approved_untouched", 0)),
            "drafts_discarded": int(pm.get("drafts_discarded", 0)),
        },
    }

