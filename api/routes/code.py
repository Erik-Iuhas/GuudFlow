"""The Code tab (plan §15): read-only tree and file views of a project's code.

Three sources: the project's accepted `main`, a run's `main` (compared with where the run
started) and one role's worktree (unmerged edits included). Nothing here writes.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import require_bearer
from api.routes.runs import _run_working_dir
from core.code_browser import CodeBrowserError, Source, read_file, tree
from core.harness.artifacts import safe_resolve
from core.project_repo import ProjectRepo

router = APIRouter(tags=["code"], dependencies=[Depends(require_bearer)])


def _one_part(*names: str) -> None:
    for name in names:
        if not name or len(Path(name).parts) != 1 or name in (".", ".."):
            raise HTTPException(status_code=404, detail="not found")


async def _project_source(request: Request, project_id: str) -> Source:
    _one_part(project_id)
    if await request.app.state.project_store.get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    repo = ProjectRepo(Path(request.app.state.data_root), project_id)
    if not repo.exists():
        raise HTTPException(status_code=404, detail="the project has no code yet")
    return Source.project(repo.path)


def _run_root(request: Request, run_id: str) -> Path:
    _one_part(run_id)
    data_root = Path(request.app.state.data_root)
    run_root = safe_resolve(data_root / "runs", run_id)
    if run_root is None or not run_root.is_dir():
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return run_root


def _run_base(run_root: Path, working: Path) -> str | None:
    """The commit the run started from (`project_run.json`), else the run repo's first commit."""
    from core.project_runs import PROJECT_RUN_JSON
    meta_file = run_root / PROJECT_RUN_JSON
    try:
        base = str(json.loads(meta_file.read_text()).get("base") or "") if meta_file.is_file() else ""
    except (OSError, json.JSONDecodeError):
        base = ""
    if base:
        return base
    import subprocess
    proc = subprocess.run(["git", "-C", str(working), "rev-list", "--max-parents=0", "main"],
                          capture_output=True, text=True)
    roots = proc.stdout.split()
    return roots[0] if proc.returncode == 0 and roots else None


def _run_source(request: Request, run_id: str, role: str | None) -> Source:
    run_root = _run_root(request, run_id)
    working = _run_working_dir(Path(request.app.state.data_root), run_id)
    if not role:
        if not (working / ".git").exists():
            raise HTTPException(status_code=404, detail="the run has no code")
        return Source.run(working, _run_base(run_root, working))
    _one_part(role)
    git_dir = safe_resolve(run_root, Path("roles") / role / "git")
    work = safe_resolve(run_root, Path("roles") / role / "work")
    if git_dir is None or work is None or not git_dir.is_dir() or not work.is_dir():
        raise HTTPException(status_code=404, detail=f"role {role!r} has no checkout in run {run_id!r}")
    return Source.role(git_dir, work)


def _tree_body(src: Source) -> dict[str, Any]:  # Any: JSON
    try:
        t = tree(src)
    except CodeBrowserError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    body: dict[str, Any] = asdict(t)  # Any: JSON
    if src.worktree is not None:   # a role: which changed file was written last
        latest: tuple[float, str] | None = None
        for e in t.entries:
            if e.status in ("A", "M"):
                p = src.worktree / e.path
                try:
                    if not p.is_symlink():
                        stamp = (p.stat().st_mtime, e.path)
                        latest = stamp if latest is None or stamp > latest else latest
                except OSError:
                    continue
        body["last_edit"] = {"path": latest[1], "at": latest[0]} if latest else None
    return body


def _file_body(src: Source, path: str, diff: bool) -> dict[str, Any]:  # Any: JSON
    try:
        return asdict(read_file(src, path, with_diff=diff))
    except CodeBrowserError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/projects/{project_id}/code/tree")
async def project_tree(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """Every file on the project's accepted `main`."""
    src = await _project_source(request, project_id)
    return await asyncio.to_thread(_tree_body, src)


@router.get("/api/projects/{project_id}/code/file")
async def project_file(request: Request, project_id: str, path: str) -> dict[str, Any]:  # Any: JSON
    """One file on the project's accepted `main`."""
    src = await _project_source(request, project_id)
    return await asyncio.to_thread(_file_body, src, path, False)


@router.get("/api/projects/{project_id}/deps")
async def project_deps(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """The project's shared dependencies (plan §16): each manager's current packages."""
    from core.deps import ProjectDeps
    _one_part(project_id)
    if await request.app.state.project_store.get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    deps = ProjectDeps(Path(request.app.state.data_root) / "projects" / project_id / "deps")
    return {"environments": [asdict(env) for env in deps.all()]}


@router.get("/api/runs/{run_id}/code/roles")
async def run_code_roles(request: Request, run_id: str) -> dict[str, Any]:  # Any: JSON
    """The roles of a run that have a checkout to look at."""
    run_root = _run_root(request, run_id)
    roles_dir = run_root / "roles"
    roles = sorted(p.name for p in roles_dir.iterdir()
                   if (p / "work").is_dir() and (p / "git").is_dir()) if roles_dir.is_dir() else []
    return {"roles": roles}


@router.get("/api/runs/{run_id}/code/tree")
async def run_tree(request: Request, run_id: str, role: str | None = None) -> dict[str, Any]:  # Any: JSON
    """The run's `main` against where it started, or (`role=`) that role's worktree."""
    src = _run_source(request, run_id, role)
    return await asyncio.to_thread(_tree_body, src)


@router.get("/api/runs/{run_id}/code/file")
async def run_file(request: Request, run_id: str, path: str, role: str | None = None,
                   diff: bool = False) -> dict[str, Any]:  # Any: JSON
    """One file of the run's `main` or of a role's worktree; `diff=1` adds its diff."""
    src = _run_source(request, run_id, role)
    return await asyncio.to_thread(_file_body, src, path, diff)


# ── what each worker was told (worker context plan A) ────────────────────────
@router.get("/api/runs/{run_id}/briefs")
async def run_briefs(request: Request, run_id: str) -> dict[str, Any]:  # Any: JSON
    """Every role's recorded turns: `{roles: {role: [{n, card, at, size}]}}`."""
    from core.harness.brief_log import BriefLog
    log_ = BriefLog(_run_root(request, run_id))
    return {"roles": {r: [{"n": e.n, "card": e.card, "at": e.at, "size": e.size}
                          for e in log_.entries(r)] for r in log_.roles()}}


@router.get("/api/runs/{run_id}/roles/{role}/briefs/{n}")
async def run_brief(request: Request, run_id: str, role: str, n: int) -> dict[str, Any]:  # Any: JSON
    """One recorded turn: the rules files, the skills offered and the brief."""
    from core.harness.brief_log import BriefLog
    _one_part(role)
    try:
        text = BriefLog(_run_root(request, run_id)).read(role, n)
    except ValueError:
        text = None
    if text is None:
        raise HTTPException(status_code=404, detail="no such brief")
    return {"role": role, "n": n, "text": text}
