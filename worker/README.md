# worker/

The worker drains the job queue and runs `AgentRunner` per job.
`docker_manager.py` owns the per-run container lifecycle (production)
or a no-op stub (dev).

## Files

- **queue.py** — `JobQueue` Protocol + `InMemoryJobQueue`. Stage 6.
- **docker_manager.py** — Container lifecycle. Stage 7. §29 resource
  limits hardcoded (rule 65). Production refuses to start without
  Docker available.
- **job_runner.py** — `JobRunner` long-loop and `run_one(job)` for
  single-job tests. Persists `meta.json` + `board.json` per run.

## Modes

`GUUDFLOW_MODE=dev` (default):

- DockerManager is a stub. `start()` / `stop()` are no-ops.
- `code-exec` runs on the host with §29 dev-mode hardening (env scrub,
  cwd pin, audit log, POSIX rlimits).

`GUUDFLOW_MODE=production`:

- DockerManager requires `docker` SDK + reachable daemon. Refuses to
  start otherwise.
- Each run gets an ephemeral container with `network_mode: none`,
  resource caps from `docker_manager.CONTAINER_LIMITS`, and a sized
  tmpfs at `/tmp`.
- `code-exec` routes inside the container via `docker exec`.

## Running the worker

In-process (default for dev MVP):

The API process can construct a `JobRunner` and spawn it as a background
task in its lifespan handler. Same `InMemoryJobQueue` instance is
shared between the API's enqueue path and the worker's drain.

Standalone (`python -m worker.job_runner`):

Sketchy until the queue moves to Redis — currently the standalone
worker has its own in-memory queue that the API doesn't see. Useful
for documentation and as a Stage-N+1 starting point.

## Test pattern

`tests/worker/test_job_runner.py` enqueues a job, runs `run_one`
synchronously with a scripted `MockLLMClient`, and asserts:

- The job status moves queued → running → complete
- `meta.json` and `board.json` are written under `data/runs/{run_id}/`
- The DockerManager stub was started and stopped exactly once
