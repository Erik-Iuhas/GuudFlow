"""The visual review loop for `visual` cards (plan §5.10, O24).

  1. Builder self-check: the brief tells the owner of a visual card to take a
     screenshot (`guudflow_screenshot`) and compare it to the card's
     `visual_refs` (`guudflow_visual_diff`) before submitting.
  2. Deterministic pre-check, no model: when the owner submits `done`, the
     runner compares the newest screenshot with each reference. Too different,
     or no screenshot at all → the card goes straight back with the numbers and
     a diff image; no reviewer turn is spent. After `MAX_PRECHECK_FAILS` the
     card goes to review anyway and the reviewer decides.
  3. Vision-gated review: the screenshot and diff are copied into the
     reviewer's read-only tool dir (`.guudflow/review/<card>/`); a reviewer with
     vision is told to look at them, one without is given the numbers only.
  4. Per-task baselines: a passed card's screenshot becomes
     `<run_root>/baselines/tasks/<card>.png` for later regression checks.
  5. Image caps: screenshots per role per turn and per run are capped
     (`ImageBudget`), so image tokens can't run away.
  6. Human annotations: visual feedback from Studio lands on the card's thread.
"""

from __future__ import annotations

import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from core.harness.mcp_server import McpIdentity, McpTool, ToolResult
from core.harness.mcp_visual import compare
from core.workspace import Task

DIFF_FAIL_FRACTION = 0.25      # more than a quarter of the pixels differ → back to the owner
MAX_PRECHECK_FAILS = 2         # then the reviewer decides
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


@dataclass
class PrecheckResult:
    """Outcome of the deterministic visual pre-check for one submission."""

    passed: bool
    detail: str
    screenshot: Path | None = None
    diff_image: Path | None = None
    fractions: dict[str, float] = field(default_factory=dict)


def latest_screenshot(tool_dir: Path) -> Path | None:
    """The newest screenshot the role took (tool dir `screens/`)."""
    screens = tool_dir / "screens"
    if not screens.is_dir():
        return None
    shots = [p for p in screens.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES and p.is_file()]
    return max(shots, key=lambda p: (p.stat().st_mtime, p.name)) if shots else None


def resolve_ref(ref: str, roots: list[Path]) -> Path | None:
    """Find a visual reference (e.g. `inputs/mockups/home.png`) under the given roots."""
    rel = Path(ref.strip().lstrip("/"))
    if ".." in rel.parts:
        return None
    for root in roots:
        for candidate in (root / rel, root / rel.relative_to("inputs") if rel.parts[:1] == ("inputs",)
                          else None):
            if candidate is not None and candidate.is_file() and not candidate.is_symlink():
                return candidate
    return None


def precheck(card: Task, tool_dir: Path, ref_roots: list[Path], out_dir: Path,
             threshold: float = DIFF_FAIL_FRACTION) -> PrecheckResult:
    """Compare the owner's newest screenshot to each of the card's visual refs."""
    shot = latest_screenshot(tool_dir)
    if shot is None:
        return PrecheckResult(False, "no screenshot was taken for this visual card: start the "
                                     "app, call guudflow_screenshot, compare it with the visual "
                                     "references, then submit again")
    if not card.visual_refs:
        return PrecheckResult(True, f"screenshot {shot.name} (no references to compare)", shot)
    out_dir.mkdir(parents=True, exist_ok=True)
    fractions: dict[str, float] = {}
    worst: tuple[float, Path | None] = (-1.0, None)
    missing = []
    with Image.open(shot) as current:
        current.load()
        for ref in card.visual_refs:
            ref_path = resolve_ref(ref, ref_roots)
            if ref_path is None:
                missing.append(ref)
                continue
            with Image.open(ref_path) as reference:
                reference.load()
                fraction, diff, _ = compare(current, reference)
            fractions[ref] = round(fraction, 4)
            diff_path = out_dir / f"diff_{len(fractions)}.png"
            diff.save(diff_path, format="PNG")
            if fraction > worst[0]:
                worst = (fraction, diff_path)
    if not fractions:
        return PrecheckResult(True, f"screenshot {shot.name}; references not found: "
                                    f"{', '.join(missing)}", shot)
    detail = "; ".join(f"{ref}: {frac:.0%} of pixels differ" for ref, frac in fractions.items())
    passed = worst[0] <= threshold
    return PrecheckResult(passed, detail + ("" if passed else
                                            f" (limit {threshold:.0%}); see the diff image"),
                          shot, worst[1], fractions)


def stage_for_reviewer(result: PrecheckResult, reviewer_tool_dir: Path, task_id: str) -> list[str]:
    """Copy screenshot and diff into the reviewer's read-only tool dir; returns agent paths."""
    dest = reviewer_tool_dir / "review" / task_id
    dest.mkdir(parents=True, exist_ok=True)
    paths = []
    for src, name in ((result.screenshot, "current"), (result.diff_image, "diff")):
        if src is not None and src.is_file():
            target = dest / f"{name}{src.suffix.lower()}"
            shutil.copyfile(src, target)
            paths.append(f".guudflow/review/{task_id}/{target.name}")
    return paths


def save_baseline(screenshot: Path | None, run_root: Path, task_id: str) -> Path | None:
    """A passed card's screenshot becomes its per-task baseline."""
    if screenshot is None or not screenshot.is_file():
        return None
    target = run_root / "baselines" / "tasks" / f"{task_id}{screenshot.suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(screenshot, target)
    return target


@dataclass
class ImageBudget:
    """Screenshot caps per role per turn and per run (§5.10 image caps)."""

    per_turn: int = 6
    per_run: int = 40
    _turn: dict[str, int] = field(default_factory=dict)
    _run: int = 0

    def new_turn(self, role: str) -> None:
        """Reset the per-turn count when a role's turn starts."""
        self._turn[role] = 0

    def take(self, role: str) -> str | None:
        """Count one image for `role`; returns why it is refused, or None."""
        if self._run >= self.per_run:
            return f"the run's image budget ({self.per_run} screenshots) is used up"
        if self._turn.get(role, 0) >= self.per_turn:
            return (f"you already took {self.per_turn} screenshots this turn; compare what you "
                    "have with guudflow_visual_diff instead")
        self._turn[role] = self._turn.get(role, 0) + 1
        self._run += 1
        return None


def capped(tool: McpTool, budget: ImageBudget) -> McpTool:
    """Wrap an image-producing tool (screenshot) with the image budget."""
    inner: Callable[[McpIdentity, dict[str, Any]], Awaitable[ToolResult]] = tool.handler

    async def handler(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:  # Any: tool args
        refused = budget.take(identity.role)
        if refused is not None:
            return ToolResult(refused, is_error=True)
        return await inner(identity, args)

    return McpTool(name=tool.name, description=tool.description,
                   input_schema=tool.input_schema, handler=handler)


def self_check_text(card: Task) -> str:
    """Brief addition for the owner of a visual card (builder self-check)."""
    refs = ", ".join(card.visual_refs) or "the card's description"
    return ("This is a visual card. Before you submit: start the app (bind 0.0.0.0), take a "
            "screenshot with `guudflow_screenshot`, compare it with "
            f"{refs} using `guudflow_visual_diff`, and fix what differs. The runner repeats "
            "this check: a screenshot that differs too much comes straight back to you.")


def reviewer_text(result: PrecheckResult, staged: list[str], vision: bool) -> str:
    """Brief addition for the reviewer of a visual card (vision-gated)."""
    head = f"Visual pre-check: {result.detail}."
    if not staged:
        return head
    if vision:
        return (f"{head} Look at the screenshot and the diff image with the read tool: "
                f"{', '.join(staged)}. Judge layout, text and states against the acceptance "
                "items; small anti-aliasing differences don't matter.")
    return (f"{head} You cannot view images; judge from the numbers above, the code and the "
            "tests.")
