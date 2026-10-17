"""What Jack sees when the owner talks to him from anywhere in Studio (docs/JACK_UNSTUCK_PLAN.md).

`OwnerContext` is what the page sends: where the owner is and which run, card, item,
requirement or component they are looking at (ids only). `digest()` turns it into a short,
host-built text for Jack's prompt: the page, the project's state and, for a run, a board digest
(cards by column and what each waits on, who is running, the last result per role, open questions,
what the overseer did). Agent-written text in it is wrapped as untrusted.

`ReadTools` are what Jack may call during a turn when the digest is not enough: one card, a
worker's brief, a turn's console, a result, the run report, the project's health. Every tool
returns a capped excerpt, and none returns a secret.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.harness.brief import wrap_untrusted
from core.signals import Signal, SignalType
from core.workspace import Task, Workspace

DIGEST_CAP = 7000
TOOL_CAP = 6000
MAX_CARDS = 30
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_PAGES = ("work", "plan", "spec", "design", "code", "sessions", "team", "record", "settings",
          "run", "projects", "other")


def _clean(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text if text and _ID.match(text) else None


@dataclass(frozen=True)
class OwnerContext:
    """Where the owner is in Studio (ids only; resolved on the host)."""

    page: str = "other"
    run_id: str | None = None
    card: str | None = None
    item: str | None = None
    requirement: str | None = None
    component: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "OwnerContext | None":  # Any: JSON
        """From the request body; None when nothing usable was sent."""
        if not isinstance(raw, Mapping):
            return None
        page = str(raw.get("page") or "other")
        ctx = cls(page=page if page in _PAGES else "other", run_id=_clean(raw.get("run_id")),
                  card=_clean(raw.get("card")), item=_clean(raw.get("item")),
                  requirement=_clean(raw.get("requirement")), component=_clean(raw.get("component")))
        return ctx

    def label(self) -> str:
        """The chip text: "run_x · T3", "Design · C2"."""
        bits = [b for b in (self.run_id, self.card, self.item, self.requirement, self.component) if b]
        head = "Run" if self.page == "run" else self.page.capitalize()
        return " · ".join([head, *bits]) if bits else head

    def to_dict(self) -> dict[str, str]:
        """The non-empty fields (stored on the owner's message)."""
        return {k: v for k, v in (("page", self.page), ("run_id", self.run_id),
                                  ("card", self.card), ("item", self.item),
                                  ("requirement", self.requirement),
                                  ("component", self.component)) if v}


# ── a run, live or saved ──────────────────────────────────────────────────────
@dataclass
class RunState:
    """A run's board and cards, from the live workspace or its saved files."""

    run_id: str
    signals: list[Signal]
    tasks: dict[str, Task]
    live: bool
    run_root: Path | None = None
    names: dict[str, str] = field(default_factory=dict)


def load_run(data_root: Path, run_id: str, workspace: Workspace | None = None) -> RunState | None:
    """The run's state (None when it does not exist)."""
    from core.run_loader import _rebuild_tasks_from_board, load_persisted_board, load_persisted_tasks
    run_root = Path(data_root) / "runs" / run_id
    if workspace is not None:
        state = RunState(run_id, list(workspace.board), dict(workspace.tasks), True,
                         run_root if run_root.is_dir() else None)
    elif run_root.is_dir():
        signals = load_persisted_board(run_root)
        tasks, _ = load_persisted_tasks(run_root)
        if not tasks:
            tasks, _ = _rebuild_tasks_from_board(signals)
        state = RunState(run_id, signals, tasks, False, run_root)
    else:
        return None
    for sig in reversed(state.signals):
        if sig.type is SignalType.TEAM:
            try:
                members = json.loads(sig.detail or "{}").get("members") or []
                state.names = {str(m["role"]): str(m["name"]) for m in members if "role" in m}
            except (ValueError, KeyError, TypeError, AttributeError):
                pass
            break
    return state


def _who(state: RunState, role: str) -> str:
    name = state.names.get(role)
    return f"{name} ({role})" if name and name != role else role


def _board_cards(state: RunState) -> list[Task]:
    return sorted((t for t in state.tasks.values() if t.story_id in ("plan", "board")),
                  key=lambda t: (int(t.task_id[1:]) if t.task_id[1:].isdigit() else 0))


def _waits(card: Task, tasks: Mapping[str, Task]) -> str:
    """What a card waits on, in words."""
    open_deps = [d for d in card.depends_on if d in tasks and tasks[d].column != "done"]
    if card.column == "blocked" or card.blocked_on:
        return "blocked on " + (", ".join(card.blocked_on) or "another role")
    if open_deps:
        return "after " + ", ".join(open_deps)
    return ""


def run_digest(state: RunState) -> str:
    """Cards by column, roles, last results, the open question and the overseer, in short lines."""
    lines = [f"Run {state.run_id}: " + ("live" if state.live else "not running")]
    cards = _board_cards(state)
    by_col: dict[str, list[Task]] = {}
    for c in cards:
        by_col.setdefault(c.column, []).append(c)
    for col in ("needs_attention", "blocked", "in_progress", "review", "todo", "done"):
        group = by_col.get(col, [])
        if not group:
            continue
        shown = group[:MAX_CARDS]
        lines.append(f"{col} ({len(group)}): " + "; ".join(
            f"{c.task_id} {c.title[:60]} [{_who(state, c.assigned_to)}]"
            + (f" {w}" if (w := _waits(c, state.tasks)) else "")
            + (f" round {c.review_round}" if c.review_round else "")
            for c in shown) + (" …" if len(group) > len(shown) else ""))
    running: set[str] = set()
    last_result: dict[str, str] = {}
    question: Signal | None = None
    overseer: list[str] = []
    stopped = ""
    for sig in state.signals:
        role = sig.from_agent or ""
        if sig.type is SignalType.STARTED:
            running.add(role)
        elif sig.type in (SignalType.COMPLETE, SignalType.FAILED, SignalType.AGENT_RESULT):
            running.discard(role)
            if sig.type is not SignalType.COMPLETE:
                last_result[role] = sig.summary
        elif sig.type is SignalType.HUMAN_NEEDED:
            question = sig
        elif sig.type is SignalType.HUMAN_RESPONDED:
            question = None
        elif sig.type in (SignalType.OVERSEER_WAKE, SignalType.OVERSEER_INTERVENTION):
            overseer.append(sig.summary)
        elif sig.type in (SignalType.STOP_REQUESTED, SignalType.RUN_STOPPED):
            stopped = sig.summary
    if state.live and running:
        lines.append("Running now: " + ", ".join(_who(state, r) for r in sorted(running)))
    if question is not None:
        lines.append(f"Waiting on the owner: {_who(state, question.from_agent or 'system')} asks: "
                     + wrap_untrusted("question", question.summary[:400]))
    if stopped:
        lines.append(f"Stopped: {stopped[:200]}")
    if overseer:
        lines.append("Overseer, latest: " + wrap_untrusted("overseer", " | ".join(overseer[-3:])[:600]))
    if last_result:
        lines.append("Last result per role:\n" + wrap_untrusted("results", "\n".join(
            f"- {_who(state, r)}: {s[:200]}" for r, s in sorted(last_result.items()))))
    return "\n".join(lines)


def card_text(state: RunState, card_id: str) -> str | None:
    """One card in full: acceptance, thread tail, waits."""
    card = state.tasks.get(card_id)
    if card is None:
        return None
    lines = [f"{card.task_id} {card.title} — {card.column}, owner {_who(state, card.assigned_to)}"
             + (f", {w}" if (w := _waits(card, state.tasks)) else "")
             + (f", review round {card.review_round}" if card.review_round else "")]
    if card.backlog_id or card.serves:
        lines.append(f"Item {card.backlog_id or '-'}; serves {', '.join(card.serves) or '-'}")
    lines += [f"- [ ] {a}" for a in card.acceptance_criteria]
    thread = [f"{e.get('kind', 'note')} {e.get('author', '')}: {str(e.get('text', ''))[:300]}"
              for e in card.thread[-8:] if isinstance(e, dict)]
    if thread:
        lines.append("Thread (latest):\n" + wrap_untrusted("card thread", "\n".join(thread)))
    return "\n".join(lines)


# ── the project ─────────────────────────────────────────────────────────────
def project_digest(data_root: Path, project_id: str, doc: Mapping[str, Any]) -> str:  # Any: JSON
    """The project's state in a few lines: spec, draft, backlog, reviews, team problems, stack."""
    from core.backlog import Backlog
    from core.plan_drafts import PlanDrafts
    from core.project_repo import ProjectRepo
    from core.spec import Spec
    from core.team_changes import team_problems
    drafts = PlanDrafts(data_root, project_id)
    spec = Spec(drafts.db.read_spec_doc())
    lines = [f"Spec v{spec.version}: {sum(r.status == 'active' for r in spec.requirements)} "
             f"requirements, {sum(d.status == 'accepted' for d in spec.decisions)} decisions, "
             f"{sum(r.status == 'active' for r in spec.rules)} rules. Stack: "
             + ("; ".join(spec.stack.lines()).replace("- ", "") if not spec.stack.empty
                else "not set")]
    draft = drafts.current()
    if draft is not None:
        lines.append(f"Open draft {draft.id}: {len(draft.changes)} change(s) waiting for the owner")
    counts: dict[str, int] = {}
    for item in Backlog(data_root, project_id).items():
        counts[item.status] = counts.get(item.status, 0) + 1
    if counts:
        lines.append("Backlog: " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
    try:
        pending = [r.run_id for r in ProjectRepo(data_root, project_id).runs()
                   if r.state == "pending"]
    except Exception:  # noqa: BLE001 — no project repo yet
        pending = []
    if pending:
        lines.append("Sessions waiting for the owner's review: " + ", ".join(pending))
    problems = team_problems([a for a in doc.get("agents") or [] if isinstance(a, dict)])
    if problems:
        lines.append("Team problems (a session cannot start): " + "; ".join(problems))
    return "\n".join(lines)


def focus_text(data_root: Path, project_id: str, ctx: OwnerContext,
               run: RunState | None) -> str:
    """The thing the owner is looking at, resolved."""
    parts: list[str] = []
    if ctx.card and run is not None:
        text = card_text(run, ctx.card)
        if text:
            parts.append("The card the owner is looking at:\n" + text)
    if ctx.item:
        from core.backlog import Backlog, BacklogError
        try:
            item = Backlog(data_root, project_id).get(ctx.item)
            parts.append("The backlog item the owner is looking at:\n" + item.brief_text()
                         + (f"\n    - for: {item.assignee}" if item.assignee else ""))
        except BacklogError:
            pass
    if ctx.requirement:
        from core.plan_drafts import PlanDrafts
        from core.spec import Spec
        spec = Spec(PlanDrafts(data_root, project_id).db.read_spec_doc())
        req = next((r for r in spec.requirements if r.id == ctx.requirement.split(".")[0]), None)
        if req is not None:
            parts.append(f"The requirement the owner is looking at: {req.id} {req.text}\n"
                         + "\n".join(f"- {c.id}: {c.text}" for c in req.checks))
    if ctx.component:
        from core.design import Design
        comp = Design.load(data_root, project_id).component(ctx.component)
        if comp is not None:
            parts.append(f"The design component the owner is looking at: {comp.id} {comp.name} "
                         f"({', '.join(comp.paths)})")
    return "\n\n".join(parts)


def digest(data_root: Path, project_id: str, doc: Mapping[str, Any], ctx: OwnerContext,  # Any: JSON
           workspace_for: Callable[[str], Workspace | None] | None = None) -> str:
    """Jack's "Where the owner is" block: page, focus, project state, run digest; capped."""
    run = None
    if ctx.run_id:
        ws = workspace_for(ctx.run_id) if workspace_for else None
        run = load_run(data_root, ctx.run_id, ws)
    parts = [f"The owner is on: {ctx.label()} (they wrote from the Jack dock on that page)."]
    focus = focus_text(data_root, project_id, ctx, run)
    if focus:
        parts.append(focus)
    parts.append("Project state:\n" + project_digest(data_root, project_id, doc))
    if run is not None:
        parts.append(run_digest(run))
    text = "\n\n".join(parts)
    return text if len(text) <= DIGEST_CAP else text[:DIGEST_CAP] + "\n… (cut; use the read tools)"


# ── read tools ───────────────────────────────────────────────────────────────
READ_TOOLS: list[dict[str, Any]] = [  # Any: JSON schema
    {"name": "read_card", "description": "One card of a run in full: acceptance, thread, what it waits on.",
     "input_schema": {"type": "object", "properties": {"run_id": {"type": "string"},
                                                       "card": {"type": "string"}},
                      "required": ["run_id", "card"]}},
    {"name": "read_brief", "description": "What a worker was told on a turn (the latest if n is omitted).",
     "input_schema": {"type": "object", "properties": {"run_id": {"type": "string"},
                                                       "role": {"type": "string"},
                                                       "n": {"type": "integer"}},
                      "required": ["run_id", "role"]}},
    {"name": "read_console", "description": "The end of a run's host console and a role's server log: refusals, errors, why something did not start.",
     "input_schema": {"type": "object", "properties": {"run_id": {"type": "string"},
                                                       "role": {"type": "string"}},
                      "required": ["run_id"]}},
    {"name": "read_results", "description": "A role's structured results in a run, latest first.",
     "input_schema": {"type": "object", "properties": {"run_id": {"type": "string"},
                                                       "role": {"type": "string"}},
                      "required": ["run_id", "role"]}},
    {"name": "read_run_report", "description": "A finished run's retrospective: outcome, friction, cost, what was left open.",
     "input_schema": {"type": "object", "properties": {"run_id": {"type": "string"}},
                      "required": ["run_id"]}},
    {"name": "read_health", "description": "The project's health: team problems, missing test runners, models without a provider, the worker queue.",
     "input_schema": {"type": "object", "properties": {}}},
]
READ_TOOL_NAMES = frozenset(t["name"] for t in READ_TOOLS)


class ReadTools:
    """Jack's read tools for one project. Every answer is capped and agent text is wrapped."""

    def __init__(self, data_root: Path, project_id: str, doc: Mapping[str, Any],  # Any: JSON
                 workspace_for: Callable[[str], Workspace | None] | None = None,
                 health: Callable[[], str] | None = None) -> None:
        self.data_root = Path(data_root)
        self.project_id = project_id
        self.doc = doc
        self.workspace_for = workspace_for
        self.health = health

    def _run(self, run_id: object) -> RunState | None:
        rid = _clean(run_id)
        if rid is None:
            return None
        # Only this project's runs.
        state = load_run(self.data_root, rid, self.workspace_for(rid) if self.workspace_for else None)
        meta = (self.data_root / "runs" / rid / "meta.json")
        if meta.is_file():
            try:
                if json.loads(meta.read_text(encoding="utf-8")).get("project_id") not in (None, self.project_id):
                    return None
            except ValueError:
                pass
        return state

    def call(self, name: str, args: Mapping[str, Any]) -> str:  # Any: tool input JSON
        """Run one tool; errors come back as text for the model."""
        try:
            out = self._call(name, args)
        except Exception as e:  # noqa: BLE001 — a read failure is an answer, never a crash
            out = f"(could not read: {e})"
        return out if len(out) <= TOOL_CAP else out[:TOOL_CAP] + "\n… (cut)"

    def _call(self, name: str, args: Mapping[str, Any]) -> str:  # Any: tool input JSON
        if name == "read_health":
            return self.health() if self.health else project_digest(self.data_root, self.project_id, self.doc)
        run = self._run(args.get("run_id"))
        if run is None:
            return "No such run in this project."
        if name == "read_card":
            return card_text(run, str(args.get("card") or "")) or "No such card."
        role = _clean(args.get("role"))
        if name == "read_brief":
            from core.harness.brief_log import BriefLog
            if run.run_root is None or role is None:
                return "No briefs recorded."
            log = BriefLog(run.run_root)
            entries = log.entries(role)
            if not entries:
                return f"No briefs recorded for {role}."
            n = args.get("n")
            pick = next((e for e in entries if e.n == n), entries[-1]) if isinstance(n, int) else entries[-1]
            body = log.read(role, pick.n) or ""
            return wrap_untrusted(f"brief {role} turn {pick.n}", body[-TOOL_CAP:])
        if name == "read_console":
            from core.harness.run_console import read_console
            if run.run_root is None:
                return "No console for this run."
            con = read_console(run.run_root, tail=60, role_tail=30)
            lines = [json.dumps(r)[:240] for r in con["lines"][-60:]]
            for r in con["roles"]:
                if role is None or r["role"] == role:
                    lines.append(f"[{r['role']} server log]")
                    lines += [str(x)[:240] for x in r["lines"]]
            return wrap_untrusted("console", "\n".join(lines))
        if name == "read_results":
            rows = [s for s in reversed(run.signals) if s.type is SignalType.AGENT_RESULT
                    and s.from_agent == role][:4]
            if not rows:
                return f"No results from {role}."
            return wrap_untrusted("results", "\n\n".join(
                f"{s.summary}\n{(s.detail or '')[:1200]}" for s in rows))
        if name == "read_run_report":
            if run.run_root is None:
                return "No report."
            path = run.run_root / "retrospective.json"
            return (wrap_untrusted("retrospective", path.read_text(encoding="utf-8")[:TOOL_CAP])
                    if path.is_file() else "No report yet (the run has not finished).")
        return f"Unknown tool {name}."


__all__ = ["OwnerContext", "READ_TOOLS", "READ_TOOL_NAMES", "ReadTools", "RunState", "card_text",
           "digest", "load_run", "project_digest", "run_digest"]
