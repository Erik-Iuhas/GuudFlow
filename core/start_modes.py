"""The start modes in one place (project-flow plan §5).

A start mode is a preset over existing levers. This module holds the table for all of them:

  - `MODES`          the project manager's paragraph (`core.pm_prompts`);
  - `TEAM_HINTS`     the meta-agent's team-template hint (`api.routes.meta`);
  - `PLANNER_TEXT`   the instruction in the planner's brief (`core.harness.spec_brief`);
  - `REVIEWER_LINES` extra lines every card review must answer, one `ItemVerdict` each;
  - `DEFAULT_RULES`  binding rules a mode brings even when the spec has none.

Nothing else decides what a mode means.
"""

from __future__ import annotations

from dataclasses import dataclass

START_MODES = ("spec_first", "prototype_first", "test_first", "research_first", "maintain",
               "adopt")
DEFAULT_START_MODE = "spec_first"


def normalise_mode(mode: object) -> str:
    """A known start mode, else `spec_first`."""
    return mode if isinstance(mode, str) and mode in START_MODES else DEFAULT_START_MODE


MODES: dict[str, str] = {
    "spec_first": """\
Start mode: spec first. No session runs until the spec is approved, so the first job is a spec \
that is good enough to build from: requirements with checks, the decisions that shape the \
build, and work items that each implement named checks. Every item you add should serve at \
least one requirement. Keep the first set of items to the smallest slice that proves the \
design.""",
    "prototype_first": """\
Start mode: prototype first. The first session builds the thinnest end-to-end slice and skips \
polish; no approved spec is needed to start. Plan session one as that slice: few items, wide \
rather than deep. Keep requirements light and mark them `assumed` until the prototype shows \
what is real. After the first accepted session you will be asked to write the spec from what \
was built.""",
    "test_first": """\
Start mode: test first. No session runs until the spec is approved. For each work item the \
team writes failing tests for its requirement checks first and builds second, so every check \
must be concrete enough to become a test. Write checks as observable behaviour with example \
inputs and outputs where you can. Say in the item's acceptance when a tester item must come \
before a builder item, and use `depends_on` for it.""",
    "research_first": """\
Start mode: research first. The first session produces knowledge, not product code. Plan it \
as `research` work items, each ending in a decision note with the options, the evidence and a \
recommendation. Requirements stay light and assumed until the research is in. No approved spec \
is needed to start. After the session you will be asked to write requirements and decisions \
from what was found.""",
    "maintain": """\
Start mode: maintain. This is existing software and the goal is small, safe change. Plan the \
smallest change that meets each item, with a regression test for every fix, and no refactoring \
outside the files the item names (`files_hint`). Prefer a rule such as "minimal diff" or \
"regression test per fix" over new requirements. Do not rewrite the spec; add to it only when \
behaviour that must not break is worth recording.""",
    "adopt": """\
Start mode: adopt. The code already exists and nobody wrote down its design. The first session \
is the tech lead's: it reads the code and records the design as it is (components, the \
interfaces actually used, the data that crosses them, the debt it sees) and changes no product \
code. Your first job is a spec of what the software does today: requirements with checks that \
match its current behaviour, marked `assumed` where you infer rather than see, and the product \
decisions the code already made. Add work items for what the owner wants next; they are planned \
against the recovered design from the second session on. If the team has no tech lead, propose \
one first (role_type `architect`, contract `tech_lead`).""",
}


TEAM_HINTS: dict[str, str] = {
    "spec_first": (
        "start mode spec_first: project manager, one or two builders and a reviewer. "
        "Every card should implement named requirement checks."
    ),
    "prototype_first": (
        "start mode prototype_first: project manager, one builder and a reviewer. "
        "The first session builds the thinnest end-to-end slice; skip polish."
    ),
    "test_first": (
        "start mode test_first: include a tester role ahead of builders. The tester "
        "writes failing tests for each item's checks first; builders depend on the tester."
    ),
    "research_first": (
        "start mode research_first: project manager, a researcher with web access and a "
        "reviewer. Research produces a decision note with options, evidence and a "
        "recommendation; no product code yet."
    ),
    "maintain": (
        "start mode maintain: project manager, one builder and a reviewer. Existing code: "
        "the smallest safe change, and a regression test per fix."
    ),
    "adopt": (
        "start mode adopt: an existing codebase joins. Include a tech lead (role_type "
        "architect, contract tech_lead) who recovers the design in the first session, plus "
        "builders, a tester and a reviewer for the sessions after it."
    ),
}

PLANNER_TEXT: dict[str, str] = {
    "spec_first": "Start mode spec first: every card implements named R checks. Put the "
                  "`R#.#` lines it implements on the card.",
    "prototype_first": "Start mode prototype first: plan the thinnest end-to-end slice that "
                       "runs, and skip polish. Few cards, wide rather than deep.",
    "test_first": "Start mode test first: for each item, add a tester card that writes failing "
                  "tests for its R checks first; the builder cards for that item depend on "
                  "the tester card.",
    "research_first": "Start mode research first: research cards produce a decision note with "
                      "options, evidence and a recommendation. Write no product code.",
    "maintain": "Start mode maintain: make the smallest change that meets each item, add a "
                "regression test per fix, and do not refactor outside the card's `files_hint`.",
    "adopt": "Start mode adopt: until the design is recorded, recover it from the code and "
             "plan no product changes. After that, plan cards against the recovered design and "
             "keep each change inside the components its card names.",
}

# Extra lines every card review must answer (one `ItemVerdict` each). spec_first adds none:
# its lines are the requirement checks already on the card.
REVIEWER_LINES: dict[str, tuple[str, ...]] = {
    "spec_first": (),
    "prototype_first": ("mode: it runs end to end (light check, polish is not required)",),
    "test_first": ("mode: the tests were written before the code and now pass",),
    "research_first": ("mode: sources are cited and the options are compared",),
    "maintain": ("mode: a regression test was added and the diff is limited to the item",),
    "adopt": ("mode: the change stays inside the design components the card names",),
}


@dataclass(frozen=True)
class DefaultRule:
    """A binding rule a start mode brings when the spec has none like it."""

    id: str            # "rule-m1": cannot collide with the spec's "rule-N"
    text: str


DEFAULT_RULES: dict[str, tuple[DefaultRule, ...]] = {
    "maintain": (
        DefaultRule("rule-m1", "Make the smallest change that meets the item"),
        DefaultRule("rule-m2", "Every fix gets a regression test"),
    ),
}
