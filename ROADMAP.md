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

**Infrastructure (the original E scope):**

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

**Multi-tenant + admin (added 2026-04-26 from
[USER_STORIES.md](docs/USER_STORIES.md) "Org admin" archetype):**

- [ ] **Per-user accounts** — own roster + audit trail per user.
      Today's single bearer token is fine for self-host; not fine
      for any deployment with > 1 human.
- [ ] **Cost dashboards** per user / project / persona — surface
      "who's burning what" beyond the per-run cost preview.
- [ ] **Org-level shared roster** — graduated personas everyone
      in the org can hire, distinct from per-user rosters.
- [ ] **Admin kill-switch** for in-flight runs — budget overrun /
      incident response. Today only the worker process can be
      stopped.
- [ ] **Audit log export** to SIEM — aggregated `access_log.jsonl`
      across runs into a queryable / exportable surface.

**Unblocks:** real horizontal scaling, multi-tenant cloud production.

**Files:** new `worker/redis_queue.py`, new `worker/kube_job_manager.py`,
new `deploy/helm/`, `worker/docker_manager.py` becomes one of two
backends.

---

## Phase G-prime — Run resume backend ✅ SHIPPED 2026-04-26

Foundational gap surfaced by the user-stories doc: the architecture
supported mid-run resume conceptually (git workspace + episodes +
reputation persist) but `board.json` was only written at end-of-run,
so a crashed run lost its signal trail. G-prime closed that gap and
shipped the resume API surface so G's resume UI is pure frontend.

- [x] **G-prime.0** ([`44c40b6`](https://github.com/Erik-Iuhas/AgentFlow/commit/44c40b6))
      Incremental board persistence — workspace persister hook +
      `board.jsonl` JSONL trail (atomic per-line append, O(1) per
      signal). End-of-run consolidated `board.json` unchanged for
      backward compat.
- [x] **G-prime.1** ([`fef6f1a`](https://github.com/Erik-Iuhas/AgentFlow/commit/fef6f1a))
      State restoration loader — `core/run_loader.py:restore_workspace`
      reads `board.jsonl` (or `board.json` fallback) +
      `artifacts.json` + `meta.json` and reconstructs ws.board,
      ws.user_board, ws.completed_agents, ws.agent_states,
      ws.iteration, ws.artifacts. `Workspace.set_artifact` routes
      through a persister so completed agents' outputs survive
      a crash.
- [x] **G-prime.2** ([`4818135`](https://github.com/Erik-Iuhas/AgentFlow/commit/4818135))
      `POST /api/runs/{id}/resume` — re-enqueues a Job with
      `payload.resume = True`. 404 unknown run, 400 already
      complete, 409 if a job for this run is queued/running.
- [x] **G-prime.3** Worker resume branch — `run_one` skips
      `_initialize_run` and calls `restore_workspace` when the
      payload carries `resume: true`. AgentRunner accepts
      `restored_workspace=` and re-attaches subscriber +
      persisters to the restored state. E2E test in
      `tests/worker/test_job_runner_resume.py`: a crashed run
      resumes and the second pass completes without re-running
      the agent that succeeded the first time.

---

## Phase G — Studio polish for workflow review ✅ SHIPPED 2026-04-26

Re-scoped against [docs/USER_STORIES.md](docs/USER_STORIES.md) +
the F-phase endpoints that landed without UI consumers (rankings,
chat, training, catalog status all have typed clients but no
components). Old version of this section was feature-listed; new
version is chunk-ordered by the story it unblocks.

### G.0 — Roster review UI

Replaces today's flat persona list. Story-blocked: tech lead +
persona author. Consumes three F endpoints with no UI today:

- [ ] Persona detail view: profile + reputation (production +
      training split) + graduation status badge + recent
      episodes + active patterns / anti-patterns
- [ ] Rankings panel — leaderboard via `/api/personas/rankings`,
      filterable by role-type / scenario
- [ ] Chat box per persona — `/api/personas/{id}/chat` with the
      recall-summary surfaced ("this answer drew on N patterns")

### G.1 — Pipeline graph view

- [ ] **`PipelineGraph` component** — DAG view of agents with
      `depends_on` edges. Node states (pending / ready / running
      / complete / failed / waiting_for_human). Used in both
      Studio (config preview) and Run (live state).
- [ ] **`AgentStateCard`** — `[role] state · iter N · last commit
      abc1234 · claimed: auth.py · 2 unread comments`. Augments
      BoardFeed for the "what's each agent doing right now"
      question.
- [ ] Group board signals by iteration internally so the agent
      card can render "iteration 2: 3 commits, 1 reject, 1 fix".

### G.2 — Onboarding flow

Story-blocked: first-timer's empty-roster, no-demo problem.

- [ ] Default personas pre-staged at install (3-5 graduated
      personas covering common roles)
- [ ] Demo project with a curated goal + inputs that produces a
      known-good result
- [ ] Guided first-run tour pointing at the studio's three live
      tabs (board, quips, git)
- [ ] Catalog-status surface — "X archetypes available" panel
      using `/api/agents/catalog/status`
- [ ] Better empty states: "What happens when you click Run?"
      preview card, "scheduler is starting agents..." placeholder

### G.3 — Run resume UI

Pure frontend; consumes G-prime's backend.

- [ ] "Resume" button on failed/paused runs in the Run view
- [ ] Run history list per project with status badges
- [ ] Show what was restored ("resumed from iteration 2,
      developer + reviewer were complete")

### G.4 — Training tab

Consumes the four `/api/training/*` endpoints from F.1.2 — none
have a UI today. Story-blocked: persona author end-to-end.

- [ ] Scenario picker — list scenarios + show goal +
      `expected_role_types` + auto-check count
- [ ] "Train this persona" launcher with persona × scenario
      selector
- [ ] Tournament launcher with multi-persona select on the same
      scenario
- [ ] Live leaderboard view — auto-polls
      `/api/training/tournaments/{id}` until `complete: true`,
      then shows the final ranking

### G.5 — Auto-tag review points + rewind UI

Layered on G-prime.

- [ ] Runner tags git at every APPROVED + iteration boundary
- [ ] UI shows tags in the Run view + lets user "rewind to tag X"
      (re-resumes a fresh run from that tag's tree state)

### G.6 — Polish

- [ ] **Branch graph** — replace flat `GitLogTab` with lanes per
      agent branch + merges as bridges
- [ ] **Inline diff viewer** — click a commit, slide-out panel
- [ ] **Per-file timeline** — every commit that touched a path
- [ ] Project list as cards, not table
- [ ] Run history sparkline (cards) — DEFERRED to Phase I if
      headless callers need their own surface
- [ ] Optimistic updates for project edits
- [ ] Toast notifications for async API errors
- [ ] Dark-mode CSS variables

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

- [x] **F.1 Training Camp** ✅ SHIPPED 2026-04-26 (DESIGN §35) —
      scenario library + rubric scorer + training-mode runner branch +
      `/api/training` endpoints (run / tournament / leaderboard) +
      graduation logic + persona rankings. Three seed scenarios ship:
      `hello_python_v1`, `python_function_v1`, `markdown_doc_v1`.
      Auto-check kinds: `files_present` + `text_contains`
      (subprocess-based pytest/ruff deferred to a follow-up that
      needs sandboxed execution). Tournaments dispatch sequentially
      through the existing JobQueue; parallel dispatch deferred.
- [x] **F.2 Archetype catalog status** ✅ SHIPPED 2026-04-26 —
      `GET /api/agents/catalog/status` returns runtime catalog ∪ YAML,
      each row tagged shipped vs described-only with per-archetype
      scenario coverage (count of training scenarios whose
      `expected_role_types` includes the archetype name). The
      content side of F.2 (graduating YAML archetypes to shipped
      concrete classes via tournaments) stays manual — the platform
      now surfaces the data that informs the decision.
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
- [ ] **F.5 Persona author depth** (future, story-driven) — backs the
      "Persona author" archetype in [USER_STORIES.md](docs/USER_STORIES.md).
      Five sub-areas, each independently shippable:
      - **F.5.0 Persona scaffolding from templates** — new endpoint
        `POST /api/personas/scaffold` that takes a template id
        (e.g. `godot_expert`, `rust_async_expert`) and produces a
        persona with character + stake framing + an initial pattern
        set + curated reference URLs already populated. Beats
        creating a blank persona and waiting for training to fill it.
      - **F.5.1 Curated reference-URL training** — extend scenarios
        to carry `reference_urls`; persona memory gets a fourth
        layer for curated references; new tool
        `web:lookup_reference(topic)` checks curated URLs before
        falling back to `web:search`. Cuts down on blind doc
        searches at runtime.
      - **F.5.2 Persona publishing** — local roster → org or public
        registry. Versioned manifest, signature, reputation export.
      - **F.5.3 Scenario authoring tooling** — scaffold a new
        scenario from a template, validate the rubric, preview
        what an agent would see. (Today: hand-author JSON.)
      - **F.5.4 Richer scenario kinds** — sandboxed pytest, real
        lint checks, multi-file refactors. Needs the worker's
        Docker sandbox surfaced for read-only exec at scoring
        time.

---

## Phase I — Headless Service (future, Mode B tack-on)

The user-stories doc captures a second deployment mode: a headless
service that takes an invoke and returns deliverables, no
interactive review. The technical primitive exists today
(`POST /api/invoke` returns 202; status pollable; deliverables
on disk) but the polish that makes it feel like a product is
not built. **Mode A is the headline product** — Phase I is the
tack-on once Mode A is solid (per Erik 2026-04-26).

Scope:

- [ ] **Webhook callbacks** on run completion — caller registers a
      `callback_url` at invoke time; runner POSTs the final result
      payload when done. Replaces the poll-only API.
- [ ] **Structured deliverable formats** per project type — JSON
      report for code-review projects, unified diff for patch
      projects, markdown bundle for doc projects. The runner emits
      a typed envelope rather than "go read the working/ tree."
- [ ] **API keys per consumer** with per-key rate limits and
      per-key scopes. Today's single `AGENTFLOW_API_TOKEN` is fine
      for one developer; not fine for "GitHub Action + internal
      tools + an SDK consumer all sharing the same key."
- [ ] **Trusted-token gate bypass** — when an authenticated CI key
      holds the right scope, skip approval gates that would
      otherwise hang an unattended run.
- [ ] **Reference GitHub Action** — a drop-in that wraps the invoke
      flow for the common "review this PR" pattern.

**Unblocks:** the "Lambda that's a multi-agent team" use case —
code review on every PR, nightly doc regeneration, scheduled
audits. Sits on top of Phases C + B; doesn't touch the runner
internals.

See [docs/USER_STORIES.md](docs/USER_STORIES.md) Mode B section
for the full motivation and the per-archetype stories this
unblocks.

---

## Phase J — Knowledge Surface (future, doc-writer toolkit)

The **doc / RFC writer** archetype in
[USER_STORIES.md](docs/USER_STORIES.md) wants more than the current
"point a doc team at a codebase and get a draft RFC." They want
deep-wiki-style codebase documentation — agents that crawl a repo
intelligently, produce cross-linked wiki output, update
incrementally as the code changes, and stay accurate over time.

This is its own capability bucket — not a sub-phase of anything
existing. New tools, a new specialist archetype, new persistent
output format.

Scope:

- [ ] **`code:summarize`** — tool that walks a directory and
      produces a structured summary (purpose, public API,
      dependencies). Tree-sitter-backed; uses repo_map under
      the hood.
- [ ] **`wiki:link`** — tool that resolves "this concept is
      defined in X" cross-references between wiki pages.
- [ ] **`wiki:update`** — incremental update tool that keeps
      wiki pages fresh against current code without regenerating
      from scratch.
- [ ] **`deep_wiki_writer` archetype** — specialist that uses
      the above tools, with a system prompt tuned for wiki-style
      output (concept-oriented, not file-oriented).
- [ ] **Persistent wiki output** under `data/projects/{id}/wiki/`
      with a stable navigation tree, versioned via git so updates
      are reviewable.

**Unblocks:** "document this codebase" as a real product surface,
not "set up a project + invoke + scrape working/."

---

## Phase K — Project context (continuous app building, future)

After test-driving G, the natural next ask is "let me iterate on a
project across many runs and have the team understand where we
are." Most review primitives already exist (reviewer
APPROVED/REJECTED, auto-tag review points, USER_COMMENT, approval
gates, continuation runs, persona memory). What's missing is **a
persistent place where the project's bigger picture lives between
runs that the team reads in.**

Light-touch — explicitly NOT a full Agile framework rewrite. Three
chunks, ~half a day total. Plan in
[`.claude/progress/plan-k.md`](.claude/progress/plan-k.md).

- [ ] **K.0** — `data/projects/{id}/context/roadmap.md` storage +
      GET/PUT endpoints. Free-form markdown the user owns.
- [ ] **K.1** — Runner threads `roadmap.md` into agent prompts as
      a new layer between project rules and persona memory. Empty
      roadmap → layer omitted.
- [ ] **K.2** — Studio "Roadmap" tab on the project page. Editor
      + save. Reads/writes via K.0's endpoints.

What this intentionally does NOT add: Sprint object, Scrum Master
archetype, Definition-of-Done formalism, backlog data model, auto-
summarisation. If a user wants formal Agile ceremonies they encode
them as markdown headings in the roadmap; the team reads markdown.
The fuller "LLM Agile" framework stays in the back pocket if real
users ask for it — adding Sprint as a structured concept on top of
an existing project is a non-breaking extension.

**Unblocks:** continuous app building. The team hired into run 3
of a project knows what runs 1 + 2 did and what's queued next,
because the roadmap.md is in their context window. The user
maintains the roadmap; the system reads it.

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

**Mode A is the headline product.** Mode B (Phase I) is a
tack-on. Multi-tenant (expanded Phase E) is paused until there's a
real deploy target. See [docs/USER_STORIES.md](docs/USER_STORIES.md)
for the full framing.

| If you want to... | Do |
|---|---|
| ...let users resume a crashed run instead of starting over | Phase G-prime ✅ |
| ...polish the daily-driver studio experience | Phase G ✅ |
| ...let a project iterate across many runs with team continuity | **Phase K** (future, ~half day) |
| ...grow persona authoring depth | Phase F.5 (future, 5 sub-areas) |
| ...ship a deep-wiki / codebase-doc product | Phase J (future) |
| ...wire AgentFlow into CI / webhooks / scheduled runs | Phase I (future, Mode B tack-on) |
| ...deploy to production for an org | Phase E (infra + multi-tenant) |
| ...demo a real interactive run | Already shipped (C + D + F + G) |

Recommended order — the developer-as-user is the current focus.
G-prime + G shipped 2026-04-26. Next:

- **Now:** test-drive the demo / studio end-to-end. The
  pre-test review (commit `aa520a4`) closed the persona auto-load
  gap and refreshed the README + BOARD; the seed personas
  install via `scripts/seed_data.py` on fresh setup; demo
  project's "Try demo" button is wired with approval gates
  disabled for friction-free first-run.
- **Then:** **K** (project context, ~half day) — gives projects
  continuity across runs. The team reads `roadmap.md` at run
  start. Unblocks "build an app over many runs."
- **Then:** **F.5** (persona author depth) — make individual
  cycles smarter (curated reference URLs, scaffolding templates,
  richer scenario kinds).
- **When there's a target:** **E** (infra + multi-tenant) for
  shared deployments; **I** (headless service) for CI / webhook
  consumers; **J** (knowledge surface) for the doc-writer
  product. Order depends on which use case shows up first.

Each phase except E is a half-day-to-two-days commitment.

---

## Tracking

When a phase ships, mark it ✅ DONE in this file and clean the
relevant items out of `.claude/progress/followups.md`. The
`.claude/progress/BOARD.md` is the always-on dashboard for current
state; this ROADMAP is the forward-looking plan.
