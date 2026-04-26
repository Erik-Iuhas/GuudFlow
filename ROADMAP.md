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

## Phase B — Close the dev→prod gap ✅ DONE (2026-04-26)

`core/runner.py:_instantiate_local_tools` now branches on
`AGENTFLOW_MODE` + a threaded `DockerManager`: production mode wires
`make_container_runner()` (new in `agents/tools/code.py`) which routes
every `code:exec` call through `DockerManager.exec_in()`. Defence in
depth: misconfigured production (no DockerManager) falls back to the
host runner, which itself refuses to run in production via its
existing mode check.

Tests in `tests/agents/test_code_exec_hardening.py` (5 new cases for
`make_container_runner`) and `tests/worker/test_job_runner.py` (4 new
cases for the AgentRunner-side wiring + factory threading).

**Unblocks:** real production-grade `code:exec` execution. The
runtime-isolation half of the production story is done; deployment
packaging is Phase E.

---

## Phase C — Make a live run feel live ✅ DONE (2026-04-26)

A live run is now interactive AND has structured review layers. User
comments land mid-run. Quips stream live. The tech lead auto-fires on
escalation. The architect runs as a final design check. Cost gates
confirm before HEAVY-tier mid-run dispatch.

- [x] **C.0** ([`095365f`](https://github.com/Erik-Iuhas/AgentFlow/commit/095365f))
      Abstract bases (Reviewer / EscalationHandler / Specialist) +
      `agents/catalog.py` + `GET /api/agents/catalog`. Refactored
      CodeReviewer + TechLead onto the new bases; added
      RequirementsWriter as the proof-of-pattern Specialist.
- [x] **C.0.5** ([`d7b09ca`](https://github.com/Erik-Iuhas/AgentFlow/commit/d7b09ca))
      Approval gates + cost preview. New SignalTypes
      (APPROVAL_NEEDED/GRANTED/DENIED), Workspace.pending_approvals,
      runner._request_approval, ProjectConfig.approval_gates with
      opinionated baselines (pre-run cost preview ON, HEAVY-tier
      mid-run dispatch ON, iteration breach at 2). Studio cost-preview
      modal + ApprovalCard + Settings tab.
- [x] **C.1** ([`e4ae1e3`](https://github.com/Erik-Iuhas/AgentFlow/commit/e4ae1e3))
      `POST /api/runs/{id}/comment` posts USER_COMMENT live. Studio
      CommentBox wired with optimistic UX.
- [x] **C.2** ([`19de77d`](https://github.com/Erik-Iuhas/AgentFlow/commit/19de77d))
      `/ws/runs/{id}/quips` streams quips live (parallel to the board
      WS). QuipEngine grew subscribe/unsubscribe/_notify; JobRunner
      builds + registers a per-run engine when a quip_registry is set.
      Studio QuipFeed switched from synthesised to real subscription.
- [x] **C.3** ([`84c8d93`](https://github.com/Erik-Iuhas/AgentFlow/commit/84c8d93))
      tech_lead auto-spawn on REJECTED threshold + reviewer-text
      detection (REJECTED in artifact → REJECTED signal, no merge).
      C.0.5 gate threaded into _dispatch_escalation: granted → handler
      proceeds, denied → side-task returns cleanly.
- [x] **C.4** ([`5943a1a`](https://github.com/Erik-Iuhas/AgentFlow/commit/5943a1a))
      Final-stage architect review pattern. AgentConfig.is_final_reviewer
      auto-fills depends_on at runtime so the architect runs LAST. Same
      C.0.5 gate fires before the architect's HEAVY-tier loop. Architect
      class shipped as a Reviewer subclass.

**Suite at end of C:** 726 passed, 2 skipped. Studio typecheck + prod
build clean.

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

## Phase D — Make the meta-agent actually propose ✅ DONE (2026-04-26)

`POST /api/meta/propose` now drives a real `MetaAgent.propose()` when
`project_id` is provided: reads inputs (README + uploads excerpts +
source_repo metadata + prior_run feedback), the union of the runtime
catalog and YAML archetype catalog, and the persona roster; then calls
the LLM with a structured `propose_team` tool. Defensive fallback to
the deterministic skeleton on any LLM failure so the route never 500s.

- [x] **D.0** ([`b3fee5a`](https://github.com/Erik-Iuhas/AgentFlow/commit/b3fee5a))
      Worker archetype YAML (`docs/worker-archetypes.yaml`, canonical) +
      markdown derivative + `core/archetype_doc.py` parser.
      `agents/catalog.list_all_archetypes()` unions runtime ∪ YAML.
      `dedupe_role_names` + `suggest_persona_id` (curated human first
      names, dedup across team).
- [x] **D.1** ([`cbbfe68`](https://github.com/Erik-Iuhas/AgentFlow/commit/cbbfe68))
      `core/meta_proposal.py` (input reading + prompt rendering +
      tool-use schema + parser); `agents/builtin/meta.MetaAgent.propose`
      with greenfield + continuation paths and skeleton fallback.
- [x] **D.2** ([`9c92388`](https://github.com/Erik-Iuhas/AgentFlow/commit/9c92388))
      `/api/meta/propose` instantiates MetaAgent when `project_id`
      given (404 unknown project), back-compat skeleton otherwise.
- [x] **D.3** ([`f33e713`](https://github.com/Erik-Iuhas/AgentFlow/commit/f33e713))
      Continuation deltas (`compute_deltas`): keep / drop / add /
      swap_tier / replace_persona, per-role rationale.
- [x] **D.4** ([`171dbe6`](https://github.com/Erik-Iuhas/AgentFlow/commit/171dbe6))
      Studio rationale rendering with file-path highlighting + delta
      table + per-role persona-hire badges; continuation entry via
      `?project_id=&prior_run_id=` query string.
- [x] **D.5** decisions.md note for option-B continuation flow (studio
      PUTs the updated team config; runner stays simple).

**Unblocks:** "describe a goal + show me your inputs, get a smart
team." Continuation runs propose deltas rather than greenfield teams.

**Files:** `docs/worker-archetypes.{yaml,md}`,
`core/{archetype_doc,meta_proposal}.py`, `agents/{catalog,builtin/meta}.py`,
`api/routes/meta.py`, `studio/src/{api,types,views/Propose}.tsx`.

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

- [ ] **F.1 Training Camp** (DESIGN §35) — toy scenario library,
      tournament mode, ranking board for hiring decisions. Critical
      for smaller-model users who need to bootstrap persona quality
      without paying for production runs.
- [ ] **F.2 Persona variations + training-camp readiness for each
      archetype** — extending `docs/worker-archetypes.yaml` with
      persona variations within each archetype (e.g. "minimalist
      frontend_dev" vs "framework-heavy frontend_dev") and
      training-camp scenarios that exercise each. F.1 produces these
      variations through tournament runs — they get added to the YAML
      as they're validated. A small generator that emits a "shipped
      vs described" status table from `agents/catalog.py` is part of
      this.
- [x] **F.3 Real `memory_writer` LLM synthesis** ✅ SHIPPED 2026-04-26
      (DESIGN §34) — three sub-chunks landed:
      - F.3.1 ([`b0bfd23`](https://github.com/Erik-Iuhas/AgentFlow/commit/b0bfd23))
        LLM-driven pattern distillation from episode clusters; bounded
        per-run, defensive fallbacks, two structured tools.
      - F.3.2 ([`761de9c`](https://github.com/Erik-Iuhas/AgentFlow/commit/761de9c))
        pattern→anti-pattern flip detection. Reordered reconciliation
        so flip wins over archive when LLM names an alternative.
      - F.3.3 ([`dc5709a`](https://github.com/Erik-Iuhas/AgentFlow/commit/dc5709a))
        deterministic style drift on three observations
        (incremental_commits, iterates_on_feedback, tests_referenced).
        style.json now persists alongside patterns.json.
      Without an LLM the writer collapses to its deterministic Stage-5
      contract — no regression for callers that don't inject one.
- [x] **F.4 Persona chat endpoint** ✅ SHIPPED 2026-04-26 (DESIGN §37) —
      `POST /api/personas/{id}/chat`: read-only consultation surface.
      Thin shell over `Persona.recall()` + a single LLM call; no tools,
      no scheduler, no memory mutation. `agents/persona_chat.py` keeps
      prompt-building separable from FastAPI. project_id / run_id
      accepted in the body for forward-compat but not yet layered into
      the prompt — that's a follow-up when a real consumer surfaces.

---

## Phase H — Speculative / not on the priority track

Things that are technically interesting and designed in DESIGN.md but
don't have a real use case driving them yet. Pick them up only when a
specific user request lands or when an adjacent phase makes them cheap.

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
