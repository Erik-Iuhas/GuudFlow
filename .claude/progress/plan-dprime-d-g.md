# Active plan — D-prime → D → G

User-chosen sequence: **Phase D-prime → Phase D → Phase G**, then Phase C
later. This file is the working plan; tick chunks as they ship.

Total estimate: ~32 hours of focused work. Will span multiple sessions.
Each chunk below is independently shippable and leaves the suite green.

---

## Phase D-prime — Rich input ingestion ✅ SHIPPED

Goal: a user can attach a PDF, paste a git URL, or pick a prior run; the
runner makes those visible to agents under `inputs/`. **All eight chunks
shipped on `main`** — see commit hashes below.

### Chunk 1 — Project input storage + upload route — [`2bcba3f`](https://github.com/Erik-Iuhas/GuudFlow/commit/2bcba3f)
- [x] `ProjectStore.project_dir/inputs_dir/uploads_dir` helpers; `create()`
      materialises uploads dir, `delete()` removes the project tree
- [x] `POST /api/projects/{id}/inputs/upload` (multipart, multi-file, streamed)
- [x] `GET /api/projects/{id}/inputs` (forward-compat shape with placeholders)
- [x] `DELETE /api/projects/{id}/inputs/uploads/{filename}`
- [x] 16 tests; path-traversal hardening; auth on writes

### Chunk 2 — Source-repo ingestion (git URL + local path) + secret scan — [`0206868`](https://github.com/Erik-Iuhas/GuudFlow/commit/0206868)
- [x] `POST /api/projects/{id}/inputs/git` — clone via mockable
      `core/source_repo.clone_git_repo`
- [x] `POST /api/projects/{id}/inputs/local_path` with
      `GUUDFLOW_LOCAL_PATH_ALLOWLIST` gate
- [x] `core/secret_scan.py` — `.env`, key extensions, SSH key names,
      private-key blocks, AWS access keys; skips `.git/`
- [x] 409 on findings; `force=true` strips + warns
- [x] 43 new tests; `inputs/source_repo.meta.json` sidecar surfaces in `GET`

### Chunk 3 — Continuation route — [`967d416`](https://github.com/Erik-Iuhas/GuudFlow/commit/967d416)
- [x] `POST /api/projects/{id}/inputs/from_run/{run_id}` — copies prior run's
      `deliverables/`, optional `feedback` body lands as `feedback.md`
- [x] `DELETE /api/projects/{id}/inputs/from_run/{run_id}`
- [x] `iter_from_runs()` helper populates `prior_runs` array on `GET /inputs`
- [x] 11 new tests

### Chunk 4 — Run initializer — [`39defa4`](https://github.com/Erik-Iuhas/GuudFlow/commit/39defa4)
- [x] `core/run_initializer.py`:
  - `seed_run(project_dir, run_root)` — copies project inputs to run inputs
  - `generate_inputs_readme(inputs_dir)` — DESIGN §33 README index
  - `seed_working_from_source(working_dir, source_repo_dir)` — copies +
    fresh `git init` + seed commit, strips upstream `.git/`
  - `bundle_prior_history(prior_run_working_dir, bundle_path)`
- [x] 18 new tests

### Chunk 5 — Wire initializer into the worker — [`7991343`](https://github.com/Erik-Iuhas/GuudFlow/commit/7991343)
- [x] `JobRunner._initialize_run()` orchestrates seed_run →
      seed_working_from_source → bundle_prior_history per attached run →
      generate_inputs_readme; runs after `run_root.mkdir`, before runner
- [x] Init failures fail the job cleanly with a descriptive error
- [x] 6 new tests; e2e covered

### Chunk 6 — Studio Inputs tab — [`1afae0e`](https://github.com/Erik-Iuhas/GuudFlow/commit/1afae0e)
- [x] `inputsApi` in `studio/src/api.ts` (list, upload, delete, importGit,
      importLocalPath, attachPriorRun, detachPriorRun)
- [x] Tab system in `Studio.tsx`: Team / Inputs
- [x] `UploadDropzone` — drag-drop, multi-file, FormData streaming
- [x] `SourceRepoField` — git URL ↔ local path toggle, secret-finding
      surface with "Import anyway" force-strip path
- [x] `PriorRunPicker` — pulls runs for project, optional feedback textarea
- [x] CSS for tabs, dropzone, warn block, source tags, danger button

### Chunk 7 — Create blank project escape hatch — [`b80ff06`](https://github.com/Erik-Iuhas/GuudFlow/commit/b80ff06)
- [x] Projects view dropdown next to "+ New project": "Propose with goal"
      vs "Create blank project"
- [x] Blank flow modal asks for project_id (required) + goal (optional),
      creates with empty agents array, routes to Studio

### Chunk 8 — Continue from run entry point — [`5627e6d`](https://github.com/Erik-Iuhas/GuudFlow/commit/5627e6d)
- [x] Run.tsx shows "Continue from this run" only when run is terminal
      (complete / max_iterations / stuck / deadline_exceeded / errored / failed)
- [x] Click → modal with feedback textarea
- [x] Submit attaches the run to the project (Chunk 3 endpoint), stamps
      `pending_continuation: { run_id, attached_at }` on project config
      for Phase D's continuation-aware meta-agent, routes to Studio

**Phase D-prime exit:** ✅ User can upload PDFs, paste a git URL, point at
a local directory, attach a prior run; runs spawned afterward have those
visible at `/workspace/inputs/` with an auto-generated README index.
Studio surfaces all of it in an Inputs tab with blank-project and
continue-from-run entry points. **Suite: 624 passed, 2 skipped.**

---

## Phase D — Real meta-agent proposal (~7 hrs, depends on D-prime)

Goal: `/api/meta/propose` reads project inputs and returns a tailored
proposal. Two flavours:
- **Greenfield / fresh-with-inputs:** propose a team from scratch
  informed by uploaded specs / source repos.
- **Continuation:** read the prior run's team + outputs + new ask,
  produce a delta proposal (keep / drop / add / swap-tier /
  replace-persona) so the user approves what's *changing* rather than
  re-approving the whole team. DESIGN §33 "Meta-agent re-evaluation".

### Chunk 1 — `MetaAgent.run()` for greenfield + uploads (~2 hrs)
- [ ] Read `inputs/README.md` if present
- [ ] Read top-N input files within token budget (reuse
      `core/context_manager.py` logic, adapted to read from inputs/)
- [ ] LLM call with META_SYSTEM_PROMPT + goal + input excerpts
- [ ] Parse structured output → `TeamProposal`
- [ ] Tests: mocked LLM returning a valid proposal; malformed output;
      empty inputs

### Chunk 2 — API wire-up (~1 hr)
- [ ] `api/routes/meta.py.propose()` accepts `project_id` (optional)
- [ ] If project_id given, instantiates MetaAgent, points it at the
      project's inputs, returns the LLM-driven proposal
- [ ] If no project_id (greenfield), still returns the deterministic
      skeleton as today
- [ ] Tests: API path with mocked MetaAgent; backwards-compat when
      project_id absent

### Chunk 3 — Studio rationale rendering (~1 hr)
- [ ] `Propose.tsx`: surface the rationale prominently, highlight any
      input file paths it mentions (`src/auth.py`, `inputs/uploads/spec.pdf`)
      as clickable links

### Chunk 4 — Continuation-aware proposals (~2 hrs)
DESIGN §33's "Meta-agent re-evaluation on continuation" — when a run is a
continuation, the meta-agent produces a *delta proposal*, not a
greenfield team.

- [ ] Detect continuation context: project's inputs include a
      `from_run_{id}/` dir, OR API call passes `prior_run_id`
- [ ] Extend `MetaAgent.run()` to load the prior run's `meta.json`,
      prior team config, and prior personas' reputation summaries
      (NOT full memory — that stays per-persona)
- [ ] Switch the system prompt for continuation: produce delta
      annotations (keep / drop / add / swap-tier / replace-persona /
      re-hire) per role with rationale
- [ ] Return shape: `{ rationale, prior_team, proposed_team, deltas[] }`
- [ ] `Propose.tsx`: when `deltas` present, render two columns
      (prior vs proposed) with the delta annotation badge per role

### Chunk 5 — Continuation runner integration (~1 hr)
- [ ] When a run is started against a project that has a continuation
      input, the runner uses the meta-agent's delta proposal as the
      effective team config (not the project's stored config — the
      project's stored config is the *base*, the delta is per-run)
- [ ] OR, simpler: the user approves the delta, which mutates the
      project's stored config for this run, and the next run starts
      from that updated config (loses delta history but simpler)
- [ ] Decision recorded in `decisions.md` once implemented

**Phase D exit:** "describe a goal + show me your inputs, get a smart
team that names your files. **Or — continue from a prior run, get a
delta proposal that says exactly what's changing about the team and
why, with prior persona memory carrying forward where it still fits.**"

---

## Phase G — Studio polish for workflow review (~16 hrs, independent)

Goal: scan a run, not read it. Can run in parallel with D-prime/D — pure
frontend.

### Chunk 1 — `PipelineGraph` (~3 hrs)
- [ ] `studio/src/components/PipelineGraph.tsx` — SVG DAG with nodes
      colored by `AgentState`. Layered layout (Sugiyama-lite or just
      column-per-depth — small DAGs)
- [ ] Use it in `Studio.tsx` (config preview) and `Run.tsx` (live state)
- [ ] Click a node → scroll the BoardFeed to that agent's signals

### Chunk 2 — `AgentStateCard` + iteration grouping (~3 hrs)
- [ ] `studio/src/lib/iteration_grouping.ts` — helper that groups signals
      by `iteration` and `from_agent`
- [ ] `studio/src/components/AgentStateCard.tsx` — one row per agent
      showing state · iter N · last commit · claimed files · unread comments
- [ ] Replace the top of `Run.tsx`'s board with a stack of these cards

### Chunk 3 — `BranchGraph` + `DiffPanel` (~4 hrs)
- [ ] `studio/src/lib/branch_layout.ts` — assign lanes per branch,
      compute commit positions
- [ ] `studio/src/components/BranchGraph.tsx` — SVG render
- [ ] `studio/src/components/DiffPanel.tsx` — slide-out panel for a
      selected commit; calls `/api/runs/{id}/board` for the relevant
      PROTOCOL signal's diff (or fetches via a new tiny endpoint if
      diffs aren't on the board)

### Chunk 4 — Project cards + run history sparkline (~2 hrs)
- [ ] `studio/src/components/RunSparkline.tsx` — tiny SVG of last N runs
- [ ] `Projects.tsx` — convert table to card grid; each card shows goal,
      agent count, last-run badge, sparkline, "Run again" button

### Chunk 5 — Empty / loading states (~1 hr)
- [ ] Project with no runs: show "What happens when you click Run?"
      preview using PipelineGraph
- [ ] Run not yet started: animated placeholder with first-agent highlight

### Chunk 6 — Optimistic updates + toasts (~2 hrs)
- [ ] `studio/src/components/Toast.tsx` + `useToast()` hook
- [ ] Studio.tsx: optimistic project edits (revert on failure)
- [ ] Replace inline error blocks with toasts in mutating views

### Chunk 7 — Dark-mode CSS variables (~1 hr)
- [ ] Move all colors to CSS variables under `:root` and `[data-theme="dark"]`
- [ ] Toggle in topbar, preference saved to localStorage

**Phase G exit:** open a live run, see at a glance who's running, what
they've claimed, what's been committed, what's been merged. Open the
project list, see status of each at a glance.

---

## Tracking

Update this file as chunks ship. When a phase is done, mark it ✅ in
ROADMAP.md and clean the corresponding entries from `followups.md`.

After D-prime + D + G are all done:
- `BOARD.md` "Next action" → Phase C (live-run wiring) or Phase B
  (production code:exec routing) per current goal
