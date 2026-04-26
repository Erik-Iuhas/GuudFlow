# Stage 7 — Worker & queue

**Budget:** 150-250k tokens
**Status:** done (2026-04-25)
**Exit criteria (CLAUDE.md):** Worker picks up jobs from queue; runs AgentRunner; updates status. docker_manager can be a stub in dev mode.

DESIGN sections: §16, §29.

## Files
- [x] `worker/queue.py` (Stage 6) — already shipped
- [x] `worker/docker_manager.py` ✅ — `DockerManager` with dev stub + production path; `CONTAINER_LIMITS` from §29 (network_mode none, mem 512m, pids 50, read_only, no-new-privileges, sized tmpfs); refuses to start in production without Docker; lazy SDK import
- [x] `worker/job_runner.py` ✅ — `serialise_project(dict→ProjectConfig)`; `JobRunner.run_one(job)` and `run_forever()`; persists `meta.json` + `board.json` per run; registers/unregisters live workspace with `RunRegistry` for the WebSocket bridge
- [x] `worker/README.md` ✅ — mode notes + dev/prod usage
- [x] `api/main.py` updated — lifespan spawns `JobRunner.run_forever()` task in dev, cancels on shutdown; `AGENTFLOW_DISABLE_INPROC_WORKER=1` opts out

## Tests
- `tests/worker/test_queue.py` (9 — Stage 6)
- `tests/worker/test_docker_manager.py` (10) — limits constants, dev stub start/exec/stop/multiple-runs, production rejects unreachable docker daemon, explicit mode override
- `tests/worker/test_job_runner.py` (8) — serialise_project minimal/full/missing, end-to-end 2-agent run via worker (developer + reviewer with scripted MockLLM, file written, branch merged, meta.json + board.json on disk), unknown project fails job, loader exception fails job, RunRegistry registration lifecycle, run_forever drain + stop

Total Stage 7 new tests: **18**. Full suite: **530 passed, 2 skipped**.

## Verification
- E2E worker test: enqueue Job → JobRunner.run_one() → drives AgentRunner with MockLLMClient → developer writes file via fs:write → auto-commit on agent/developer → reviewer git:diffs → APPROVED → branch merged → memory_writer creates episode → meta.json + board.json persisted → job status COMPLETE
- DockerManager dev stub returns ContainerHandle(is_stub=True), exec returns ExecResult(is_stub=True)
- DockerManager production with broken client raises DockerManagerError on construction
- API lifespan starts worker in dev, opts out via AGENTFLOW_DISABLE_INPROC_WORKER=1

## Notes
- The standalone `python -m worker.job_runner` entry point exists but currently has its own InMemoryJobQueue, which doesn't share work with the API process. Real cross-process coordination requires the Redis-backed queue (follow-on). For dev MVP the in-process worker in the API's lifespan is the supported path.
- Production DockerManager construction REQUIRES a successful `docker.ping()`. If the SDK is missing OR the daemon is unreachable, instantiation raises `DockerManagerError`. This implements CLAUDE.md "worker refuses to start in production without Docker" verbatim.
- Container resource limits (`CONTAINER_LIMITS`) are hardcoded per §29 rule 65 — not user-configurable. A test asserts the spec values directly so changes require a deliberate edit.
