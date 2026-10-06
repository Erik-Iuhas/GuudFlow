"""The project manager: the plan conversation as a host-side LLM call (plan §3, §4).

`ProjectManager.turn()` stores the human's message, shows the model the goal, the spec, the
open draft, the recent conversation, the backlog and (as untrusted data) the latest session
reports and input files, and asks it to answer with one validating tool, `submit_plan_turn`
(a `PlanTurn`). An invalid answer gets one retry with the error text; a second miss raises
`ProjectManagerError`. In `update` mode the changes go into the project's open draft (nothing
is applied until a human approves it); in `discuss` mode only the reply is kept.

Like `core/groom.py` this is one `LLMClient` call with no files and no shell, so tests drive it
with a scripted client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.harness.brief import wrap_untrusted
from core.harness.results import result_json_schema, validate_result
from core.llm.client import LLMClient, LLMMessage, LLMResponse
from core.plan_drafts import Draft, PlanDrafts, PlanTurn, validate_turn
from core.pm_prompts import Phase, Seats, normalise_mode, pm_system_prompt
from core.spec import Spec

log = logging.getLogger(__name__)

TOOL_NAME = "submit_plan_turn"
MAX_TOKENS = 8000
HISTORY_MESSAGES = 20
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

Mode = Literal["update", "discuss"]


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
                 *, model: str | None = None) -> None:
        self.llm = llm
        self.data_root = Path(data_root)
        self.project_id = project_id
        self.doc: Mapping[str, Any] = project_doc or {}
        self.model = model
        self.seats = Seats.from_project(self.doc)
        self.start_mode = normalise_mode(self.doc.get("start_mode"))
        self.drafts = PlanDrafts(self.data_root, project_id)
        self._project_dir = self.data_root / "projects" / project_id

    # ── public ──────────────────────────────────────────────────────────────
    async def turn(self, text: str | None, *, mode: Mode = "update", phase: Phase = "plan",
                   extra_context: str | None = None,
                   instruction: str | None = None) -> TurnOutcome:
        """One exchange with the project manager.

        `text` is the human's message (stored first); `extra_context` is untrusted material
        (wrapped in delimiters); `instruction` is a trusted nudge from GuudFlow itself.
        """
        new: list[dict[str, Any]] = []  # Any: JSON rows
        if text is not None and text.strip():
            new.append(await asyncio.to_thread(self.drafts.add_message, "human", text.strip(),
                                               None, mode))
        context = await asyncio.to_thread(self._context, text, instruction, extra_context)
        turn, usage = await self._ask(phase, context)
        draft: Draft | None
        if mode == "update" and turn.changes:
            draft = await asyncio.to_thread(self._merge, turn)
        else:
            draft = await asyncio.to_thread(self.drafts.current)
        cost, tokens = self._usage(usage)
        reply = turn.reply.strip()
        if turn.questions:
            reply += "\n\n" + "\n".join(f"Question: {q.strip()}" for q in turn.questions)
        new.append(await asyncio.to_thread(
            self.drafts.add_message, "pm", reply, draft.id if draft else None, mode,
            cost, tokens))
        return TurnOutcome(messages=new, draft=draft, reply=turn.reply,
                           questions=list(turn.questions))

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
    async def _ask(self, phase: Phase, context: str) -> tuple[PlanTurn, list[LLMResponse]]:
        system = pm_system_prompt(seats=self.seats, start_mode=self.start_mode, phase=phase)
        spec = Spec(self.drafts.db.read_spec_doc())
        backlog_doc = self.drafts.db.read_backlog_doc()
        responses: list[LLMResponse] = []
        problems: list[str] = []
        content = context
        for attempt in (1, 2):
            if problems:
                content = (context + "\n\nYour previous answer was rejected for these "
                           "problems:\n" + "\n".join(f"- {p}" for p in problems)
                           + f"\nCall {TOOL_NAME} again with a corrected answer.")
            kwargs: dict[str, Any] = {} if self.model is None else {"model": self.model}
            response = await self.llm.complete(
                system=system, messages=[LLMMessage(role="user", content=content)],
                tools=[turn_tool()], max_tokens=MAX_TOKENS, **kwargs)
            responses.append(response)
            call = next((c for c in response.tool_calls if c.name == TOOL_NAME), None)
            if call is None:
                problems = [f"you did not call {TOOL_NAME}"]
                log.warning("pm: attempt %d made no %s call", attempt, TOOL_NAME)
                continue
            turn, error = validate_result(PlanTurn, call.arguments)
            if turn is None:
                problems = (error or "invalid payload").splitlines()
                continue
            problems = validate_turn(turn, spec, backlog_doc)
            if not problems:
                return turn, responses
        raise ProjectManagerError(problems)

    def _merge(self, turn: PlanTurn) -> Draft:
        draft = self.drafts.current() or self.drafts.open_draft()
        return self.drafts.merge_pm_turn(draft, turn, lead_held=self.seats.lead)

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

    def _draft_summary(self) -> str:
        draft = self.drafts.current()
        if draft is None or not draft.changes:
            return "(none)"
        lines = [f"Draft {draft.id}, based on spec version {draft.base_version}:"]
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
