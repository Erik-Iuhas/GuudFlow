# AgentFlow — User Stories

This file captures **who shows up at AgentFlow and what they're trying
to do**, grounded in the architecture we've actually built (Phases A,
B, C, D-prime, D, F shipped). The original story — "describe a goal,
get a team, watch them work" — is still true but it's no longer the
whole truth.

This doc exists to inform Phase G (studio polish), the deployment
story (Phase E), and any future phases that touch onboarding,
auth/billing, or output formats. It is **not** a feature list — it
is the answer to "what would actually make a real user pay for this."

---

## Mode A is the product. Mode B is a tack-on (future).

**Decision (Erik, 2026-04-26):** Mode A — the interactive studio
where a developer drives a team — is the core product. Everything
else is additive. Mode B framing stays in this doc as a forward-
looking note so future phases know what they're building toward,
but it does **not** drive any current priority.

### Mode A — Interactive Studio ("Cursor for a team of specialists")

A developer opens the studio, describes a goal, watches a team of
personas work, comments mid-run, reviews the diff, and ships the
result. Sessions are minutes-to-hours, iterative, high-feedback. The
user is **in the loop**.

Today's stack supports this end-to-end: meta-agent proposal,
approval gates, websocket board+quip stream, comment endpoint,
visual-feedback request, persona memory accumulating across runs.

This is the product we're refining.

### Mode B — Headless Service (future, Phase I)

A caller (CI, webhook, cron, another product) POSTs to an invoke
endpoint and gets back deliverables. The technical primitive exists
today (`POST /api/invoke` returns 202 + run_id; status is pollable;
deliverables are on disk) but the polish that makes it feel like a
product — webhook callbacks, structured output formats per project
type, API keys per consumer, trusted-token gate bypass — is not yet
built. **Tracked as Phase I in ROADMAP.** Don't ship Mode A features
that compromise to also serve Mode B.

### Why the distinction is still worth writing down

The two modes want **opposite defaults** in several places. When we
do eventually build Mode B, knowing this in advance means we don't
warp Mode A's defaults to "compromise" — Mode A keeps its defaults
and Mode B gets its own:

| Concern | Mode A default (today) | Mode B default (Phase I) |
|---|---|---|
| Approval gates | On (cost preview, HEAVY-tier dispatch) | Off (auth token = trust) |
| Run progress | Stream live to UI | Webhook callback when done |
| Output format | Workspace + git log + UI tabs | Single structured artifact (JSON / markdown / patch) |
| Personas | User's roster | Pre-staged "service" personas |
| Auth | One bearer token, single user | API key per consumer, rate-limited |
| Cost reporting | Live during run | Returned with the result |
| Failure handling | Surface to user, await guidance | Retry policy + error payload |

---

## User archetypes

Concrete-enough to be useful, not so granular we end up with twelve
of them.

### 1. Solo developer (Mode A)
Builds with AgentFlow as a daily driver. Has a small persona roster
(3–5 they trust). Sessions are 30–90 min on real features. Cares
about: low friction, persona memory paying off, cost transparency.

### 2. Tech lead / senior engineer (Mode A)
Curates a team-shared instance. Authors persona rules, reviews
trained personas before they go to production runs, configures
approval gates conservatively. Cares about: predictability,
cost ceilings, audit log.

### 3. Platform / DevOps engineer (Mode B)
Wires AgentFlow into the dev pipeline. Sets up a webhook from
GitHub → triggers a code-review project → posts the review back as
PR comments. Cares about: reliable headless invocation, structured
outputs, low ops burden.

### 4. Documentation / RFC writer (Mode A, sometimes B)
Uses heavy-doc personas to draft specs, RFCs, marketing copy from
sketchy notes. Mode B variant: a "document this codebase" service
that runs nightly and updates internal docs.

### 5. Persona author (Mode A + Training Camp)
Builds specialist personas: trains them on internal style scenarios,
runs tournaments to validate, pins them into the team's roster.
Today they use: F.1 training-camp endpoints + leaderboard + graduation.

### 6. First-time user (Mode A onboarding)
Has just `npm run setup`'d. Wants to feel value in the first 10
minutes. Doesn't have a persona roster yet, doesn't know what an
archetype is, doesn't want to read DESIGN.md. Cares about: defaults
that work, a guided first run.

### 7. Org admin (both modes)
Manages a multi-user deployment. Cares about: per-user cost limits,
shared roster vs per-user roster, RBAC per project, audit log
exportable to SIEM.

---

## User stories by archetype

Format: **As an X, I want Y, so that Z.** Each story is tagged with
mode and current support level (`✓` shipped, `~` partial, `✗` missing).

### Solo developer (Mode A)

- ✓ As a solo dev, I want the meta-agent to propose a sensible team
  from a goal description so that I don't have to design a team
  myself for one-off projects.
- ✓ As a solo dev, I want to attach a spec PDF and an existing
  GitHub repo as project inputs so the team works against real
  context, not a bare goal.
- ✓ As a solo dev, I want to watch agents work live and comment
  mid-run when I see them go astray — without restarting.
- ✓ As a solo dev, I want continuation runs that read my prior
  run's output + my new feedback so the second pass actually
  improves on the first.
- ~ As a solo dev, I want the first 10 minutes after install to
  produce a working result without me reading docs. (Today: I
  need to set up an API key, pick a persona, write a goal,
  approve a cost preview — too many steps.)
- ~ As a solo dev, I want to **resume** a paused / failed run
  rather than start over. The architecture supports this — git
  workspace + episodes + reputation persist; the gap is that
  board.json only writes at end-of-run, so a crashed run loses
  its signal trail. **Phase G-prime** ships the backend; the
  studio button lands in Phase G.
- ✗ As a solo dev, I want to **rewind to a specific review
  point** (e.g. when reviewer last approved) rather than just
  resume from the most recent state. Layered on G-prime via
  auto-tag-at-APPROVED — Phase G item.
- ✗ As a solo dev, I want personas I've used to **suggest
  themselves** for similar goals next time. (Today: meta-agent
  re-evaluates from scratch.)
- ✗ As a solo dev, I want to **pick personas first**, then have
  the meta-agent orchestrate them into a team. (Today: meta
  picks the personas; user reviews. Pick-then-orchestrate is a
  meta-agent v2 phase — future.)

### Tech lead (Mode A)

- ✓ As a tech lead, I want approval gates before HEAVY-tier
  dispatch and at iteration breach so the team can't burn through
  budget on a misunderstood task.
- ✓ As a tech lead, I want per-project persona rules
  (`persona_rules/{id}.md`) so my team's personas follow our
  conventions on each codebase.
- ~ As a tech lead, I want a **roster review** UI — see every
  persona's reputation, recent runs, graduation status, and pin
  the ones I trust. (Today: list endpoint + episodes endpoint
  exist; UI is a flat list.)
- ✗ As a tech lead, I want **per-user cost ceilings** — Aria's
  not allowed to burn more than $5 in a run, period. (Today:
  approval gates are project-level, not per-user-per-run.)
- ✗ As a tech lead, I want to **export an audit log** of every
  run, every approval, every credential broker call to my SIEM.
  (Today: access_log.jsonl per run, no aggregation, no export.)

### Platform / DevOps engineer (Mode B)

- ✓ As a platform engineer, I want `POST /api/invoke/{project}`
  to return immediately with a run_id so my CI doesn't block.
- ~ As a platform engineer, I want a **webhook callback** on run
  completion so I don't have to poll. (Today: poll-only.)
- ~ As a platform engineer, I want a **structured deliverable**
  format (JSON for review reports, unified diff for patches,
  markdown for docs) — not "go read the working/ git tree."
  (Today: deliverables are workspace files; caller has to know
  the project type.)
- ✗ As a platform engineer, I want an **API key per consumer**
  with per-key rate limits — not one bearer token shared across
  GitHub Actions, internal tools, and my SDK. (Today: single
  `AGENTFLOW_API_TOKEN`.)
- ✗ As a platform engineer, I want a **GitHub Action** I can drop
  into a repo's workflow that runs an AgentFlow project on a PR
  and posts the result as a comment. (Today: would need to glue
  curl + jq myself.)
- ✗ As a platform engineer, I want **scheduled runs** — "every
  Monday 6am, run the audit project against the prod codebase
  and post the result to #engineering." (Today: no scheduling
  surface.)
- ✗ As a platform engineer, I want **trusted-token gates** — if
  the request comes from an authenticated CI key with the right
  scope, skip approval gates. (Today: gates apply uniformly.)

### Doc / RFC writer (Mode A or B)

- ✓ As a writer, I want to point a doc-writing team at a codebase
  and a few notes and get a draft RFC back.
- ✓ As a writer, I want to ask questions of a persona that
  worked on a project ("what trade-offs did you consider?") via
  the chat endpoint (F.4).
- ~ As a writer, I want personas that are *good at writing*, not
  just "doc_writer-v1 fresh from install." (Today: trained
  personas exist; no curated published roster.)
- ✗ As a writer, I want a **deep-wiki-style codebase
  documentation toolkit** — agents that crawl a repo, produce
  cross-linked wiki output, and update incrementally as the code
  changes. New tools (`code:summarize`, `wiki:link`,
  `wiki:update`) and a doc-writer specialist tuned for it.
  Phase J — Knowledge Surface (future).
- ✗ As a writer, I want a **"document this codebase" headless
  endpoint** that takes a git URL and returns a markdown bundle.
  (Today: I'd need to set up a project, attach the repo, invoke,
  poll, retrieve. Too many steps for a one-shot consumer.)
  Mode B / Phase I.

### Persona author (Mode A + Training Camp)

- ✓ As a persona author, I want a scenario library and a rubric
  scorer so I can train a fresh persona affordably (F.1 shipped).
- ✓ As a persona author, I want tournaments to compare candidate
  personas on the same scenario.
- ✓ As a persona author, I want graduation thresholds so an
  ungraduated persona warns when hired into production.
- ✗ As a persona author, I want to **publish a persona** to my
  team's roster (or a public registry) so others can hire it,
  with versioning and a manifest. (Today: `data/personas/{id}/`
  is local-only.) Phase F.5.
- ✗ As a persona author, I want to **scaffold a domain-specialized
  persona** ("Godot expert", "Rust async expert") from a template
  rather than starting from a blank profile. The scaffold
  pre-fills character + stake framing + an initial pattern set
  + curated reference URLs so the persona starts useful, not
  empty. Phase F.5.
- ✗ As a persona author, I want **training to include
  reference-URL curation**, not just scenarios. Today agents do
  blind `web:fetch` at runtime and re-discover the same docs over
  and over. A persona trained with curated references for its
  domain would have those URLs in memory, callable via
  `web:lookup_reference(topic)` before falling back to search.
  Phase F.5.
- ✗ As a persona author, I want **scenario authoring tooling** —
  scaffold a new scenario from a template, validate the rubric,
  preview what an agent would see. (Today: hand-author JSON.)
  Phase F.5.
- ✗ As a persona author, I want **richer scenario kinds** —
  sandboxed pytest, real lint checks, multi-file refactors.
  (Today: 3 toy scenarios, `files_present` +
  `text_contains` only.) Phase F.5.

### First-time user (Mode A onboarding)

- ~ As a first-timer, I want a **single-command setup** that gets
  me to a working studio with sample data. (Today:
  `./scripts/setup.sh` + edit `.env` + run two services + paste
  token. Workable but not magical.)
- ✗ As a first-timer, I want **default personas** pre-staged at
  install so my first run isn't with empty-memory agents.
  (Today: empty roster; first run is "fresh persona" quality.)
- ✗ As a first-timer, I want a **"try it" demo project** with a
  curated goal + inputs that produces a known-good result so I
  can see what a successful run looks like before I bring my
  own work.
- ✗ As a first-timer, I want a **guided tour** of the studio's
  three live tabs (board, quips, git) so I understand what
  I'm looking at.

### Org admin (both modes)

- ✗ As an admin, I want **per-user accounts** with their own
  rosters and audit trails. (Today: single-tenant, single token.)
- ✗ As an admin, I want **cost dashboards** per user, per project,
  per persona — so I can see who's burning what. (Today: no
  cost telemetry surface beyond the per-run cost preview.)
- ✗ As an admin, I want to **share a roster across the org** —
  graduated personas everyone can hire. (Today: file-system
  roster, no sharing layer.)
- ✗ As an admin, I want a **kill-switch** for in-flight runs
  during a budget overrun or incident. (Today: no admin override
  beyond stopping the worker process.)

---

## Gaps grouped by phase

What's blocking each archetype, mapped to the phase that owns it.

### Phase G-prime — Run resume backend (pre-G, ships first)

Foundational gap surfaced by Erik: the architecture supports
mid-run resume conceptually but `board.json` is only persisted
at end-of-run, so a crashed run loses its signal trail. G-prime
fixes that and ships the resume API surface so G's resume UI is
pure frontend work.

See [.claude/progress/plan-g-prime.md](../.claude/progress/plan-g-prime.md)
for the four-chunk plan.

### Phase G — Studio polish (Mode A, current focus)

Stories ✗/~ in **Solo developer** + **Tech lead** + **First-time
user** + **Persona author**. Re-scoped against the user stories
plus the F-phase endpoint UIs that landed without consumers
(rankings, chat, training, catalog status all have typed clients
but no UI yet).

Story-backed chunks:

1. **G.0 — Roster review UI.** Persona detail view with
   graduation status + episode list + training history. Rankings
   panel (consumes `/api/personas/rankings`). Chat box per
   persona (consumes `/api/personas/{id}/chat`). Replaces today's
   flat persona list. Unblocks tech lead + persona author.
2. **G.1 — Pipeline graph view.** DAG of agents with `depends_on`
   edges; node states pending/ready/running/complete/failed.
   Solo dev "what's the team doing?" answer in one glance.
3. **G.2 — Onboarding flow.** Default personas pre-staged at
   install + a demo project + guided first-run tour. Surfaces
   `/api/agents/catalog/status` so first-timer sees "X
   archetypes available." Biggest conversion risk today.
4. **G.3 — Run resume UI.** "Resume" button on failed/paused
   runs + run history list. Consumes G-prime's backend so this
   is pure frontend.
5. **G.4 — Training tab.** Scenario picker, "Train this persona"
   launcher, tournament launcher with persona-multi-select,
   leaderboard with auto-refresh. Consumes
   `/api/training/{scenarios, run, tournament, tournaments/{id}}` —
   four F.1.2 endpoints currently with no UI consumer. Unblocks
   persona author end-to-end.
6. **G.5 — Auto-tag review points + rewind UI.** Git tag at
   APPROVED + iteration boundaries; UI lets user rewind to a
   tag, not just resume from latest.
7. **G.6 — Polish.** Dark mode, toast notifications,
   project-list-as-cards, optimistic updates for project edits.

G chunks 0–4 each unblock a story-backed archetype. 5–6 are
polish that rank below.

### Phase I — Headless Service (Mode B, future)

**New phase. Not in ROADMAP yet.** Tack-on once Mode A is solid.
Stories ✗ in **Platform engineer** + **Doc writer (Mode B)**:
- Webhook callbacks on run completion
- Structured deliverable formats per project type (JSON for
  reviews, unified diff for patches, markdown for docs)
- API keys per consumer + per-key rate limits + per-key scopes
- Trusted-token gate bypass for unattended runs
- Reference GitHub Action that wraps the invoke flow

### Phase E (expanded) — Multi-tenant + admin (future)

**Expansion of existing Phase E.** Stories ✗ in **Org admin**.
Beyond the current Redis+Kube+Helm scope, production deploy
should also cover:
- Per-user accounts with their own rosters + audit trails
- Cost dashboards per user / project / persona
- Shared org-level roster (graduated personas everyone hires)
- Admin kill-switch for in-flight runs

Without this expansion, "production deploy" means single-tenant
self-host only.

### Phase F.5 — Persona author depth (future, extends F)

**New sub-phase. Not in ROADMAP yet.** Stories ✗ in
**Persona author**:
- Publish a persona (local roster → org or public registry)
- Scenario authoring tooling (scaffold + validate + preview)
- Richer scenario kinds (sandboxed pytest, real lint checks)
- Larger scenarios (multi-file refactors, real test suites)

Today's F.1 covers training-mechanics; F.5 covers the
authoring-experience layer on top.

---

## What this means for Phase G next steps

ROADMAP's current Phase G is feature-listed (pipeline graph,
state cards, branch graph, optimistic updates, dark mode). Most
of those are still good, but the order should follow the story
priorities above AND fold in the F-phase endpoint UIs that have
typed clients but no consuming components yet.

Plan-g.md chunks (when we open Phase G):

1. **G.0** — Roster review UI (consumes 3 F endpoints: persona
   detail, rankings, chat)
2. **G.1** — Pipeline graph
3. **G.2** — Onboarding (default personas, demo project,
   catalog-status surface)
4. **G.3** — Run resume UI (consumes G-prime backend)
5. **G.4** — Training tab (consumes 4 F.1.2 endpoints —
   currently no UI for any of them)
6. **G.5** — Auto-tag review points + rewind UI
7. **G.6** — Polish (dark mode, toasts, card layout, optimistic
   project edits)

Mode-B-flavoured items currently sitting in G ("run history
sparkline" etc.) should move to Phase I when that opens.
