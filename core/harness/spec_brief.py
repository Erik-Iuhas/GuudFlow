"""The project spec as brief sections (project-flow plan §1 calls 5-6, §4, §5).

A run of a project that has a spec, or a start mode, tells its roles more than the card:

  - the planner gets "The spec": the requirements its selected items serve (with their checks),
    the binding decisions, the rules, the instruction to keep every `R#.#:` acceptance line on
    the card that implements it, and the start mode's planner paragraph;
  - a card role gets the requirements of its card's item, the binding decisions and the rules
    that apply to its role type (preferences are marked as preferences);
  - a reviewer gets the same, and one extra acceptance line per binding rule
    (`rule-N: <text>`), plus the start mode's reviewer lines. Each line needs its own
    `ItemVerdict` (`check_review_items`). The builder's own check does not see these lines.

Requirement, decision and rule text was approved by a human in a draft, so it is trusted and
not wrapped. A project with no spec and no start mode gets none of this (`SpecBrief.load`
returns None), so old projects and one-shot runs behave as before.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from typing import TYPE_CHECKING

from core.backlog import BacklogItem
from core.spec import Decision, Requirement, Rule, Spec, resolve_acceptance
from core.spec_adherence import CHECK_LINE
from core.start_modes import DEFAULT_RULES, PLANNER_TEXT, REVIEWER_LINES, START_MODES
from core.work_db import WorkDB

if TYPE_CHECKING:
    from core.harness.tasks import PlanResult

GROUND_POINTER = ("The stack, binding rules and decisions are in your rules (Ground rules) and in "
                  "`.guudflow/project/SPEC.md`.")
KEEP_LINES = ("Keep every `R#.#:` acceptance line of an item on the card that implements it, "
              "word for word. GuudFlow also adds the line of every check a card `serves`, and "
              "puts an item's check that no card carries on that item's first card.")


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class SpecBrief:
    """The spec and start mode of the run's project, ready to render into briefs."""

    spec: Spec
    start_mode: str | None = None

    @classmethod
    def load(cls, data_root: Path | None, project_id: str | None,
             start_mode: object = None) -> SpecBrief | None:
        """None when the project has no spec rows and no (known) start mode."""
        mode = start_mode if isinstance(start_mode, str) and start_mode in START_MODES else None
        spec = Spec({})
        project_dir = (Path(data_root) / "projects" / project_id
                       if data_root is not None and project_id else None)
        if project_dir is not None and (project_dir / "work.db").is_file():
            spec = Spec(WorkDB(project_dir).read_spec_doc())
        has_spec = bool(spec.version or spec.requirements or spec.decisions or spec.rules
                        or not spec.stack.empty)
        if not has_spec and mode is None:
            return None
        return cls(spec=spec, start_mode=mode)

    # ── what applies ────────────────────────────────────────────────────────
    def resolve(self, items: Sequence[BacklogItem]) -> list[BacklogItem]:
        """Items with their check references resolved to the current check text (§11.2)."""
        return resolve_items(self.spec, items)

    def rules(self, role_type: str) -> list[Rule]:
        """Active spec rules for the role type, plus the start mode's default rules the spec
        does not already say."""
        out = [r for r in self.spec.rules if r.status == "active"
               and (role_type == "*" or _applies(r, role_type))]
        have = {_one_line(r.text).casefold() for r in self.spec.rules if r.status == "active"}
        for default in DEFAULT_RULES.get(self.start_mode or "", ()):
            if _one_line(default.text).casefold() not in have:
                out.append(Rule(id=default.id, text=default.text, source="default"))
        return out

    def all_rules(self) -> list[Rule]:
        """Every active rule, defaults included (for the spec slice)."""
        return self.rules("*")

    def binding_decisions(self) -> list[Decision]:
        """Accepted binding decisions."""
        return [d for d in self.spec.decisions if d.binding and d.status == "accepted"]

    def requirements_of(self, serves: Sequence[str]) -> list[Requirement]:
        """The active requirements a list of ids names (unknown or dropped ones are skipped)."""
        wanted = list(dict.fromkeys(serves))
        found = {r.id: r for r in self.spec.requirements if r.status == "active"}
        return [found[i] for i in wanted if i in found]

    def check_methods(self) -> dict[str, str]:
        """How each active check is verified (`R3.2` -> unit | manual | visual | perf)."""
        return {c.id: c.method for r in self.spec.requirements if r.status == "active"
                for c in r.checks if c.method}

    def ids(self) -> list[str]:
        """Active requirement ids and their check ids (what a card may say it serves)."""
        out: list[str] = []
        for r in self.spec.requirements:
            if r.status == "active":
                out.append(r.id)
                out += [c.id for c in r.checks]
        return out

    # ── check lines on cards (worker context plan D1, D2) ───────────────────
    def check_line(self, check_id: str) -> str | None:
        """`R3.2: <current text>` for an active check, else None."""
        try:
            check = self.spec.check(check_id.upper())
            req = self.spec.requirement(check_id.upper().split(".")[0])
        except Exception:  # noqa: BLE001 — SpecError: unknown or bad id
            return None
        return f"{check.id}: {_one_line(check.text)}" if req.status == "active" else None

    def with_check_lines(self, plan: "PlanResult", items: Sequence[BacklogItem]
                         ) -> tuple["PlanResult", list[str]]:
        """The plan with its check lines written by GuudFlow, not copied by the model:

        - every check a card `serves` gets its `R#.#: <text>` acceptance line;
        - every check of a session item that no card carries is added to the first card of
          that item (the planner is told which).

        Returns the new plan and the check ids that were attached to an item's card."""
        from core.harness.tasks import card_serves
        from core.spec_adherence import CHECK_LINE
        tasks = [t.model_copy(update={"acceptance": list(t.acceptance)}) for t in plan.tasks]

        def ids_on(lines: Sequence[str]) -> set[str]:
            return {m.group(1).upper() for line in lines if (m := CHECK_LINE.match(line))}

        for t in tasks:
            have = ids_on(t.acceptance)
            for sid in card_serves(t.acceptance, t.serves):
                if "." in sid and sid not in have and (line := self.check_line(sid)):
                    t.acceptance.append(line)
                    have.add(sid)
        attached: list[str] = []
        carried = set().union(*(ids_on(t.acceptance) for t in tasks)) if tasks else set()
        for item in items:
            if getattr(item, "pending", "") == "done":
                continue
            target = next((t for t in tasks if t.backlog_id == item.id), None)
            if target is None:
                continue
            wanted = [m.group(1).upper() for line in item.acceptance
                      if (m := CHECK_LINE.match(line))]
            for rid in item.serves:
                try:
                    wanted += [c.id for c in self.spec.requirement(rid).checks]
                except Exception:  # noqa: BLE001 — unknown requirement: nothing to add
                    continue
            for cid in dict.fromkeys(wanted):
                if cid not in carried and (line := self.check_line(cid)):
                    target.acceptance.append(line)
                    carried.add(cid)
                    attached.append(cid)
        return plan.model_copy(update={"tasks": tasks}), attached

    def open_requirements(self, limit: int = 30) -> str | None:
        """Requirements with a check that is not met yet, for a session with no items (D3)."""
        rows = [r for r in self.spec.requirements if r.status == "active"
                and any(c.state != "met" for c in r.checks)]
        if not rows:
            return None
        return "\n".join(_req_text(r, []) for r in rows[:limit]) + (
            f"\n- … {len(rows) - limit} more in `.guudflow/project/SPEC.md`"
            if len(rows) > limit else "")

    def checks_section(self, acceptance: Sequence[str]) -> str | None:
        """"Checks this card closes": each `R#.#` line with how it is verified (D4)."""
        from core.spec_adherence import CHECK_LINE
        methods = self.check_methods()
        how = {"unit": "a test you write and run; name the test file and test in evidence",
               "visual": "a screenshot with the screenshot tool; give its path in evidence",
               "perf": "a measurement you run; give the number in evidence",
               "manual": "the owner checks it at acceptance; do not block on it"}
        lines = []
        for line in acceptance:
            found = CHECK_LINE.match(line)
            if found:
                method = methods.get(found.group(1).upper(), "unit")
                lines.append(f"- **{line.strip()}** → {how.get(method, how['unit'])}")
        if not lines:
            return None
        return ("These acceptance lines close the session's requirements. Answer each one in "
                "`acceptance` with its id kept exactly as written; the reviewer confirms each, "
                "and accepting the session records it in the spec. The `close-a-check` skill "
                "says how.\n" + "\n".join(lines))

    # ── review lines ────────────────────────────────────────────────────────
    def rule_lines(self, owner_type: str) -> list[str]:
        """One `rule-N: <text>` line per binding rule that applies to the card's owner."""
        return [f"{r.id}: {_one_line(r.text)}" for r in self.rules(owner_type)
                if r.strength == "binding"]

    def reviewer_lines(self, owner_type: str) -> list[str]:
        """Lines added to a card's review criteria: the stack, binding rules, then the mode's
        lines (worker context plan B7)."""
        stack = self.spec.stack
        what = ", ".join(x for x in (stack.language, stack.framework) if x)
        head = ([f"stack: the change is written in {what} and uses no other language, "
                 f"framework or test runner"] if what else [])
        return head + self.rule_lines(owner_type) + list(
            REVIEWER_LINES.get(self.start_mode or "", ()))

    # ── sections ────────────────────────────────────────────────────────────
    def planner_section(self, items: Sequence[BacklogItem]) -> str | None:
        """"The spec" for the planner; None when there is nothing to say."""
        parts: list[str] = []
        serving: dict[str, list[str]] = {}
        for item in items:
            for rid in item.serves:
                serving.setdefault(rid, []).append(item.id)
        reqs = self.requirements_of(list(serving))
        if reqs:
            parts.append("Requirements these items serve:\n"
                         + "\n".join(_req_text(r, serving[r.id]) for r in reqs))
        parts.append(GROUND_POINTER)
        if items:
            parts.append(KEEP_LINES + " The reviewer answers each of those lines.")
        if not any(i.pending != "done" for i in items):
            open_reqs = self.open_requirements()
            if open_reqs:
                parts.append("No backlog items were picked, so work towards these open "
                             "requirements. Every card names the checks it serves in `serves`; "
                             "GuudFlow adds their lines to the card:\n" + open_reqs)
        if self.start_mode:
            parts.append(PLANNER_TEXT[self.start_mode])
        return "\n\n".join(parts) if parts else None

    def card_section(self, role_type: str, item: BacklogItem | None, *,
                     owner_type: str | None = None, extra_lines: Sequence[str] = (),
                     serves: Sequence[str] = ()) -> str | None:
        """"The spec" for a card role (the card's item) or its reviewer (`extra_lines`).

        `serves` (the card's checks or requirements) names the requirements first and marks
        the checks this card is for."""
        parts: list[str] = []
        wanted = [s.split(".")[0] for s in serves] + (list(item.serves) if item else [])
        reqs = self.requirements_of(wanted)
        if reqs:
            parts.append("Requirements this card serves:\n"
                         + "\n".join(_req_text(r, []) for r in reqs))
        checks = [s for s in serves if "." in s]
        if checks:
            parts.append("This card is for " + ", ".join(checks)
                         + ". Say in your summary how each one is met.")
        if parts:
            parts.append(GROUND_POINTER)
        if extra_lines:
            parts.append(
                "Review checklist additions. Besides the card's acceptance items, answer each "
                "of these lines with its own entry in `items` (`item` is the line's text, or "
                "at least its leading id). A binding rule that is not met fails the review "
                "with a specific `detail`:\n" + "\n".join(f"- [ ] {line}" for line in extra_lines))
        return "\n\n".join(parts) if parts else None

    def ground_rules(self, role_type: str, *, design_decisions: Sequence[Mapping[str, object]] = ()
                     ) -> str | None:
        """The standing rules every turn of this role follows (worker context plan B3): the
        stack, the binding rules and decisions, the preferences, and the other accepted decisions
        as context. None when there is nothing to say."""
        parts: list[str] = []
        stack = self.spec.stack
        if not stack.empty:
            parts.append("### The stack (the owner's choice: build with exactly this)\n"
                         + "\n".join(stack.lines())
                         + "\nDo not add another language, framework or test runner. The merge "
                           "refuses files in another language.")
        parts += [p.replace("Binding decisions (follow them):", "### Binding decisions")
                  .replace("Binding rules (a reviewer checks each one):", "### Binding rules "
                           "(a reviewer checks each one)")
                  .replace("Preferences (follow them where reasonable; nobody fails a review "
                           "for them):", "### Preferences (nobody fails a review for them)")
                  for p in self._shared(role_type=role_type)]
        plain = [d for d in self.spec.decisions if d.status == "accepted" and not d.binding]
        if plain:
            parts.append("### Other decisions (the current choice; follow unless the card says "
                         "otherwise)\n" + "\n".join(f"- **{d.id}** {d.title}: {d.choice}"
                                                     for d in plain))
        tech = [d for d in design_decisions if d.get("status", "accepted") == "accepted"]
        if tech:
            parts.append("### Technical decisions of the design\n" + "\n".join(
                f"- **{d.get('id')}** {d.get('title')}: {d.get('choice')}"
                + (" (binding)" if d.get("binding") else "") for d in tech))
        return "\n\n".join(parts) if parts else None

    def _shared(self, *, planner: bool = False, role_type: str = "*") -> list[str]:
        parts: list[str] = []
        decisions = self.binding_decisions()
        if decisions:
            parts.append("Binding decisions (follow them):\n" + "\n".join(
                f"- **{d.id}** {d.title}: {d.choice} (because {d.reason})" for d in decisions))
        rules = self.rules("*" if planner else role_type)  # the planner sees every rule
        binding = [r for r in rules if r.strength == "binding"]
        prefs = [r for r in rules if r.strength != "binding"]
        if binding:
            parts.append("Binding rules (a reviewer checks each one):\n" + "\n".join(
                f"- **{r.id}** {_one_line(r.text)}" for r in binding))
        if prefs:
            parts.append("Preferences (follow them where reasonable; nobody fails a review "
                         "for them):\n" + "\n".join(
                             f"- **{r.id}** (preference) {_one_line(r.text)}" for r in prefs))
        return parts


def _applies(rule: Rule, role_type: str) -> bool:
    """Does the rule reach this role type? A rule whose `applies_to` names no known role type
    (an older rule written with a role name) is read as `*` rather than reaching nobody
    (worker context plan B5)."""
    from core.harness.capabilities import ROLE_TYPES
    targets = [t for t in rule.applies_to if t == "*" or t in ROLE_TYPES]
    return not targets or "*" in targets or role_type in targets


def _req_text(req: Requirement, served_by: Sequence[str]) -> str:
    flags = ", assumed" if req.assumed else ""
    by = f" (items {', '.join(served_by)})" if served_by else ""
    lines = [f"- **{req.id}** [{req.kind}, {req.priority}{flags}] {_one_line(req.text)}{by}"]
    lines += [f"    - {c.id}: {_one_line(c.text)}" for c in req.checks]
    return "\n".join(lines)


def resolve_items(spec: Spec, items: Sequence[BacklogItem]) -> list[BacklogItem]:
    """Backlog items whose acceptance check references (`R4.8`) read as the current text."""
    return [replace(i, acceptance=resolve_acceptance(spec, i.acceptance)) for i in items]


def missing_check_lines(items: Sequence[BacklogItem],
                        card_acceptance: Sequence[Sequence[str]]) -> list[str]:
    """`R#.#` ids of the items' acceptance lines that no card carries."""
    on_cards = {CHECK_LINE.match(line).group(1).upper()  # type: ignore[union-attr]
                for lines in card_acceptance for line in lines if CHECK_LINE.match(line)}
    out: list[str] = []
    for item in items:
        for line in item.acceptance:
            found = CHECK_LINE.match(line)
            if found and found.group(1).upper() not in on_cards \
                    and found.group(1).upper() not in out:
                out.append(found.group(1).upper())
    return out


def spec_slice(data_root: Path, project_id: str, run_id: str,
               start_mode: object = None) -> dict[str, object]:
    """What the run must follow, with the verdicts its reviews have given so far.

    `{project_id, requirements: [Requirement + served_by], decisions, rules: [Rule + verdicts],
    checks: {"R4.2": {state, run_id}}}`, for the requirements the run's items serve. Reads the
    run's board file, so it works while the run is live. A check shows this run's latest
    verdict when it has one, else the state stored in the spec.
    """
    from core import project_runs
    from core.backlog import Backlog
    from core.spec_adherence import check_verdicts, read_board, reviews, rule_counts

    run_root = Path(data_root) / "runs" / run_id
    items = project_runs.selection(run_root) or Backlog(data_root, project_id).in_run(run_id)
    brief = SpecBrief.load(data_root, project_id, start_mode) or SpecBrief(Spec({}), None)
    serving: dict[str, list[str]] = {}
    for item in items:
        for rid in item.serves:
            serving.setdefault(rid, []).append(item.id)
    reqs = brief.requirements_of(list(serving))
    found = reviews(read_board(run_root))
    live = check_verdicts(found)
    counts = rule_counts(found)
    checks: dict[str, dict[str, object]] = {}
    req_rows: list[dict[str, object]] = []
    for r in reqs:
        for c in r.checks:
            if c.id in live:
                checks[c.id] = {"state": "met" if live[c.id] else "unmet", "run_id": run_id}
            else:
                checks[c.id] = {"state": c.state, "run_id": c.run_id}
        row = r.to_dict()
        row.pop("history", None)
        req_rows.append({**row, "served_by": serving[r.id]})
    served = {r.id for r in reqs}
    decisions = [d.to_dict() for d in brief.spec.decisions if d.status == "accepted"
                 and (d.binding or served.intersection(d.serves))]
    for d in decisions:
        d.pop("history", None)
    return {"project_id": project_id, "requirements": req_rows, "decisions": decisions,
            "rules": [{**r.to_dict(), "verdicts": counts.get(r.id, {"met": 0, "unmet": 0})}
                      for r in brief.all_rules()],
            "checks": checks}
