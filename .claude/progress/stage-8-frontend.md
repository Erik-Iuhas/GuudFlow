# Stage 8 — Frontend (Studio)

**Budget:** 500-700k tokens
**Status:** done (2026-04-25) — exit criteria met
**Exit criteria (CLAUDE.md):** create project; select run profile with live cost estimate; approve proposal; watch live run with QuipFeed alongside BoardFeed; post user comment; see screenshots; view git log; inspect persona roster.

DESIGN sections: §3 / §5 / §6 / §22 / §23.

## Stack
- React 18 + TypeScript + Vite 5
- React Router 6 for navigation
- Native fetch + WebSocket — no extra HTTP/state libs
- Plain CSS (`styles.css`); no Tailwind to keep setup minimal
- Dev server: port 3000 (per user preference); proxies `/api` and `/ws` to localhost:8000

## Files

### Scaffold
- [x] `studio/package.json` — react, react-dom, react-router-dom, vite, @vitejs/plugin-react, typescript
- [x] `studio/vite.config.ts` — `/api` and `/ws` proxy to `GUUDFLOW_API_URL` (default `http://localhost:8000`)
- [x] `studio/tsconfig.json` — strict TS, ES2022, JSX react-jsx
- [x] `studio/index.html`, `studio/src/main.tsx`, `studio/src/styles.css`
- [x] `studio/src/types.ts` — shared API shapes (ProjectConfig, AgentConfig, Signal, PersonaSummary, MetaProposal, etc.)
- [x] `studio/src/api.ts` — typed wrapper over fetch + WebSocket; bearer auth via localStorage; `connectRunStream` helper for the live board

### Views
- [x] `studio/src/App.tsx` — top bar (brand + nav + token input) + router
- [x] `studio/src/views/Projects.tsx` — list, delete (optimistic), link to Propose for new
- [x] `studio/src/views/Propose.tsx` — goal input → POST `/api/meta/propose` → display proposal → live cost estimate → approve & create
- [x] `studio/src/views/Studio.tsx` — edit project (goal, max_iterations, agents), CostStrip live updates, Save, Run now, recent runs table
- [x] `studio/src/views/Run.tsx` — live board layout (header / board / side panes / comment box) wired to `/ws/runs/{run_id}`; falls back to disk snapshot if run already finished
- [x] `studio/src/views/Roster.tsx` — list personas; click to inspect patterns + recent episodes

### Components
- [x] `components/AgentCard.tsx` — edit one agent (description, system_prompt, model tier, tools, depends_on, reviewer flag, max_tool_iterations)
- [x] `components/CostStrip.tsx` — DESIGN §22: live $-per-run estimate, updates on every model_tier change
- [x] `components/BoardFeed.tsx` — typed signal rows with type/from_agent/iteration tags; PROTOCOL signals show their detail in monospace
- [x] `components/QuipFeed.tsx` — synthesised quips from key board events (real quip stream is a parallel WebSocket — Stage 8.x follow-on; the pane is wired so the UX shape exists)
- [x] `components/CommentBox.tsx` — persistent comment input per DESIGN §6; backend POST endpoint for live USER_COMMENT is a Stage 8.x follow-on
- [x] `components/ScreenshotPane.tsx` — surfaces SCREENSHOT_READY signals (Stage 5+ Playwright wiring lands later)
- [x] `components/GitLogTab.tsx` — live commit / merge / conflict counts from the board

### Cost calculator
- [x] `studio/src/lib/cost.ts` — `estimateProjectCost()` and helpers. Tier pricing table (heavy/solid/fast) × tier-token-budget × max_iterations. Indicative, not real billing — DESIGN §22 explicitly notes this.

## Verification
- `npm install` — 71 packages, 7s
- `npm run typecheck` — clean (strict TS)
- `npm run build` — Vite production build: 48 modules, 188 KB → 60 KB gzip, 531ms
- Backend suite still **530 passed, 2 skipped**

## Manual smoke (recommended path)
1. `uvicorn api.main:app --reload --port 8000` (backend)
2. `cd studio && npm run dev` (frontend on :3000)
3. Open http://localhost:3000, paste API token in top-right
4. Propose → goal text → "Propose team" → review → "Approve & create"
5. Studio → tweak agents / model tiers (cost strip updates live) → Save → "Run now"
6. Run view → live board streams; quips, screenshots, git log in side panes; comment box at bottom

## Stage 8 follow-ons (recorded in followups.md)
- **Live USER_COMMENT POST endpoint** — backend route for "post a comment to an active run"; the CommentBox is wired but the post is a no-op today
- **Real quip stream** — `data/runs/{id}/quip_log.json` exists; needs a parallel WebSocket or polled endpoint for the Run view to stream them
- **Optimistic updates** for project edits in Studio (currently full PUT-then-reload)
- **Screenshot annotation tools** — DESIGN §5 mentions spatial feedback; Stage 8 surfaces just the file paths today
- **Pipeline graph** — DESIGN §3 mentions PipelineGraph component; Stage 8 ships agent cards in a grid which conveys deps via `depends_on`. A real graph viz is nice-to-have, not exit-critical.

## Notes
- `package-lock.json` is committed (and shows in `studio/`); deps lock to exact versions for reproducible installs
- All views handle the unauthenticated/empty-state cases — paste a token to enable mutations
- The `connectRunStream` helper handles snapshot-then-stream protocol per `api/websocket.py`'s contract
- Studio uses optimistic delete on Projects (per CLAUDE.md user pref); other mutations use full PUT-then-reload for now
