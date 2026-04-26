# AgentFlow Build Board

**Current stage:** ✅ Initial build + Phase A + D-prime + B + C + D + F + G-prime + G + K shipped.
**Last updated:** 2026-04-26
**Tests:** 899 passed, 2 skipped (POSIX-only); studio typecheck + prod build green

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
**Mode A is product-ready for first-user testing.** Phases A + B + C
+ D-prime + D + F + G-prime + G shipped. 885 backend tests pass;
studio typecheck + prod build clean. The persona auto-load gap is
closed (G.7 inline fix); seed personas Mike/Sarah/Alex install via
`scripts/seed_data.py` on fresh setup; the studio's "Try demo"
button gives first-timers a known-good run on day one.

**Recommended next** (after Erik test-drives the current product):
- **F.5** (persona author depth) — makes individual cycles
  smarter (scaffolding, packaging, curated reference URLs,
  richer scenario kinds).
- **Phase J** (knowledge surface) — deep-wiki / codebase-doc
  toolkit. New archetype + tools.
- **Phase I** (headless service) — Mode B tack-on. Webhooks +
  structured deliverables + per-consumer API keys.
- **Phase E** (production deploy + multi-tenant) — paused until
  there's a deploy target.

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
- ✅ **G** — shipped 2026-04-26. Studio polish, story-backed.
  G.0 roster review (reputation + chat + rankings + anti-patterns),
  G.1 pipeline graph + agent state cards, G.2 onboarding (seed
  personas + demo project + catalog status), G.3 resume UI,
  G.4 training tab (scenarios + tournaments + leaderboard),
  G.5 auto-tag review points + tags panel, G.6 toasts.
  Deferred: branch graph, diff viewer, dark mode (each is its
  own lift, not blocking any user story).
- ✅ **K** — shipped 2026-04-26. Project context: roadmap.md the
  team reads at run start. Three chunks: K.0 storage + endpoints
  (8 tests), K.1 runner reads + threads as Layer 3.8 in agent
  prompts (7 tests), K.2 studio Roadmap tab. Empty roadmap is a
  first-class state — layer is omitted entirely. Light-touch
  (~half day); the fuller "LLM Agile" framework stays in the
  back pocket if real users ask for ceremonies.
- **F.5** (future) — persona author depth: publish, scenario
  authoring, richer scenario kinds, scaffolding templates,
  curated reference-URL training
- **J** (future) — knowledge surface: deep-wiki / codebase-doc
  toolkit, deep_wiki_writer archetype
- **I** (future, Mode B tack-on) — headless service: webhook
  callbacks, structured deliverables, per-consumer API keys
- **E** (paused) — production deploy (Redis + Kube + Helm) +
  multi-tenant admin (per-user accounts, cost dashboards, kill
  switch, audit export)
- **H** (speculative — visual_checker Playwright + UICanvas)

### Closed follow-ups
- Persona auto-load gap (was: scheduled remote agent in 14 days) —
  closed inline 2026-04-26 during the pre-test review. Runner
  reads `cfg.persona_id` and attaches via `Persona.load` from
  `personas_root` (threaded through by the worker). Journey test's
  custom factory wrapper removed.

## Deferred follow-ons
[followups.md](followups.md) is the running inventory of punted items. ROADMAP.md groups them into shippable phases.

## Workflow reminder
1. Read this BOARD.
2. Open the active per-stage file → read exit criteria.
3. Re-read relevant DESIGN.md section(s).
4. Implement → tests → run pytest → tick boxes → update BOARD.
5. Stage 3 is a hard gate — no Stage 4+ work until all 6 scheduler tests pass.
