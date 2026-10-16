"""Standing context for workers, delivered the agentic way (worker context plan R, B, C, E, G).

Three layers, each with one job:

- **Rules** (`rules/guudflow.md` in the role's host-only tool directory, listed under opencode's
  `instructions`): what holds for every turn of the role. The ground rules (stack, binding rules
  and decisions), the role's job, where it is in the project, where the spec lives, and what
  "done" means. They sit in the system prompt on every model call and survive compaction.
- **Skills** (`skills/<name>/SKILL.md`, opened on demand by opencode's `skill` tool): how to do
  recurring kinds of work well. Text only, from `data/skills/`, picked per role and stack.
- **Project files** (`project/SPEC.md`, `DESIGN.md`, `CARDS.md`, `STACK.md`): the spec, design
  and board as they are at spawn, for the worker to read when it needs them.

Everything here is written by the host into the tool directory, which is read-only to the agent
(`/work/.guudflow` in a sandbox). opencode's own project config, Claude-compatible files and
external skills stay switched off (`server.LOCKED_FLAGS`), so nothing an agent writes becomes an
instruction.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from core.spec import Spec, Stack

RULES_DIR = "rules"
RULES_FILE = "guudflow.md"
SKILLS_DIR = "skills"
PROJECT_DIR = "project"
SKILL_ROOT = Path(__file__).resolve().parents[2] / "data" / "skills"

# Folders that say nothing about the project's own code.
_SKIP = {".git", ".guudflow", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
         ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next", ".cache", "coverage",
         ".idea", ".vscode", ".opencode"}
_MANIFESTS = ("package.json", "requirements.txt", "pyproject.toml", "setup.py", "Cargo.toml",
              "go.mod", "bun.lockb", "tsconfig.json", "vite.config.ts", "vite.config.js")
_TEST_DIRS = ("tests", "test", "__tests__", "spec")
MAX_LAYOUT_LINES = 40

# ── skills ─────────────────────────────────────────────────────────────────────
_BY_ROLE: dict[str, tuple[str, ...]] = {
    "planner": ("orient", "write-cards", "when-to-block"),
    "architect": ("orient", "write-cards", "when-to-block"),
    "builder": ("orient", "plan-then-edit", "test-first", "close-a-check", "resolve-conflict",
                "when-to-block"),
    "frontend_builder": ("orient", "plan-then-edit", "test-first", "close-a-check",
                         "resolve-conflict", "when-to-block"),
    "tester": ("orient", "test-first", "close-a-check", "resolve-conflict", "when-to-block"),
    "reviewer": ("orient", "review-a-card"),
    "researcher": ("orient", "when-to-block"),
    "it_agent": ("orient",),
    "overseer": ("orient",),
}
# Stack skills, picked by words in the stack's language and framework (first match wins).
_STACK_SKILLS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"pygame", re.I), "python-pygame"),
    (re.compile(r"\bbun\b", re.I), "typescript-bun"),
    (re.compile(r"vitest", re.I), "node-vitest"),
    (re.compile(r"python", re.I), "python-pytest"),
)
_WRITES_CODE = {"builder", "frontend_builder", "tester", "reviewer", "planner", "architect"}


def available_skills() -> dict[str, str]:
    """Skill name → its one-line description, for every skill in the library."""
    out: dict[str, str] = {}
    if not SKILL_ROOT.is_dir():
        return out
    for folder in sorted(SKILL_ROOT.iterdir()):
        md = folder / "SKILL.md"
        if md.is_file():
            found = re.search(r"^description:\s*(.+)$", md.read_text(encoding="utf-8"), re.M)
            out[folder.name] = found.group(1).strip() if found else ""
    return out


def skills_for(role_type: str, stack: Stack | None = None,
               extra: Sequence[str] = ()) -> list[str]:
    """The skills a role is offered: its role type's set, the stack's skill and any extra."""
    names = list(_BY_ROLE.get(role_type, ("orient",)))
    if stack is not None and role_type in _WRITES_CODE:
        text = f"{stack.language} {stack.framework} {stack.test_command}"
        for pattern, skill in _STACK_SKILLS:
            if pattern.search(text):
                names.append(skill)
                break
    names += list(extra)
    have = available_skills()
    return [n for n in dict.fromkeys(names) if n in have]


def write_skills(tool_dir: Path, names: Sequence[str],
                 project_skills: Path | None = None) -> Path:
    """Copy the chosen skills into `<tool_dir>/skills` (replacing what was there) and return
    the folder. Project skills a person approved (`data/projects/{id}/skills`) are copied too."""
    dest = Path(tool_dir) / SKILLS_DIR
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in names:
        src = SKILL_ROOT / name / "SKILL.md"
        if src.is_file():
            (dest / name).mkdir()
            shutil.copyfile(src, dest / name / "SKILL.md")
    if project_skills is not None and project_skills.is_dir():
        for folder in sorted(project_skills.iterdir()):
            md = folder / "SKILL.md"
            if md.is_file() and not md.is_symlink() and re.fullmatch(r"[a-z0-9-]{2,40}",
                                                                      folder.name):
                (dest / folder.name).mkdir(exist_ok=True)
                shutil.copyfile(md, dest / folder.name / "SKILL.md")
    return dest


def project_skill_names(project_skills: Path | None) -> list[str]:
    """Names of the project's approved skills."""
    if project_skills is None or not project_skills.is_dir():
        return []
    return sorted(f.name for f in project_skills.iterdir()
                  if (f / "SKILL.md").is_file() and re.fullmatch(r"[a-z0-9-]{2,40}", f.name))


# ── where you are (plan E1) ────────────────────────────────────────────────────
def _entries(root: Path, rel: Path, depth: int) -> Iterable[tuple[Path, bool]]:
    try:
        items = sorted(os.scandir(root / rel), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        return
    for entry in items:
        if entry.name in _SKIP or entry.is_symlink():
            continue
        if entry.name.startswith(".") and entry.name not in (".github",):
            continue
        is_dir = entry.is_dir()
        yield rel / entry.name, is_dir
        if is_dir and depth > 1:
            yield from _entries(root, rel / entry.name, depth - 1)


def layout(worktree: Path, depth: int = 2) -> list[str]:
    """The project's top-level layout to `depth`, folders with a trailing slash, capped."""
    lines: list[str] = []
    counts: dict[Path, int] = {}
    for path, is_dir in _entries(worktree, Path(), depth):
        parent = path.parent
        counts[parent] = counts.get(parent, 0) + 1
        if counts[parent] > 12:
            if counts[parent] == 13:
                lines.append("  " * (len(path.parts) - 1) + "- …")
            continue
        lines.append("  " * (len(path.parts) - 1) + f"- {path.name}{'/' if is_dir else ''}")
        if len(lines) >= MAX_LAYOUT_LINES:
            lines.append("- … (more)")
            break
    return lines


def where_you_are(worktree: Path, *, stack: Stack | None = None,
                  check_command: str = "", sandbox: bool = True) -> str:
    """The "Where you are" rules section: the root, the layout, manifests, tests and the check
    command, so a worker does not spend its first calls rebuilding them."""
    root = "/work" if sandbox else str(worktree)
    parts = [f"You are at the project root, `{root}`. Paths in cards and briefs are relative to it."]
    lines = layout(worktree)
    if lines:
        parts.append("Layout (generated by GuudFlow at the start of this turn):\n" + "\n".join(lines))
    else:
        parts.append("The project has no files yet: you are starting it.")
    manifests = [m for m in _MANIFESTS if (worktree / m).is_file()]
    if manifests:
        parts.append("Manifests: " + ", ".join(f"`{m}`" for m in manifests))
    tests = [d for d in _TEST_DIRS if (worktree / d).is_dir()]
    if stack is not None and stack.test_dir and stack.test_dir not in tests:
        tests.insert(0, stack.test_dir)
    if tests:
        parts.append("Tests live in: " + ", ".join(f"`{t}/`" for t in tests))
    command = check_command or (stack.test_command if stack is not None else "")
    if command:
        parts.append(f"The check command: `{command}`. Run it before you finish.")
    parts.append("Start from the files your card or review names. Do not sweep the whole tree; "
                 "the `orient` skill says how to look around in a few calls.")
    return "\n\n".join(parts)


# ── jobs (plan C1) ─────────────────────────────────────────────────────────────
PLANNER_JOB = (
    "You are the team's tech lead and you plan every session.\n"
    "- Every session starts with your turn: you write every card the team works on, each with "
    "the checks it closes (`serves`). Your result is the card list, never code.\n"
    "- You never write or edit product code. Your edits are limited to `docs/`; the design goes "
    "in your result.\n"
    "- When a card is blocked on you, answer it by changing cards: reword, split, add the "
    "missing piece, or decide the open question in `notes`. Do not re-plan the session.\n"
    "- The `write-cards` skill is your checklist.")
_JOBS: dict[str, str] = {
    "builder": "You build the card you are given: code and its tests, in the stack, and nothing "
               "outside the card. You finish with `guudflow_submit_result`.",
    "frontend_builder": "You build the card you are given (the user interface): code and its "
                        "tests, in the stack, and nothing outside the card.",
    "tester": "You prove the card's checks with tests you run. You add tests; you change product "
              "code only when the card says so.",
    "reviewer": "You review one card's change at a time and answer every line of its checklist "
                "with evidence. You never edit files.",
    "researcher": "You research what the card asks and write your findings where it says.",
}


def job_text(role_type: str, *, planner: bool, description: str = "") -> str | None:
    """The fixed job for the role type; the team's own description follows as "Also"."""
    base = PLANNER_JOB if planner else _JOBS.get(role_type)
    if base is None:
        return None
    extra = description.strip()
    return base + (f"\n\nAlso, from the team: {extra}" if extra and planner else "")


# ── project files (plan G1) ────────────────────────────────────────────────────
@dataclass
class ProjectFiles:
    """What goes into `.guudflow/project/`."""

    spec: Spec | None = None
    design_text: str | None = None
    cards: Sequence[Mapping[str, object]] = field(default_factory=list)


def _cards_md(cards: Sequence[Mapping[str, object]]) -> str:
    if not cards:
        return "# Cards\n\nNo cards in this session yet.\n"
    lines = ["# Cards of this session", ""]
    for c in cards:
        serves = ", ".join(map(str, c.get("serves") or [])) or "—"
        waits = ", ".join(map(str, c.get("depends_on") or []))
        lines.append(f"## {c.get('id')} {c.get('title')}")
        lines.append(f"- owner: {c.get('owner')} · column: {c.get('column')} · serves: {serves}"
                     + (f" · waits on: {waits}" if waits else ""))
        files = c.get("files") or []
        if files:
            lines.append("- files: " + ", ".join(f"`{f}`" for f in files))  # type: ignore[union-attr]
        for a in c.get("acceptance") or []:  # type: ignore[union-attr]
            lines.append(f"  - [ ] {a}")
        lines.append("")
    return "\n".join(lines)


def write_project_files(tool_dir: Path, files: ProjectFiles) -> Path:
    """Write SPEC.md, DESIGN.md, CARDS.md and STACK.md into `<tool_dir>/project`."""
    dest = Path(tool_dir) / PROJECT_DIR
    dest.mkdir(parents=True, exist_ok=True)
    spec = files.spec or Spec({})
    body = spec.render(limit=500)
    (dest / "SPEC.md").write_text(
        "# The spec\n\nRequirements (with their checks and states), decisions and rules, as "
        "approved. Look an item up by id with `guudflow_spec`.\n\n"
        + (body or "The project has no spec yet.") + "\n", encoding="utf-8")
    (dest / "DESIGN.md").write_text("# The design\n\n" + (files.design_text or
                                                          "There is no design yet.") + "\n",
                                    encoding="utf-8")
    (dest / "CARDS.md").write_text(_cards_md(files.cards), encoding="utf-8")
    stack = spec.stack
    (dest / "STACK.md").write_text("# The stack\n\n" + ("\n".join(stack.lines()) if not stack.empty
                                                       else "Not set yet.") + "\n",
                                   encoding="utf-8")
    return dest


# ── the rules file ─────────────────────────────────────────────────────────────
def rules_text(*, role: str, name: str, role_type: str, planner: bool,
               ground_rules: str | None, where: str, skills: Sequence[str],
               description: str = "", project_notes: str | None = None) -> str:
    """The role's rules file (`rules/guudflow.md`)."""
    who = f"{name} ({role})" if name and name != role else role
    parts = [f"# GuudFlow rules for {who}",
             "These rules hold for every turn. Your brief gives this turn's work."]
    job = job_text(role_type, planner=planner, description=description)
    if job:
        parts.append("## Your job\n" + job)
    parts.append("## Ground rules\n" + (ground_rules or "No stack or rules are set for this "
                                         "project yet. Follow the existing code."))
    if project_notes and project_notes.strip():
        parts.append("## The project's AGENTS.md (written by a person)\n"
                     + project_notes.strip()[:6000])
    parts.append("## Where you are\n" + where)
    parts.append(
        "## The spec, design and cards\n"
        "Read them instead of guessing or grepping the code:\n"
        "- `.guudflow/project/SPEC.md`: every requirement with its checks, decisions and rules;\n"
        "- `.guudflow/project/DESIGN.md`: components, interfaces, technical decisions;\n"
        "- `.guudflow/project/CARDS.md`: this session's cards, owners and what they wait on;\n"
        "- `.guudflow/project/STACK.md`: the stack.\n"
        "`guudflow_spec` looks one up by id (`R3.2`, `D2`, `rule-1`, `T4`).")
    if skills:
        descriptions = available_skills()
        parts.append("## Skills\nOpen a skill with the `skill` tool when its work comes up:\n"
                     + "\n".join(f"- `{s}`: {descriptions.get(s, '')}" for s in skills))
    parts.append(
        "## Done means\n"
        "- the card's acceptance items and `R#.#` checks are met, each with evidence you saw;\n"
        "- the check command was run in this turn and its result is in your summary;\n"
        "- only the stack's language and tools were used;\n"
        "- you finished with `guudflow_submit_result` (status done, blocked or failed).")
    return "\n\n".join(parts) + "\n"


def write_rules(tool_dir: Path, text: str) -> Path:
    """Write the rules file and return its path."""
    dest = Path(tool_dir) / RULES_DIR
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / RULES_FILE
    path.write_text(text, encoding="utf-8")
    return path


__all__ = ["PLANNER_JOB", "ProjectFiles", "available_skills", "job_text", "layout",
           "project_skill_names", "rules_text", "skills_for", "where_you_are", "write_project_files",
           "write_rules", "write_skills"]
