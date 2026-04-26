"""Tests for worker/queue.py."""

from __future__ import annotations

import asyncio

import pytest

from worker.queue import (
    InMemoryJobQueue,
    JOB_COMPLETE,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    Job,
)


def _job(job_id="j1", project_id="p1", run_id="r1") -> Job:
    return Job(job_id=job_id, project_id=project_id, run_id=run_id)


async def test_enqueue_and_dequeue():
    q = InMemoryJobQueue()
    job = _job()
    await q.enqueue(job)
    out = await q.dequeue(timeout=1.0)
    assert out is job


async def test_dequeue_timeout():
    q = InMemoryJobQueue()
    out = await q.dequeue(timeout=0.05)
    assert out is None


async def test_get_returns_job_after_enqueue():
    q = InMemoryJobQueue()
    job = _job()
    await q.enqueue(job)
    assert await q.get("j1") is job
    assert await q.get("ghost") is None


async def test_update_status_sets_timestamps():
    q = InMemoryJobQueue()
    job = _job()
    await q.enqueue(job)
    assert job.status == JOB_QUEUED
    await q.update_status("j1", JOB_RUNNING)
    assert job.status == JOB_RUNNING
    assert job.started_at is not None

    await q.update_status("j1", JOB_COMPLETE)
    assert job.status == JOB_COMPLETE
    assert job.finished_at is not None


async def test_update_status_with_error():
    q = InMemoryJobQueue()
    job = _job()
    await q.enqueue(job)
    await q.update_status("j1", JOB_FAILED, error="boom")
    assert job.status == JOB_FAILED
    assert job.error == "boom"


async def test_list_returns_recent_first():
    q = InMemoryJobQueue()
    j1 = Job(job_id="j1", project_id="p", run_id="r1", enqueued_at="2026-04-01T00:00:00Z")
    j2 = Job(job_id="j2", project_id="p", run_id="r2", enqueued_at="2026-04-02T00:00:00Z")
    await q.enqueue(j1)
    await q.enqueue(j2)
    items = await q.list()
    assert items[0].job_id == "j2"
    assert items[1].job_id == "j1"


async def test_list_filtered_by_project():
    q = InMemoryJobQueue()
    await q.enqueue(_job("j1", project_id="p1"))
    await q.enqueue(_job("j2", project_id="p2"))
    items = await q.list(project_id="p1")
    assert len(items) == 1
    assert items[0].project_id == "p1"


async def test_list_respects_limit():
    q = InMemoryJobQueue()
    for i in range(10):
        await q.enqueue(_job(f"j{i}"))
    items = await q.list(limit=3)
    assert len(items) == 3


async def test_update_unknown_status_is_noop():
    q = InMemoryJobQueue()
    # Should not raise.
    await q.update_status("ghost", JOB_RUNNING)
