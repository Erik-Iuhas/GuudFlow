"""RubricScorer — post-run training rubric evaluation (DESIGN §35).

When a run is in `mode == "training"` and the project carries a
`training_scenario_id`, the runner triggers `RubricScorer.on_run_complete`
after the scheduler finishes. The scorer:

  1. Loads the scenario's rubric.json from disk via `core.training.scenario`
  2. Evaluates each `auto_check` against the run's working/ workspace
  3. Computes a weighted score in [0, 1]
  4. Posts a single `RUBRIC_SCORE` signal with summary + detail JSON

No LLM call. No scheduler involvement. Scoring failures (missing
scenario dir, malformed rubric) are surfaced as a RUBRIC_SCORE signal
with score=0 and an error in the detail — the run itself shouldn't
fail because the scorer couldn't read its inputs.

Phase F.1.1 ships two `auto_check` kinds:
  - `files_present` — every path in `params.paths` exists in working/
  - `text_contains` — `params.path` exists AND contains `params.needle`

Subprocess-based checks (pytest, ruff) need sandboxed execution and
are deferred to a follow-up phase.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.base import BaseAgent
from core.signals import Signal, SignalType
from core.training.scenario import (
    AutoCheck,
    Scenario,
    ScenarioError,
    load_scenario,
)


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
    """Reads rubric.json + workspace.working/, posts RUBRIC_SCORE.

    Constructed by the runner; the runner calls `on_run_complete` after
    the scheduler finishes when the project is in training mode.
    """

    def __init__(self, **kwargs):
        super().__init__(
            role=kwargs.pop("role", "rubric_scorer"),
            description="Post-run training rubric scorer.",
            **kwargs,
        )

    async def on_run_complete(
        self,
        workspace,
        project,
        run_id: str,
        run_root: Path,
        scenarios_root: Path,
    ) -> RubricResult | None:
        """Evaluate the project's training scenario against the run's
        workspace and post RUBRIC_SCORE. Returns the structured result
        for callers (the tournament launcher) that want to read the
        score without re-parsing the board signal.
        """
        scenario_id = getattr(project, "training_scenario_id", None)
        if not scenario_id:
            return None

        scenario_dir = Path(scenarios_root) / scenario_id
        try:
            scenario = load_scenario(scenario_dir)
        except ScenarioError as e:
            workspace.post_signal(Signal(
                type=SignalType.RUBRIC_SCORE,
                summary=f"rubric scoring failed: {e}",
                from_agent=self.role,
                detail=json.dumps({"score": 0.0, "error": str(e),
                                   "scenario_id": scenario_id}),
            ))
            return RubricResult(scenario_id=scenario_id, score=0.0, checks=[])

        results = await _evaluate_rubric(scenario, workspace)
        score = _weighted_score(results, scenario.rubric.total_auto_weight)

        workspace.post_signal(Signal(
            type=SignalType.RUBRIC_SCORE,
            summary=f"rubric score: {score:.2f} ({scenario_id})",
            from_agent=self.role,
            detail=json.dumps({
                "score": score,
                "scenario_id": scenario_id,
                "checks": [
                    {"name": r.name, "passed": r.passed, "weight": r.weight,
                     "detail": r.detail}
                    for r in results
                ],
            }),
        ))
        return RubricResult(scenario_id=scenario_id, score=score, checks=results)


# ── Check evaluators ──────────────────────────────────────────────────────


async def _evaluate_rubric(scenario: Scenario, workspace) -> list[CheckResult]:
    out: list[CheckResult] = []
    for check in scenario.rubric.auto_checks:
        out.append(await _evaluate_check(check, workspace))
    return out


async def _evaluate_check(check: AutoCheck, workspace) -> CheckResult:
    if check.kind == "files_present":
        passed, detail = await _check_files_present(check.params, workspace)
    elif check.kind == "text_contains":
        passed, detail = await _check_text_contains(check.params, workspace)
    else:
        passed, detail = False, f"unsupported check kind: {check.kind}"
    return CheckResult(name=check.name, weight=check.weight,
                       passed=passed, detail=detail)


async def _check_files_present(
    params: dict[str, Any], workspace
) -> tuple[bool, str]:
    paths = params.get("paths") or []
    if not isinstance(paths, list) or not paths:
        return False, "no paths specified"
    files = getattr(workspace, "files", None)
    missing: list[str] = []
    for p in paths:
        ok = False
        try:
            if files is not None:
                ok = await files.exists(str(p))
        except Exception:
            ok = False
        if not ok:
            missing.append(str(p))
    if missing:
        return False, f"missing: {', '.join(missing)}"
    return True, "all present"


async def _check_text_contains(
    params: dict[str, Any], workspace
) -> tuple[bool, str]:
    path = params.get("path")
    needle = params.get("needle")
    if not isinstance(path, str) or not isinstance(needle, str):
        return False, "path/needle missing"
    files = getattr(workspace, "files", None)
    if files is None:
        return False, "workspace has no file layer"
    try:
        content = await files.read(path)
    except Exception as e:
        return False, f"read failed: {e}"
    if needle in content:
        return True, "matched"
    return False, "no match"


def _weighted_score(results: list[CheckResult], total_weight: float) -> float:
    """Weighted score in [0, 1]. If total_weight is zero the score is
    zero — guards against divide-by-zero on a malformed rubric (the
    loader already rejects this case, so it's defence in depth)."""
    if total_weight <= 0:
        return 0.0
    earned = sum(r.weight for r in results if r.passed)
    return round(earned / total_weight, 4)
