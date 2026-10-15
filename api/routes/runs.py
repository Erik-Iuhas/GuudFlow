"""/api/runs — run history, artifacts, and live mid-run interaction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.auth import require_bearer
from core.signals import Signal, SignalType
from worker.queue import JOB_QUEUED, JOB_RUNNING, Job


def _run_working_dir(data_root: Path, run_id: str) -> Path:
    """Track 4.4 — resolve the agents' working tree for `run_id`.

    Reads `working_dir` from the run's meta.json if present (Track 4.4
    began persisting it). In in-place mode (Track 4.3) this points at
    the user's chosen directory; in normal isolated runs it's the
    legacy `<run>/workspace/working/`. Falls back to the legacy path
    for older runs whose meta.json predates this field, so the Files /
    Git tabs keep working on historical runs.
    """
    legacy = data_root / "runs" / run_id / "workspace" / "working"
    meta_path = data_root / "runs" / run_id / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            override = meta.get("working_dir")
            if override:
                p = Path(override)
                if p.is_dir():
                    return p
        except (OSError, json.JSONDecodeError):
            pass
    return legacy


# ── Graph helpers (M5.C4) ──────────────────────────────────────────────────


_ESCALATION_HANDLER_ROLE_TYPES = {"tech_lead"}


def _get_archetype_kind(cfg: dict, all_roles: set[str] | None = None) -> str:
    """Derive the visual pipeline band from stored AgentConfig fields.

    Uses pipeline position, not the class hierarchy (Architect extends Reviewer
    in the abstract layer, but sits at the *top* of the pipeline visually).

    Band order (top → bottom in the graph):
      planner           — root nodes: no depends_on, not a reviewer
      specialist        — middle workers: has depends_on, not a reviewer
      reviewer          — is_reviewer / is_final_reviewer
      escalation_handler— tech_lead and equivalents
    """
    role_type = cfg.get("agent_class_role_type") or ""

    if role_type in _ESCALATION_HANDLER_ROLE_TYPES:
        return "escalation_handler"
    if cfg.get("is_reviewer") or cfg.get("is_final_reviewer"):
        return "reviewer"
    if not cfg.get("depends_on"):
        return "planner"
    return "specialist"


def _depends_on_edges(agents: list[dict]) -> list[tuple[str, str, str]]:
    edges = []
    for cfg in agents:
        role = cfg.get("role", "")
        for dep in (cfg.get("depends_on") or []):
            if dep != role:
                edges.append((dep, role, "depends_on"))
    return edges


def _can_assign_to_edges(
    agents: list[dict], rbac: dict[str, Any]
) -> list[tuple[str, str, str]]:
    all_roles = {cfg.get("role") for cfg in agents if cfg.get("role")}
    edges = []
    for cfg in agents:
        role = cfg.get("role", "")
        explicit_manages = cfg.get("manages") or []
        if explicit_manages:
            for target in explicit_manages:
                if target != role and target in all_roles:
                    edges.append((role, target, "can_assign_to"))
        else:
            scopes = (rbac.get(role) or {}).get("allowed_scopes") or []
            if "assign-task" in scopes:
                for target in sorted(all_roles):
                    if target != role:
                        edges.append((role, target, "can_assign_to"))
    return edges


def _escalates_to_edges(agents: list[dict]) -> list[tuple[str, str, str]]:
    all_role_set = {cfg.get("role") for cfg in agents if cfg.get("role")}
    edges = []
    for cfg in agents:
        role = cfg.get("role", "")
        explicit_esc = cfg.get("escalates_to") or []
        if explicit_esc:
            for target in explicit_esc:
                if target != role and target in all_role_set:
                    edges.append((role, target, "escalates_to"))
        else:
            if "tech_lead" in all_role_set and role != "tech_lead":
                edges.append((role, "tech_lead", "escalates_to"))
    return edges


router = APIRouter(prefix="/api/runs", tags=["runs"])


def _store(request: Request):
    return request.app.state.run_store


@router.get("")
async def list_runs(
    request: Request,
    project_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict]:
    runs = await _store(request).list(project_id=project_id, limit=limit)
    # Reconcile meta.json status against the live registry:
    #   - In-registry → status stays / becomes "running" (handles resumes)
    #   - meta says "running" but NOT in registry → downgrade to "stale"
    #     (worker died before finalising; startup recovery normally rewrites
    #     these to disk too, but defending here covers the in-flight case
    #     where the worker died this session before we restarted)
    registry = getattr(request.app.state, "run_registry", None)
    active_ids: set[str] = set(registry.runs.keys()) if registry is not None else set()
    for r in runs:
        rid = r.get("run_id")
        if rid in active_ids:
            r["status"] = "running"
        elif r.get("status") == "running":
            r["status"] = "stale"
    return runs


@router.get("/{run_id}")
async def get_run(request: Request, run_id: str) -> dict:
    meta = await _store(request).get(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    # Same reconciliation as list_runs — orphaned "running" → "stale".
    registry = getattr(request.app.state, "run_registry", None)
    if registry is not None and meta.get("status") == "running":
        if run_id not in registry.runs:
            meta["status"] = "stale"
    await _add_harness_meta(request, meta)
    return meta


async def _add_harness_meta(request: Request, meta: dict) -> None:
    """Fill `harness` and `token_budget_usd` from the project when the run meta lacks
    them (Studio's cockpit picks its view and budget meter from these), plus the
    board's review-column WIP cap so Studio never hardcodes it."""
    from core.harness.task_board import BoardLimits
    meta.setdefault("review_column_cap", BoardLimits().review_column_cap)
    if "harness" in meta and "token_budget_usd" in meta:
        return
    store = getattr(request.app.state, "project_store", None)
    project = await store.get(meta["project_id"]) if store and meta.get("project_id") else None
    project = project or {}
    meta.setdefault("harness", "legacy" if project.get("harness") == "legacy" else "opencode")
    budget = (project.get("approval_gates") or {}).get("token_budget_usd")
    meta.setdefault("token_budget_usd", budget if isinstance(budget, (int, float)) else None)


@router.get("/{run_id}/board")
async def get_run_board(request: Request, run_id: str) -> list[dict]:
    board = await _store(request).get_board(run_id)
    if board is None:
        raise HTTPException(status_code=404, detail=f"board for {run_id!r} not found")
    return board


@router.get("/{run_id}/desks", dependencies=[Depends(require_bearer)])
async def get_run_desks(request: Request, run_id: str) -> dict:
    """Return the per-agent task desks for a run (M3.5 chunk 8).

    Live runs read from the in-memory workspace; completed runs read
    `tasks.jsonl` from disk via RunStore. The response shape:

      {
        "desks": {role: [task, ...], ...},
        "tasks_by_id": {task_id: task, ...}
      }

    Bearer-auth gated. Read-only.
    """
    from worker.job_runner import serialise_task

    registry = getattr(request.app.state, "run_registry", None)
    workspace = await registry.get(run_id) if registry is not None else None
    if workspace is not None:
        # Synthetic `root:*` tasks are runner plumbing (Track 7) and must
        # not surface in the Kanban — they carry the project goal verbatim
        # and would otherwise show as phantom "assigned" cards.
        tasks_by_id = {
            tid: serialise_task(t)
            for tid, t in workspace.tasks.items()
            if not tid.startswith("root:")
        }
        desks: dict[str, list[dict]] = {}
        for role, ids in workspace.task_queues.items():
            seen: set[str] = set()
            desks[role] = []
            for tid in ids:
                if tid in seen or tid not in tasks_by_id:
                    continue
                seen.add(tid)
                desks[role].append(tasks_by_id[tid])
        return {"desks": desks, "tasks_by_id": tasks_by_id}

    # Run is not active — fall through to disk.
    payload = await _store(request).get_tasks(run_id)
    if payload is None:
        raise HTTPException(
            status_code=404, detail=f"run {run_id!r} not found",
        )
    return payload


@router.get("/{run_id}/workers", dependencies=[Depends(require_bearer)])
async def get_run_workers(request: Request, run_id: str) -> dict:
    """Track 11 — live cognition snapshot for the Workers tab.

    Returns one entry per agent role with the current state Studio
    needs to render the cognition view:

      {
        "workers": [
          {
            "role": "dev",
            "state": "running" | "complete" | "pending" | "...",
            "iteration": int,
            "active_subtask": {"id": ..., "goal": ..., "parent_task_id": ...} | None,
            "current_tool_call": {"name": ..., "arguments_preview": ..., "elapsed_seconds": ...} | None,
            "last_tool_summary": {"name": ..., "summary": ..., "ago_seconds": ...} | None,
            "iteration_cost_usd": float,
            "subtask_history": [{"id": ..., "goal": ..., "status": ...}, ...]
          },
          ...
        ],
        "live": bool
      }

    Live runs read from the in-memory workspace + agent state; completed
    runs degrade to a "live=false" snapshot derived from tasks.jsonl
    only. Studio polls this every ~1.5s while the Workers tab is open.
    """
    import json
    import time as _time
    from pathlib import Path as _Path

    registry = getattr(request.app.state, "run_registry", None)
    workspace = await registry.get(run_id) if registry is not None else None
    runner = await registry.get_runner(run_id) if registry is not None else None

    if workspace is None or runner is None:
        # Run is not active — degrade gracefully. Read tasks from disk
        # and skip the live-only fields (current tool call, etc).
        payload = await _store(request).get_tasks(run_id)
        if payload is None:
            raise HTTPException(
                status_code=404, detail=f"run {run_id!r} not found",
            )
        # Group tasks by assigned_to to reconstruct a basic worker view.
        tasks_by_role: dict[str, list[dict]] = {}
        for t in (payload.get("tasks_by_id") or {}).values():
            assignee = t.get("assigned_to") or "?"
            tasks_by_role.setdefault(assignee, []).append(t)
        workers = []
        for role, tasks in sorted(tasks_by_role.items()):
            history = [
                {"id": t["task_id"], "goal": t.get("goal", ""), "status": t.get("status", "")}
                for t in tasks if not t["task_id"].startswith("root:")
            ]
            workers.append({
                "role": role,
                "state": "complete",
                "iteration": 0,
                "active_subtask": None,
                "current_tool_call": None,
                "last_tool_summary": None,
                "iteration_cost_usd": 0.0,
                "subtask_history": history,
            })
        return {"workers": workers, "live": False}

    # Live snapshot.
    workers = []
    now = _time.time()
    for cfg in runner.project.agents:
        role = cfg.role
        agent = (runner.agents or {}).get(role)
        state = workspace.agent_states.get(role)
        state_str = state.value if state is not None else "pending"

        # Active subtask (Track 7).
        active_sub_id = (workspace.active_subtask or {}).get(role)
        active_sub: dict | None = None
        if active_sub_id and not active_sub_id.startswith("root:"):
            t = workspace.tasks.get(active_sub_id)
            if t is not None:
                active_sub = {
                    "id": t.task_id,
                    "goal": t.goal,
                    "parent_task_id": t.parent_task_id,
                    "acceptance": list(t.acceptance_criteria or [])[:5],
                }

        # In-flight tool call (Track 11 BaseAgent hook).
        current_tool: dict | None = None
        last_summary: dict | None = None
        iteration_cost = 0.0
        iter_no = 0
        if agent is not None:
            ct = getattr(agent, "_current_tool_call", None)
            if ct is not None:
                name, args, started = ct
                # Truncate args preview to keep payload small.
                args_preview = json.dumps(args, default=str)
                if len(args_preview) > 200:
                    args_preview = args_preview[:197] + "..."
                current_tool = {
                    "name": name,
                    "arguments_preview": args_preview,
                    "elapsed_seconds": round(now - started, 1),
                }
            ls = getattr(agent, "_last_tool_summary", None)
            if ls is not None:
                lname, lsummary, lended = ls
                last_summary = {
                    "name": lname,
                    "summary": lsummary,
                    "ago_seconds": round(now - lended, 1),
                }
            m = getattr(agent, "_current_metrics", None)
            if m is not None:
                iteration_cost = float(getattr(m, "estimated_cost_usd", 0.0) or 0.0)
                iter_no = int(getattr(m, "iteration", 0) or 0)

        # Subtask history — flat list of all non-root tasks assigned to
        # this role, oldest first, with status. Caller paginates / collapses
        # in the UI.
        history: list[dict] = []
        for tid in workspace.task_queues.get(role, []):
            if tid.startswith("root:"):
                continue
            t = workspace.tasks.get(tid)
            if t is None:
                continue
            history.append({
                "id": t.task_id,
                "goal": t.goal,
                "status": t.status,
                "parent_task_id": t.parent_task_id,
            })

        workers.append({
            "role": role,
            "state": state_str,
            "iteration": iter_no,
            "active_subtask": active_sub,
            "current_tool_call": current_tool,
            "last_tool_summary": last_summary,
            "iteration_cost_usd": iteration_cost,
            "subtask_history": history,
        })

    return {"workers": workers, "live": True}


@router.get("/{run_id}/graph", dependencies=[Depends(require_bearer)])
async def get_run_graph(request: Request, run_id: str) -> dict:
    """Return the agent hierarchy graph for a run (M5.C4).

    Derives nodes from the project's agents list and three edge kinds:
      - depends_on: from AgentConfig.depends_on links
      - can_assign_to: roles with RBAC assign-task scope → all other roles
      - escalates_to: all non-tech_lead roles → tech_lead (if present)

    Returns 404 if the run or its project doesn't exist.
    """
    run_meta = await _store(request).get(run_id)
    if run_meta is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")

    project_id = run_meta.get("project_id")
    if not project_id:
        raise HTTPException(status_code=500, detail="run has no project_id")

    project = await request.app.state.project_store.get(project_id)
    if project is None:
        raise HTTPException(
            status_code=404, detail=f"project {project_id!r} not found"
        )

    agents: list[dict] = project.get("agents") or []
    rbac: dict[str, Any] = project.get("rbac") or {}

    persona_store = getattr(request.app.state, "persona_store", None)
    nodes = []
    for cfg in agents:
        role = cfg.get("role", "")
        persona_id = cfg.get("persona_id") or None
        persona_name = role
        if persona_id and persona_store is not None:
            persona_data = await persona_store.get(persona_id)
            if persona_data:
                persona_name = persona_data.get("name") or persona_id
        nodes.append({
            "role": role,
            "archetype": _get_archetype_kind(cfg),
            "persona_name": persona_name,
            "persona_id": persona_id,
            "model_tier": cfg.get("model_tier") or "solid",
            "model_id": cfg.get("model_id") or "",
            "is_final_reviewer": bool(cfg.get("is_final_reviewer")),
        })

    raw_edges: list[tuple[str, str, str]] = []
    raw_edges.extend(_depends_on_edges(agents))
    raw_edges.extend(_can_assign_to_edges(agents, rbac))
    raw_edges.extend(_escalates_to_edges(agents))

    seen: set[tuple[str, str, str]] = set()
    edges = []
    for from_role, to_role, kind in raw_edges:
        key = (from_role, to_role, kind)
        if key not in seen:
            seen.add(key)
            edges.append({"from_role": from_role, "to_role": to_role, "kind": kind})

    return {"nodes": nodes, "edges": edges}


@router.get("/{run_id}/git/commits")
async def list_git_commits(request: Request, run_id: str) -> list[dict]:
    """Return commits for a run's git workspace, one entry per commit.

    Each entry: {sha, short_sha, subject, author_role, branch, committed_at,
                 stats: {files: N, insertions: N, deletions: N}}.
    Author_role is derived from the branch name (agent/backend → backend;
    main → runner). Returns 404 if no git workspace exists.
    """
    from git import InvalidGitRepositoryError, NoSuchPathError, Repo

    data_root = Path(request.app.state.data_root)
    working_dir = _run_working_dir(data_root, run_id)
    try:
        repo = Repo(working_dir)
    except (InvalidGitRepositoryError, NoSuchPathError):
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} has no git workspace",
        )

    # Build a sha → branch map. Agent branches claim FIRST so that commits
    # they authored stay attributed to them even after the runner's
    # land-on-main pass merged them into main. Commits only reachable from
    # main (e.g. the initial workspace commit) fall through to a second
    # pass that attributes them to main. Without this the graph collapses
    # to "main" the moment any agent branch lands.
    sha_to_branch: dict[str, str] = {}
    all_refs = [r for r in repo.references if hasattr(r, "commit")]
    non_main_refs = sorted(
        [r for r in all_refs if r.name not in ("main", "master")],
        key=lambda r: r.name,
    )
    main_refs = [r for r in all_refs if r.name in ("main", "master")]
    for ref in non_main_refs + main_refs:
        try:
            for commit in repo.iter_commits(ref):
                if commit.hexsha not in sha_to_branch:
                    sha_to_branch[commit.hexsha] = ref.name
        except Exception:
            pass

    out: list[dict] = []
    seen: set[str] = set()
    try:
        commits = list(repo.iter_commits("--all", max_count=200))
    except Exception:
        commits = []

    for commit in commits:
        sha = commit.hexsha
        if sha in seen:
            continue
        seen.add(sha)

        branch = sha_to_branch.get(sha, "")
        # Prefer author name parsed back from "Name (role)" format;
        # fall back to deriving role from branch name for old runs.
        author_name_str = str(commit.author) if commit.author else ""
        import re as _re
        _role_match = _re.search(r"\(([^)]+)\)$", author_name_str)
        if _role_match:
            author_role = _role_match.group(1)
        elif branch.startswith("agent/"):
            author_role = branch[len("agent/"):]
        elif branch in ("main", "master"):
            author_role = "runner"
        else:
            author_role = branch or "unknown"

        try:
            stats = commit.stats.total
            commit_stats = {
                "files": stats.get("files", 0),
                "insertions": stats.get("insertions", 0),
                "deletions": stats.get("deletions", 0),
            }
        except Exception:
            commit_stats = {"files": 0, "insertions": 0, "deletions": 0}

        out.append({
            "sha": sha,
            "short_sha": sha[:7],
            "subject": commit.message.strip().splitlines()[0] if commit.message else "",
            "author_role": author_role,
            "branch": branch,
            "committed_at": commit.committed_date,
            "stats": commit_stats,
            "parent_shas": [p.hexsha for p in commit.parents],
        })

    out.sort(key=lambda c: c["committed_at"])
    return out


@router.get("/{run_id}/git/diff", dependencies=[Depends(require_bearer)])
async def get_git_diff(
    request: Request,
    run_id: str,
    ref: str = Query(..., description="commit SHA"),
    base: str | None = Query(None, description="optional base SHA: diff base..ref"),
) -> dict:
    """Return the unified diff for a single commit, or for the range base..ref.

    A card's merge is recorded as (base, head) on main, so `base` gives exactly
    what that merge brought in, however many commits it had.
    Returns {sha, diff: str}. 404 if workspace missing or sha unknown.
    """
    from git import InvalidGitRepositoryError, NoSuchPathError, Repo

    data_root = Path(request.app.state.data_root)
    working_dir = _run_working_dir(data_root, run_id)
    try:
        repo = Repo(working_dir)
    except (InvalidGitRepositoryError, NoSuchPathError):
        raise HTTPException(status_code=404, detail=f"run {run_id!r} has no git workspace")

    try:
        commit = repo.commit(ref)
    except Exception:
        raise HTTPException(status_code=404, detail=f"sha {ref!r} not found")

    base_commit = None
    if base is not None:
        try:
            base_commit = repo.commit(base)
        except Exception:
            raise HTTPException(status_code=404, detail=f"sha {base!r} not found")

    try:
        if base_commit is not None:
            diff_text = repo.git.diff(base_commit.hexsha, commit.hexsha)
        elif commit.parents:
            diff_text = repo.git.diff(commit.parents[0].hexsha, commit.hexsha)
        else:
            diff_text = repo.git.show(commit.hexsha, "--format=", "--patch")
    except Exception as e:
        diff_text = f"(could not render diff: {e})"

    return {"sha": commit.hexsha, "diff": diff_text}


@router.get("/{run_id}/roles/{role}/changes", dependencies=[Depends(require_bearer)])
async def get_role_changes(request: Request, run_id: str, role: str,
                           scope: str = "now") -> dict[str, Any]:
    """A role's work in progress: its worktree against its local `main`, read-only.

    `scope=run`: instead, everything the role merged into the run's main since the run
    started (its own commits), for a role whose turns are already merged.

    `main` is the role's copy as of its last sync (each spawn starts with one), so
    during a turn this is exactly what the turn has written so far. Nothing is
    committed or fetched. 404 when the run or the role's checkout is missing.
    """
    from dataclasses import asdict

    from core.git_workspace import GitWorkspace
    from core.harness.artifacts import safe_resolve
    from core.harness.git_roles import GitRoleError, RoleClones

    data_root = Path(request.app.state.data_root)
    run_root = safe_resolve(data_root / "runs", run_id)
    if run_root is None or len(Path(run_id).parts) != 1 or len(Path(role).parts) != 1:
        raise HTTPException(status_code=404, detail="not found")
    clones = RoleClones(GitWorkspace(_run_working_dir(data_root, run_id)), run_root / "roles")
    git_dir = safe_resolve(run_root, Path("roles") / role / "git")
    if git_dir is None or not git_dir.is_dir() or not clones.path(role).is_dir():
        raise HTTPException(status_code=404, detail=f"role {role!r} has no checkout in run {run_id!r}")
    try:
        if scope == "run":
            from core.project_runs import PROJECT_RUN_JSON
            meta_file = run_root / PROJECT_RUN_JSON
            meta = json.loads(meta_file.read_text()) if meta_file.is_file() else {}
            base = str(meta.get("base") or "") or clones.main.repo.git.rev_list(
                "--max-parents=0", "main").split()[0]
            changes = clones.merged_by_role(role, base)
        else:
            changes = clones.working_changes(role)
    except GitRoleError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"role": role, **asdict(changes)}


@router.post("/{run_id}/approval/{approval_id}", dependencies=[Depends(require_bearer)])
async def post_approval_decision(
    request: Request,
    run_id: str,
    approval_id: str,
    body: dict = Body(...),
) -> dict:
    """Resolve a pending approval gate (Phase C.0.5).

    The runner posts an APPROVAL_NEEDED signal and parks until the
    user calls this endpoint with `decision: "grant"|"deny"`. On
    success, the matching APPROVAL_GRANTED / APPROVAL_DENIED signal
    is posted to the active workspace and the runner's awaiting
    subscriber resumes.

    Returns 404 if the run isn't active or the approval id isn't pending.
    """
    raw_decision = (body.get("decision") or "").strip().lower()
    if raw_decision not in ("grant", "deny"):
        raise HTTPException(
            status_code=400,
            detail="decision must be 'grant' or 'deny'",
        )
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise HTTPException(status_code=400, detail="note must be a string")
    # opencode harness tool permissions (kind "tool_permission"): a grant may be
    # for this call only ("once") or for the rest of the run ("run").
    scope = str(body.get("scope") or "once").lower()
    if scope not in ("once", "run"):
        raise HTTPException(status_code=400, detail="scope must be 'once' or 'run'")

    registry = request.app.state.run_registry
    workspace = await registry.get(run_id)
    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} is not active",
        )

    decision_word = "granted" if raw_decision == "grant" else "denied"
    req = workspace.record_approval(approval_id, decision_word, note=note)
    if req is None:
        raise HTTPException(
            status_code=404,
            detail=f"approval {approval_id!r} not pending on run {run_id!r}",
        )

    sig_type = (
        SignalType.APPROVAL_GRANTED if decision_word == "granted"
        else SignalType.APPROVAL_DENIED
    )
    signal = Signal(
        type=sig_type,
        summary=f"approval {decision_word}: {req.summary}",
        detail=(f"approval_id={req.id}; kind={req.kind}"
                + (f"; scope={scope}" if decision_word == "granted" else "")),
        from_agent="user",
        iteration=workspace.iteration,
    )
    workspace.post_signal(signal)
    return {
        "ok": True,
        "approval_id": req.id,
        "decision": decision_word,
        "scope": scope if decision_word == "granted" else None,
        "type": sig_type.value,
    }


@router.post("/{run_id}/comment", dependencies=[Depends(require_bearer)])
async def post_user_comment(
    request: Request,
    run_id: str,
    body: dict = Body(...),
) -> dict:
    """Post a USER_COMMENT (or USER_COMMENT_PRIORITY) to the live run's
    workspace. Agents check the user_board at step boundaries and absorb
    new comments — see DESIGN §6.

    Returns 410 if the run isn't active; the user_board is mid-run only.
    Comments on a finished run have no audience.
    """
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    raw_priority = (body.get("priority") or "normal").lower()
    if raw_priority not in ("normal", "high"):
        raise HTTPException(status_code=400, detail="priority must be 'normal' or 'high'")

    registry = request.app.state.run_registry
    workspace = await registry.get(run_id)
    if workspace is None:
        raise HTTPException(
            status_code=410,
            detail=f"run {run_id!r} is not active; comments only land mid-run",
        )

    sig_type = (
        SignalType.USER_COMMENT_PRIORITY if raw_priority == "high"
        else SignalType.USER_COMMENT
    )
    signal = Signal(
        type=sig_type,
        summary=text,
        from_user=True,
        priority=raw_priority,
        iteration=workspace.iteration,
    )
    workspace.post_signal(signal)
    return {
        "ok": True,
        "signal_id": str(signal.id),
        "type": sig_type.value,
        "iteration": workspace.iteration,
    }


class WakeBody(BaseModel):
    """Body of POST /{run_id}/overseer/wake."""

    text: str = Field(default="The owner asked you to look at the run.", max_length=2000)


@router.post("/{run_id}/overseer/wake", dependencies=[Depends(require_bearer)])
async def wake_overseer(request: Request, run_id: str, body: WakeBody | None = None) -> dict:
    """Wake the overseer by hand: posts an ESCALATION from the owner, which the escalation
    detector turns into an overseer wake at the next watchdog pass. 410 if the run is not
    active, 409 if the run has no overseer (plain-roles runs have none)."""
    workspace = await request.app.state.run_registry.get(run_id)
    if workspace is None:
        raise HTTPException(status_code=410, detail=f"run {run_id!r} is not active")
    roles = {s.from_agent for s in workspace.board} | set(getattr(workspace, "agent_roles", []) or [])
    team = next((s for s in reversed(workspace.board) if s.type is SignalType.TEAM), None)
    team_text = f"{team.summary} {team.detail or ''}" if team is not None else ""
    if "overseer" not in team_text and not any("overseer" in r for r in roles if r):
        raise HTTPException(status_code=409, detail="this run has no overseer: it only runs "
                            "when the team has a planner (task cards)")
    text = ((body.text if body else "") or "The owner asked you to look at the run.").strip()
    signal = Signal(type=SignalType.ESCALATION, summary=f"owner: {text}"[:300], detail=text,
                    from_agent="owner", iteration=workspace.iteration)
    workspace.post_signal(signal)
    return {"ok": True, "signal_id": str(signal.id)}


class RespondBody(BaseModel):
    """Body of POST /{run_id}/respond."""

    answer: str = Field(min_length=1, max_length=4000)


@router.post("/{run_id}/respond", dependencies=[Depends(require_bearer)])
async def respond_to_pause(request: Request, run_id: str, body: RespondBody) -> dict:
    """Answer whatever paused the run (an agent's ask_human, the overseer's wake cap,
    a blocked cycle) and let it carry on.

    Posts HUMAN_RESPONDED (carrying the pending `question_id` when the pause came from
    ask_human, so the waiting tool call returns), clears `pending_human`, posts the answer
    as a priority comment so every role sees it in its next brief, and wakes the scheduler.
    410 if the run is not active, 409 if nothing is pending.
    """
    answer = body.answer.strip()
    if not answer:
        raise HTTPException(status_code=400, detail="answer is required")
    workspace = await request.app.state.run_registry.get(run_id)
    if workspace is None:
        raise HTTPException(status_code=410, detail=f"run {run_id!r} is not active")
    pending = workspace.pending_human
    if pending is None:
        raise HTTPException(status_code=409, detail="nothing is waiting for an answer")
    if pending.from_agent == "system":
        raise HTTPException(status_code=409, detail="the run was stopped; it cannot be resumed")

    question_id: str | None = None
    for sig in reversed(workspace.board):
        if sig.type is SignalType.HUMAN_NEEDED:
            try:
                raw = json.loads(sig.detail or "{}")
            except ValueError:
                raw = {}
            if isinstance(raw, dict) and raw.get("question_id"):
                question_id = str(raw["question_id"])
            break
    detail: dict[str, Any] = {"answer": answer}
    if question_id:
        detail["question_id"] = question_id
    responded = Signal(
        type=SignalType.HUMAN_RESPONDED, summary=answer, detail=json.dumps(detail),
        from_user=True, iteration=workspace.iteration,
    )
    workspace.post_signal(responded)
    workspace.pending_human = None
    workspace.post_signal(Signal(
        type=SignalType.USER_COMMENT_PRIORITY, summary=answer, from_user=True,
        priority="high", iteration=workspace.iteration,
    ))
    wake = workspace.controls.get("wake")
    if wake is not None:
        wake()
    return {"ok": True, "signal_id": str(responded.id), "question_id": question_id}


class LimitsBody(BaseModel):
    """Body of PATCH /{run_id}/limits."""

    budget_usd: float | None = None
    extend_minutes: int | None = None
    # plan §11.6: "none" removes the time limit, "soft" lets running work finish, "hard"
    # ends the session on time.
    time_limit: Literal["none", "soft", "hard"] | None = None


MAX_BUDGET_USD = 1000.0


@router.patch("/{run_id}/limits", dependencies=[Depends(require_bearer)])
async def change_limits(request: Request, run_id: str, body: LimitsBody) -> dict:
    """Raise a live run's spend cap and/or push its deadline out.

    `budget_usd` must exceed what the run has spent and be at most 1000;
    `extend_minutes` (1..240) is added to the run's deadline; `time_limit` switches how the
    deadline acts (none / soft / hard). A later deadline or "none" ends a soft-limit drain,
    so new work starts again.
    """
    import time

    registry = request.app.state.run_registry
    workspace = await registry.get(run_id)
    if workspace is None:
        raise HTTPException(status_code=410, detail=f"run {run_id!r} is not active")
    if body.budget_usd is None and body.extend_minutes is None and body.time_limit is None:
        raise HTTPException(status_code=400,
                            detail="give budget_usd, extend_minutes and/or time_limit")
    if (body.time_limit in ("soft", "hard") and workspace.deadline_at is None
            and body.extend_minutes is None):
        raise HTTPException(status_code=400,
                            detail="this session has no time limit; give extend_minutes too")
    if body.extend_minutes is not None and not 1 <= body.extend_minutes <= 240:
        raise HTTPException(status_code=400, detail="extend_minutes must be 1..240")
    spend_fn = workspace.controls.get("spend")
    spend = float(spend_fn()) if spend_fn is not None else 0.0
    if body.budget_usd is not None:
        if body.budget_usd > MAX_BUDGET_USD:
            raise HTTPException(status_code=400, detail=f"budget_usd must be <= {MAX_BUDGET_USD:g}")
        if body.budget_usd <= spend:
            raise HTTPException(
                status_code=400,
                detail=f"budget_usd must be above the ${spend:.4f} already spent")
        if workspace.controls.get("set_budget") is None:
            raise HTTPException(status_code=409, detail="this run has no live budget to change")

    changes: dict[str, Any] = {}
    if body.budget_usd is not None:
        workspace.controls["set_budget"](body.budget_usd)
        changes["budget_usd"] = body.budget_usd
    if body.time_limit == "none":
        workspace.deadline_at = None
        workspace.draining = False
        changes["time_limit"] = "none"
    elif body.time_limit is not None:
        workspace.deadline_mode = body.time_limit
        changes["time_limit"] = body.time_limit
    if body.extend_minutes is not None and body.time_limit != "none":
        base = max(workspace.deadline_at or time.time(), time.time())
        workspace.deadline_at = base + body.extend_minutes * 60
        workspace.draining = False      # a later deadline: new work may start again
        changes["extend_minutes"] = body.extend_minutes
    workspace.post_signal(Signal(
        type=SignalType.RUN_LIMITS_CHANGED, iteration=workspace.iteration,
        summary="run limits changed: " + ", ".join(f"{k}={v}" for k, v in changes.items()),
        detail=json.dumps({**changes, "deadline_at": workspace.deadline_at, "spend_usd": spend}),
    ))
    wake = workspace.controls.get("wake")
    if wake is not None:
        wake()
    runner = await registry.get_runner(run_id)
    budget = body.budget_usd
    if budget is None and runner is not None:
        gate = runner.project.approval_gates.get("token_budget_usd")
        budget = float(gate) if isinstance(gate, (int, float)) else None
    return {"ok": True, "budget_usd": budget, "spend_usd": spend,
            "deadline_at": workspace.deadline_at,
            "time_limit": "none" if workspace.deadline_at is None else workspace.deadline_mode}


class VisualFeedback(BaseModel):
    """Body of POST /{run_id}/visual_feedback."""

    request_id: str = Field(min_length=1)
    feedback: str = Field(min_length=1)


@router.post("/{run_id}/visual_feedback", dependencies=[Depends(require_bearer)])
async def post_visual_feedback(request: Request, run_id: str, body: VisualFeedback) -> dict:
    """Answer an agent's `request_visual_feedback` with a USER_VISUAL_FEEDBACK signal.

    The `request_id` comes from the SCREENSHOT_READY signal the agent posted. The waiting
    agent resumes with `feedback`; the task flow also files it on the requester's card.
    410 if the run is not active (nobody is waiting).
    """
    text = body.feedback.strip()
    if not text:
        raise HTTPException(status_code=400, detail="feedback is required")
    workspace = await request.app.state.run_registry.get(run_id)
    if workspace is None:
        raise HTTPException(status_code=410, detail=f"run {run_id!r} is not active")
    signal = Signal(
        type=SignalType.USER_VISUAL_FEEDBACK, summary=text, from_user=True,
        detail=json.dumps({"request_id": body.request_id, "feedback": text}),
        iteration=workspace.iteration)
    workspace.post_signal(signal)
    return {"ok": True, "signal_id": str(signal.id), "type": signal.type.value}


@router.get("/{run_id}/tags")
async def list_run_tags(request: Request, run_id: str) -> list[dict]:
    """List git review-point tags for a run (Phase G.5).

    The runner auto-tags every APPROVED merge as
    `review/<iteration>/<reviewer-role>`. This endpoint reads them
    off the run's working/ git repo so the studio can show them in
    a "review points" panel.

    Returns 404 if the run has no working/ git repo (run never
    started, or never persisted).
    """
    from git import InvalidGitRepositoryError, NoSuchPathError, Repo

    data_root = Path(request.app.state.data_root)
    working_dir = _run_working_dir(data_root, run_id)
    try:
        repo = Repo(working_dir)
    except (InvalidGitRepositoryError, NoSuchPathError):
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} has no git workspace",
        )

    out: list[dict] = []
    for tag in repo.tags:
        commit = tag.commit
        tag_obj = getattr(tag, "tag", None)
        out.append({
            "name": tag.name,
            "sha": commit.hexsha,
            "short_sha": commit.hexsha[:7],
            "message": tag_obj.message if tag_obj is not None else "",
            "commit_message": commit.message.strip(),
            "tagged_at": (
                tag_obj.tagged_date if tag_obj is not None else commit.committed_date
            ),
        })
    out.sort(key=lambda t: t["tagged_at"])
    return out


@router.get("/{run_id}/deliverables/{path:path}")
async def download_deliverable(request: Request, run_id: str, path: str):
    deliverables = _store(request).deliverables_dir(run_id)
    target = (deliverables / path).resolve()
    deliverables_resolved = deliverables.resolve()
    try:
        target.relative_to(deliverables_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes deliverables/")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"deliverable {path!r} not found")
    return FileResponse(target)


@router.post(
    "/{run_id}/resume",
    dependencies=[Depends(require_bearer)],
    status_code=202,
)
async def resume_run(request: Request, run_id: str) -> dict:
    """Re-enqueue a Job for an existing run (Phase G-prime.2).

    The worker dequeues the job, sees `payload.resume == True`, calls
    `core.run_loader.restore_workspace` to rebuild the workspace from
    disk (board.jsonl + artifacts.json), and drives the scheduler from
    the restored state. Completed agents don't re-run; failed agents
    pick up where they left off.

    Status codes:
    - 202: job enqueued, returns the new `job_id` (run_id is reused)
    - 400: run is already complete (no point resuming)
    - 404: run doesn't exist on disk
    - 409: a non-terminal job exists for this run (queued or running)
    """
    meta = await _store(request).get(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")

    status = (meta.get("status") or "").lower()
    if status == "complete":
        raise HTTPException(
            status_code=400,
            detail=f"run {run_id!r} is already complete; nothing to resume",
        )

    queue = request.app.state.queue
    existing_jobs = await queue.list(limit=500)
    active = [
        j for j in existing_jobs
        if j.run_id == run_id and j.status in (JOB_QUEUED, JOB_RUNNING)
    ]
    if active:
        raise HTTPException(
            status_code=409,
            detail=(
                f"run {run_id!r} already has an active job "
                f"({active[0].job_id}, status={active[0].status})"
            ),
        )

    project_id = meta.get("project_id")
    if not project_id:
        raise HTTPException(
            status_code=500,
            detail=f"run {run_id!r} meta has no project_id",
        )

    job_id = f"job_{uuid4().hex[:10]}"
    await queue.enqueue(Job(
        job_id=job_id,
        project_id=project_id,
        run_id=run_id,
        payload={"resume": True},
    ))

    return {
        "accepted": True,
        "run_id": run_id,
        "job_id": job_id,
        "project_id": project_id,
    }


@router.post("/{run_id}/stop", dependencies=[Depends(require_bearer)])
async def stop_run(request: Request, run_id: str) -> dict:
    """Hard-cancel an active run.

    Sets workspace.pending_human (so the scheduler stops new spawns) and
    cancels all in-flight agent tasks immediately via runner.force_stop().
    Idempotent — a second call is a no-op.

    Returns 404 if the run isn't currently active in the registry.
    """
    from core.workspace import HumanRequest

    registry = request.app.state.run_registry
    workspace = await registry.get(run_id)
    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} is not active",
        )

    if workspace.pending_human is None:
        workspace.pending_human = HumanRequest(
            from_agent="system",
            question="Run stopped by user.",
            iteration=workspace.iteration,
        )
        workspace.post_signal(Signal(
            type=SignalType.HUMAN_NEEDED,
            summary="Run stopped by user",
            detail="A user pressed Stop. All in-flight agents are being cancelled.",
            from_agent="system",
            iteration=workspace.iteration,
        ))

        runner = await registry.get_runner(run_id)
        if runner is not None:
            await runner.force_stop()

    return {"ok": True, "run_id": run_id}


@router.patch("/{run_id}/agent_config/{role}", dependencies=[Depends(require_bearer)])
async def patch_agent_config(
    request: Request,
    run_id: str,
    role: str,
    body: dict[str, Any] = Body(...),
) -> dict:
    """Hot-reload an agent's config for the live run.

    Applies supported fields (declared_local_tools, model_tier, etc.) to the
    in-memory agent so the next iteration picks them up. Returns 404 if the
    run isn't active or the role doesn't exist.
    """
    registry = request.app.state.run_registry
    runner = await registry.get_runner(run_id)
    if runner is None:
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} is not active — changes will apply to future runs only",
        )
    try:
        runner.update_agent_config(role, body)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {"ok": True, "run_id": run_id, "role": role}


# ── File content endpoints (M6.C4) ────────────────────────────────────────────

_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".whl", ".exe", ".bin",
    ".pyc", ".pyd", ".so", ".dll",
}
_MAX_FILE_BYTES = 500 * 1024  # 500 KB


def _safe_workspace_path(run_id: str, rel_path: str, data_root: str) -> Path:
    """Resolve rel_path under the run's working/ dir with path-escape check."""
    working = _run_working_dir(Path(data_root), run_id).resolve()
    target = (working / rel_path).resolve()
    try:
        target.relative_to(working)
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes workspace")
    return target


def _is_binary(path: Path, sample: bytes) -> bool:
    if path.suffix.lower() in _BINARY_EXTENSIONS:
        return True
    return b"\x00" in sample[:8192]


@router.get("/{run_id}/files", dependencies=[Depends(require_bearer)])
async def get_file_content(
    request: Request,
    run_id: str,
    path: str = Query(..., description="relative path within workspace/working/"),
    ref: str | None = Query(None, description="git SHA to read from; absent = working tree"),
) -> dict:
    """Return the content of a workspace file (M6.C4).

    Response: {path, content, encoding: "utf-8"|"base64"}
    Binary or unreadable files: {path, error: "binary or too large"}
    """
    import base64

    data_root = request.app.state.data_root
    target = _safe_workspace_path(run_id, path, data_root)

    if ref:
        # Read via git show
        from git import InvalidGitRepositoryError, NoSuchPathError, Repo
        working = _run_working_dir(Path(data_root), run_id).resolve()
        try:
            repo = Repo(working)
            content_bytes = repo.git.show(f"{ref}:{path}").encode("utf-8", errors="replace")
        except (InvalidGitRepositoryError, NoSuchPathError):
            raise HTTPException(status_code=404, detail="no git workspace")
        except Exception:
            raise HTTPException(status_code=404, detail=f"path {path!r} not found at ref {ref!r}")
    else:
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"file {path!r} not found")
        size = target.stat().st_size
        if size > _MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="file exceeds 500 KB limit")
        content_bytes = target.read_bytes()

    if _is_binary(target, content_bytes):
        return {"path": path, "error": "binary or too large"}

    try:
        text = content_bytes.decode("utf-8")
        return {"path": path, "content": text, "encoding": "utf-8"}
    except UnicodeDecodeError:
        return {"path": path, "content": base64.b64encode(content_bytes).decode(), "encoding": "base64"}


@router.get("/{run_id}/files/history", dependencies=[Depends(require_bearer)])
async def get_file_history(
    request: Request,
    run_id: str,
    path: str = Query(..., description="relative path within workspace/working/"),
) -> list[dict]:
    """Return commits that touched a file (M6.C4).

    Response: [{sha, subject, author_role, committed_at}, ...] descending time.
    Capped at 50 entries.
    """
    from git import InvalidGitRepositoryError, NoSuchPathError, Repo

    data_root = request.app.state.data_root
    working = _run_working_dir(Path(data_root), run_id).resolve()
    try:
        repo = Repo(working)
    except (InvalidGitRepositoryError, NoSuchPathError):
        raise HTTPException(status_code=404, detail="no git workspace")

    try:
        commits = list(repo.iter_commits("--all", paths=path, max_count=50))
    except Exception:
        return []

    # Build sha → branch map
    sha_to_branch: dict[str, str] = {}
    for ref in repo.references:
        if hasattr(ref, "commit"):
            try:
                for c in repo.iter_commits(ref, max_count=200):
                    if c.hexsha not in sha_to_branch:
                        sha_to_branch[c.hexsha] = ref.name
            except Exception:
                pass

    out: list[dict] = []
    for commit in commits:
        branch = sha_to_branch.get(commit.hexsha, "")
        if branch.startswith("agent/"):
            author_role = branch[len("agent/"):]
        elif branch in ("main", "master"):
            author_role = "runner"
        else:
            author_role = branch or "unknown"

        out.append({
            "sha": commit.hexsha,
            "subject": commit.message.strip().splitlines()[0] if commit.message else "",
            "author_role": author_role,
            "committed_at": commit.committed_date,
        })

    return out


@router.get("/{run_id}/git/branches", dependencies=[Depends(require_bearer)])
async def get_git_branches(request: Request, run_id: str) -> list[str]:
    """Return all branches in the run's git workspace.

    Includes ``main`` first (seed commit + landed work) and then every
    ``agent/{role}`` / ``{persona}/{role}`` branch. Without main in the
    list, users can't see the imported seed state until an agent commits.

    Response: ["main", "diana/game_architect", ...]
    """
    from git import InvalidGitRepositoryError, NoSuchPathError, Repo

    data_root = request.app.state.data_root
    working = _run_working_dir(Path(data_root), run_id).resolve()
    try:
        repo = Repo(working)
    except (InvalidGitRepositoryError, NoSuchPathError):
        raise HTTPException(status_code=404, detail="no git workspace")

    main_branches = [
        h.name for h in repo.heads if h.name in ("main", "master")
    ]
    other_branches = sorted(
        h.name for h in repo.heads if h.name not in ("main", "master")
    )
    return main_branches + other_branches


@router.get("/{run_id}/git/tree", dependencies=[Depends(require_bearer)])
async def get_git_tree(
    request: Request,
    run_id: str,
    branch: str = Query(..., description="branch name, e.g. agent/developer"),
) -> list[dict]:
    """Return the full file tree of a branch via git ls-tree.

    Response: [{path, type: "blob"|"tree", size: int|null}, ...]
    Only blobs at any depth are returned (trees are omitted from the list).
    """
    from git import InvalidGitRepositoryError, NoSuchPathError, Repo

    data_root = request.app.state.data_root
    working = _run_working_dir(Path(data_root), run_id).resolve()
    try:
        repo = Repo(working)
    except (InvalidGitRepositoryError, NoSuchPathError):
        raise HTTPException(status_code=404, detail="no git workspace")

    if branch not in [h.name for h in repo.heads]:
        raise HTTPException(status_code=404, detail=f"branch {branch!r} not found")

    try:
        tree = repo.heads[branch].commit.tree
    except Exception:
        raise HTTPException(status_code=404, detail="could not read tree")

    out: list[dict] = []
    def _walk(t, prefix: str = "") -> None:
        for blob in t.blobs:
            out.append({"path": f"{prefix}{blob.name}", "type": "blob", "size": blob.size})
        for sub in t.trees:
            _walk(sub, f"{prefix}{sub.name}/")

    _walk(tree)
    out.sort(key=lambda f: f["path"])
    return out


# ── Track 1: iteration metrics + context log ─────────────────────────────────


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file and return a list of parsed objects. Returns [] if missing."""
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


@router.get("/{run_id}/iteration_metrics", dependencies=[Depends(require_bearer)])
async def get_iteration_metrics(request: Request, run_id: str) -> list[dict]:
    """Return all IterationMetrics rows for a run (Track 1).

    Reads iteration_metrics.jsonl from the run root directory.
    Returns 404 if the run directory doesn't exist at all.
    """
    data_root = Path(request.app.state.data_root)
    run_root = data_root / "runs" / run_id
    if not run_root.exists():
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return _read_jsonl(run_root / "iteration_metrics.jsonl")


@router.get("/{run_id}/console", dependencies=[Depends(require_bearer)])
async def get_console(request: Request, run_id: str, tail: int = 400) -> dict:
    """The run's host-side console (scheduler, spawns, errors) and each role's opencode
    server log tail: why something did not happen. 404 if the run does not exist."""
    from core.harness.run_console import read_console
    run_root = Path(request.app.state.data_root) / "runs" / run_id
    if not run_root.exists():
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return read_console(run_root, tail=max(1, min(tail, 2000)))


@router.get("/{run_id}/context_log", dependencies=[Depends(require_bearer)])
async def get_context_log(request: Request, run_id: str) -> list[dict]:
    """Return all ContextSnapshot rows for a run (Track 1).

    Reads context_log.jsonl from the run root directory.
    Returns 404 if the run directory doesn't exist at all.
    """
    data_root = Path(request.app.state.data_root)
    run_root = data_root / "runs" / run_id
    if not run_root.exists():
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return _read_jsonl(run_root / "context_log.jsonl")


@router.get("/{run_id}/agent_turns", dependencies=[Depends(require_bearer)])
async def get_agent_turns(
    request: Request,
    run_id: str,
    role: str | None = None,
) -> list[dict]:
    """Return all agent turn records for a run.

    Each entry contains the full message thread (prompt, tool calls, tool
    results, final LLM response) plus token counts and timing.
    Pass ?role=<role> to filter to a specific agent.
    """
    data_root = Path(request.app.state.data_root)
    run_root = data_root / "runs" / run_id
    if not run_root.exists():
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    rows = _read_jsonl(run_root / "agent_turns.jsonl")
    if role:
        rows = [r for r in rows if r.get("role") == role]
    return rows

