"""FastAPI app — mounts all routers, initialises shared state.

DESIGN §16. Single ASGI app. Stores (projects, runs, personas) and the
job queue live on `app.state` so routes can reach them via the request.

Run with:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

# Load .env before anything else reads env vars. The Anthropic SDK
# pulls ANTHROPIC_API_KEY from os.environ at client construction
# time; the auth bearer pulls GUUDFLOW_API_TOKEN. Both need the
# .env values present before they're imported anywhere.
#
# `override=True` is intentional: an inherited shell environment
# may have stale or empty values for these keys (Claude Code
# sessions, IDE-launched terminals, parent CI shells). The .env
# file is the developer's explicit source of truth — let it win.
try:
    from dotenv import load_dotenv  # python-dotenv (in requirements.txt)
    load_dotenv(override=True)
except ImportError:
    # If python-dotenv isn't installed for some reason, fall back to
    # whatever's already in the process env. Better than crashing.
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agents.builtin import register_builtins
from api.routes import agents as agents_router
from api.routes import cards as cards_router
from api.routes import fs as fs_router
from api.routes import harness as harness_router
from api.routes import inputs as inputs_router
from api.routes import invoke as invoke_router
from api.routes import meta as meta_router
from api.routes import personas as personas_router
from api.routes import projects as projects_router
from api.routes import runs as runs_router
from api.routes import dashboard as dashboard_router
from api.routes import example_projects as example_projects_router
from api.routes import models as models_router
from api.routes import settings as settings_router
from api.routes import tools as tools_router
from api.routes import training as training_router
from api.storage import PersonaStore, ProjectStore, RunStore
from api.websocket import QuipRegistry, RunRegistry, router as ws_router
from worker.docker_manager import DockerManager
from worker.job_runner import JobRunner
from worker.queue import InMemoryJobQueue


def _data_root() -> Path:
    """Resolve the storage root.

    Track 4: routes through core/storage.py which honours the storage_mode
    toggle (`global` | `in-directory`). The legacy `GUUDFLOW_DATA_DIR` env
    var still works because it's read by storage.py as `GUUDFLOW_DATA_ROOT`
    via the `data_root` settings key — but the new env var name is
    `GUUDFLOW_DATA_ROOT` (or `GUUDFLOW_STORAGE_ROOT` in in-directory mode).
    """
    # Honour legacy GUUDFLOW_DATA_DIR for backward-compat: if set, treat it
    # as an explicit storage root in global mode.
    legacy = os.environ.get("GUUDFLOW_DATA_DIR")
    if legacy and not os.environ.get("GUUDFLOW_STORAGE_MODE"):
        return Path(legacy).resolve()
    from core import storage as _storage
    return _storage.data_root().resolve()


# Register catalog-able builtins at module import so `GET /api/agents/catalog`
# is populated even when the lifespan hasn't fired (test app, single-shot
# scripts). Idempotent.
register_builtins()


def _start_inproc_worker(app: FastAPI, data_root: Path) -> "asyncio.Task":
    """Build the in-process JobRunner for `data_root` and spawn its
    worker task. Stashes both on `app.state` and returns the task so the
    lifespan exit can await it.

    Used by lifespan startup AND by the settings PUT hot-reload path
    (Track 4.4) — when storage_root changes, we tear down the old
    JobRunner and start a new one bound to the new path so freshly
    enqueued runs land in the new `<root>/runs/`.
    """
    async def loader(project_id: str):
        return await app.state.project_store.get(project_id)

    app.state.job_runner = JobRunner(
        queue=app.state.queue,
        project_loader=loader,
        data_root=data_root,
        docker_manager=DockerManager(),
        run_registry=app.state.run_registry,
        quip_registry=app.state.quip_registry,
    )
    worker_task = asyncio.create_task(app.state.job_runner.run_forever())
    app.state.worker_task = worker_task
    return worker_task


async def restart_inproc_worker(app: FastAPI, data_root: Path) -> None:
    """Cleanly stop the current worker and start a new one bound to
    `data_root`. Caller must guarantee no jobs are mid-flight."""
    existing = getattr(app.state, "worker_task", None)
    runner = getattr(app.state, "job_runner", None)
    if existing is not None and runner is not None:
        runner.stop()
        try:
            await asyncio.wait_for(existing, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            existing.cancel()
    _start_inproc_worker(app, data_root)


def _recover_orphaned_runs(runs_dir: Path) -> None:
    """Mark any run still tagged status="running" as "stale".

    The in-process worker is the only thing that drives runs in dev mode,
    so a process restart leaves "running" rows behind that will never
    finalise. Reading status purely from disk would forever lie to the
    dashboard. Rewriting once at startup is safe — if the same run is
    re-enqueued (resume) it will overwrite back to "running" itself.

    Tolerant of malformed meta.json / missing dirs / permission errors —
    nothing here should crash startup.
    """
    if not runs_dir.exists():
        return
    import logging as _logging
    log = _logging.getLogger(__name__)
    recovered = 0
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        meta_path = run_dir / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("status") != "running":
            continue
        meta["status"] = "stale"
        meta["stale_reason"] = "worker process died before run finalised"
        try:
            meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            recovered += 1
        except OSError as exc:
            log.warning("could not rewrite meta.json for %s: %s", run_dir.name, exc)
    if recovered:
        log.info("recovered %d orphaned 'running' run(s) → 'stale'", recovered)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: build stores + queue + worker. Shutdown: stop the worker.

    In dev mode the worker drains the same in-memory queue the API enqueues
    to. In production the queue swaps to Redis and the worker runs as a
    separate process; this in-process worker is then disabled via
    `GUUDFLOW_DISABLE_INPROC_WORKER=1`.
    """
    data_root = _data_root()
    try:
        data_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # Don't crash startup — log and fall back to default.
        import logging
        logging.error(
            "Failed to create storage root %s: %s. "
            "Falling back to ./data — fix your settings to use the intended location.",
            data_root, e,
        )
        data_root = Path("./data").resolve()
        data_root.mkdir(parents=True, exist_ok=True)

    # Track 4: in in-directory mode, ensure the .flow/ scaffold exists.
    # init_flow_dir is idempotent — safe to call every startup.
    # ANY failure here MUST NOT crash the lifespan — log and continue with
    # whatever's there. Otherwise a misconfigured settings.json bricks the
    # backend until the user manually edits the file.
    from core import storage as _storage
    cfg = _storage.get_storage_config()
    if cfg.mode == "in-directory":
        try:
            from core.flow_dir import init_flow_dir, FlowDirError
            init_flow_dir(data_root.parent)
        except (FlowDirError, OSError, Exception) as e:
            import logging
            logging.warning(
                "init_flow_dir failed at %s: %s. "
                "In-directory mode will work as long as the path exists; "
                "fix your settings if this is unexpected.",
                data_root.parent, e,
            )

    app.state.data_root = data_root
    app.state.project_store = ProjectStore(data_root)
    app.state.run_store = RunStore(data_root / "runs")

    # Recover from orphaned "running" runs left behind when the previous
    # worker process died (uvicorn --reload restart, crash, hard kill).
    # In dev mode the in-process worker IS the only thing that runs these,
    # so any meta.json still saying "running" at startup is by definition
    # orphaned — mark them "stale" so the dashboard stops showing them
    # as live and the user can decide what to do with them.
    _recover_orphaned_runs(data_root / "runs")

    # Personas root is ALWAYS global regardless of storage_mode.
    # Track 4 §35 — personas cross directories by design.
    app.state.persona_store = PersonaStore(_storage.personas_root().resolve())
    app.state.queue = InMemoryJobQueue()
    app.state.run_registry = RunRegistry()
    app.state.quip_registry = QuipRegistry()

    worker_task = None
    if os.environ.get("GUUDFLOW_DISABLE_INPROC_WORKER") != "1":
        worker_task = _start_inproc_worker(app, data_root)

    try:
        yield
    finally:
        if app.state.__dict__.get("worker_task") is not None:
            app.state.job_runner.stop()
            try:
                await asyncio.wait_for(
                    app.state.worker_task, timeout=5.0,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                app.state.worker_task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(
        title="GuudFlow",
        description="Managed multi-agent orchestration platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("GUUDFLOW_CORS_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(projects_router.router)
    app.include_router(inputs_router.router)
    app.include_router(fs_router.router)
    app.include_router(agents_router.router)
    app.include_router(runs_router.router)
    app.include_router(cards_router.router)
    app.include_router(personas_router.router)
    app.include_router(meta_router.router)
    app.include_router(invoke_router.router)
    app.include_router(dashboard_router.router)
    app.include_router(example_projects_router.router)
    app.include_router(models_router.router)
    app.include_router(harness_router.router)
    app.include_router(settings_router.router)
    app.include_router(tools_router.router)
    app.include_router(training_router.router)
    app.include_router(ws_router)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    # If a studio bundle path is configured, serve it at the root so a
    # single container hosts the API + frontend. API routes registered
    # above take precedence — FastAPI matches exact + prefixed paths
    # before falling through to the static mount.
    studio_dist = os.environ.get("GUUDFLOW_STUDIO_DIST")
    if studio_dist and Path(studio_dist).is_dir():
        app.mount(
            "/",
            StaticFiles(directory=studio_dist, html=True),
            name="studio",
        )

    return app


app = create_app()
