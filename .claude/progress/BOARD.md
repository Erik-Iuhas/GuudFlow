# AgentFlow Build Board

**Current stage:** ✅ Initial build + Phase A + D-prime + B + C + D + F + G-prime shipped.
**Last updated:** 2026-04-26
**Tests:** 880 passed, 2 skipped (POSIX-only); studio typecheck + prod build green

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
**Phases B + C shipped.** Production code:exec routing through the
sandbox container (B), full live-run interactivity + cost-control
gates (C: abstract bases, approval gates, USER_COMMENT, quip stream,
tech_lead escalation, architect final-review). 726 backend tests
passing, studio typecheck + prod build clean.

**Recommended next:** **Phase G** (studio polish for workflow review)
is the headline UX upgrade. **Phase F** (Training Camp, persona chat,
graduating archetypes from YAML to shipped classes) is the next big
content phase. Phase E (production deploy) is paused until there's a
real target.

- ✅ **D-prime** — shipped 2026-04-26
- ✅ **B** — shipped 2026-04-26 ([`56f3523`](https://github.com/Erik-Iuhas/AgentFlow/commit/56f3523))
- ✅ **C** — shipped 2026-04-26 (C.0 → C.0.5 → C.1 → C.2 → C.3 → C.4)
- ✅ **D** — shipped 2026-04-26 (D.0 → D.1 → D.2 → D.3 → D.4 → D.5)
- ✅ **F** — shipped 2026-04-26. F.3 (memory_writer LLM synthesis),
  F.4 (persona chat), F.1 (Training Camp: scenarios + rubric +
  tournaments + graduation + rankings), F.2 (catalog status:
  shipped vs described view)
- ✅ **G-prime** — shipped 2026-04-26. Run resume backend:
  incremental board.jsonl + artifact persistence, run_loader,
  `POST /api/runs/{id}/resume`, worker resume branch. E2E:
  failed run resumes without re-running completed agents.
- **G** — studio polish, story-backed (re-scoped against
  [docs/USER_STORIES.md](../../docs/USER_STORIES.md) 2026-04-26).
  Seven chunks: G.0 roster review, G.1 pipeline graph, G.2
  onboarding, G.3 resume UI (consumes G-prime), G.4 training
  tab, G.5 auto-tag review points, G.6 polish.
- **F.5** (future) — persona author depth: publish, scenario
  authoring, richer scenario kinds
- **I** (future, Mode B tack-on) — headless service: webhook
  callbacks, structured deliverables, per-consumer API keys
- **E** (paused) — production deploy + multi-tenant admin
- **E** (Redis queue + KubeJobManager + Helm — when there's a deploy target)
- **H** (speculative — visual_checker Playwright + UICanvas)

### Pending follow-ups
- Persona auto-load gap in `AgentRunner._build_agents` — scheduled
  remote agent in 14 days will open a PR. Until then, runs invoked via
  the worker leave `agent.persona = None` even when project config
  sets `persona_id`, so `memory_writer` is a no-op for those runs.
  Tests work around this with a custom runner_factory wrapper.

## Deferred follow-ons
[followups.md](followups.md) is the running inventory of punted items. ROADMAP.md groups them into shippable phases.

## Workflow reminder
1. Read this BOARD.
2. Open the active per-stage file → read exit criteria.
3. Re-read relevant DESIGN.md section(s).
4. Implement → tests → run pytest → tick boxes → update BOARD.
5. Stage 3 is a hard gate — no Stage 4+ work until all 6 scheduler tests pass.
