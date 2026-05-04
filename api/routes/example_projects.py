"""/api/example-projects — one-click project templates.

Two endpoints:

1. `GET /api/example-projects` — discover available templates from
   `data/example_projects/`. Public, mirrors the training scenarios
   listing pattern.
2. `POST /api/example-projects/{id}/instantiate` — auth-gated. Creates
   a new project from the template, copying any `seed_inputs/` content
   into the project's `inputs/uploads/` directory. Returns the created
   project so the caller can navigate straight to it.

Project id collisions are handled by suffixing `-2`, `-3`, …  unless
the caller supplies an explicit `project_id` in the body, in which
case a 409 is returned on collision. Goal can be overridden via the
body too (the studio modal pre-fills the template default but lets
the user edit before submit).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from api.auth import require_bearer
from core.example_projects import (
    ExampleProjectError,
    find_example_project,
    list_example_projects,
    materialize_project_config,
)


router = APIRouter(prefix="/api/example-projects", tags=["example-projects"])


def _templates_root(request: Request) -> Path:
    """Where templates live. Tests can override via
    `app.state.example_projects_root`; production falls back to
    `<repo>/data/example_projects/` regardless of `data_root` so the
    shipped catalog is always discoverable even when the user has
    pointed `data_root` at a fresh empty directory."""
    override = getattr(request.app.state, "example_projects_root", None)
    if override is not None:
        return Path(override)
    # Templates ship with the codebase, not with user data — resolve
    # relative to the repo root (this file lives at api/routes/).
    return Path(__file__).resolve().parent.parent.parent / "data" / "example_projects"


@router.get("")
async def list_templates(request: Request) -> list[dict[str, Any]]:
    """Returns every template under the templates root, sorted by id.
    Malformed templates are silently skipped (the loader logs them; one
    bad template shouldn't break the whole gallery)."""
    return [t.to_summary() for t in list_example_projects(_templates_root(request))]


@router.post(
    "/{template_id}/instantiate",
    dependencies=[Depends(require_bearer)],
    status_code=status.HTTP_201_CREATED,
)
async def instantiate(
    request: Request,
    template_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Create a project from a template.

    Body (all fields optional):
        project_id: str  — override the template's default. Collision
                           returns 409 instead of auto-suffixing.
        goal:       str  — override the template's default goal.

    Returns the created project config (same shape as
    `POST /api/projects` returns)."""
    root = _templates_root(request)
    try:
        template = find_example_project(root, template_id)
    except ExampleProjectError as e:
        raise HTTPException(status_code=500, detail=str(e))
    if template is None:
        raise HTTPException(
            status_code=404, detail=f"template {template_id!r} not found"
        )

    store = request.app.state.project_store

    explicit_id = body.get("project_id")
    if explicit_id is not None and not isinstance(explicit_id, str):
        raise HTTPException(status_code=400, detail="project_id must be a string")
    if explicit_id is not None and not explicit_id.strip():
        raise HTTPException(status_code=400, detail="project_id cannot be empty")

    if explicit_id:
        if (await store.get(explicit_id)) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"project {explicit_id!r} already exists",
            )
        project_id = explicit_id
    else:
        project_id = await _next_available_id(store, template.default_project_id)

    raw_goal = body.get("goal")
    if raw_goal is not None and not isinstance(raw_goal, str):
        raise HTTPException(status_code=400, detail="goal must be a string")
    goal = raw_goal if raw_goal else template.default_goal

    config = materialize_project_config(template, project_id, goal)
    try:
        created = await store.create(config)
    except ValueError as e:
        # Race window between _next_available_id and create — extremely
        # unlikely in dev (single-process), but surface honestly.
        raise HTTPException(status_code=409, detail=str(e))

    if template.seed_inputs_dir is not None:
        _copy_seed_inputs(template.seed_inputs_dir, store.uploads_dir(project_id))

    return {
        "project": created,
        "from_template": template_id,
    }


async def _next_available_id(store, base: str) -> str:
    """Return `base` if free, else `base-2`, `base-3`, … until one
    isn't taken. Bounded at 100 attempts so a pathologically polluted
    store surfaces a 500 instead of looping forever."""
    if (await store.get(base)) is None:
        return base
    for n in range(2, 101):
        candidate = f"{base}-{n}"
        if (await store.get(candidate)) is None:
            return candidate
    raise HTTPException(
        status_code=500,
        detail=f"could not find a free project id under {base!r} after 100 attempts",
    )


def _copy_seed_inputs(seed_dir: Path, uploads_dir: Path) -> None:
    """Copy every regular file under `seed_dir` (recursively) into
    `uploads_dir`. Mirrors the directory structure so a template can
    ship sub-folders if it needs to. Symlinks are followed once and
    then treated as the resolved file — no symlink chains land in
    user data."""
    uploads_dir.mkdir(parents=True, exist_ok=True)
    for src in seed_dir.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(seed_dir)
        dst = uploads_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
