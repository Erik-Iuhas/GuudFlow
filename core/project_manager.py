"""The project manager: the plan conversation as a host-side LLM call (plan §3, §4).

`ProjectManager.turn()` stores the human's message, shows the model the goal, the spec, the
open draft, the recent conversation, the backlog and (as untrusted data) the latest session
reports and input files, and asks it to answer with one validating tool, `submit_plan_turn`
(a `PlanTurn`). An invalid answer gets one retry with the error text; after that the valid
changes are kept and the rest reported (`TurnOutcome.status` "partial"), and a turn with nothing
usable is stored as "failed". Every call's cost goes to `pm_costs.jsonl`
(docs/PM_SELF_REPAIR_PLAN.md). In `update` mode the changes go into the project's open draft (nothing
is applied until a human approves it); in `discuss` mode only the reply is kept.

Like `core/groom.py` this is one `LLMClient` call with no files and no shell, so tests drive it
with a scripted client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.harness.brief import wrap_untrusted
from core.harness.results import result_json_schema, validate_result
from core.llm.client import LLMClient, LLMMessage, LLMResponse
from core.plan_drafts import (
    Draft,
    PlanDrafts,
    PlanTurn,
    Problem,
    change_target,
    classify_problem,
    draft_refs,
    resolve_predicted_ids,
    split_turn,
    validate_turn,
)
from core.pm_prompts import Phase, Seats, normalise_mode, persona_layers, pm_system_prompt
from core.spec import Spec

log = logging.getLogger(__name__)

TOOL_NAME = "submit_plan_turn"
# Thinking at the project's effort counts against this too, so it leaves room for both.
MAX_TOKENS = 32000
HISTORY_MESSAGES = 20
PM_COSTS_FILE = "pm_costs.jsonl"
REPAIR_ROUNDS = 2          # repair calls after a turn that left changes out
DEFAULT_TURN_BUDGET_USD = 1.0  # repairs stop once a turn has spent this (pm_turn_budget_usd)
REPAIR_CHANGE_CAP = 600    # characters of a left-out change shown back in a repair call
SESSIONS_SHOWN = 3
BACKLOG_SHOWN = 60
FILES_SHOWN = 200
README_CAP = 6000
EXTRA_CAP = 12000
SESSION_CAP = 6000
SUMMARY_CHANGE_CAP = 240

FIRST_DRAFT_INSTRUCTION = (
    "This is the start of planning. There is no conversation yet. From the goal and the input "
    "files, draft the first spec: the requirements (each with checks), the decisions the build "
    "needs, and 3 to 6 initial work items. Mark what you assumed, and ask at most one blocking "
    "question.")

MEMORY_HEADING = "What you have learned on earlier projects"
_AFTER_SPEC = "Open draft (proposed, not yet part of the spec):"

Mode = Literal["update", "discuss"]


def _insert_memory(context: str, memory: str) -> str:
    """Put the recalled memory below the spec and rules (before the open draft)."""
    if not memory:
        return context
    head, sep, tail = context.partition(_AFTER_SPEC)
    return head + memory + "\n\n" + sep + tail if sep else context + "\n\n" + memory


class ProjectManagerError(RuntimeError):
    """The model could not produce a valid turn (after the one retry)."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("the project manager's answer was invalid: " + "; ".join(problems))
        self.problems = problems


@dataclass
class TurnOutcome:
    """What one turn produced."""

    messages: list[dict[str, Any]]  # Any: JSON rows; the new ones (human's, then the PM's)
    draft: Draft | None             # the open draft after the turn
    reply: str = ""
    questions: list[str] = field(default_factory=list)
    status: TurnStatus = "complete"   # complete | partial (some changes left out) | failed
    problems: list[str] = field(default_factory=list)  # why changes were left out
    notes: list[str] = field(default_factory=list)     # ids GuudFlow read as refs


TurnStatus = Literal["complete", "partial", "failed"]


@dataclass
class AskResult:
    """What the model calls of one ask produced (`turn` is None when nothing was usable)."""

    turn: PlanTurn | None
    responses: list[LLMResponse]
    warnings: dict[int, list[str]]
    notes: list[str]
    left_out: list[Problem]
    failure: list[str] = field(default_factory=list)  # why nothing was usable


def pm_spend(project_dir: Path, now: float | None = None) -> dict[str, float | int]:
    """What the project manager's calls cost (from `pm_costs.jsonl`): all time, the last
    24 hours, and how much of it bought nothing (cut off, no call, invalid, rejected)."""
    path = Path(project_dir) / PM_COSTS_FILE
    now = time.time() if now is None else now
    total = day = wasted = 0.0
    calls = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        cost = float(row.get("cost_usd") or 0.0)
        calls += 1
        total += cost
        if now - float(row.get("at") or 0.0) <= 86_400:
            day += cost
        if row.get("outcome") in ("cut_off", "no_call", "invalid"):
            wasted += cost
    return {"calls": calls, "total_usd": round(total, 4), "last_24h_usd": round(day, 4),
            "wasted_usd": round(wasted, 4)}


def _cached_content(context: str, tail: str) -> list[dict[str, Any]]:  # Any: content blocks
    """The user message as text blocks, with a cache breakpoint after the part that does not
    change within a turn (everything before the open draft), so repair calls and retries
    re-read it from the prompt cache."""
    head, sep, rest = context.partition(_AFTER_SPEC)
    blocks: list[dict[str, Any]] = [{"type": "text", "text": head,  # Any: content block
                                     "cache_control": {"type": "ephemeral"}}]
    body = (sep + rest if sep else "") + tail
    if body:
        blocks.append({"type": "text", "text": body})
    return blocks


# How to fix each kind of left-out change, shown with the problem (self-repair plan J4).
WORKED_EXAMPLES: dict[str, str] = {
    "unknown_requirement": (
        "serve a requirement that exists (R#), or one this draft adds by its ref: "
        "`\"serves\": [\"new:3\"]`. Ids like R14 exist only after approval."),
    "unknown_item": "depend on an open item (B#) or on an item this draft adds by its ref (new:N).",
    "ref_not_in_draft": ("a ref must be defined by a change in this turn or the open draft; add "
                         "that change or use an existing id."),
    "missing_check": ("give the requirement at least one check: `\"checks\": [{\"text\": "
                      "\"Bun test: ...\", \"method\": \"unit\"}]`."),
    "missing_group": ("set `group` on the change to a reason name, and describe that reason in "
                      "`groups` with `summary` and `because`."),
    "pinned_field": "a person set this field; leave it out of the change.",
    "dependency_cycle": "drop one `depends_on` so the items no longer wait on each other.",
    "needs_left_out": "send it again together with the change it needs.",
}


def _repair_text(round_no: int, left_out: list[Problem]) -> str:
    """The request for corrected versions of only the changes that were left out."""
    lines = [f"\n\nRepair round {round_no}. Your other changes are already in the open draft "
             "above; do not send them again. These changes were left out:"]
    for p in left_out:
        lines.append(f"- {p.message}")
        if p.change_json:
            lines.append(f"  it was: {p.change_json}")
        if p.code in WORKED_EXAMPLES:
            lines.append(f"  how to fix: {WORKED_EXAMPLES[p.code]}")
    lines.append(f"Call {TOOL_NAME} with ONLY corrected versions of these changes. Reuse their "
                 "refs; a ref in the open draft may be served or depended on. Describe their "
                 "reason in `groups` unless the open draft already lists it. If one cannot be "
                 "fixed, leave it out and say why in `reply` in one or two sentences.")
    return "\n".join(lines)


def _fix_groups(turn: PlanTurn, problems: list[Problem]) -> PlanTurn:
    """Repair the reasons in place of rejecting them: a reason described twice keeps its first
    description, and a quote that was never said is dropped (the reason stays)."""
    codes = {p.code for p in problems}
    if not codes & {"group_described_twice", "quote_not_said"}:
        return turn
    seen: set[str] = set()
    groups = []
    for g in turn.groups:
        if g.name in seen:
            continue
        seen.add(g.name)
        if "quote_not_said" in codes and g.quote and any(
                p.code == "quote_not_said" and repr(g.name) in p.message for p in problems):
            g = g.model_copy(update={"quote": None})
        groups.append(g)
    return turn.model_copy(update={"groups": groups})


def _strip_discriminators(node: Any) -> Any:  # Any: JSON schema node
    """Drop pydantic's `discriminator` mappings: they point at `$defs` that inlining removed."""
    if isinstance(node, dict):
        return {k: _strip_discriminators(v) for k, v in node.items() if k != "discriminator"}
    if isinstance(node, list):
        return [_strip_discriminators(v) for v in node]
    return node


def turn_tool() -> dict[str, Any]:  # Any: JSON schema
    """The `submit_plan_turn` tool definition (the `PlanTurn` schema, fully inlined)."""
    return {"name": TOOL_NAME,
            "description": ("Submit your answer: a plain-language reply, the proposed changes "
                            "to the spec and backlog, and at most one blocking question."),
            "input_schema": _strip_discriminators(result_json_schema(PlanTurn))}


class ProjectManager:
    """The plan conversation of one project."""

    def __init__(self, llm: LLMClient, data_root: Path, project_id: str,
                 project_doc: Mapping[str, Any] | None,  # Any: project JSON
                 *, model: str | None = None, effort: str | None = None) -> None:
        self.llm = llm
        self.data_root = Path(data_root)
        self.project_id = project_id
        self.doc: Mapping[str, Any] = project_doc or {}
        self.model = model
        # The provider's effort word for every PM call (None: the provider's default).
        self.effort = effort
        self.seats = Seats.from_project(self.doc)
        self.start_mode = normalise_mode(self.doc.get("start_mode"))
        self.drafts = PlanDrafts(self.data_root, project_id)
        self._project_dir = self.data_root / "projects" / project_id
        self._persona: Any = None  # Any: Jack (agents.persona.Persona), loaded per turn

    # ── public ──────────────────────────────────────────────────────────────
    async def turn(self, text: str | None, *, mode: Mode = "update", phase: Phase = "plan",
                   extra_context: str | None = None,
                   instruction: str | None = None) -> TurnOutcome:
        """One exchange with the project manager.

        `text` is the human's message (stored first); `extra_context` is untrusted material
        (wrapped in delimiters); `instruction` is a trusted nudge from GuudFlow itself.

        In `update` mode the valid changes go into the draft at once. Changes left out get up
        to `REPAIR_ROUNDS` repair calls that ask for corrected versions of only those changes,
        with the rest of the turn already in the draft; a problem that comes back unchanged on
        the same change ends the repair (docs/PM_SELF_REPAIR_PLAN.md §3, §6).
        """
        new: list[dict[str, Any]] = []  # Any: JSON rows
        if text is not None and text.strip():
            new.append(await asyncio.to_thread(self.drafts.add_message, "human", text.strip(),
                                               None, mode))
        memory = await self._memory_context(text)

        async def build() -> str:
            ctx = await asyncio.to_thread(self._context, text, instruction, extra_context)
            return _insert_memory(ctx, memory)

        sources = [str(m["text"]) for m in await asyncio.to_thread(self.drafts.messages, 500)
                   if m.get("author") == "human"] + ([extra_context] if extra_context else [])
        asked = await self._ask(phase, await build(), sources)
        turn = asked.turn
        responses = list(asked.responses)
        notes = list(asked.notes)
        draft: Draft | None = await asyncio.to_thread(self.drafts.current)
        if turn is not None and mode == "update" and turn.changes:
            draft = await asyncio.to_thread(self._merge, turn, asked.warnings)
        left_out = list(asked.left_out) if mode == "update" else []
        seen_codes: dict[str, list[str]] = {}
        for p in left_out:
            seen_codes.setdefault(p.code, []).append(p.message)
        repairs: list[str] = []
        dropped: list[Problem] = []
        seen = {(p.code, p.target) for p in left_out}
        budget = self._turn_budget()
        for round_no in range(1, REPAIR_ROUNDS + 1):
            if turn is None or not left_out:
                break
            spent, _ = self._usage(responses)
            if spent is not None and spent >= budget:
                log.warning("pm: turn spent $%.2f of its $%.2f budget; no repair", spent, budget)
                break
            fix = await self._ask(phase, await build(), sources,
                                  repair=_repair_text(round_no, left_out), attempts=1)
            responses += fix.responses
            notes += fix.notes
            if fix.turn is None:
                break
            if fix.turn.reply.strip():
                repairs.append(fix.turn.reply.strip())
            if not fix.turn.changes and not fix.left_out:
                dropped, left_out = left_out, []
                break
            if fix.turn.changes:
                draft = await asyncio.to_thread(self._merge, fix.turn, fix.warnings)
            left_out = list(fix.left_out)
            for p in left_out:
                seen_codes.setdefault(p.code, []).append(p.message)
            keys = {(p.code, p.target) for p in left_out}
            if keys <= seen:
                break
            seen |= keys
        problems = [p.message for p in left_out]
        status: TurnStatus = ("failed" if turn is None
                              else "partial" if problems else "complete")
        cost, tokens = self._usage(responses)
        if turn is None:
            reply = ("I could not finish this turn, and nothing was added to the draft. "
                     "What went wrong:\n" + "\n".join(f"- {p}" for p in asked.failure))
            problems = list(asked.failure)
        else:
            reply = turn.reply.strip()
            if turn.questions:
                reply += "\n\n" + "\n".join(f"Question: {q.strip()}" for q in turn.questions)
            if repairs:
                reply += "\n\nRepairs: " + " ".join(repairs)
            if dropped:
                reply += ("\n\nLeft out on purpose:\n"
                          + "\n".join(f"- {p.message}" for p in dropped))
            if problems:
                reply += ("\n\nLeft out of the draft until fixed:\n"
                          + "\n".join(f"- {p}" for p in problems))
        new.append(await asyncio.to_thread(
            self.drafts.add_message, "pm", reply, draft.id if draft else None, mode,
            cost, tokens, status, problems))
        if seen_codes:
            from core.pm_memory import record_turn_problems
            await asyncio.to_thread(record_turn_problems, self.data_root, self.project_id,
                                    f"turn-{new[-1].get('id', '')}", seen_codes, problems)
        return TurnOutcome(messages=new, draft=draft, reply=turn.reply if turn else reply,
                           questions=list(turn.questions) if turn else [],
                           status=status, problems=problems, notes=notes)

    async def start(self) -> TurnOutcome:
        """The first draft: no human text, the PM drafts spec, decisions and items."""
        return await self.turn(None, mode="update", phase="plan",
                               instruction=FIRST_DRAFT_INSTRUCTION)

    async def after_session(self, run_id: str, report: str) -> TurnOutcome:
        """Turn a finished session's report (from the caller) into a draft of suggestions."""
        return await self.turn(
            None, mode="update", phase="after_session",
            extra_context=f"Report of session {run_id}:\n{report}",
            instruction=(f"Session {run_id} has finished. Propose spec suggestions, follow-up "
                         "work items and the check states it could not settle."))

    async def derive_spec(self, run_id: str, report: str) -> TurnOutcome:
        """Write requirements from what a session built or researched (from its report)."""
        return await self.turn(
            None, mode="update", phase="derive_spec",
            extra_context=f"Report of session {run_id}:\n{report}",
            instruction=(f"Session {run_id} has finished. Derive the spec from what it built "
                         "or found."))

    # ── the model call ──────────────────────────────────────────────────────
    async def _ask(self, phase: Phase, context: str, sources: list[str] | None = None, *,
                   repair: str | None = None, attempts: int = 2) -> AskResult:
        """Ask for a turn (or, with `repair`, for fixes); whatever is valid is kept.

        A cut-off, a missing tool call or a payload that does not parse gets a retry while
        `attempts` last. A turn that parses is split (`split_turn`): its valid changes are
        returned for the draft and the blocked ones come back as `left_out` for the caller's
        repair rounds (no full retry). Every call is
        recorded in `pm_costs.jsonl`.
        """
        system = pm_system_prompt(seats=self.seats, start_mode=self.start_mode, phase=phase,
                                  has_tech_lead=self._has_tech_lead())
        if self._persona is not None:
            system = persona_layers(self._persona) + "\n\n" + system
        spec = Spec(self.drafts.db.read_spec_doc())
        spec_doc = self.drafts.db.read_spec_doc()
        backlog_doc = self.drafts.db.read_backlog_doc()
        current = self.drafts.current()
        refs = draft_refs(current)
        known_groups = set(current.groups) if current else set()
        responses: list[LLMResponse] = []
        problems: list[str] = []
        best: tuple[PlanTurn, list[Problem], list[str]] | None = None
        kind = "repair" if repair else "turn"
        # Every retry here is for an answer with nothing usable in it.
        for attempt in range(1, attempts + 1):
            tail = repair or ""
            if problems:
                tail += ("\n\nYour previous answer was rejected for these problems:\n"
                         + "\n".join(f"- {p}" for p in problems)
                         + f"\nCall {TOOL_NAME} again with a corrected answer.")
            kwargs: dict[str, Any] = {} if self.model is None else {"model": self.model}
            if self.effort:
                kwargs["effort"] = self.effort
            response = await self.llm.complete(
                system=system, messages=[LLMMessage(role="user",
                                                    content=_cached_content(context, tail))],
                tools=[turn_tool()], max_tokens=MAX_TOKENS, cache_system=True, **kwargs)
            responses.append(response)
            if response.stop_reason == "max_tokens":
                # A cut-off tool call parses as a shorter, valid-looking payload (the changes
                # are lost and only the reply survives), so it is never accepted.
                problems = ["your answer was cut off at the output limit: keep `reply` under "
                            "200 words and propose fewer, smaller changes"]
                self._record_call(phase, kind, attempt, response, "cut_off")
                log.warning("pm: %s attempt %d hit the output limit", kind, attempt)
                continue
            call = next((c for c in response.tool_calls if c.name == TOOL_NAME), None)
            if call is None:
                problems = [f"you did not call {TOOL_NAME}"]
                self._record_call(phase, kind, attempt, response, "no_call")
                log.warning("pm: %s attempt %d made no %s call", kind, attempt, TOOL_NAME)
                continue
            turn, error = validate_result(PlanTurn, call.arguments)
            if turn is None:
                problems = (error or "invalid payload").splitlines()
                self._record_call(phase, kind, attempt, response, "invalid")
                log.warning("pm: %s attempt %d invalid: %s", kind, attempt,
                            "; ".join(problems)[:500])
                continue
            notes = resolve_predicted_ids(turn, spec_doc, backlog_doc, current)
            found = [classify_problem(p) for p in validate_turn(
                turn, spec, backlog_doc, self._team(), sources, known_groups, refs)]
            turn = _fix_groups(turn, found)
            blocking = [p for p in found if p.severity == "block"]
            best = (turn, found, notes)
            self._record_call(phase, kind, attempt, response, "rejected" if blocking else "ok")
            if blocking:
                # No full retry: the valid changes are kept and the caller asks for repairs
                # of only the blocked ones, which costs a fraction of a new turn.
                log.warning("pm: %s attempt %d left out: %s", kind, attempt,
                            "; ".join(p.message for p in blocking)[:500])
            break
        if best is None:
            return AskResult(None, responses, {}, [], [], failure=problems)
        turn, found, notes = best
        kept, warnings, left_out = split_turn(turn, found)
        for p in left_out:
            if p.change:
                p.target = change_target(turn.changes[p.change - 1].model_dump(mode="json"))
                p.change_json = json.dumps(turn.changes[p.change - 1].model_dump(
                    mode="json", exclude_none=True), ensure_ascii=False)[:REPAIR_CHANGE_CAP]
        return AskResult(kept, responses, warnings, notes, left_out)

    def _turn_budget(self) -> float:
        """The most one turn may spend before repairs stop (gate `pm_turn_budget_usd`)."""
        gates = self.doc.get("approval_gates") or {}
        try:
            value = float(gates.get("pm_turn_budget_usd") or DEFAULT_TURN_BUDGET_USD)
        except (TypeError, ValueError):
            return DEFAULT_TURN_BUDGET_USD
        return value if value > 0 else DEFAULT_TURN_BUDGET_USD

    def _record_call(self, phase: str, kind: str, attempt: int, response: LLMResponse,
                     outcome: str) -> None:
        """Append one call to the project's `pm_costs.jsonl`, whatever came of it."""
        cost, tokens = self._usage([response])
        row = {"at": time.time(), "phase": phase, "kind": kind, "attempt": attempt,
               "model": response.model or self.model, "effort": self.effort,
               "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
               "cache_read_tokens": response.cache_read_tokens,
               "cache_creation_tokens": response.cache_creation_tokens,
               "tokens": tokens, "cost_usd": cost, "outcome": outcome}
        path = self.drafts.db.project_dir / PM_COSTS_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            log.warning("pm: could not record a call in %s", path, exc_info=True)

    def _merge(self, turn: PlanTurn, warnings: Mapping[int, list[str]] | None = None) -> Draft:
        draft = self.drafts.current() or self.drafts.open_draft()
        return self.drafts.merge_pm_turn(draft, turn, lead_held=self.seats.lead,
                                         warnings=warnings)

    def _usage(self, responses: list[LLMResponse]) -> tuple[float | None, int | None]:
        """Cost (USD) and tokens of the calls; (None, None) when the client reported none."""
        tokens = sum(r.input_tokens + r.output_tokens + r.cache_read_tokens
                     + r.cache_creation_tokens for r in responses)
        if not tokens:
            return None, None
        from core.llm.registry import ModelRegistry
        try:
            registry = ModelRegistry.for_data_root(self.data_root)
        except Exception:  # noqa: BLE001 — a missing price list must not fail the turn
            log.warning("pm: model registry unreadable; cost not recorded", exc_info=True)
            return None, tokens
        cost = 0.0
        for r in responses:
            info = registry.get(r.model) or (registry.get(self.model) if self.model else None)
            if info is None:
                continue
            cost += (r.input_tokens * info.cost_per_1k_input
                     + r.output_tokens * info.cost_per_1k_output
                     + r.cache_read_tokens * info.cache_read_per_1k
                     + r.cache_creation_tokens * info.cache_write_price) / 1000
        return round(cost, 6), tokens

    # ── context ─────────────────────────────────────────────────────────────
    async def _memory_context(self, text: str | None) -> str:
        """Load Jack and recall what he learned on earlier projects ("" with no memory).

        Never fails a turn: with Jack unreadable the plan conversation runs without him.
        """
        try:
            from core.pm_persona import load_pm
            self._persona = await asyncio.to_thread(load_pm, self.data_root)
            if not self._persona.has_memory():
                return ""
            goal = str(self.doc.get("goal") or "").strip()
            recalled = await self._persona.recall(f"{goal}\n{text or ''}".strip())
            body = recalled.render_for_prompt()
        except Exception:  # noqa: BLE001 — see docstring
            log.warning("pm: memory unavailable", exc_info=True)
            return ""
        if not body:
            return ""
        return (MEMORY_HEADING + ":\n" + body + "\n(The project's own rules and spec above win "
                "over anything you remember when they disagree.)")

    def _context(self, text: str | None, instruction: str | None,
                 extra: str | None) -> str:
        spec = Spec(self.drafts.db.read_spec_doc())
        parts = [f"Project goal:\n{str(self.doc.get('goal') or '').strip() or '(none given)'}",
                 f"Start mode: {self.start_mode}. Lead seat: "
                 f"{'a human' if self.seats.lead else 'empty (you)'}. "
                 f"Reviewer seat: {self.seats.reviewer}."]
        rendered = spec.render()
        parts.append(f"Current spec (approved version {spec.version}):\n"
                     + (rendered or "(empty: nothing is approved yet)"))
        parts.append(self._team_context())
        parts.append(self._kind_context())
        from core.contract_measures import render_for_pm as render_measures
        measures = render_measures(self.data_root, self.project_id)
        if measures:
            parts.append(measures)
        parts.append("Open draft (proposed, not yet part of the spec):\n"
                     + self._draft_summary())
        parts.append("Open backlog:\n" + self._backlog_summary())
        history = self._history(text)
        if history:
            parts.append("Recent plan conversation:\n" + history)
        sessions = self._sessions()
        if sessions:
            parts.append("Latest sessions:\n" + wrap_untrusted("session summaries", sessions))
        readme = self._project_dir / "inputs" / "README.md"
        if readme.is_file():
            body = readme.read_text(encoding="utf-8", errors="replace")[:README_CAP]
            parts.append("Input files overview:\n" + wrap_untrusted("inputs/README.md", body))
        files = self._repo_files()
        if files:
            parts.append("Files in the project repo's main:\n"
                         + wrap_untrusted("repo file list", "\n".join(files)))
        if extra and extra.strip():
            parts.append("Additional material:\n"
                         + wrap_untrusted("extra context", extra.strip()[:EXTRA_CAP]))
        if instruction:
            parts.append(instruction)
        if text is not None and text.strip():
            parts.append(f"The human says now:\n{text.strip()}")
        return "\n\n".join(parts)

    def _has_tech_lead(self) -> bool:
        """Does a role on the team own the design (role contracts plan §2.2)?"""
        from core.contracts import ContractError, load_for_project, team_owner
        try:
            lib = load_for_project(self.data_root, self.project_id)
        except ContractError:
            log.warning("pm: project contracts unreadable", exc_info=True)
            return False
        return team_owner(self._team(), "design", lib) is not None

    def _team(self) -> list[dict[str, Any]]:  # Any: agent rows
        """The project's current agents (empty when the doc has none)."""
        agents = self.doc.get("agents")
        if not isinstance(agents, list):
            return []
        return [a for a in agents if isinstance(a, dict) and a.get("role")]

    def _team_context(self) -> str:
        """The current team, one line per role (team ops in a draft change it, plan §10.2)."""
        team = self._team()
        if not team:
            return "Current team: (none recorded)"
        from core.harness.capabilities import infer_role_type
        lines = []
        for a in team:
            role = str(a["role"])
            role_type = infer_role_type(role, is_reviewer=bool(a.get("is_reviewer")),
                                        declared=str(a.get("role_type") or ""))
            after = ", ".join(a.get("depends_on") or []) or "-"
            reviews = ", ".join(a.get("review_of") or []) or "-"
            from core.contracts import contract_id_for
            lines.append(f"- {role} ({role_type}, {a.get('model_tier') or 'solid'}, contract "
                         f"{contract_id_for(a, role_type)}); after: {after}; "
                         f"reviews: {reviews}")
        return "Current team:\n" + "\n".join(lines)

    def _team_contracts(self) -> list[str]:
        """The contract id of each role on the team (role type default when unnamed)."""
        from core.contracts import contract_id_for
        from core.harness.capabilities import infer_role_type
        out = []
        for a in self._team():
            role_type = infer_role_type(str(a["role"]), is_reviewer=bool(a.get("is_reviewer")),
                                        declared=str(a.get("role_type") or ""))
            out.append(contract_id_for(a, role_type))
        return out

    def _kind_context(self) -> str:
        """The project kind's phases and team (role contracts plan §3), or the kinds to pick
        from when none is set."""
        from core.kinds import KindError, kind_of, load_kinds, render_for_pm
        try:
            if not self.doc.get("kind"):
                kinds = load_kinds()
                return ("Project kind: not set yet (treated as software_app). "
                        + self._history_note() + "Kinds you can propose with `set_kind`:\n"
                        + "\n".join(
                            f"- {k.id}: {k.title}. {k.purpose} Team: "
                            f"{', '.join(c for c in k.team if c != 'product_manager')}; "
                            f"start modes: {', '.join(k.start_modes)}." for k in kinds.values()))
            return render_for_pm(kind_of(self.doc), team_contracts=self._team_contracts())
        except KindError:
            log.warning("pm: project kinds unreadable", exc_info=True)
            return "Project kind: (the kind library could not be read)"

    def _history_note(self) -> str:
        """What the project already has, so an older project is moved over with `adopt`."""
        from core.design import Design
        from core.project_repo import ProjectRepo
        try:
            accepted = sum(1 for r in ProjectRepo(self.data_root, self.project_id).runs()
                           if r.state == "accepted")
            has_design = not Design.load(self.data_root, self.project_id).empty
        except Exception:  # noqa: BLE001 — history is context, never a failure
            log.warning("pm: project history unreadable", exc_info=True)
            return ""
        if not accepted:
            return ""
        return (f"It already has {accepted} accepted session(s) and "
                + ("a recorded design. " if has_design else
                   "no recorded design, so propose `start_mode` adopt with its kind. "))

    def _draft_summary(self) -> str:
        draft = self.drafts.current()
        if draft is None or not draft.changes:
            return "(none)"
        lines = [f"Draft {draft.id}, based on spec version {draft.base_version}:"]
        if draft.groups:
            lines.append("Reasons: " + "; ".join(
                f"{name} ({g.get('because', '')})" for name, g in draft.groups.items()))
        for c in draft.changes:
            state = {True: "ticked", False: "rejected", None: "undecided"}[c.accepted]
            body = json.dumps(c.change, ensure_ascii=False)
            if len(body) > SUMMARY_CHANGE_CAP:
                body = body[:SUMMARY_CHANGE_CAP] + "..."
            lines.append(f"- {c.cid} [{state}, by {c.by}] {body}")
        return "\n".join(lines)

    def _backlog_summary(self) -> str:
        items = [r for r in self.drafts.db.read_backlog_doc().get("items", [])
                 if r.get("status") in ("open", "in_run")]
        if not items:
            return "(empty)"
        lines = []
        for r in items[:BACKLOG_SHOWN]:
            serves = ", ".join(r.get("serves") or [])
            lines.append(f"- {r['id']} [{r.get('status')}] {r.get('title')}"
                         + (f" (serves {serves})" if serves else ""))
        if len(items) > BACKLOG_SHOWN:
            lines.append(f"- (+{len(items) - BACKLOG_SHOWN} more)")
        return "\n".join(lines)

    def _history(self, current: str | None) -> str:
        rows = self.drafts.messages(HISTORY_MESSAGES + 1)
        # The human's message of this turn was just stored; it is shown last, on its own.
        if current and rows and rows[-1]["author"] == "human":
            rows = rows[:-1]
        rows = rows[-HISTORY_MESSAGES:]
        return "\n".join(f"{r['author']}: {r['text']}" for r in rows)

    def _sessions(self) -> str:
        from core.project_repo import ProjectRepo
        try:
            runs = ProjectRepo(self.data_root, self.project_id).runs()[:SESSIONS_SHOWN]
        except Exception:  # noqa: BLE001 — the conversation works without session history
            log.warning("pm: run states unreadable", exc_info=True)
            return ""
        out: list[str] = []
        for r in runs:
            lines = [f"Session {r.run_id}: {r.state}, {r.commits} commits, "
                     f"{len(r.files)} files changed ({', '.join(r.files[:8])})"]
            if r.note:
                lines.append(f"Decision note: {r.note}")
            carry = self.data_root / "runs" / r.run_id / "carry_forward.json"
            if carry.is_file():
                try:
                    doc = json.loads(carry.read_text(encoding="utf-8"))
                    if isinstance(doc, dict) and doc.get("text"):
                        lines.append("Carry-forward: " + str(doc["text"]))
                except ValueError:
                    pass
            out.append("\n".join(lines))
        return "\n\n".join(out)[:SESSION_CAP]

    def _repo_files(self) -> list[str]:
        repo = self._project_dir / "repo.git"
        if not (repo / "HEAD").is_file():
            return []
        proc = subprocess.run(["git", "--git-dir", str(repo), "ls-tree", "-r", "--name-only",
                               "main"], capture_output=True, text=True)
        if proc.returncode != 0:
            return []
        names = proc.stdout.splitlines()
        shown = names[:FILES_SHOWN]
        if len(names) > FILES_SHOWN:
            shown.append(f"(+{len(names) - FILES_SHOWN} more)")
        return shown
