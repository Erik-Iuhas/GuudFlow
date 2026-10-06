"""guudflow oneshot — run the default team on one goal, in this process.

    python -m cli.oneshot "Build a typed Python client for the Open-Meteo API with tests" \\
        --web researcher --budget 5 --demo [--example api-client-library] \\
        [--sandbox auto|docker|host]

`scripts/guudflow oneshot ...` is a thin shell wrapper around the same module
(the repo has no pyproject, so there is no console-script entry point).

The run is in-process with `AgentRunner` (no queue, no API). Board events print
live, one line each (`type  role  summary`), and at the end the run summary and
the per-role cost table print. `--budget` is the gateway's spend cap in USD;
`--demo` auto-approves the team; `--sandbox host` runs without containers
(trusted local demos only, refused when GUUDFLOW_ENV=production). Model keys
come from the environment, as for any run; they never go into the project.

Exit status: 0 when the run completed, 1 otherwise, 2 on bad arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from core.harness.run import HarnessSettings
from core.oneshot import (
    OneShotRequest,
    build_oneshot_project,
    oneshot_summary,
    render_cost_table,
    render_summary,
    seed_example_inputs,
)
from core.runner import AgentRunner
from core.scheduler import SchedulerStatus
from core.signals import Signal


def build_parser() -> argparse.ArgumentParser:
    """The argument parser for `python -m cli.oneshot`."""
    p = argparse.ArgumentParser(prog="guudflow oneshot", description=__doc__.split("\n\n")[0])
    p.add_argument("goal", help="what to build, in plain language")
    p.add_argument("--web", choices=["researcher", "off"], default="researcher",
                   help="researcher: include the web-enabled researcher (default); off: no web access")
    p.add_argument("--budget", type=float, default=5.0, help="spend cap in USD (default 5)")
    p.add_argument("--demo", action="store_true", help="auto-approve the team and its capabilities")
    p.add_argument("--example", default=None, metavar="NAME",
                   help="seed the run's inputs from data/example_projects/NAME (e.g. api-client-library)")
    p.add_argument("--sandbox", choices=["auto", "docker", "host"], default=None,
                   help="where agents run (default $GUUDFLOW_SANDBOX, else auto: docker "
                        "when available)")
    p.add_argument("--run-dir", default=None, help="run directory (default ./data/runs/<run id>)")
    p.add_argument("--max-minutes", type=float, default=60.0, help="wall-clock limit (default 60)")
    return p


def _print_event(sig: Signal) -> None:
    """One line per board signal: type, role, summary."""
    who = sig.from_agent or "-"
    print(f"[{sig.type.value}] {who}: {(sig.summary or '').strip()[:200]}", flush=True)


async def run_oneshot(args: argparse.Namespace, settings: HarnessSettings | None = None) -> int:
    """Build the project from `args`, run it, print the summary. Returns the exit code."""
    req = OneShotRequest(goal=args.goal, web=args.web, budget_usd=args.budget, demo=args.demo)
    project = build_oneshot_project(req, example=args.example)
    run_id = f"run_{uuid4().hex[:10]}"
    run_root = Path(args.run_dir) if args.run_dir else Path("data") / "runs" / run_id
    runner = AgentRunner(project=project, run_id=run_id, run_root=run_root,
                         max_wall_seconds=args.max_minutes * 60)
    runner.harness_settings = settings or (
        HarnessSettings(sandbox=args.sandbox) if args.sandbox else HarnessSettings())
    if args.example:
        seeded = seed_example_inputs(args.example, run_root)
        print(f"Seeded {len(seeded)} input file(s) from example {args.example!r}", flush=True)

    print(f"Project {project.project_id}, run {run_id}, team: "
          f"{', '.join(a.role for a in project.agents)}, budget ${args.budget:g}", flush=True)
    print(f"Run directory: {run_root}", flush=True)
    unsubscribe = runner.workspace.subscribe(_print_event)
    try:
        result = await runner.run()
    finally:
        unsubscribe()

    summary = oneshot_summary(run_root, result)
    print("\n" + render_summary(summary))
    print("\nCost per role:\n" + render_cost_table(summary["costs"]))
    return 0 if result.status is SchedulerStatus.COMPLETE else 1


def main(argv: list[str] | None = None, *, settings: HarnessSettings | None = None) -> int:
    """CLI entry point. `settings` lets tests inject a fake-LLM `HarnessSettings`."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        # Validate before any work starts so bad input exits 2 with a clear message.
        build_oneshot_project(OneShotRequest(goal=args.goal, web=args.web, budget_usd=args.budget,
                                             demo=args.demo), example=args.example)
    except ValueError as exc:
        parser.error(str(exc))
    return asyncio.run(run_oneshot(args, settings))


if __name__ == "__main__":
    sys.exit(main())
