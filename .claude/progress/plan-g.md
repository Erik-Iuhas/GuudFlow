# Active plan — Phase G — Studio polish for workflow review

Status: planned. Estimated ~3-5 days across 7 chunks. Re-scoped
story-first per [docs/USER_STORIES.md](../../docs/USER_STORIES.md)
plus folding in the F-phase endpoint UIs that landed without
consumers.

## Goal

Make the studio feel like a tool a developer uses daily, not an
MVP. Each chunk unblocks a specific user story; each chunk is
independently shippable and verifiable.

## Conventions

- All new components live in `studio/src/components/`; new views in
  `studio/src/views/`. Reuse existing CSS variables in `styles.css`.
- Each chunk: one feature commit + studio typecheck + prod build
  green.
- No automated frontend tests (Stage 8 convention). Manual smoke
  per chunk: open the studio, hit the new surface, verify it
  renders and the network tab shows the right calls.

---

## G.0 — Roster review UI (~half day)

### Story
Tech lead + persona author both blocked on "see what each persona
is and how it's performing." Today's `views/Roster.tsx` is a flat
list with patterns + episodes; missing reputation breakdown,
anti-patterns, graduation status, chat.

### What lands

Extend `views/Roster.tsx` PersonaDetail:

- **Reputation card** — production / training run counts side by
  side, graduation status badge ("graduated" / "ungraduated"), avg
  rubric score from training reputation if present. Data already
  in `PersonaSummary.reputation`; render only.
- **Anti-patterns section** — patterns.json already returns them;
  the existing detail view ignores `anti_patterns`. Render
  alongside patterns with a different visual treatment (e.g.
  red-tinged tag).
- **Chat box** — collapsible card under detail; uses
  `personasApi.chat(id, {message})` from F.4. Shows the recall
  summary ("drew on N patterns / M episodes") below each reply.
- **Rankings tab** — toggle between "All personas" (current list)
  and "Leaderboard" (rankings via `personasApi.rankings`). Filter
  inputs for `role_type` + `scenario`.

### Files
- `studio/src/views/Roster.tsx` — extend
- `studio/src/components/PersonaChatBox.tsx` — new
- `studio/src/components/RankingsPanel.tsx` — new
- `styles.css` — graduation badge variants, chat layout

### Verify
- Typecheck + prod build green
- Open `/roster`, click a persona, see reputation card + anti-
  patterns + a chat conversation (with a MockLLMClient or a real
  ANTHROPIC_API_KEY)
- Toggle to leaderboard, filter by role_type, see ordering

Commit: `feat(studio): roster review with reputation + chat + rankings (Phase G.0)`

---

## G.1 — Pipeline graph view (~1 day)

### Story
Solo dev "what's the team doing right now?" — today only the
BoardFeed answers, and you have to read it linearly. Needs a DAG
view of agents with `depends_on` edges.

### What lands

New `PipelineGraph` component that renders the project's agents as
nodes (color-coded by `agent_states[role]`) and `depends_on` as
edges. Used in two places:

1. **Studio config preview** — when editing a project, show the
   proposed DAG so the user can spot disconnected agents or weird
   dependencies.
2. **Run view** — alongside BoardFeed, show live state per
   agent. Auto-updates as signals land.

Plus `AgentStateCard` component:
- `[role] state · iter N · last commit abc1234 · claimed: auth.py · 2 unread comments`
- Hover-to-expand: list of board signals from this iteration

### Files
- `studio/src/components/PipelineGraph.tsx` — new (SVG-based, no
  external lib for now; can add `@gitgraph/react`-style lib in
  G.6 polish if needed)
- `studio/src/components/AgentStateCard.tsx` — new
- `studio/src/views/Studio.tsx` — embed PipelineGraph
- `studio/src/views/Run.tsx` — embed PipelineGraph + AgentStateCard
- `studio/src/lib/iterations.ts` — group board signals by
  iteration helper

### Verify
- A 4-agent project renders 4 nodes + correct edges
- Live run: nodes flip color as STARTED → COMPLETE signals land
- AgentStateCard shows last commit hash from GIT_COMMIT detail

Commit: `feat(studio): pipeline graph + agent state cards (Phase G.1)`

---

## G.2 — Onboarding flow (~half day)

### Story
First-timer's empty-roster, no-demo problem. After install, the
studio shows "0 personas, 0 projects, 0 runs" and the user has to
read DESIGN.md to know what to do.

### What lands

- **Default personas** — pre-staged at install. 3-5 graduated
  personas covering common roles (frontend_dev, backend_dev,
  code_reviewer, doc_writer, tech_lead). Authored by hand for
  this phase; F.5 adds the scaffolding tooling.
- **Demo project** — a curated `data/projects/demo/` with a goal
  + an `inputs/` README + a sample task. Available at first
  studio load via a "Try demo" button on the empty Projects view.
- **Empty-state copy** — instead of "no projects yet," show
  "Welcome to AgentFlow. Try the demo project, or describe a goal
  to propose a team."
- **Catalog status surface** — small panel on the empty-Roster
  state showing "X archetypes available; Y trained" using
  `agentsApi.catalogStatus`.

### Files
- `data/personas/_seed/` — checked-in seed manifest; install
  script copies into `data/personas/{id}/`
- `data/projects/demo/` — checked-in demo project manifest
- `scripts/setup.sh` + `scripts/setup.ps1` — copy seeds at install
- `studio/src/views/Projects.tsx` — empty-state panel + "Try demo"
- `studio/src/views/Roster.tsx` — empty-state with catalog status

### Verify
- Fresh `data/` directory + run setup → studio shows seed personas
- Empty Projects view shows demo card; clicking it loads the demo
  project's Studio view

Commit: `feat(studio): onboarding flow with seed personas + demo (Phase G.2)`

---

## G.3 — Run resume UI (~2-3 hrs)

### Story
"Resume" button on failed/paused runs. Pure frontend on top of
G-prime's backend.

### What lands

- **Resume button** in the Run view header when run status is
  `failed`. Calls `runsApi.resume(runId)` (G-prime.2) and
  navigates to the new job's progress.
- **Run history list** per project — surfaces all runs for a
  project (existing `runsApi.list(projectId)`) with status badges
  + date + a Resume action on failed runs.
- **"Resumed from" indicator** in the Run view when the run was
  resumed (read from `meta.json` payload markers or board for a
  RESUMED-style synthetic signal — small backend addition: the
  resume worker branch can post a synthetic `STARTED` signal with
  `summary="resumed from iteration N"`).

### Files
- `studio/src/views/Run.tsx` — Resume button + resumed-from banner
- `studio/src/components/RunHistoryList.tsx` — new
- `studio/src/views/Studio.tsx` or `Projects.tsx` — embed
  RunHistoryList per project

Optional small backend (~15 min): in
`worker/job_runner.py` resume branch, post a synthetic signal
("resumed run; restored N completed agents") so the Run view's
banner has something to read.

### Verify
- Manually fail a run (kill the worker mid-iteration), verify the
  "Resume" button appears, click it, verify the second pass
  completes without re-running completed agents
- Run history list shows multiple runs for a project ordered
  newest-first

Commit: `feat(studio): run resume UI consuming G-prime backend (Phase G.3)`

---

## G.4 — Training tab (~1 day)

### Story
Persona author needs to launch training runs + tournaments + read
leaderboards. Today the four `/api/training/*` endpoints have
typed clients but no UI; persona author has to use curl.

### What lands

New top-level `/training` route in studio:

- **Scenario picker** — list scenarios via
  `trainingApi.listScenarios()`. Each card shows goal + role types
  + auto-check count.
- **"Train this persona" launcher** — picks a persona × scenario,
  POSTs `trainingApi.run`, navigates to the resulting Run view.
- **Tournament launcher** — multi-select personas + one scenario,
  POSTs `trainingApi.tournament`, navigates to a new
  `/training/tournaments/:id` view.
- **Leaderboard view** — auto-polls
  `trainingApi.tournamentResult(id)` until `complete: true`,
  renders a sorted table with scores. While in-progress, shows
  "Running..." for cells with `score: null`.

### Files
- `studio/src/views/Training.tsx` — new top-level view
- `studio/src/views/Tournament.tsx` — new (leaderboard polling)
- `studio/src/components/ScenarioCard.tsx` — new
- `studio/src/App.tsx` — add `/training` + `/training/tournaments/:id`
  routes + nav link

### Verify
- Open `/training`, see scenarios listed
- Launch a single training run, verify it completes and produces
  a RUBRIC_SCORE on the run's board
- Launch a 2-persona tournament, watch the leaderboard fill in

Commit: `feat(studio): training tab — scenarios + tournaments + leaderboard (Phase G.4)`

---

## G.5 — Auto-tag review points + rewind UI (~half day)

### Story
"Rewind to a specific known-good state" beyond just "resume from
latest." The runner already commits to git; tag those commits at
review boundaries and let the user pick from them.

### What lands

**Backend:** at each APPROVED + iteration boundary, the runner
calls `git tag -a review/<n> -m "..."` on `main` (or the relevant
branch). Tags are listable via existing `git:log` tool.

**API:** `GET /api/runs/{run_id}/tags` returns the list with
metadata (which iteration, which agent, message).

**UI:** Run view tags panel + "Rewind to here" button on each tag
that calls a new `POST /api/runs/{run_id}/rewind` endpoint. Rewind
re-runs from the tagged tree (sets `payload.resume_from_tag = "<name>"`,
worker checks out that tag in working/ before restoring state).

### Files
- `core/runner.py` — tag at APPROVED + iteration boundaries
- `core/git_workspace.py` — add `list_tags()` method
- `core/run_loader.py` — extend with `restore_workspace_at_tag`
- `api/routes/runs.py` — `GET /tags` + `POST /rewind`
- `worker/job_runner.py` — handle `payload.resume_from_tag`
- `studio/src/views/Run.tsx` — tags panel + rewind action
- Tests for the new endpoints + the tag-checkout logic

### Verify
- Run completes with multiple tags created
- Rewind to an earlier tag, verify the new run starts from that
  tree state (file contents match the tag's commit)

Commit: `feat(runner): auto-tag review points + rewind UI (Phase G.5)`

---

## G.6 — Polish (~half day)

Lower priority once stories are in. Pick from the list as time
allows.

- [ ] **Branch graph** — replace flat `GitLogTab` with lanes per
      agent branch + merges as bridges (`@gitgraph/react`)
- [ ] **Inline diff viewer** — click a commit, slide-out panel
- [ ] **Per-file timeline** — every commit that touched a path
- [ ] Project list as cards, not table
- [ ] Optimistic updates for project edits
- [ ] Toast notifications for async API errors
- [ ] Dark-mode CSS variables

Each is a separate small commit. Polish chunk is the only one
without a strict ordering — ship what feels worth doing first.

---

## Verification at end of phase

```bash
python -m pytest                                  # full suite stays green
cd studio && npm run typecheck && npm run build  # frontend clean
```

### Acceptance — manual user-story walkthrough

A first-timer can:
1. Run setup → see seed personas in Roster (G.2)
2. Click "Try demo" → see the demo project loaded (G.2)
3. Watch the run live with pipeline graph + agent state cards (G.1)
4. Resume if it fails (G.3, on G-prime)
5. Inspect personas: reputation, anti-patterns, chat (G.0)
6. Train a persona on a scenario, run a tournament (G.4)
7. Rewind a finished run to a review point (G.5)

If all seven flows work, Phase G is done.
