"""The tech lead before the run: prepare a session with a person (worker context plan H).

A host-side turn of the project's session planner (the tech lead, else the planner) returns the
same `PlanResult` the in-run planner returns, validated by the same checks, plus its assumptions
and questions. The prompt sections and the checks come from an offline `TaskFlow`, so a plan made
here and one made in a run look the same (H6).

A person works on it in the Design tab: talks to the tech lead, edits cards, answers the owner's
questions (each answer becomes a spec decision, so it reaches every worker) and approves. A
session started from the approved plan skips the planner's first turn (`hand_to_run`, read by
`core/harness/run.py`). Start warns when the spec, the design or `main` changed since approval
(`stale`).

One plan per project at a time, in `data/projects/{id}/preflight.json`. Every call's cost goes to
the project's `pm_costs.jsonl` as planning spend (phase "preflight").
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.backlog import Backlog, BacklogError, BacklogItem
from core.harness.results import result_json_schema, validate_result
from core.harness.tasks import PlanResult
from core.llm.client import LLMClient, LLMMessage, LLMResponse
from core.json_store import read_json, write_json

log = logging.getLogger(__name__)

PREFLIGHT_JSON = "preflight.json"
RUN_PLAN_JSON = "preflight_plan.json"     # in the run root: the plan the run executes
TOOL_NAME = "submit_session_plan"
MAX_TOKENS = 32000
ATTEMPTS = 2
FILES_SHOWN = 200
HISTORY_SHOWN = 20
Status = Literal["draft", "approved", "used", "discarded"]


class PreflightError(ValueError):
    """A preflight operation was refused (no planner, wrong state, open questions...)."""


class Assumption(BaseModel):
    """Something the tech lead assumed or needs decided before the session."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=10, max_length=600,
                      description="The assumption or the question, in plain language.")
    decided_by: Literal["tech_lead", "owner"] = Field(
        description="tech_lead: you decided it (the owner may override). owner: only the owner "
                    "can decide it (a product choice, scope, a stack the spec does not fix); "
                    "the session cannot start until it is answered.")
    choice: str = Field(default="", max_length=400,
                        description="What you chose, or what you propose for an owner question.")


class PreflightTurn(BaseModel):
    """The tech lead's answer when preparing a session."""

    model_config = ConfigDict(extra="forbid")

    reply: str = Field(max_length=2000, description="A short plain-language reply to the owner.")
    plan: PlanResult
    assumptions: list[Assumption] = Field(default_factory=list, max_length=15)


def preflight_tool() -> dict[str, Any]:  # Any: JSON schema
    """The `submit_session_plan` tool definition."""
    from core.project_manager import _strip_discriminators
    return {"name": TOOL_NAME,
            "description": ("Submit the prepared session: a short reply to the owner, the session "
                            "plan (the same task list you would return in a run, with any design "
                            "changes) and your assumptions and questions."),
            "input_schema": _strip_discriminators(result_json_schema(PreflightTurn))}


@dataclass
class _Slot:
    """The parts of a team row `TaskFlow.build` reads."""

    role: str
    depends_on: list[str] = field(default_factory=list)
    review_of: list[str] = field(default_factory=list)
    max_review_rounds: int = 2


def _noop(*_: object) -> None:
    return None


class Preflight:
    """The prepared session of one project."""

    def __init__(self, data_root: Path, project_id: str,
                 project_doc: Mapping[str, Any] | None = None) -> None:  # Any: project JSON
        self.data_root = Path(data_root)
        self.project_id = project_id
        self.doc: Mapping[str, Any] = project_doc or {}
        self.project_dir = self.data_root / "projects" / project_id
        self.path = self.project_dir / PREFLIGHT_JSON

    # ── store ───────────────────────────────────────────────────────────────
    def current(self) -> dict[str, Any] | None:  # Any: JSON
        """The open plan (draft or approved), with `stale` reasons; None when there is none."""
        row = read_json(self.path, None)
        if not isinstance(row, dict) or row.get("status") not in ("draft", "approved"):
            return None
        row["stale"] = self.stale(row) if row.get("status") == "approved" else []
        row["open_questions"] = [a["id"] for a in row.get("assumptions") or []
                                 if a.get("decided_by") == "owner" and not a.get("answer")]
        return row

    def _require(self, status: Sequence[str] = ("draft", "approved")) -> dict[str, Any]:  # Any: JSON
        row = read_json(self.path, None)
        if not isinstance(row, dict) or row.get("status") not in status:
            raise PreflightError("there is no prepared session; prepare one first")
        return row

    def _save(self, row: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
        row.pop("stale", None)
        row.pop("open_questions", None)
        row["updated_at"] = time.time()
        write_json(self.path, row)
        out = self.current()
        return out if out is not None else row


    # ── the turn ────────────────────────────────────────────────────────────
    def _team(self) -> list[dict[str, Any]]:  # Any: agent rows
        return [a for a in self.doc.get("agents") or [] if isinstance(a, dict) and a.get("role")]

    def _items(self, ids: Sequence[str]) -> list[BacklogItem]:
        backlog = Backlog(self.data_root, self.project_id)
        out: list[BacklogItem] = []
        for i in ids:
            try:
                out.append(backlog.get(i))
            except BacklogError as e:
                raise PreflightError(str(e)) from None
        return out

    def _flow(self, goal: str, items: Sequence[BacklogItem]) -> tuple[Any, str, Any]:  # Any: TaskFlow, Contract
        """An offline task flow set up like a run's, for the planner's sections and checks."""
        from core.contracts import contracts_for_team, load_for_project
        from core.design import Design
        from core.harness.capabilities import infer_role_type
        from core.harness.spec_brief import SpecBrief
        from core.harness.task_flow import TaskFlow
        from core.knowledge import load_facts
        from core.pm_prompts import Seats
        from core.project_runs import average_card_cost, load_notebooks
        from core.workspace import Workspace

        rows = self._team()
        types = {str(a["role"]): infer_role_type(str(a["role"]),
                                                 is_reviewer=bool(a.get("is_reviewer")),
                                                 declared=str(a.get("role_type") or ""))
                 for a in rows}
        contracts = contracts_for_team(
            [(str(a["role"]), types[str(a["role"])], a.get("contract") or None) for a in rows],
            load_for_project(self.data_root, self.project_id))
        planner = next((r for r, c in contracts.items() if c.may_change("session_plan")), None)
        slots = [_Slot(str(a["role"]), list(a.get("depends_on") or []),
                       list(a.get("review_of") or []), int(a.get("max_review_rounds") or 2))
                 for a in rows]
        flow = TaskFlow.build(slots, types, Workspace(run_id="preflight", goal=goal),
                              lambda: None, post=_noop, escalate=_noop, planner_role=planner)
        if flow is None:
            raise PreflightError("the team has no tech lead or planner to prepare a session")
        gates = self.doc.get("approval_gates") or {}
        raw = gates.get("token_budget_usd")
        flow.size_budget = (float(raw) if isinstance(raw, (int, float)) and raw else None,
                            average_card_cost(self.data_root, self.project_id))
        flow.spec = SpecBrief.load(self.data_root, self.project_id, self.doc.get("start_mode"))
        flow.backlog = flow.spec.resolve(items) if flow.spec is not None else list(items)
        contract = contracts.get(flow.planner)
        flow.designer = bool(contract and contract.may_change("design"))
        flow.data_root, flow.project_id = self.data_root, self.project_id
        flow.design = Design.load(self.data_root, self.project_id)
        flow.design_auto = not Seats.from_project(dict(self.doc)).lead
        flow.adopt = self.doc.get("start_mode") == "adopt"
        flow.facts = load_facts(self.data_root, self.project_id)
        if flow.designer:
            flow.notebooks = load_notebooks(self.data_root, self.project_id, list(types))
        return flow, flow.planner, contract

    def _system(self, planner: str, contract: Any, flow: Any) -> str:  # Any: Contract, TaskFlow
        """The tech lead's standing instructions: its contract, the ground rules, write-cards."""
        from core.contracts import render_for_brief
        from core.crew import crew_names
        from core.harness.worker_rules import SKILL_ROOT

        names = crew_names(self.project_id, [str(a["role"]) for a in self._team()],
                           explicit={str(a["role"]): a.get("name") for a in self._team()})
        parts = [f"You are {names.get(planner, planner)}, the {planner} of this project. You are "
                 "preparing the next working session with the owner BEFORE it starts: nobody is "
                 "building yet. Plan the session exactly as you would in your first turn of a "
                 "run (the same cards, the same rules), and say what you assumed. You cannot "
                 "read or run code here; use the file list and the knowledge base you are shown."]
        if contract is not None:
            parts.append("## Your role contract\n\n" + render_for_brief(contract, None, names))
        if flow.spec is not None:
            design = flow.design
            ground = flow.spec.ground_rules(
                "*", design_decisions=design.decisions() if design is not None else ())
            if ground:
                parts.append("## Ground rules\n\n" + ground)
        skill = SKILL_ROOT / "write-cards" / "SKILL.md"
        if skill.is_file():
            body = skill.read_text(encoding="utf-8")
            if body.startswith("---"):
                body = body.split("---", 2)[-1]
            parts.append("## How to write cards\n\n" + body.strip())
        parts.append(f"Answer only by calling `{TOOL_NAME}`. Mark an assumption `owner` only when "
                     "the owner must decide it (a product choice, scope, a stack the spec does not "
                     "fix); decide technical questions yourself and list them as `tech_lead`.")
        return "\n\n".join(parts)

    def _context(self, goal: str, sections: Sequence[tuple[str, str]], row: Mapping[str, Any],  # Any: JSON
                 text: str | None) -> str:
        from core.harness.brief import wrap_untrusted
        parts = [f"# The session\n\nGoal: {goal or self.doc.get('goal') or '(none given)'}"]
        parts += [f"## {title}\n\n{body}" for title, body in sections]
        files = self._repo_files()
        if files:
            parts.append(wrap_untrusted("the files on main", "\n".join(files)))
        history = [m for m in row.get("messages") or []][-HISTORY_SHOWN:]
        if history:
            parts.append("## The conversation so far\n\n" + "\n".join(
                f"- **{m['author']}**: {m['text']}" for m in history))
        if row.get("plan"):
            parts.append("## Your current plan (revise it; keep what the owner did not ask to "
                         "change)\n\n```json\n" + json.dumps(row["plan"], indent=1)[:20000]
                         + "\n```")
        answered = [a for a in row.get("assumptions") or [] if a.get("answer")]
        if answered:
            parts.append("## The owner's answers\n\n" + "\n".join(
                f"- {a['text']} → **{a['answer']}**" for a in answered))
        if text:
            parts.append("## The owner says\n\n" + text)
        return "\n\n".join(parts)

    def _repo_files(self) -> list[str]:
        repo = self.project_dir / "repo.git"
        if not (repo / "HEAD").is_file():
            return []
        proc = subprocess.run(["git", "--git-dir", str(repo), "ls-tree", "-r", "--name-only",
                               "main"], capture_output=True, text=True)
        if proc.returncode != 0:
            return []
        names = proc.stdout.splitlines()
        return names[:FILES_SHOWN] + ([f"(+{len(names) - FILES_SHOWN} more)"]
                                      if len(names) > FILES_SHOWN else [])

    async def _ask(self, llm: LLMClient, row: dict[str, Any], text: str | None, *,  # Any: JSON
                   model: str | None, effort: str | None) -> dict[str, Any]:
        items = self._items(row.get("items") or [])
        flow, planner, contract = self._flow(str(row.get("goal") or ""), items)
        turn_plan = flow.begin_turn(planner)
        assert turn_plan is not None
        system = self._system(planner, contract, flow)
        context = self._context(str(row.get("goal") or ""), turn_plan.extra_sections, row, text)
        problems: list[str] = []
        responses: list[LLMResponse] = []
        accepted: PreflightTurn | None = None
        left: list[str] = []
        for attempt in range(1, ATTEMPTS + 1):
            tail = ("" if not problems else "\n\nYour previous answer was rejected:\n"
                    + "\n".join(f"- {p}" for p in problems) + f"\nCall {TOOL_NAME} again.")
            kwargs: dict[str, Any] = {} if model is None else {"model": model}
            if effort:
                kwargs["effort"] = effort
            response = await llm.complete(
                system=system, messages=[LLMMessage(role="user", content=context + tail)],
                tools=[preflight_tool()], max_tokens=MAX_TOKENS, cache_system=True, **kwargs)
            responses.append(response)
            call = next((c for c in response.tool_calls if c.name == TOOL_NAME), None)
            if response.stop_reason == "max_tokens":
                problems = ["your answer was cut off: plan fewer cards with shorter text"]
            elif call is None:
                problems = [f"you did not call {TOOL_NAME}"]
            else:
                turn, error = validate_result(PreflightTurn, call.arguments)
                if turn is None:
                    problems = (error or "invalid payload").splitlines()
                else:
                    plan = self._with_checks(flow, turn.plan)
                    problems = list(turn_plan.check(plan) if turn_plan.check else [])
                    accepted, left = turn.model_copy(update={"plan": plan}), problems
                    if not problems:
                        self._record(responses[-1:], model, effort, "ok", attempt)
                        break
            self._record(responses[-1:], model, effort, "rejected", attempt)
        if accepted is None:
            raise PreflightError("the tech lead gave no usable plan: " + "; ".join(problems))
        cost = sum(self._cost([r], model) or 0.0 for r in responses)
        old = {a.get("text"): a for a in row.get("assumptions") or []}
        row["plan"] = accepted.plan.model_dump(mode="json", exclude_none=True)
        row["assumptions"] = [
            {"id": f"A{n}", **a.model_dump(mode="json"),
             "answer": (old.get(a.text) or {}).get("answer") or ""}
            for n, a in enumerate(accepted.assumptions, 1)]
        row["problems"] = left
        row["spend_usd"] = round(float(row.get("spend_usd") or 0.0) + cost, 6)
        row.setdefault("messages", []).append(
            {"author": planner, "text": accepted.reply, "at": time.time(),
             "cost_usd": round(cost, 6)})
        row["planner"] = planner
        return row

    def _with_checks(self, flow: Any, plan: PlanResult) -> PlanResult:  # Any: TaskFlow
        """The plan with the check lines GuudFlow writes (worker context plan D1)."""
        if flow.spec is None:
            return plan
        return flow.spec.with_check_lines(plan, flow.backlog)[0]

    # ── public ──────────────────────────────────────────────────────────────
    async def prepare(self, llm: LLMClient, items: Sequence[str], goal: str = "", *,
                      model: str | None = None, effort: str | None = None) -> dict[str, Any]:  # Any: JSON
        """Start a new prepared session (replaces an open one)."""
        prev = read_json(self.path, None)
        n = int(prev.get("n", 0)) + 1 if isinstance(prev, dict) else 1
        row: dict[str, Any] = {"id": f"P{n}", "n": n, "status": "draft", "items": list(items),
                               "goal": goal.strip(), "plan": None, "assumptions": [],
                               "messages": [], "spend_usd": 0.0, "problems": [],
                               "created_at": time.time()}
        row = await self._ask(llm, row, None, model=model, effort=effort)
        return self._save(row)

    async def message(self, llm: LLMClient, text: str, *, model: str | None = None,
                      effort: str | None = None) -> dict[str, Any]:  # Any: JSON
        """The owner talks to the tech lead; the plan is revised (back to draft)."""
        row = self._require()
        row.setdefault("messages", []).append({"author": "owner", "text": text.strip(),
                                               "at": time.time()})
        row["status"] = "draft"
        row = await self._ask(llm, row, text.strip(), model=model, effort=effort)
        return self._save(row)

    def edit_cards(self, tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:  # Any: JSON
        """A person replaces the plan's cards. Refused when the result does not pass the same
        checks as a planner's plan; the plan goes back to draft."""
        row = self._require()
        plan = PlanResult.model_validate({**(row.get("plan") or {}), "tasks": list(tasks)})
        flow, planner, _ = self._flow(str(row.get("goal") or ""),
                                      self._items(row.get("items") or []))
        plan = self._with_checks(flow, plan)
        turn_plan = flow.begin_turn(planner)
        problems = list(turn_plan.check(plan) if turn_plan and turn_plan.check else [])
        if problems:
            raise PreflightError("; ".join(problems))
        row["plan"] = plan.model_dump(mode="json", exclude_none=True)
        row["problems"] = []
        row["status"] = "draft"
        row.setdefault("messages", []).append({"author": "owner", "text": "edited the cards",
                                               "at": time.time()})
        return self._save(row)

    def answer(self, assumption_id: str, answer: str) -> dict[str, Any]:  # Any: JSON
        """Answer (or override) an assumption. The answer becomes a spec decision, so it reaches
        every worker (worker context plan H4, B2)."""
        row = self._require()
        found = next((a for a in row.get("assumptions") or [] if a.get("id") == assumption_id),
                     None)
        if found is None:
            raise PreflightError(f"unknown assumption {assumption_id!r}")
        text = answer.strip()
        if not text:
            raise PreflightError("the answer is empty")
        found["answer"] = text
        found["answered_at"] = time.time()
        found["decision"] = self._record_decision(found, text)
        return self._save(row)

    def _record_decision(self, assumption: Mapping[str, Any], answer: str) -> str | None:  # Any: JSON
        from core.plan_drafts import AddDecision, PlanDrafts
        title = str(assumption.get("text") or "").strip().rstrip("?")[:120]
        try:
            result = PlanDrafts(self.data_root, self.project_id).apply_direct(
                AddDecision(op="add_decision", ref="new:1", title=title or "Owner's answer",
                            choice=answer,
                            reason="The owner answered the tech lead's question while "
                                   "preparing the session."),
                "human")
        except Exception:  # noqa: BLE001 — the answer is kept on the plan either way
            log.warning("preflight: the answer was not recorded as a decision", exc_info=True)
            return None
        return next(iter(result.created.values()), None)

    def approve(self) -> dict[str, Any]:  # Any: JSON
        """Approve the plan. Refused while an owner question is open or the plan has problems."""
        row = self._require(("draft",))
        if not row.get("plan"):
            raise PreflightError("there is no plan to approve")
        open_q = [a["id"] for a in row.get("assumptions") or []
                  if a.get("decided_by") == "owner" and not a.get("answer")]
        if open_q:
            raise PreflightError("answer the owner's questions first: " + ", ".join(open_q))
        if row.get("problems"):
            raise PreflightError("the plan still has problems: " + "; ".join(row["problems"]))
        row["status"] = "approved"
        row["approved_at"] = time.time()
        row["basis"] = self.basis()
        return self._save(row)

    def discard(self) -> None:
        """Drop the open plan."""
        row = self._require()
        row["status"] = "discarded"
        self._save(row)

    def basis(self) -> dict[str, Any]:  # Any: JSON
        """What the plan was made on: the spec and design versions and `main`."""
        from core.design import Design
        from core.spec import Spec
        from core.work_db import WorkDB
        spec_version = (Spec(WorkDB(self.project_dir).read_spec_doc()).version
                        if (self.project_dir / "work.db").is_file() else 0)
        main = ""
        repo = self.project_dir / "repo.git"
        if (repo / "HEAD").is_file():
            proc = subprocess.run(["git", "--git-dir", str(repo), "rev-parse", "--verify", "-q",
                                   "main"], capture_output=True, text=True)
            main = proc.stdout.strip() if proc.returncode == 0 else ""
        return {"spec_version": spec_version,
                "design_version": Design.load(self.data_root, self.project_id).version,
                "main": main}

    def stale(self, row: Mapping[str, Any]) -> list[str]:  # Any: JSON
        """Why an approved plan may be out of date (empty: it is current)."""
        then = row.get("basis") or {}
        now = self.basis()
        out = []
        if then.get("spec_version") != now["spec_version"]:
            out.append(f"the spec changed (v{then.get('spec_version')} → v{now['spec_version']})")
        if then.get("design_version") != now["design_version"]:
            out.append(f"the design changed (v{then.get('design_version')} → "
                       f"v{now['design_version']})")
        if then.get("main") != now["main"]:
            out.append("main has new commits")
        return out

    def hand_to_run(self, plan_id: str, run_id: str, run_root: Path) -> bool:
        """Give the approved plan to a starting run (it skips the planner's first turn)."""
        row = read_json(self.path, None)
        if not isinstance(row, dict) or row.get("id") != plan_id or row.get("status") != "approved":
            return False
        write_json(Path(run_root) / RUN_PLAN_JSON, {"id": plan_id, "plan": row["plan"],
                                                    "planner": row.get("planner")})
        row["status"] = "used"
        row["run_id"] = run_id
        self._save(row)
        return True

    # ── cost ───────────────────────────────────────────────────────────────
    def _cost(self, responses: Sequence[LLMResponse], model: str | None) -> float | None:
        if not any(r.input_tokens or r.output_tokens for r in responses):
            return None
        from core.llm.registry import ModelRegistry
        try:
            registry = ModelRegistry.for_data_root(self.data_root)
        except Exception:  # noqa: BLE001 — a missing price list must not fail the turn
            return None
        cost = 0.0
        for r in responses:
            info = registry.get(r.model) or (registry.get(model) if model else None)
            if info is None:
                continue
            cost += (r.input_tokens * info.cost_per_1k_input
                     + r.output_tokens * info.cost_per_1k_output
                     + r.cache_read_tokens * info.cache_read_per_1k
                     + r.cache_creation_tokens * info.cache_write_price) / 1000
        return round(cost, 6)

    def _record(self, responses: Sequence[LLMResponse], model: str | None, effort: str | None,
                outcome: str, attempt: int) -> None:
        """Planning spend: one row per call in the project's `pm_costs.jsonl`."""
        from core.project_manager import PM_COSTS_FILE
        for r in responses:
            row = {"at": time.time(), "phase": "preflight", "kind": "turn", "attempt": attempt,
                   "model": r.model or model, "effort": effort,
                   "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                   "cache_read_tokens": r.cache_read_tokens,
                   "cache_creation_tokens": r.cache_creation_tokens,
                   "tokens": r.input_tokens + r.output_tokens + r.cache_read_tokens
                   + r.cache_creation_tokens,
                   "cost_usd": self._cost([r], model), "outcome": outcome}
            try:
                self.project_dir.mkdir(parents=True, exist_ok=True)
                with (self.project_dir / PM_COSTS_FILE).open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
            except OSError:
                log.warning("preflight: could not record a call", exc_info=True)


def run_plan(run_root: Path) -> tuple[PlanResult, str] | None:
    """The approved plan a run was started with, and its id (None: plan in the run)."""
    row = read_json(Path(run_root) / RUN_PLAN_JSON, None)
    if not isinstance(row, dict) or not isinstance(row.get("plan"), dict):
        return None
    try:
        return PlanResult.model_validate(row["plan"]), str(row.get("id") or "")
    except ValueError:
        log.warning("preflight: the run's plan does not parse; the planner plans instead")
        return None


__all__ = ["Assumption", "Preflight", "PreflightError", "PreflightTurn", "RUN_PLAN_JSON",
           "preflight_tool", "run_plan"]
