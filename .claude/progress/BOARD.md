# AgentFlow Build Board

**Current stage:** ✅ Initial build complete — all 9 stages shipped.
**Last updated:** 2026-04-25
**Tests:** 530 passed, 2 skipped (POSIX-only); studio typecheck + prod build green

## Stage status
- [x] Stage 1 — Foundation primitives  (29/29 tests)
- [x] Stage 2 — Workspace & state      (103/103 tests; 5 modules + persona skeleton)
- [x] Stage 3 — Scheduler ⚠️ GATE       (7/7 tests; 6 patterns + no-gather contract)
- [x] Stage 4 — Agents                 (228/228 tests; persona, tools, BaseAgent, registry + 13 post-stage tools + hardening)
- [x] Stage 5 — Runner & builtins      (45/45 tests; runner, quip_engine, 7 builtin agent classes, memory_writer §34 reconciliation, e2e)
- [x] Stage 6 — API + loop guards      (98/98 tests; 5 routes, websocket, queue, OpenAI-compat adapter, LoopGuard + scheduler deadline)
- [x] Stage 7 — Worker & queue         (18/18 tests; DockerManager dev stub + production guard, JobRunner e2e, in-process worker wired into API lifespan)
- [x] Stage 8 — Frontend               (Vite + React + TS, 5 views, 7 components, live cost calc, WebSocket Run view, typecheck + prod build green)
- [x] Stage 9 — Sandbox                (Dockerfile + README + build script; image-only, no Python changes)

## Token spend
| Stage | Budget    | Actual | Notes                                        |
|-------|-----------|--------|----------------------------------------------|
| 1     | 150-250k  | ~95k   | within budget; signals + protocol + tests    |
| 2     | 350-500k  | ~330k  | within budget; 5 modules + persona skeleton  |
| 3     | 200-350k  | ~85k   | well within budget; gate cleared first try   |
| 4     | 450-650k  | ~640k  | within budget; 11 modules + 138 tests        |
| 5     | 600-850k  | ~720k  | within budget; e2e verified                  |
| 6     | 200-300k  | -      |                                              |
| 7     | 150-250k  | ~190k  | within budget; dev stub + e2e worker         |
| 8     | 500-700k  | ~480k  | within budget; build + typecheck green       |
| 9     | 50-100k   | ~30k   | within budget; image-only, build deferred    |

## Next action
**Initial build complete + Phase A shipped.** All 9 stages green, 530 backend tests passing, studio typecheck + prod build clean. Repo pushed to `Erik-Iuhas/AgentFlow` (private). `setup.sh` / `setup.ps1` / `Dockerfile` / `docker-compose.yml` written. Embeddings deps split into `requirements-optional.txt` (default install is now ~60s).

**Pick up from [ROADMAP.md](../../ROADMAP.md).** Three goal-driven sequences are documented; recommended default is **D-prime → D → B → C → E → F**.

- **D-prime** (rich input ingestion: upload API, git URL clone, between-run continuation, runner init step, Studio Inputs tab) is the prerequisite for anything user-facing
- **D** (real meta-agent proposal that reads `inputs/`)
- **B** (`code:exec` → DockerManager.exec_in() routing in production)
- **C** (live USER_COMMENT POST + real quip stream + tech-lead escalation auto-detection)
- **E** (Redis queue + KubeJobManager + Helm — when there's a deploy target)
- **F** (Training Camp, real LLM-driven memory synthesis, persona chat, etc.)

## Deferred follow-ons
[followups.md](followups.md) is the running inventory of punted items. ROADMAP.md groups them into shippable phases.

## Workflow reminder
1. Read this BOARD.
2. Open the active per-stage file → read exit criteria.
3. Re-read relevant DESIGN.md section(s).
4. Implement → tests → run pytest → tick boxes → update BOARD.
5. Stage 3 is a hard gate — no Stage 4+ work until all 6 scheduler tests pass.
