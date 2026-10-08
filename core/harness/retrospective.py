"""End-of-run retrospective, playbook update and carry-forward brief (plan §11.4, O20).

After an opencode run (and after `memory_writer`), `finalize_run()`:

  1. builds a deterministic `Retrospective` from what the run left on disk
     (board.jsonl, tasks.jsonl, llm_costs.jsonl): cost per role, cards and
     their review rounds, interventions and whether they worked, recurring
     review issues, stall minutes;
  2. turns it into a conservative `PlaybookDiff` (every entry backed by this
     run as evidence; nothing applies to a team by itself, constraint #24) and
     stores it with the run's metrics in the project playbook;
  3. writes a carry-forward (`carry_forward.json` + `.md`): what is done and
     verified with commit refs, the open cards with their threads, files of
     note and decisions. A continuation run starts from it, so seeded files are
     attributed to "previous run, commit X" instead of appearing authorless
     (the run_632863526b continuation spiral).

Everything here is file-in, file-out and never raises into the run.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from core.playbook import Playbook, PlaybookDiff, PlaybookEntry

log = logging.getLogger(__name__)

CARRY_FORWARD_JSON = "carry_forward.json"
CARRY_FORWARD_MD = "carry_forward.md"
RETRO_JSON = "retrospective.json"
STALL_GAP_MINUTES = 10.0


def _jsonl(path: Path) -> list[dict[str, Any]]:  # Any: rows are free-form JSON
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _detail(row: dict[str, Any]) -> dict[str, Any]:  # Any: signal detail JSON
    try:
        value = json.loads(row.get("detail") or "{}")
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _ts(row: dict[str, Any]) -> float:  # Any: signal row
    try:
        return datetime.fromisoformat(str(row.get("timestamp"))).timestamp()
    except ValueError:
        return 0.0


@dataclass
class CardSummary:
    """One card as the retrospective sees it."""

    task_id: str
    title: str
    owner: str
    column: str
    kind: str
    review_round: int
    acceptance: list[str]
    commits: list[str] = field(default_factory=list)
    thread: list[dict[str, Any]] = field(default_factory=list)  # Any: ThreadEntry dicts


@dataclass
class InterventionOutcome:
    """An overseer intervention and whether its target recovered afterwards."""

    type: str
    target: str
    detail: dict[str, Any]  # Any: the intervention payload
    worked: bool


@dataclass
class CardCost:
    """Spend on one card (or one non-card phase) from the gateway's labelled rows (W3)."""

    card: str                      # task id, or "planning" / "notebooks" / "other"
    roles: list[str]
    calls: int = 0
    cost_usd: float = 0.0
    fresh_turns: int = 0           # turns that started a fresh session
    kept_turns: int = 0            # turns that continued a session
    carried_tokens: int = 0        # most context carried into one of its turns
    cold_calls: int = 0
    cold_cost_usd: float = 0.0


def cost_by_card(rows: list[dict[str, Any]]) -> list[CardCost]:  # Any: cost-log rows
    """Group cost-log rows by card, most expensive first. Unlabelled (older) logs give []."""
    if not any("session" in r for r in rows):
        return []
    out: dict[str, CardCost] = {}
    turn_seen: set[tuple[str, int]] = set()
    for r in rows:
        card = r.get("card") or ("notebooks" if r.get("session") == "notebook"
                                 else "planning" if r.get("role") == "planner" else "other")
        entry = out.setdefault(card, CardCost(card=card, roles=[]))
        role = str(r.get("role") or "?")
        if role not in entry.roles:
            entry.roles.append(role)
        cost = float(r.get("cost_usd") or 0.0)
        entry.calls += 1
        entry.cost_usd += cost
        carried = int(r.get("carried_tokens") or 0)
        entry.carried_tokens = max(entry.carried_tokens, carried)
        session = str(r.get("session") or "")
        # A turn is counted once: every call in it carries the same turn number.
        turn = (role, int(r.get("turn") or 0))
        if session in ("fresh", "kept") and turn not in turn_seen:
            turn_seen.add(turn)
            if session == "fresh":
                entry.fresh_turns += 1
            else:
                entry.kept_turns += 1
        if r.get("cold"):
            entry.cold_calls += 1
            entry.cold_cost_usd += cost
    for entry in out.values():
        entry.cost_usd = round(entry.cost_usd, 6)
        entry.cold_cost_usd = round(entry.cold_cost_usd, 6)
    return sorted(out.values(), key=lambda c: -c.cost_usd)


@dataclass
class Retrospective:
    """What happened in one run, computed without a model."""

    run_id: str
    status: str
    cost_by_role: dict[str, float]
    cards: list[CardSummary]
    interventions: list[InterventionOutcome]
    recurring_issues: list[tuple[str, str]]          # (card id, item/file)
    review_rounds_used: dict[str, int]               # "owner->reviewer" → max rounds
    stall_minutes: float
    roles_completed: list[str]
    card_costs: list[CardCost] = field(default_factory=list)
    # Per role: how often it blocked, asked a person, or had a command refused, and on which
    # model (flow hardening §8), so a team choice can see which models stall.
    friction: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def total_cost(self) -> float:
        """Total gateway spend."""
        return round(sum(self.cost_by_role.values()), 6)

    def metrics(self) -> dict[str, float]:
        """Per-run metrics stored in the playbook (§11.4 "measuring it")."""
        completed = max(1, len(self.roles_completed))
        done_ids = {c.task_id for c in self.cards if c.column == "done"}
        return {
            "stall_minutes": round(self.stall_minutes, 1),
            "interventions": float(len(self.interventions)),
            "interventions_worked": float(sum(i.worked for i in self.interventions)),
            "review_rounds_used": float(sum(c.review_round for c in self.cards)),
            "cards_done": float(sum(c.column == "done" for c in self.cards)),
            "cards_total": float(len(self.cards)),
            "cost_usd": self.total_cost,
            "cost_per_completed_role": round(self.total_cost / completed, 6),
            "cost_per_card_done": round(
                sum(c.cost_usd for c in self.card_costs if c.card in done_ids)
                / max(1, len(done_ids)), 6),
            "cold_cost_usd": round(sum(c.cold_cost_usd for c in self.card_costs), 6),
            "blocked_results": float(sum(f.get("blocked", 0) for f in self.friction.values())),
            "human_questions": float(sum(f.get("asked_human", 0)
                                         for f in self.friction.values())),
            "refused_commands": float(sum(f.get("refused", 0) for f in self.friction.values())),
        }

    def to_json(self) -> dict[str, Any]:  # Any: JSON document
        """Serialisable form (retrospective.json)."""
        data = asdict(self)
        data["total_cost"] = self.total_cost
        data["metrics"] = self.metrics()
        return data


def build_retrospective(run_root: Path, run_id: str) -> Retrospective:
    """Read the run directory and summarise it."""
    board = _jsonl(run_root / "board.jsonl")
    latest: dict[str, dict[str, Any]] = {}
    for row in _jsonl(run_root / "tasks.jsonl"):
        if row.get("story_id") in ("plan", "board") and row.get("task_id"):
            latest[str(row["task_id"])] = row
    cards = [CardSummary(task_id=tid, title=str(r.get("title") or r.get("goal") or ""),
                         owner=str(r.get("assigned_to") or ""), column=str(r.get("column") or ""),
                         kind=str(r.get("kind") or "code"),
                         review_round=int(r.get("review_round") or 0),
                         acceptance=[str(a) for a in r.get("acceptance_criteria") or []],
                         thread=[e for e in r.get("thread") or [] if isinstance(e, dict)])
             for tid, r in latest.items()]
    by_id = {c.task_id: c for c in cards}
    for card in cards:
        card.commits = [str(e["data"]["merge"][1]) for e in card.thread
                        if isinstance(e.get("data"), dict)
                        and isinstance(e["data"].get("merge"), list)
                        and len(e["data"]["merge"]) == 2]

    cost: dict[str, float] = defaultdict(float)
    cost_rows = _jsonl(run_root / "llm_costs.jsonl")
    for row in cost_rows:
        cost[str(row.get("role") or "?")] += float(row.get("cost_usd") or row.get("cost") or 0.0)

    status = "complete"
    completed: list[str] = []
    progress_marks: list[float] = []
    for row in board:
        kind = row.get("type")
        if kind == "COMPLETE" and row.get("from_agent"):
            completed.append(str(row["from_agent"]))
        if kind in ("COMPLETE", "GIT_MERGE") or (kind == "TASK_MOVED"
                                                 and _detail(row).get("to") == "done"):
            progress_marks.append(_ts(row))
        if kind == "HUMAN_NEEDED":
            status = "needed_human"
    if any(c.column != "done" for c in cards):
        status = "incomplete" if status == "complete" else status
    times = sorted(t for t in [_ts(r) for r in board[:1]] + progress_marks if t)
    stall = sum(max(0.0, (b - a) / 60 - STALL_GAP_MINUTES) for a, b in zip(times, times[1:]))

    interventions = []
    for row in board:
        if row.get("type") != "OVERSEER_INTERVENTION":
            continue
        payload = _detail(row)
        target = str(payload.get("task_id") or payload.get("role") or payload.get("author_role")
                     or "")
        if target in by_id:
            worked = by_id[target].column == "done"
        else:
            owned = [c for c in cards if c.owner == target]
            worked = bool(owned) and all(c.column == "done" for c in owned) \
                or (not owned and target in completed)
        interventions.append(InterventionOutcome(type=str(payload.get("type") or "?"),
                                                 target=target, detail=payload, worked=worked))

    recurring: list[tuple[str, str]] = []
    for card in cards:
        seen: dict[str, set[int]] = defaultdict(set)
        round_no = 0
        for entry in card.thread:
            data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
            if entry.get("kind") == "note" and "merge" in data:
                round_no += 1
            elif entry.get("kind") == "review_issue":
                subject = str(data.get("item") or data.get("file") or "").strip()
                if subject:
                    seen[subject].add(round_no)
        recurring += [(card.task_id, s) for s, rounds in seen.items() if len(rounds) >= 2]

    rounds_used: dict[str, int] = {}
    for row in board:
        if row.get("type") == "REJECTED":
            payload = _detail(row)
            for author in payload.get("authors") or []:
                key = f"{author}->{row.get('from_agent')}"
                rounds_used[key] = max(rounds_used.get(key, 0), int(payload.get("round") or 0))

    return Retrospective(run_id=run_id, status=status, cost_by_role=dict(cost), cards=cards,
                         interventions=interventions, recurring_issues=recurring,
                         review_rounds_used=rounds_used, stall_minutes=stall,
                         roles_completed=sorted(set(completed)),
                         card_costs=cost_by_card(cost_rows),
                         friction=_friction(board, cost_rows))


_REFUSED = ("prevents you from using", "permission denied", "rejected permission",
            "not allowed")


def _friction(board: list[dict[str, Any]], cost_rows: list[dict[str, Any]]  # Any: JSON rows
              ) -> dict[str, dict[str, Any]]:  # Any: counts and the model name
    """Per role: blocked results, questions to a person, refused commands, and its model."""
    out: dict[str, dict[str, Any]] = {}

    def bump(role: str, key: str) -> None:
        row = out.setdefault(role, {"blocked": 0, "asked_human": 0, "refused": 0})
        row[key] += 1

    for row in board:
        role = str(row.get("from_agent") or "")
        if not role or role in ("runner", "git", "overseer", "human"):
            continue
        kind = row.get("type")
        if kind == "BLOCKED":
            bump(role, "blocked")
        elif kind == "HUMAN_NEEDED" and "cycle" not in str(row.get("summary", "")):
            bump(role, "asked_human")
        elif kind in ("TOOL_CALL", "THINKING"):
            text = f"{row.get('summary', '')} {row.get('detail', '')}".casefold()
            if any(p in text for p in _REFUSED):
                bump(role, "refused")
    for row in cost_rows:
        role, model = str(row.get("role") or ""), row.get("model")
        if role in out and model:
            out[role]["model"] = str(model)
    return out


def playbook_diff(retro: Retrospective, playbook: Playbook) -> PlaybookDiff:
    """Evidence-backed playbook changes from one run (conservative, §11.4).

    - a review issue that recurred on a card → `known_pitfalls`
    - a rebrief that unstuck its target → `brief_addenda` for that role
    - an edge that needed every round it had → `review_rounds` (+1)
    - a run where every card finished → `task_templates` (the card titles)
    Entries that already exist are reinforced instead of duplicated.
    """
    proposals: list[tuple[str, str, Any]] = []  # Any: playbook values are JSON
    for card_id, subject in retro.recurring_issues:
        card = next(c for c in retro.cards if c.task_id == card_id)
        proposals.append(("known_pitfalls", card.owner,
                          f"Review kept failing on: {subject} ({card.title})"))
    for iv in retro.interventions:
        if iv.type == "rebrief" and iv.worked and iv.detail.get("addendum"):
            proposals.append(("brief_addenda", str(iv.detail.get("role") or iv.target),
                              str(iv.detail["addendum"])))
        if iv.type == "extend_review" and iv.worked:
            edge = f"{iv.detail.get('author_role')}->{iv.detail.get('reviewer_role')}"
            used = retro.review_rounds_used.get(edge, 0)
            proposals.append(("review_rounds", edge, max(used, 1) + 1))
    if retro.cards and all(c.column == "done" for c in retro.cards):
        proposals.append(("task_templates", "", [c.title for c in retro.cards]))

    from core.playbook import make_entry_id

    diff = PlaybookDiff()
    existing = {e.id for e in playbook.entries(include_archived=False)}
    for section, key, value in proposals:
        entry_id = make_entry_id(section, key, value)
        if entry_id in existing:
            if entry_id not in diff.reinforced:
                diff.reinforced.append(entry_id)
        elif not any(e.id == entry_id for e in diff.added):
            diff.added.append(PlaybookEntry.create(section, key, value, retro.run_id))
    return diff


def _git_files(repo: Path) -> list[str]:
    if not (repo / ".git").exists():
        return []
    proc = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "ls-files"], cwd=repo,
                          capture_output=True, text=True)
    return [f for f in proc.stdout.splitlines() if f] if proc.returncode == 0 else []


def _head(repo: Path) -> str:
    proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo,
                          capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def carry_forward(retro: Retrospective, run_root: Path) -> dict[str, Any]:  # Any: JSON doc
    """The carry-forward document for the next run of this project."""
    repo = run_root / "workspace" / "working"
    head = _head(repo)
    done = [{"task_id": c.task_id, "title": c.title, "owner": c.owner,
             "commit": (c.commits[-1][:10] if c.commits else head)}
            for c in retro.cards if c.column == "done"]
    open_cards = [{"task_id": c.task_id, "title": c.title, "owner": c.owner,
                   "column": c.column, "kind": c.kind, "acceptance": c.acceptance,
                   "review_round": c.review_round, "thread": c.thread[-12:]}
                  for c in retro.cards if c.column != "done"]
    decisions = [e.get("text", "") for c in retro.cards for e in c.thread
                 if e.get("kind") == "decision"]
    decisions_md = repo / "docs" / "decisions.md"
    files = [f for f in _git_files(repo) if not f.startswith(".")][:80]

    lines = [f"Previous run: `{retro.run_id}` ({retro.status}), main at commit `{head}`.", ""]
    if done:
        lines.append("Done and verified (do not redo these; build on them):")
        lines += [f"- {d['task_id']} {d['title']} — {d['owner']}, previous run, commit "
                  f"`{d['commit']}`" for d in done]
        lines.append("")
    if open_cards:
        lines.append("Still open (carried onto this run's board with their threads):")
        for c in open_cards:
            issues = [e.get("text", "") for e in c["thread"] if e.get("kind") == "review_issue"]
            lines.append(f"- {c['task_id']} {c['title']} — {c['owner']}, was {c['column']}, "
                         f"round {c['review_round']}")
            lines += [f"    - open issue: {i}" for i in issues[-3:]]
        lines.append("")
    if files:
        lines.append(f"Files already in the repo (from the previous run, commit `{head}`): "
                     + ", ".join(files[:40]) + (" …" if len(files) > 40 else ""))
        lines.append("")
    if decisions or decisions_md.is_file():
        lines.append("Decisions made: " + ("; ".join(decisions[-6:]) if decisions else "")
                     + (" (see docs/decisions.md)" if decisions_md.is_file() else ""))
    return {"run_id": retro.run_id, "status": retro.status, "head": head, "done": done,
            "open_cards": open_cards, "files": files, "decisions": decisions,
            "text": "\n".join(lines).strip()}


def finalize_run(run_root: Path, run_id: str, project_id: str | None,
                 data_root: Path | None) -> Retrospective | None:
    """Write retrospective + carry-forward and update the project playbook. Never raises."""
    try:
        retro = build_retrospective(run_root, run_id)
        (run_root / RETRO_JSON).write_text(json.dumps(retro.to_json(), indent=2))
        cf = carry_forward(retro, run_root)
        (run_root / CARRY_FORWARD_JSON).write_text(json.dumps(cf, indent=2))
        (run_root / CARRY_FORWARD_MD).write_text(cf["text"] + "\n")
        if project_id and data_root is not None:
            playbook = Playbook.load(data_root, project_id)
            diff = playbook_diff(retro, playbook)
            diff.apply(playbook, run_id)
            playbook.record_run_metrics(run_id, retro.metrics())
            playbook.save()
        return retro
    except Exception:  # the run already finished; never fail it here
        log.exception("retrospective for %s failed", run_id)
        return None


def load_prior_carry_forward(run_root: Path) -> list[dict[str, Any]]:  # Any: JSON docs
    """Carry-forward docs of the runs this run continues (inputs/from_run_<id>/)."""
    inputs = run_root / "workspace" / "inputs"
    docs: list[dict[str, Any]] = []
    if not inputs.is_dir():
        return docs
    for entry in sorted(inputs.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("from_run_"):
            continue
        prior_id = entry.name[len("from_run_"):]
        for candidate in (entry / CARRY_FORWARD_JSON, run_root.parent / prior_id / CARRY_FORWARD_JSON):
            if candidate.is_file():
                try:
                    docs.append(json.loads(candidate.read_text()))
                except ValueError:
                    log.warning("unreadable carry-forward for %s", prior_id)
                break
    return docs
