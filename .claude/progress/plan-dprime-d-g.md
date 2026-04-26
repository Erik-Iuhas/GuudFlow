# Active plan — D-prime → D → G

User-chosen sequence: **Phase D-prime → Phase D → Phase G**, then Phase C
later. This file is the working plan; tick chunks as they ship.

Total estimate: ~32 hours of focused work. Will span multiple sessions.
Each chunk below is independently shippable and leaves the suite green.

---

## Phase D-prime — Rich input ingestion

Goal: a user can attach a PDF, paste a git URL, or pick a prior run; the
runner makes those visible to agents under `inputs/`.

### Chunk 1 — Project input storage + upload route (~2 hrs)
- [ ] Extend `api/storage.py`: `ProjectStore.inputs_dir(project_id)` helper;
      ensure the dir exists at project create time
- [ ] New `api/routes/inputs.py` with `POST /api/projects/{id}/inputs/upload`
      (multipart, single or many files); saves under
      `data/projects/{id}/inputs/uploads/`
- [ ] `GET /api/projects/{id}/inputs` — list current inputs (uploads, source_repo, prior runs)
- [ ] `DELETE /api/projects/{id}/inputs/uploads/{filename}` — remove an upload
- [ ] Tests: upload happy path, bad project_id, list endpoint, delete

### Chunk 2 — Source-repo ingestion (git URL + local path) + secret scan (~2.5 hrs)

Git URL and local-path are the same §33 source kind ("seed for working/"),
just from different inputs. Same destination, same secret-scan, same
mutual-exclusion. One chunk, two endpoints.

- [ ] `POST /api/projects/{id}/inputs/git` — clones a public repo into
      `data/projects/{id}/inputs/source_repo/`; cleans existing if re-cloning
- [ ] `POST /api/projects/{id}/inputs/local_path` — copies a directory
      from the API's filesystem into the same `inputs/source_repo/`.
      Body: `{ "path": "/abs/path/to/dir" }`.
      Validates the path is an existing directory. Documents the
      "API has read access to its own filesystem" caveat — fine for
      single-user dev, gated by `AGENTFLOW_LOCAL_PATH_ALLOWLIST`
      (comma-separated path prefixes) for multi-tenant setups.
- [ ] `core/secret_scan.py` — basic regex/path scan for `.env`, key files,
      common secret patterns; returns list of warnings
- [ ] Refuse the import if secrets found and `force=true` not set;
      otherwise strip + warn
- [ ] Tests: clone via mocked subprocess, local-path copy, secret
      detection, force override, allowlist enforcement

### Chunk 3 — Continuation route (~1 hr)
- [ ] `POST /api/projects/{id}/inputs/from_run/{run_id}` — copy a prior run's
      `deliverables/` into `data/projects/{id}/inputs/from_run_{run_id}/`
- [ ] Optionally include `feedback.md` (passed in body)
- [ ] Tests: continuation copy happy path, missing run, no deliverables

### Chunk 4 — Run initializer (~3 hrs)
- [ ] `core/run_initializer.py`:
  - `seed_run(project_dir, run_root)` — copies project inputs to run inputs
  - `generate_inputs_readme(inputs_dir)` — auto-generates `inputs/README.md`
  - `seed_working_from_source(working_dir, source_repo_dir)` — copies +
    fresh `git init` + initial seed commit
  - `bundle_prior_history(prior_run_dir, target)` — `.history.bundle` per §33
- [ ] Tests for each method

### Chunk 5 — Wire initializer into the worker (~30 min)
- [ ] `worker/job_runner.py.run_one()` calls `run_initializer.seed_run()`
      before constructing AgentRunner
- [ ] Test: end-to-end with a project that has uploaded inputs; verify
      run's `inputs/README.md` exists + uploads are present in run dir

### Chunk 6 — Studio Inputs tab (~3.5 hrs)
- [ ] `studio/src/api.ts` — input endpoints (upload, git, local_path,
      from_run, list, delete)
- [ ] `studio/src/views/Inputs.tsx` (or tab inside Studio.tsx)
- [ ] `UploadDropzone` component
- [ ] `SourceRepoField` component — toggle between "git URL" and
      "local path" sub-inputs (same destination, mutually exclusive
      with each other)
- [ ] `PriorRunPicker` component (selects from this project's prior runs)
- [ ] Inputs list view — shows current uploads, source_repo (with origin
      indicator: git/local), attached prior runs; delete button per item
- [ ] Manual smoke (no automated frontend tests per Stage 8 convention)

### Chunk 7 — "Create blank project" escape hatch (~30 min)
- [ ] Projects view: dropdown next to "+ New project" → "Propose with goal"
      vs "Create blank project"
- [ ] Blank route: `Studio.tsx` opens with an empty agents array

**Phase D-prime exit:** user can upload a PDF, paste a git URL, point
at a local directory, attach a prior run; runs spawned afterward have
those visible at `/workspace/inputs/`.

---

## Phase D — Real meta-agent proposal (~4 hrs, depends on D-prime)

Goal: `/api/meta/propose` reads project inputs and returns a tailored
proposal that mentions specific files.

### Chunk 1 — `MetaAgent.run()` implementation (~2 hrs)
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

**Phase D exit:** "describe a goal + show me your inputs, get a smart
team that names your files."

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
