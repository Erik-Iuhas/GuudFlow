"""FastAPI app — mounts all routers, initialises shared state.

DESIGN §16. Single ASGI app. Stores (projects, runs, personas) and the
job queue live on `app.state` so routes can reach them via the request.

Run with:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
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
from api.routes import fs as fs_router
from api.routes import inputs as inputs_router
from api.routes import invoke as invoke_router
from api.routes import meta as meta_router
from api.routes import personas as personas_router
from api.routes import projects as projects_router
from api.routes import runs as runs_router
from api.routes import dashboard as dashboard_router
from api.routes import models as models_router
from api.routes import tools as tools_router
from api.routes import training as training_router
from api.storage import PersonaStore, ProjectStore, RunStore
from api.websocket import QuipRegistry, RunRegistry, router as ws_router
from worker.docker_manager import DockerManager
from worker.job_runner import JobRunner
from worker.queue import InMemoryJobQueue


def _data_root() -> Path:
    return Path(os.environ.get("GUUDFLOW_DATA_DIR", "./data")).resolve()


# Register catalog-able builtins at module import so `GET /api/agents/catalog`
# is populated even when the lifespan hasn't fired (test app, single-shot
# scripts). Idempotent.
register_builtins()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: build stores + queue + worker. Shutdown: stop the worker.

    In dev mode the worker drains the same in-memory queue the API enqueues
    to. In production the queue swaps to Redis and the worker runs as a
    separate process; this in-process worker is then disabled via
    `GUUDFLOW_DISABLE_INPROC_WORKER=1`.
    """
    data_root = _data_root()
    data_root.mkdir(parents=True, exist_ok=True)

    app.state.data_root = data_root
    app.state.project_store = ProjectStore(data_root)
    app.state.run_store = RunStore(data_root / "runs")
    app.state.persona_store = PersonaStore(data_root / "personas")
    app.state.queue = InMemoryJobQueue()
    app.state.run_registry = RunRegistry()
    app.state.quip_registry = QuipRegistry()

    worker_task = None
    if os.environ.get("GUUDFLOW_DISABLE_INPROC_WORKER") != "1":
        async def loader(project_id: str):
            return await app.state.project_store.get(project_id)

        # LLM left as None so the JobRunner uses the default (Anthropic SDK).
        # Tests inject their own runner directly.
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

    try:
        yield
    finally:
        if worker_task is not None:
            app.state.job_runner.stop()
            try:
                await asyncio.wait_for(worker_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                worker_task.cancel()


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
    app.include_router(personas_router.router)
    app.include_router(meta_router.router)
    app.include_router(invoke_router.router)
    app.include_router(dashboard_router.router)
    app.include_router(models_router.router)
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
