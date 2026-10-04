"""The generic one-shot: one goal in, a sensible team runs it end to end.

docs/OPENCODE_HARNESS_PLAN.md section 10. This module is the shared core of the
two entry points (`cli/oneshot.py` and `POST /api/oneshot`):

  - `build_oneshot_project` loads the default team (`data/teams/default_oneshot.json`)
    and returns a `ProjectConfig` for the opencode harness, with the budget and
    the web choice applied.
  - `summarise_costs` / `render_cost_table` turn the gateway's cost log
    (`{run_root}/llm_costs.jsonl`) into a per-role breakdown.
  - `oneshot_summary` collects what a run produced: status, cards by column,
    merged files and costs.
  - `seed_example_inputs` seeds a run's `inputs/` from an example project.

API keys never appear in the project: the gateway holds them on the host.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from core.example_projects import ExampleProjectError, load_example_project
from core.runner import AgentConfig, ProjectConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEAM_PATH = REPO_ROOT / "data" / "teams" / "default_oneshot.json"
EXAMPLES_ROOT = REPO_ROOT / "data" / "example_projects"
RESEARCHER_ROLE = "researcher"
MAX_BUDGET_USD = 1000.0

WebMode = Literal["researcher", "off"]
WEB_MODES: tuple[str, ...] = ("researcher", "off")

# Card columns in board order (plan section 4.6).
COLUMNS: tuple[str, ...] = ("todo", "in_progress", "review", "done", "blocked", "needs_attention")


@dataclass
class OneShotRequest:
    """What the user asked for. Shared by the CLI and the API."""

    goal: str
    web: WebMode = "researcher"
    budget_usd: float = 5.0
    demo: bool = False
    repo: str | None = None
    project_id: str | None = None

    def validate(self) -> None:
        """Raise ValueError when the request cannot be run."""
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise ValueError("goal must be a non-empty string")
        if self.web not in WEB_MODES:
            raise ValueError(f"web must be one of {list(WEB_MODES)}, got {self.web!r}")
        budget = self.budget_usd
        if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget):
            raise ValueError("budget must be a number")
        if not 0 < budget <= MAX_BUDGET_USD:
            raise ValueError(f"budget must be greater than 0 and at most {MAX_BUDGET_USD:g} USD")
        if self.project_id is not None and not self.project_id.strip():
            raise ValueError("project_id cannot be empty")


def load_team(team_path: Path | str = DEFAULT_TEAM_PATH) -> dict[str, Any]:
    """Read a team file; raises ValueError when it has no agents."""
    data = json.loads(Path(team_path).read_text(encoding="utf-8"))
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError(f"team file {team_path} has no agents")
    return data


def _agent_from_dict(raw: dict[str, Any]) -> AgentConfig:
    return AgentConfig(
        role=str(raw["role"]),
        description=str(raw.get("description", "")),
        depends_on=[str(d) for d in raw.get("depends_on") or []],
        model_tier=str(raw.get("model_tier", "solid")),
        is_reviewer=bool(raw.get("is_reviewer", False)),
        role_type=str(raw.get("role_type", "")),
        capabilities={str(k): str(v) for k, v in (raw.get("capabilities") or {}).items()},
        review_of=[str(r) for r in raw.get("review_of") or []],
        max_review_rounds=int(raw.get("max_review_rounds", 2)),
    )


def build_oneshot_project(
    req: OneShotRequest,
    *,
    team_path: Path | str = DEFAULT_TEAM_PATH,
    example: str | None = None,
) -> ProjectConfig:
    """Build the one-shot `ProjectConfig` for `req`.

    `web="off"` drops the researcher (and removes it from every `depends_on`).
    The budget becomes `approval_gates["token_budget_usd"]`, which the gateway
    enforces. `demo=True` also turns off the pre-run cost preview: demo means the
    team is auto-approved. `example` is the slug of a project under
    `data/example_projects/`; it is validated and recorded in
    `approval_gates["example"]` (the CLI seeds its inputs, see `seed_example_inputs`).
    """
    req.validate()
    if example is not None:
        _example_dir(example)
    team = load_team(team_path)
    raw_agents = [dict(a) for a in team["agents"]]
    if req.web == "off":
        raw_agents = [a for a in raw_agents if a["role"] != RESEARCHER_ROLE]
        for a in raw_agents:
            a["depends_on"] = [d for d in a.get("depends_on") or [] if d != RESEARCHER_ROLE]
    agents = [_agent_from_dict(a) for a in raw_agents]

    project = ProjectConfig(
        project_id=(req.project_id or f"oneshot-{uuid4().hex[:8]}").strip(),
        goal=req.goal.strip(),
        agents=agents,
        harness="opencode",
    )
    project.approval_gates["token_budget_usd"] = float(req.budget_usd)
    project.approval_gates["demo"] = bool(req.demo)
    project.approval_gates["team_id"] = str(team.get("team_id", "default_oneshot"))
    if req.demo:
        project.approval_gates["pre_run_cost_preview"] = False
    if example is not None:
        project.approval_gates["example"] = example
    if req.repo:
        project.approval_gates["repo"] = req.repo
    return project


def project_to_dict(project: ProjectConfig) -> dict[str, Any]:
    """The stored-project dict for `project` (the shape `serialise_project` reads)."""
    data = asdict(project)
    return json.loads(json.dumps(data, default=str))


# ── Example seeding ────────────────────────────────────────────────────────


def _example_dir(slug: str) -> Path:
    """Resolve an example slug to its template dir; ValueError when unknown."""
    if not slug or "/" in slug or "\\" in slug or slug.startswith((".", "_")):
        raise ValueError(f"invalid example name {slug!r}")
    path = EXAMPLES_ROOT / slug
    try:
        load_example_project(path)
    except ExampleProjectError as exc:
        raise ValueError(f"unknown example {slug!r}: {exc}") from exc
    return path


def seed_example_inputs(slug: str, run_root: Path) -> list[str]:
    """Copy an example project's `seed_inputs/` into the run's `inputs/uploads/`.

    Same destination as the example-projects API (`uploads/`). Returns the
    relative paths copied (empty when the example ships no seed inputs), and
    refreshes `inputs/README.md` when anything was copied.
    """
    from core import run_initializer

    example = load_example_project(_example_dir(slug))
    if example.seed_inputs_dir is None:
        return []
    inputs = Path(run_root) / "workspace" / "inputs"
    uploads = inputs / "uploads"
    copied: list[str] = []
    for src in sorted(example.seed_inputs_dir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(example.seed_inputs_dir)
        dst = uploads / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel.as_posix())
    if copied:
        run_initializer.generate_inputs_readme(inputs)
    return copied


# ── Costs ──────────────────────────────────────────────────────────────────

TOTAL_ROLE = "TOTAL"


def summarise_costs(run_root: Path | str) -> list[dict[str, Any]]:
    """Per-role spend from `{run_root}/llm_costs.jsonl`, plus a total row.

    Each row: role, calls, input_tokens, output_tokens, cache_read_tokens,
    images, cost_usd. Roles keep the order of their first call. Returns an empty
    list when the log is missing or empty; malformed lines are skipped.
    """
    path = Path(run_root) / "llm_costs.jsonl"
    if not path.is_file():
        return []
    by_role: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        row = by_role.setdefault(str(rec.get("role") or "unknown"), _empty_row(str(rec.get("role") or "unknown")))
        row["calls"] += 1
        for key in ("input_tokens", "output_tokens", "cache_read_tokens", "images"):
            row[key] += int(rec.get(key) or 0)
        row["cost_usd"] += float(rec.get("cost_usd") or 0.0)
    if not by_role:
        return []
    rows = list(by_role.values())
    total = _empty_row(TOTAL_ROLE)
    for row in rows:
        for key in ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "images", "cost_usd"):
            total[key] += row[key]
    for row in (*rows, total):
        row["cost_usd"] = round(row["cost_usd"], 6)
    return [*rows, total]


def _empty_row(role: str) -> dict[str, Any]:
    return {"role": role, "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "images": 0, "cost_usd": 0.0}


def render_cost_table(rows: list[dict[str, Any]]) -> str:
    """Fixed-width text table for `summarise_costs` rows."""
    if not rows:
        return "No model calls recorded."
    headers = ("role", "calls", "input", "output", "images", "cost (USD)")
    body = [(str(r["role"]), str(r["calls"]), f"{r['input_tokens']:,}", f"{r['output_tokens']:,}",
             str(r["images"]), f"${r['cost_usd']:.4f}") for r in rows]
    widths = [max(len(h), *(len(b[i]) for b in body)) for i, h in enumerate(headers)]

    def fmt(cells: tuple[str, ...]) -> str:
        first = cells[0].ljust(widths[0])
        rest = [c.rjust(widths[i + 1]) for i, c in enumerate(cells[1:])]
        return "  ".join([first, *rest])

    rule = "  ".join("-" * w for w in widths)
    lines = [fmt(headers), rule]
    for i, cells in enumerate(body):
        if rows[i]["role"] == TOTAL_ROLE:
            lines.append(rule)
        lines.append(fmt(cells))
    return "\n".join(lines)


# ── Run summary ────────────────────────────────────────────────────────────


def _cards_by_column(run_root: Path) -> dict[str, list[dict[str, str]]]:
    """Final state of each card from `tasks.jsonl` (the last record per task wins)."""
    path = run_root / "tasks.jsonl"
    latest: dict[str, dict[str, Any]] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("task_id") and not str(rec["task_id"]).startswith("root:"):
                latest[str(rec["task_id"])] = rec
    cards: dict[str, list[dict[str, str]]] = {}
    for rec in latest.values():
        column = str(rec.get("column") or "todo")
        cards.setdefault(column, []).append({
            "id": str(rec["task_id"]),
            "title": str(rec.get("title") or rec.get("goal") or ""),
            "owner": str(rec.get("assigned_to") or ""),
        })
    return {c: sorted(cards[c], key=lambda x: x["id"])
            for c in (*COLUMNS, *sorted(set(cards) - set(COLUMNS))) if c in cards}


def _merged_files(run_root: Path) -> list[str]:
    """Tracked files of the run's working repo (`git ls-files`); empty when unavailable."""
    working = run_root / "workspace" / "working"
    if not (working / ".git").exists():
        return []
    try:
        out = subprocess.run(["git", "ls-files"], cwd=working, check=True,
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted(line for line in out.splitlines() if line)


def oneshot_summary(run_root: Path | str, result: Any) -> dict[str, Any]:
    """What the run produced.

    `result` is the runner's `SchedulerResult` (anything with `status`,
    `completed` and `failed`). Returns status, completed/failed roles, cards by
    column, merged files and the cost rows.
    """
    root = Path(run_root)
    status = getattr(result.status, "value", result.status)
    return {
        "status": str(status),
        "completed": list(result.completed),
        "failed": list(result.failed),
        "cards": _cards_by_column(root),
        "files": _merged_files(root),
        "costs": summarise_costs(root),
    }


def render_summary(summary: dict[str, Any]) -> str:
    """Human-readable text for `oneshot_summary` output (used by the CLI)."""
    lines = [f"Status: {summary['status']}",
             f"Completed roles: {', '.join(summary['completed']) or '-'}"]
    if summary["failed"]:
        lines.append(f"Failed roles: {', '.join(summary['failed'])}")
    if summary["cards"]:
        lines.append("Cards:")
        for column, cards in summary["cards"].items():
            for card in cards:
                lines.append(f"  [{column}] {card['id']} {card['title']} ({card['owner']})")
    if summary["files"]:
        lines.append(f"Files in the merged repo ({len(summary['files'])}):")
        lines.extend(f"  {f}" for f in summary["files"])
    return "\n".join(lines)
