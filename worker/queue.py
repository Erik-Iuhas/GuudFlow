"""Job queue — abstraction layer.

DESIGN §16. Stage 6 ships an in-memory queue (good for dev + tests).
Stage 7 will add a Redis-backed implementation behind the same Protocol.

A `Job` represents one queued run. The API enqueues jobs at invoke time;
the worker (Stage 7) drains the queue and runs each via `AgentRunner`.
For Stage 6, an in-process worker can drain the queue inline.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_COMPLETE = "complete"
JOB_FAILED = "failed"


@dataclass
class Job:
    """One queued run."""

    job_id: str
    project_id: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = JOB_QUEUED
    enqueued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None


class JobQueue(Protocol):
    """Minimal queue contract. The InMemoryJobQueue satisfies this; a
    Redis variant (Stage 7) does too."""

    async def enqueue(self, job: Job) -> None: ...
    async def dequeue(self, timeout: float | None = None) -> Job | None: ...
    async def get(self, job_id: str) -> Job | None: ...
    async def update_status(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
    ) -> None: ...
    async def list(self, project_id: str | None = None, limit: int = 50) -> list[Job]: ...


class InMemoryJobQueue:
    """In-memory queue. Suitable for dev, tests, and single-process MVP."""

    def __init__(self):
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, job: Job) -> None:
        async with self._lock:
            self._jobs[job.job_id] = job
        await self._queue.put(job)

    async def dequeue(self, timeout: float | None = None) -> Job | None:
        try:
            if timeout is None:
                job = await self._queue.get()
            else:
                job = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return job

    async def get(self, job_id: str) -> Job | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def update_status(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = status
            now = datetime.now(timezone.utc).isoformat()
            if status == JOB_RUNNING and job.started_at is None:
                job.started_at = now
            if status in (JOB_COMPLETE, JOB_FAILED):
                job.finished_at = now
            if error:
                job.error = error

    async def list(self, project_id: str | None = None, limit: int = 50) -> list[Job]:
        async with self._lock:
            jobs = list(self._jobs.values())
        if project_id is not None:
            jobs = [j for j in jobs if j.project_id == project_id]
        jobs.sort(key=lambda j: j.enqueued_at, reverse=True)
        return jobs[:limit]
