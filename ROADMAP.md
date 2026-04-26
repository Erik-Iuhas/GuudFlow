# AgentFlow Roadmap

Sequenced next steps after the initial build. Each phase has a clear
"what it unblocks" so you can prioritize based on what you want to do
first (deploy? demo a real run? hand to another developer?).

The longer inventory of deferred items lives in
[`.claude/progress/followups.md`](.claude/progress/followups.md). This
file groups them into shippable phases.

---

## Phase A — Make the repo runnable by another human ✅ DONE

- [x] `README.md` — quickstart, architecture, configuration, status
- [x] `scripts/setup.sh` + `scripts/setup.ps1` — venv + deps + playwright
- [x] Host `Dockerfile` (multi-stage: builds studio + runs API)
- [x] `docker-compose.yml` — full stack with Docker socket mount + data volume

**Unblocks:** anyone clones the repo and gets running in 5 minutes.

---

## Phase B — Close the dev→prod gap (~1 hour)

> See "How to pick what's next" at the bottom — for **demo-to-real-users**
> goals, do D-prime + D before B.

Today the sandbox image is built and verified, but the runner doesn't
yet route `code:exec` through it in production mode. This is a small
wire-up and the highest-value next step for anyone deploying.

- [ ] Detect `AGENTFLOW_MODE=production` in `core/runner.py`
- [ ] Replace the dev host `code:exec` runner with a container-routed
      runner that calls `DockerManager.exec_in(run_id, lang, code)`
- [ ] Add a test (mocked DockerManager) verifying the routing happens
- [ ] Document the switch in `README.md`

**Unblocks:** real production-grade `code:exec` execution. Until this
lands, "production mode" still runs LLM-generated code on the host.

**Files:** `agents/tools/code.py`, `core/runner.py`,
`tests/agents/test_code_exec_hardening.py`

---

## Phase C — Make a live run feel live (~half day)

The Run view in the studio streams the board over WebSocket and that
works. But three pieces are stubbed and the run experience is less
interactive than it should be.

- [ ] **Live `USER_COMMENT` POST endpoint** — `POST /api/runs/{run_id}/comment`
      that posts a `USER_COMMENT` signal to the active workspace.
      Frontend's `CommentBox` is already wired; backend is a no-op today.
- [ ] **Real quip stream** — quips are written to
      `data/runs/{id}/quip_log.json` but the studio's `QuipFeed`
      synthesises pseudo-quips from board events. Either:
      - extend the existing `/ws/runs/{id}` to also stream quips, OR
      - add a parallel `/ws/runs/{id}/quips`
- [ ] **Tech-lead escalation auto-detection** — runner counts
      `REJECTED` signals per role; at threshold (default 2 per CLAUDE.md
      Escalation rules), spawns the `tech_lead` agent with the
      relevant context. Today `tech_lead` exists as a class but is
      never auto-activated.

**Unblocks:** a real demo run feels interactive. Users can steer
mid-flight; the platform escalates automatically when stuck.

**Files:** `api/routes/runs.py` (new comment route), `api/websocket.py`
or new `api/routes/quips.py`, `core/runner.py` (rejection counting +
tech_lead dispatch).

---

## Phase D-prime — Rich input ingestion (~1-2 days)

Today's UX is "type a goal in a textbox." A serious user with a 30-page
PDF spec, an existing GitHub repo to refactor, or a prior run they
want to continue from has nowhere to put that. The DESIGN handles this
in detail (§33 — Run Initialization with five patterns: greenfield,
git_url, uploads, prior_run, mixed) but only `FileWorkspace.read_input()`
and the `inputs/` layout are built. The ingestion plumbing isn't.

This phase is the prerequisite for Phase D — the meta-agent can't
read inputs that aren't there.

- [ ] **Upload API** — `POST /api/projects/{id}/inputs/upload` accepting
      multipart file uploads. Saves to
      `data/projects/{id}/inputs/uploads/`.
- [ ] **Git URL ingestion** — `POST /api/projects/{id}/inputs/git`
      that clones a public repo (or auth'd via the broker) into
      `data/projects/{id}/inputs/source_repo/`.
- [ ] **Between-run continuation** — `POST /api/projects/{id}/inputs/from_run/{run_id}`
      that copies a prior run's `deliverables/` into the next run's
      inputs along with a `feedback.md` (DESIGN §33 "Continuation runs").
      This is **not** the in-run review loop — that's already handled
      by the scheduler's iteration cycle (DESIGN §19): when a reviewer
      rejects mid-run, the developer fixes on the same branch and the
      reviewer reapproves, all bounded by `max_iterations`.
      Continuation here is the **between-run** case: a run *completed*
      yesterday, you reviewed the deliverables today, and you want a
      fresh run that builds on them.
- [ ] **Runner initialization step** — at run start, copy
      project-level inputs into the run's workspace inputs, generate
      `inputs/README.md` index (DESIGN §33), optionally seed
      `working/` from `source_repo/` and run `git init`.
- [ ] **Studio Inputs tab** on project page — upload widget, paste
      git URL, list prior runs. Each upload is a card showing what
      the meta-agent will see. Mirrors the §33 layout.
- [ ] **"Create blank project" button** on Projects view — escape
      hatch for power users who already know the team they want and
      don't need a meta-agent proposal.
- [ ] Secret-scan on git clone (DESIGN rule 86) — reject or strip
      files matching common secret patterns; surface a warning.

**Unblocks:** users can show up with real-world complexity. The
platform can ingest a spec PDF + a GitHub repo + last week's run's
deliverables and use all of them.

**Files:** new upload routes in `api/routes/projects.py` or new
`api/routes/inputs.py`; new `core/run_initializer.py` for the
ingestion logic; studio additions for `views/Inputs.tsx` (or a tab
inside `Studio.tsx`).

---

## Phase D — Make the meta-agent actually propose (~half day, depends on D-prime)

`POST /api/meta/propose` currently returns a deterministic
developer+reviewer skeleton. With D-prime done, the meta-agent has
real material to read.

- [ ] Implement `MetaAgent.run()` properly:
  - Read `inputs/README.md` and the highest-priority artifacts within
    token budget (DESIGN §33 rule 84)
  - Drive an LLM call with the META_SYSTEM_PROMPT + goal + input excerpts
  - Parse the LLM's structured output into a `TeamProposal`
  - Return rationale + agents + RBAC, with the rationale specifically
    referencing the inputs ("I see you've provided an OpenAPI spec at
    `inputs/uploads/api.yaml` — proposing a developer who reads it as
    a contract...")
- [ ] Wire `/api/meta/propose` to actually invoke `MetaAgent.run()`
      and pass the project's accumulated inputs through
- [ ] Update the proposal UI to render rationale that references
      specific input files

**Unblocks:** "describe a goal + show me your inputs, get a smart
team." This is the moment the platform feels intelligent rather than
form-driven.

**Files:** `agents/builtin/meta.py`, `api/routes/meta.py`,
`studio/src/views/Propose.tsx`.

---

## Phase E — Production deploy story (~1 day, when there's a target)

For multi-pod / k8s deployments, today's `InMemoryJobQueue` and
`DockerManager`-via-socket-mount don't translate cleanly. Skip this
until you have somewhere to actually deploy.

- [ ] **Redis-backed `JobQueue`** — `worker/redis_queue.py` implementing
      the `JobQueue` Protocol. Toggle via `AGENTFLOW_QUEUE_BACKEND=redis`.
- [ ] **`KubeJobManager`** — k8s-native equivalent of `DockerManager`
      that creates ephemeral Pods (with NetworkPolicy denyAll, read-only
      rootfs, resource limits, runAsNonRoot) instead of Docker
      containers. Same interface; runner is agnostic to which one's
      active.
- [ ] **Helm chart** under `deploy/helm/` — Service for the API,
      Deployments for API and worker, optional Redis dependency,
      persistent volume for `data/`, NetworkPolicies that keep sandbox
      Pods isolated.
- [ ] Document the deploy walkthrough in `README.md` or
      `deploy/README.md`.

**Unblocks:** real horizontal scaling, multi-tenant deploys, cloud
production.

**Files:** new `worker/redis_queue.py`, new `worker/kube_job_manager.py`,
new `deploy/helm/`, `worker/docker_manager.py` becomes one of two
backends.

---

## Phase G — Studio polish for workflow review (~2-3 days)

The Stage 8 frontend hits the exit criteria but it's intentionally
MVP-functional — every view works, nothing is *designed-for-comprehension*.
Real users want to scan a workflow, not read it line by line. Three
concrete gaps user feedback flagged:

### Workflow at a glance
- [ ] **`PipelineGraph` component** — DAG view of agents with `depends_on`
      edges. Node states (pending / ready / running / complete / failed /
      waiting_for_human). Used in both Studio (config preview) and Run
      (live state).
- [ ] **Project list as cards, not table** — last-run status badge,
      run count, time since last run, quick "run again" button.
- [ ] **Run history mini-chart** on each project card — sparkline of
      recent runs (success / failed / partial).

### Per-agent stage cards in the Run view
- [ ] **`AgentStateCard`** — replaces or augments BoardFeed for the
      "what's each agent doing right now" question:
      `[role] state · iter N · last commit abc1234 · claimed: auth.py · 2 unread comments`
- [ ] Group board signals by iteration internally so the agent card
      can render "iteration 2: 3 commits, 1 reject, 1 fix"
- [ ] Hover-to-expand to see the iteration's signal stream

### Git tracking that's actually visual
- [ ] **Branch graph** — replace flat `GitLogTab` with a real branch
      visualization: lanes per agent branch, commits as nodes, merges
      as bridges, conflicts highlighted. Could use a small library
      (e.g. `@gitgraph/react`) or hand-roll SVG; either way, not
      heavy.
- [ ] **Inline diff viewer** — click a commit, see the diff in a
      slide-out panel. Backend already has board signals carrying
      commit hashes; frontend just needs to render the diff.
- [ ] **Per-file timeline** — pick a path (`src/auth.py`), see every
      commit that touched it across all branches, with an iteration
      tag.

### Better empty / loading states
- [ ] When a project has no runs: show a "What happens when you click
      Run?" card with the proposed pipeline preview, not "no runs
      yet".
- [ ] When a run has no signals yet: animated placeholder showing
      "scheduler is starting agents..." with the expected first agent
      highlighted.

### Other polish that came up
- [ ] Optimistic updates for project edits in Studio (currently full
      PUT-then-reload)
- [ ] Toast notifications for async API errors (currently inline error
      blocks in each view)
- [ ] Dark-mode CSS variables (light mode only today)

**Unblocks:** "I can look at this run and understand what's happening
in 5 seconds." Today you have to read the board chronologically —
fine for debugging, terrible for review.

**Files:** new `studio/src/components/PipelineGraph.tsx`,
`AgentStateCard.tsx`, `BranchGraph.tsx`, `DiffPanel.tsx`; updates to
`views/Projects.tsx` (cards) and `views/Run.tsx` (new layout); a
small grouping helper in `studio/src/lib/`.

---

## Phase F — Long-term moat features (each is a project)

These are designed in DESIGN.md but aren't on the critical path. Pick
them up when real users tell you which one matters.

- [ ] **Training Camp** (DESIGN §35) — toy scenario library,
      tournament mode, ranking board for hiring decisions. Critical
      for smaller-model users who need to bootstrap persona quality
      without paying for production runs.
- [ ] **Real `memory_writer` LLM synthesis** (DESIGN §34) — the
      data-layer reconciliation works (lifecycle transitions,
      contradiction reconciliation, episode staleness). The LLM-driven
      *distillation* — turning clusters of episodes into new patterns —
      is scaffolded but not implemented.
- [ ] **Persona chat endpoint** (DESIGN §37) — `POST /api/personas/{id}/chat`
      for one-off conversations with a persona outside any run. Marked
      future/optional in the design.
- [ ] **Real Playwright wiring for `visual_checker`** (DESIGN §5) —
      Stage 5 has the agent shell. Production rendering wire-up
      (host-side Playwright, screenshot delivery to the user, spatial
      annotation tools).
- [ ] **UICanvas** (DESIGN §15) — structured spatial language for
      design-driven projects. Marked future-only.

---

## How to pick what's next

| If you want to... | Do |
|---|---|
| ...let someone else run the repo | Phase A (done) |
| ...deploy to production safely | Phase B + Phase E |
| ...let real users show up with rich asks (PDFs, repos, prior runs) | **Phase D-prime + Phase D** |
| ...let users **review** a run easily (the headline UX question) | **Phase G** (studio polish) |
| ...demo a real interactive run | Phase C + Phase D |
| ...make agents genuinely smarter over time | Phase F (training camp + memory synthesis) |

Recommended order depends on your near-term goal:

- **Demo to real users:** **D-prime → D → C → B → E → F** (rich ingestion + smart proposals first; production-grade exec after the UX validates)
- **Ship to production for trusted users:** **B → E → D-prime → D → C → F** (lock down `code:exec` and the deploy story first; UX polish after)
- **Default if you're not sure:** **D-prime → D → B → C** (the proposal flow is the platform's headline UX; D-prime is the prerequisite that's currently silent)

Each phase except E is a half-day-to-two-days commitment.

---

## Tracking

When a phase ships, mark it ✅ DONE in this file and clean the
relevant items out of `.claude/progress/followups.md`. The
`.claude/progress/BOARD.md` is the always-on dashboard for current
state; this ROADMAP is the forward-looking plan.
