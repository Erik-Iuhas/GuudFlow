"""RubricScorer — training-mode auto-grader.

DESIGN §35. In training mode, after the run completes, the rubric scorer
executes the scenario's `auto_checks` against the workspace and
posts a `RUBRIC_SCORE` signal. Manual checks are surfaced in the UI for
the human to score.

Stage 5 ships the auto-check evaluator for two simple check types:
  - `files_present` — verify a list of paths exist in working/
  - `text_match` — verify a file contains a regex
Other types (pytest, ruff) are wired via `code:exec` calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.base import BaseAgent


@dataclass
class CheckResult:
    name: str
    weight: float
    passed: bool
    detail: str = ""


@dataclass
class RubricResult:
    scenario_id: str
    score: float  # 0..1
    checks: list[CheckResult] = field(default_factory=list)


class RubricScorer(BaseAgent):
    role: str = "rubric_scorer"

    def __init__(self, **kwargs):
        super().__init__(
            role=kwargs.pop("role", "rubric_scorer"),
            description="Auto-grades a training run against a scenario rubric.",
            **kwargs,
        )

    async def score(self, workspace, scenario: dict[str, Any]) -> RubricResult:
        """Run all `auto_checks` in the scenario rubric. Returns the
        weighted result. The runner persists this and updates training
        reputation."""
        scenario_id = scenario.get("id", "unknown")
        auto_checks = scenario.get("auto_checks", []) or []
        results: list[CheckResult] = []

        for check in auto_checks:
            name = check.get("name", "anon")
            weight = float(check.get("weight", 0.0))
            kind = check.get("type", "")

            if kind == "files_present":
                paths = check.get("paths", []) or []
                missing: list[str] = []
                for p in paths:
                    try:
                        ok = await workspace.files.exists(p)
                    except Exception:
                        ok = False
                    if not ok:
                        missing.append(p)
                passed = not missing
                detail = "all present" if passed else f"missing: {', '.join(missing)}"
            elif kind == "text_match":
                path = check.get("path", "")
                pattern = check.get("pattern", "")
                try:
                    content = await workspace.files.read(path)
                    passed = bool(re.search(pattern, content))
                    detail = "matched" if passed else "no match"
                except Exception as e:
                    passed = False
                    detail = f"read failed: {e}"
            else:
                # Defer to code:exec-style handling later (pytest, ruff).
                passed = False
                detail = f"unsupported auto_check type: {kind}"

            results.append(CheckResult(name=name, weight=weight, passed=passed, detail=detail))

        total_weight = sum(r.weight for r in results)
        if total_weight <= 0:
            score = 0.0
        else:
            score = sum(r.weight for r in results if r.passed) / total_weight

        return RubricResult(scenario_id=scenario_id, score=score, checks=results)
