# Stage 6 — API layer + loop guards

**Budget:** 200-300k tokens
**Status:** done (2026-04-25) — exit criteria met, plus loop-protection extras
**Exit criteria (CLAUDE.md):** POST a project; start a run via WebSocket; receive board events live; invoke endpoint returns 202 with bearer auth.

DESIGN sections: §7, §10, §16, §29 (auth), §35 (smaller-model story drove OpenAI-compat).

## Files

### Worker / queue
- [x] `worker/queue.py` ✅ — `Job`, `JobQueue` Protocol, `InMemoryJobQueue` (asyncio.Queue + lock), status lifecycle (queued→running→complete/failed) with timestamps and error capture

### API
- [x] `api/auth.py` ✅ — `require_bearer` dependency. Reads `GUUDFLOW_API_TOKEN`; permissive in dev when unset; refused in production mode. Uses `Depends(_bearer_scheme)` (CRITICAL — without `Depends` FastAPI mis-parses the dep function as a route signature, broke every protected POST until fixed)
- [x] `api/storage.py` ✅ — `ProjectStore` (atomic JSON via tempfile/shutil.move), `RunStore` (reads run dirs), `PersonaStore` (reads persona dirs, summarises with episode count + reputation)
- [x] `api/main.py` ✅ — FastAPI app + lifespan + CORS + `/health`
- [x] `api/websocket.py` ✅ — `RunRegistry` (active runs by id), `/ws/runs/{run_id}` endpoint that races queue.get() against websocket.receive() so disconnect breaks the loop. Falls back to disk snapshot for finished runs
- [x] `api/routes/projects.py` ✅ — list/get/create/update/delete; auth on writes; uses `Body(...)` for dict bodies
- [x] `api/routes/runs.py` ✅ — list/get/board/deliverable download; path-safety check on deliverables
- [x] `api/routes/personas.py` ✅ — list/get/episodes/patterns; skips `_example/` and other underscore-prefixed
- [x] `api/routes/meta.py` ✅ — POST /propose returns deterministic skeleton; placeholder until LLM-driven proposal lands
- [x] `api/routes/invoke.py` ✅ — POST `/api/invoke/{project_id}` returns 202 with run_id (DESIGN §10); GET `/{job_id}/status`; GET `/mcp/manifest` lists all projects as MCP tool definitions

### LLM adapters (§35 prep)
- [x] `core/llm.py` updates ✅ — `OpenAIClient` over httpx (covers OpenAI, Ollama, LM Studio, vLLM, OpenRouter); message-shape translation between our internal Anthropic-style format and OpenAI's flatter format (assistant tool_calls + role=tool); `ollama()` and `openai_compatible()` convenience factories

### Loop protection (post-Stage-6 follow-on)
- [x] `core/loop_guard.py` ✅ — `LoopGuardConfig`, `LoopGuard`, `LoopGuardVerdict`. Three detectors: wall-clock deadline (abort), repeated identical (name,args,error_class) for N hits (nudge), alternating cycle in last K calls (nudge), repeated error class across any args (nudge). Verdict either aborts the agent attempt or appends a `[LOOP GUARD]` user message to the next LLM turn so the model sees an explicit "try something different".
- [x] `Workspace.deadline_at` + `SchedulerStatus.DEADLINE_EXCEEDED` — run-level wall-clock budget enforced by the scheduler at the top of each iteration.
- [x] Runner integration — `_generic_tool_use_loop` instantiates LoopGuard, calls `record()` after each tool result, injects nudges, returns early on abort.

## Tests
- `tests/api/test_projects.py` (14)
- `tests/api/test_runs.py` (9)
- `tests/api/test_personas.py` (8)
- `tests/api/test_meta.py` (3)
- `tests/api/test_invoke.py` (7)
- `tests/api/test_websocket.py` (4)
- `tests/worker/test_queue.py` (9)
- `tests/core/test_llm_openai.py` (22)
- `tests/core/test_loop_guard.py` (15)
- `tests/core/test_scheduler_deadline.py` (3)
- `tests/core/test_runner_loop_guard.py` (4)

Total Stage 6 tests: **98**. Full suite: **512 passed, 2 skipped**.

## Verification
- POST a project (auth required), GET project list, GET single project, PUT update, DELETE — all wired
- POST `/api/invoke/{project_id}` returns 202 with run_id + job_id
- WebSocket: connects, receives initial snapshot, receives live signals as workspace.post_signal fires, falls back to on-disk board for finished runs
- LoopGuard: scripted MockLLM that repeats the same failed call 3 times triggers a `[LOOP GUARD]` user message in the next LLM call; wall-clock deadline aborts the attempt with a clear reason
- Scheduler: workspace.deadline_at in the past → `SchedulerStatus.DEADLINE_EXCEEDED`

## Notes / decisions

- The 422 → embed → background-pytest debugging loop while building Stage 6 was itself an example of the problem LoopGuard is designed to address. The fix (Depends + Body) is logged in decisions.md so the next session doesn't repeat it.
- LoopGuard is per-attempt — a respawn (e.g. after HUMAN_RESPONDED) gets a fresh guard. This is intentional: a paused agent's prior history shouldn't poison a fresh continuation.
- Run-level budget defaults to 30 minutes (1800s); per-attempt LoopGuard budget defaults to 10 minutes. Both configurable.
- The OpenAI-compat adapter does not yet wire prompt-based tool calling for models without function-calling support; if a smaller-model user wants this, it's a separate adapter (FollowUp).
