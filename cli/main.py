"""GuudFlow CLI entry point.

Subcommand layout mirrors the studio's main views without trying to
reproduce every panel — one verb per common task. The CLI is a thin
client over the FastAPI backend; it never touches the data dir
directly.

Usage examples:
    python -m cli dashboard
    python -m cli projects
    python -m cli runs watch run_abc123
    python -m cli runs comment run_abc123 "ship it"
    python -m cli examples
    python -m cli examples use rougelike-pack
    python -m cli invoke my-project --confirm
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Callable

from cli.client import APIError, Client
from cli.render import (
    color,
    fmt_relative_time,
    fmt_status,
    render_dashboard,
    render_run_summary,
    render_signal_row,
    render_table,
)


# ── Output helpers ──────────────────────────────────────────────────────────


def _print(line: str = "") -> None:
    print(line)


def _print_err(line: str) -> None:
    print(line, file=sys.stderr)


def _emit_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str, ensure_ascii=False))


# ── Command implementations ─────────────────────────────────────────────────


def cmd_health(client: Client, args: argparse.Namespace) -> int:
    payload = client.health()
    if args.json:
        _emit_json(payload)
    else:
        _print(color("OK", "green") if payload.get("ok") else color("FAIL", "red"))
        _print(color(f"  api: {client.base_url}", "dim"))
    return 0


def cmd_dashboard(client: Client, args: argparse.Namespace) -> int:
    payload = client.dashboard()
    if args.json:
        _emit_json(payload)
        return 0
    _print(render_dashboard(payload))
    return 0


# ---- projects --------------------------------------------------------------


def cmd_projects_list(client: Client, args: argparse.Namespace) -> int:
    projects = client.list_projects()
    if args.json:
        _emit_json(projects)
        return 0
    if not projects:
        _print(color("no projects", "dim"))
        return 0
    rows = [
        (
            p.get("project_id", ""),
            p.get("name", "") or p.get("project_id", ""),
            str(len(p.get("agents") or [])),
            (p.get("goal") or "").splitlines()[0] if p.get("goal") else "",
        )
        for p in projects
    ]
    _print(
        render_table(
            ["id", "name", "agents", "goal"],
            rows,
            max_widths=[24, 24, 7, 60],
        )
    )
    return 0


def cmd_projects_show(client: Client, args: argparse.Namespace) -> int:
    project = client.get_project(args.project_id)
    if args.json:
        _emit_json(project)
        return 0
    _print(color(project.get("project_id", ""), "bold"))
    if project.get("goal"):
        _print(color("goal: ", "dim") + project["goal"])
    agents = project.get("agents") or []
    _print("")
    _print(color(f"agents ({len(agents)})", "bold"))
    if agents:
        rows = [
            (
                a.get("role", ""),
                a.get("persona_id", "") or "-",
                a.get("model_tier", "") or "-",
                a.get("model_id", "") or "-",
                ",".join(a.get("depends_on") or []) or "-",
            )
            for a in agents
        ]
        _print(
            render_table(
                ["role", "persona", "tier", "model", "depends_on"],
                rows,
                max_widths=[20, 20, 8, 30, 30],
            )
        )
    return 0


def cmd_projects_delete(client: Client, args: argparse.Namespace) -> int:
    if not args.yes:
        _print_err(
            f"refusing to delete {args.project_id!r} without --yes"
        )
        return 2
    client.delete_project(args.project_id)
    _print(color(f"deleted {args.project_id}", "green"))
    return 0


# ---- runs ------------------------------------------------------------------


def cmd_runs_list(client: Client, args: argparse.Namespace) -> int:
    runs = client.list_runs(project_id=args.project, limit=args.limit)
    if args.json:
        _emit_json(runs)
        return 0
    if not runs:
        _print(color("no runs", "dim"))
        return 0
    rows = [
        (
            r.get("run_id", ""),
            r.get("project_id", ""),
            fmt_status(r.get("status", "")),
            fmt_relative_time(r.get("created_at")),
            (r.get("goal") or "").splitlines()[0] if r.get("goal") else "",
        )
        for r in runs
    ]
    _print(
        render_table(
            ["run", "project", "status", "started", "goal"],
            rows,
            max_widths=[16, 20, 14, 8, 60],
        )
    )
    return 0


def cmd_runs_show(client: Client, args: argparse.Namespace) -> int:
    meta = client.get_run(args.run_id)
    if args.json:
        _emit_json(meta)
        return 0
    _print(render_run_summary(meta))
    return 0


def cmd_runs_board(client: Client, args: argparse.Namespace) -> int:
    meta = client.get_run(args.run_id)
    board = client.get_run_board(args.run_id)
    if args.type:
        wanted = {t.upper() for t in args.type}
        board = [s for s in board if s.get("type", "").upper() in wanted]
    if args.tail:
        board = board[-args.tail :]
    if args.json:
        _emit_json(board)
        return 0
    _print(render_run_summary(meta))
    _print("")
    _print(color(f"board ({len(board)} signals)", "bold"))
    for sig in board:
        _print(render_signal_row(sig))
    return 0


def cmd_runs_watch(client: Client, args: argparse.Namespace) -> int:
    """Poll the board endpoint and print only new signals.

    Runs until the run reaches a terminal status (everything that
    isn't 'running'/'queued'/'pending'), or until Ctrl-C.
    """
    interval = max(0.5, args.interval)
    seen: set[str] = set()
    last_status: str | None = None
    try:
        while True:
            try:
                meta = client.get_run(args.run_id)
                board = client.get_run_board(args.run_id)
            except APIError as e:
                _print_err(color(f"poll error: {e}", "red"))
                time.sleep(interval)
                continue

            status = (meta.get("status") or "").lower()
            if status != last_status:
                _print(
                    color(f"-- run {args.run_id} status: ", "dim")
                    + fmt_status(meta.get("status", ""))
                )
                last_status = status

            for sig in board:
                sig_id = str(sig.get("id") or "")
                if sig_id and sig_id in seen:
                    continue
                if sig_id:
                    seen.add(sig_id)
                _print(render_signal_row(sig))

            terminal = status not in {"running", "queued", "pending", ""}
            if terminal:
                _print(color(f"-- run terminal: {status}", "dim"))
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        _print(color("\n-- watch interrupted", "dim"))
        return 130


def cmd_runs_comment(client: Client, args: argparse.Namespace) -> int:
    payload = client.post_run_comment(
        args.run_id,
        args.text,
        priority="high" if args.high else "normal",
    )
    if args.json:
        _emit_json(payload)
    else:
        _print(color(f"posted {payload.get('type')} (i{payload.get('iteration')})", "green"))
    return 0


def cmd_runs_approve(client: Client, args: argparse.Namespace) -> int:
    decision = "deny" if args.deny else "grant"
    payload = client.post_run_approval(
        args.run_id, args.approval_id, decision, note=args.note
    )
    if args.json:
        _emit_json(payload)
    else:
        _print(color(f"approval {payload.get('decision')}", "green" if decision == "grant" else "yellow"))
    return 0


def cmd_runs_stop(client: Client, args: argparse.Namespace) -> int:
    payload = client.stop_run(args.run_id)
    if args.json:
        _emit_json(payload)
    else:
        _print(color(f"stop requested for {args.run_id}", "yellow"))
    return 0


def cmd_runs_resume(client: Client, args: argparse.Namespace) -> int:
    payload = client.resume_run(args.run_id)
    if args.json:
        _emit_json(payload)
        return 0
    _print(
        color(
            f"resume queued: job {payload.get('job_id')} for run {payload.get('run_id')}",
            "green",
        )
    )
    return 0


# ---- personas --------------------------------------------------------------


def cmd_personas_list(client: Client, args: argparse.Namespace) -> int:
    personas = client.list_personas()
    if args.json:
        _emit_json(personas)
        return 0
    if not personas:
        _print(color("no personas", "dim"))
        return 0
    rows = [
        (
            p.get("persona_id", ""),
            p.get("name", ""),
            p.get("specialty", "") or "-",
            str(p.get("n_episodes", 0)),
            "yes" if p.get("degraded") else "",
        )
        for p in personas
    ]
    _print(
        render_table(
            ["id", "name", "specialty", "episodes", "degraded"],
            rows,
            max_widths=[24, 22, 26, 9, 10],
        )
    )
    return 0


def cmd_personas_show(client: Client, args: argparse.Namespace) -> int:
    persona = client.get_persona(args.persona_id)
    if args.json:
        _emit_json(persona)
        return 0
    _print(color(persona.get("name") or persona.get("persona_id", ""), "bold"))
    _print(color("id:        ", "dim") + persona.get("persona_id", ""))
    if persona.get("specialty"):
        _print(color("specialty: ", "dim") + persona["specialty"])
    if persona.get("character"):
        _print(color("character: ", "dim") + persona["character"])
    if persona.get("goal"):
        _print(color("goal:      ", "dim") + persona["goal"])
    _print(color("episodes:  ", "dim") + str(persona.get("n_episodes", 0)))
    rep = persona.get("reputation") or {}
    prod = rep.get("production") or {}
    if prod:
        total = prod.get("total_runs", 0)
        approved = prod.get("approved_runs", 0)
        rate = f"{int(approved / total * 100)}%" if total else "-"
        _print(color("runs:      ", "dim") + f"{total} ({approved} approved, {rate})")
    if persona.get("degraded"):
        missing = ",".join(persona.get("missing_files") or [])
        _print(color(f"degraded — missing: {missing}", "red"))
    return 0


# ---- examples --------------------------------------------------------------


def cmd_examples_list(client: Client, args: argparse.Namespace) -> int:
    examples = client.list_examples()
    if args.json:
        _emit_json(examples)
        return 0
    if not examples:
        _print(color("no example projects", "dim"))
        return 0
    rows = [
        (
            e.get("id", ""),
            e.get("title", "") or e.get("id", ""),
            ",".join(e.get("tags") or []),
            (e.get("description") or "").splitlines()[0],
        )
        for e in examples
    ]
    _print(
        render_table(
            ["id", "title", "tags", "description"],
            rows,
            max_widths=[24, 28, 24, 60],
        )
    )
    return 0


def cmd_examples_use(client: Client, args: argparse.Namespace) -> int:
    payload = client.instantiate_example(
        args.template_id, project_id=args.project_id, goal=args.goal
    )
    if args.json:
        _emit_json(payload)
        return 0
    project = payload.get("project") or {}
    _print(
        color(
            f"created project {project.get('project_id')} from template {payload.get('from_template')}",
            "green",
        )
    )
    return 0


# ---- propose ---------------------------------------------------------------


def cmd_propose(client: Client, args: argparse.Namespace) -> int:
    if not args.goal and not args.project:
        _print_err("propose requires --goal or --project")
        return 2
    goal = args.goal
    if args.project and not goal:
        project = client.get_project(args.project)
        goal = project.get("goal") or ""
        if not goal:
            _print_err(f"project {args.project!r} has no goal; pass --goal explicitly")
            return 2
    payload = client.propose(goal=goal, project_id=args.project)
    if args.json:
        _emit_json(payload)
        return 0
    if payload.get("rationale"):
        _print(color("rationale:", "bold"))
        _print(payload["rationale"])
        _print("")
    agents = payload.get("agents") or []
    _print(color(f"proposed agents ({len(agents)})", "bold"))
    rows = [
        (
            a.get("role", ""),
            a.get("persona_id", "") or "-",
            ",".join(a.get("depends_on") or []) or "-",
            (a.get("description") or "").splitlines()[0],
        )
        for a in agents
    ]
    _print(
        render_table(
            ["role", "persona", "depends_on", "description"],
            rows,
            max_widths=[20, 20, 24, 60],
        )
    )
    return 0


# ---- invoke ----------------------------------------------------------------


def cmd_invoke(client: Client, args: argparse.Namespace) -> int:
    body: dict[str, Any] = {}
    if args.confirm:
        body["confirm"] = True
    if args.context:
        body["context"] = args.context
    payload = client.invoke(args.project_id, body)
    if args.json:
        _emit_json(payload)
        return 0

    if payload.get("requires_approval"):
        est = payload.get("estimated_cost") or {}
        _print(color("cost preview gate (re-run with --confirm to start):", "yellow"))
        for k in ("min_cost_usd", "max_cost_usd", "expected_cost_usd"):
            if k in est:
                _print(f"  {k}: ${est[k]}")
        return 0

    _print(
        color(
            f"queued run {payload.get('run_id')} (job {payload.get('job_id')})",
            "green",
        )
    )
    if args.watch:
        watch_args = argparse.Namespace(
            run_id=payload["run_id"],
            interval=1.5,
            json=False,
        )
        return cmd_runs_watch(client, watch_args)
    return 0


# ── Argparse wiring ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="guudflow",
        description="GuudFlow CLI — terminal access to the same surfaces as the studio.",
    )
    p.add_argument(
        "--api",
        help="API base URL (default: $GUUDFLOW_API_URL or http://127.0.0.1:8000)",
        default=None,
    )
    p.add_argument(
        "--token",
        help="Bearer token for credentialed routes (default: $GUUDFLOW_API_TOKEN)",
        default=None,
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of formatted text.",
    )

    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sp = sub.add_parser("health", help="Ping the API.")
    sp.set_defaults(func=cmd_health)

    sp = sub.add_parser("dashboard", help="Show the dashboard summary.")
    sp.set_defaults(func=cmd_dashboard)

    # projects
    proj = sub.add_parser("projects", help="Project commands.")
    proj_sub = proj.add_subparsers(dest="action", metavar="ACTION")

    sp = proj_sub.add_parser("list", help="List projects (default action).")
    sp.set_defaults(func=cmd_projects_list)

    sp = proj_sub.add_parser("show", help="Show a project's config.")
    sp.add_argument("project_id")
    sp.set_defaults(func=cmd_projects_show)

    sp = proj_sub.add_parser("delete", help="Delete a project.")
    sp.add_argument("project_id")
    sp.add_argument("--yes", action="store_true", help="Confirm destructive op.")
    sp.set_defaults(func=cmd_projects_delete)

    proj.set_defaults(func=cmd_projects_list, action="list")

    # runs
    runs = sub.add_parser("runs", help="Run commands.")
    runs_sub = runs.add_subparsers(dest="action", metavar="ACTION")

    sp = runs_sub.add_parser("list", help="List runs.")
    sp.add_argument("--project", help="Filter by project id.")
    sp.add_argument("--limit", type=int, default=25)
    sp.set_defaults(func=cmd_runs_list)

    sp = runs_sub.add_parser("show", help="Show run metadata.")
    sp.add_argument("run_id")
    sp.set_defaults(func=cmd_runs_show)

    sp = runs_sub.add_parser("board", help="Show the board signals for a run.")
    sp.add_argument("run_id")
    sp.add_argument("--tail", type=int, help="Show only the last N signals.")
    sp.add_argument(
        "--type",
        action="append",
        help="Filter by signal type (repeatable).",
    )
    sp.set_defaults(func=cmd_runs_board)

    sp = runs_sub.add_parser("watch", help="Tail the board feed live (polls).")
    sp.add_argument("run_id")
    sp.add_argument(
        "--interval",
        type=float,
        default=1.5,
        help="Poll interval seconds (default 1.5, min 0.5).",
    )
    sp.set_defaults(func=cmd_runs_watch)

    sp = runs_sub.add_parser("comment", help="Post a USER_COMMENT to a live run.")
    sp.add_argument("run_id")
    sp.add_argument("text")
    sp.add_argument("--high", action="store_true", help="Send as USER_COMMENT_PRIORITY.")
    sp.set_defaults(func=cmd_runs_comment)

    sp = runs_sub.add_parser("approve", help="Resolve a pending approval gate.")
    sp.add_argument("run_id")
    sp.add_argument("approval_id")
    sp.add_argument("--deny", action="store_true", help="Deny instead of grant.")
    sp.add_argument("--note", help="Optional reviewer note.")
    sp.set_defaults(func=cmd_runs_approve)

    sp = runs_sub.add_parser("stop", help="Cancel an active run.")
    sp.add_argument("run_id")
    sp.set_defaults(func=cmd_runs_stop)

    sp = runs_sub.add_parser("resume", help="Re-queue a terminated run.")
    sp.add_argument("run_id")
    sp.set_defaults(func=cmd_runs_resume)

    runs.set_defaults(func=cmd_runs_list, action="list", project=None, limit=25)

    # personas
    pers = sub.add_parser("personas", help="Persona roster commands.")
    pers_sub = pers.add_subparsers(dest="action", metavar="ACTION")

    sp = pers_sub.add_parser("list", help="List personas.")
    sp.set_defaults(func=cmd_personas_list)

    sp = pers_sub.add_parser("show", help="Show a persona's profile.")
    sp.add_argument("persona_id")
    sp.set_defaults(func=cmd_personas_show)

    pers.set_defaults(func=cmd_personas_list, action="list")

    # examples
    ex = sub.add_parser("examples", help="Example project templates.")
    ex_sub = ex.add_subparsers(dest="action", metavar="ACTION")

    sp = ex_sub.add_parser("list", help="List example templates.")
    sp.set_defaults(func=cmd_examples_list)

    sp = ex_sub.add_parser("use", help="Instantiate a template into a project.")
    sp.add_argument("template_id")
    sp.add_argument("--project-id", dest="project_id", help="Override project id.")
    sp.add_argument("--goal", help="Override the template default goal.")
    sp.set_defaults(func=cmd_examples_use)

    ex.set_defaults(func=cmd_examples_list, action="list")

    # propose
    sp = sub.add_parser("propose", help="Generate an agent team proposal.")
    sp.add_argument("--project", help="Project id whose goal/inputs feed the proposal.")
    sp.add_argument("--goal", help="Goal text to propose against.")
    sp.set_defaults(func=cmd_propose)

    # invoke
    sp = sub.add_parser("invoke", help="Trigger a run for a project.")
    sp.add_argument("project_id")
    sp.add_argument("--confirm", action="store_true", help="Bypass cost preview gate.")
    sp.add_argument("--context", help="Optional context payload.")
    sp.add_argument("--watch", action="store_true", help="Tail the run after enqueue.")
    sp.set_defaults(func=cmd_invoke)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Callable[[Client, argparse.Namespace], int] | None = getattr(
        args, "func", None
    )
    if func is None:
        parser.print_help()
        return 2

    client = Client(base_url=args.api, token=args.token)
    try:
        return func(client, args)
    except APIError as e:
        _print_err(color(f"error: {e}", "red"))
        if e.status == 401:
            _print_err(
                color(
                    "  hint: pass --token or set GUUDFLOW_API_TOKEN",
                    "dim",
                )
            )
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
