# Plan, spec, seats and start modes: implementation plan

**Status:** written 2026-10-06; stages 0–4 implemented. Stage 5 is still open (§8). The per-package status table is at the end (§9).
**Design source:** storyboards v4 (the `GuudFlow storyboards v4` canvas), `docs/INTAKE_PLAN.md`
§10–§11, `docs/MCP_PLAN.md`.
**Scope:**
- a plan conversation that edits a versioned **spec** (requirements and decisions) through
  **drafts**;
- **seats** (Owner, Lead, Reviewer) that change who decides and what the project manager does;
- five **start modes**;
- the spec carried into briefs and reviews, with **binding rules** checked by the reviewer;
- the Studio restructure (Work, Plan, Sessions, Team, Record, Settings), a session Team/Graph
  toggle, the record and export, and MCP servers.

Everything builds on what exists: the backlog (`core/backlog.py`, `work.db`), runs, the review
queue, task cards, `ReviewResult`/`ItemVerdict`, the meta-agent and grooming, and project rules.
The legacy harness is not touched.

---

## 1. Design calls

| # | Call | Why |
|---|---|---|
| 1 | The **meta-agent is the project manager (PM)**. The PM is a host-side `LLMClient` call with one validating tool, like `core/groom.py`. It is not an opencode role and has no sandbox | It needs no files and no shell; host-side keeps it cheap and testable with a mocked LLM |
| 2 | **Three id spaces users see:** `R` requirements, `D` decisions, `B` work items (today's backlog). Rules get `rule-N`. Cards (`T`) stay internal | Fewer layers (INTAKE_PLAN §10) |
| 3 | **The spec lives in `work.db`** in new tables, with one global spec version that bumps when a draft is approved. Requirements and decisions keep their own version history | One transaction for draft approval, using the same DB that already serialises backlog writes |
| 4 | **Every spec change goes through a draft.** A human's direct edit in the technical view is a one-change draft approved on the spot | One code path for history, record and export |
| 5 | **Requirement checks have ids** (`R4.2`). When the PM creates a work item for a requirement, it copies the checks into the item's acceptance as `R4.2: <text>`. The planner keeps those lines on the cards that implement them, and reviewer verdicts on them update the check state when the session is **accepted** | Deterministic tracking of whether a requirement is met, using existing machinery (`ItemVerdict`) |
| 6 | **Binding rules become review checklist lines.** Each card review gets one extra item per binding rule (`rule-2: <text>`), so the reviewer must give an `ItemVerdict` for it. An unmet rule fails review through the existing loop. Preferences go into the brief only | Adherence uses the review edge as it is; no new protocol |
| 7 | **Reviewer seat v1 gates the accept, not mid-session merges.** When the seat is on, a finished session cannot be accepted until a person co-signs its items in the review queue. "Request changes" is a reject with comments, which go into the next brief through the existing `review_note` | Mid-session human gates are scheduler work (deferred to stage 5) |
| 8 | **Lead seat:** when held, decision changes in a draft start *unticked* and need an explicit tick. When empty, the PM's decisions start *ticked*, marked "decided by the project manager", and the PM prompt switches to tech-lead stance (§4) | Reuses the draft mechanism; no timeouts |
| 9 | **Start modes are presets** over existing levers: team template, PM prompt section, planner instructions, reviewer checklist lines, and one gate (spec approval before the first session) | Modes change behaviour, not just a label |
| 10 | **Sessions are runs.** "Session N" is the project's Nth run; the run id stays visible in the technical view | Words only; no data change |
| 11 | **Third-party MCP servers are proxied through GuudFlow's MCP server on the host** (`docs/MCP_PLAN.md`) | Keys stay on the host; sandboxes have no egress |
| 12 | **The node view stays.** The Sessions tab is the cockpit with a Team / Graph toggle; Team is the default | User request |

## 2. Data model (`core/spec.py`, tables in `work.db`)

```python
Kind = Literal["feature", "constraint", "quality", "non_goal"]
Priority = Literal["must", "should", "could", "wont"]

@dataclass
class Check:            # one acceptance check of a requirement
    id: str             # "R4.2"
    text: str
    state: Literal["unknown", "met", "unmet"] = "unknown"
    run_id: str | None = None        # the accepted session whose review set the state

@dataclass
class Requirement:
    id: str             # "R4"
    text: str
    kind: Kind
    priority: Priority
    checks: list[Check]
    assumed: bool = False            # PM assumption, not yet confirmed by a human
    experiment: bool = False
    status: Literal["active", "dropped"] = "active"
    source: str = "human"            # human | pm | session:<run_id>
    version: int = 1
    history: list[dict]              # earlier versions: {version, text, checks, priority, at, by, draft}

@dataclass
class Decision:
    id: str             # "D3"
    title: str
    choice: str
    reason: str                      # >= 20 chars
    serves: list[str]                # requirement ids
    binding: bool = False            # True: becomes a rule (source D3)
    status: Literal["accepted", "superseded", "dropped"] = "accepted"
    decided_by: str = "pm"           # pm | human
    supersedes: str | None = None
    version: int = 1
    history: list[dict]

@dataclass
class Rule:
    id: str             # "rule-2"
    text: str
    source: str                      # R1 | D2 | human | default
    applies_to: list[str]            # role types, or ["*"]
    strength: Literal["binding", "preference"] = "binding"
    status: Literal["active", "dropped"] = "active"
```

New tables:
- `spec_items(kind TEXT, id TEXT PRIMARY KEY, num INTEGER, data TEXT)`, where `kind` is
  requirement, decision or rule;
- `plan_messages(id INTEGER PRIMARY KEY, at REAL, author TEXT, text TEXT, draft_id TEXT,
  mode TEXT)`;
- `drafts(id TEXT PRIMARY KEY, base_version INTEGER, status TEXT, data TEXT)`;
- `meta` gains the keys `spec_version`, `next_R`, `next_D`, `next_rule` and `next_draft`.

`BacklogItem` gains `serves: list[str]` (requirement ids). It is editable and defaults to `[]`.

**Seats and mode** live in the project document (`project.json`), next to `approval_gates`:

```json
"seats": {"lead": false, "reviewer": "off" | "all" | "must"},
"start_mode": "spec_first" | "prototype_first" | "test_first" | "research_first" | "maintain"
```

The Owner seat always exists, so it is not stored.

## 3. Drafts and the PM protocol (`core/plan_drafts.py`, `core/project_manager.py`)

**Changes** are a Pydantic discriminated union on `op`:

| op | payload |
|---|---|
| `add_requirement` | `ref` ("new:1"), text, kind, priority, checks[str] (≥1 unless non_goal), assumed, experiment |
| `edit_requirement` | `id`, any of text, priority, kind, checks (full list), assumed, experiment |
| `drop_requirement` | `id`, reason |
| `add_decision` | `ref`, title, choice, reason, serves[], binding |
| `supersede_decision` | `id` (the old one), `ref`, title, choice, reason, serves[], binding |
| `add_item` | `ref`, title, acceptance[str], serves[], kind, size, depends_on[] (B ids or refs) |
| `edit_item` | `id`, any editable backlog field |
| `add_rule` / `drop_rule` | text, applies_to, strength / `id`, reason |

**`PlanTurn`**, the PM's tool payload:

```
{reply: str (plain language to the human), changes: [Change] (≤ 20), questions: [str]}
```

**Validation, done in the tool call:**
- ids exist, and refs resolve within the same turn;
- no dependency cycles (reusing `_check_deps`);
- a decision's reason is at least 20 characters;
- a requirement has at least one check unless it is a non-goal;
- a change that edits a field a human pinned is refused, using the same "pinned" rule as backlog
  edits (`pinned_fields` per object).

An invalid payload gets one retry with the error text, the same rule as agent turns.

**A draft** is `{id, base_version, status: open|approved|discarded, changes: [{cid, change,
accepted: bool|None, by: "pm"|"human"}]}`. One draft is open at a time. Each PM turn in update
mode replaces the PM's proposed changes in that draft, keeping the ones a human already
ticked or rejected unless the PM explicitly revises them.

**Approving a draft** applies the accepted changes in one `work.db` transaction:
- the spec version goes up by one;
- each touched object gets a history entry;
- items are created or edited through the `Backlog` methods;
- requirement checks are copied into new items' acceptance as `R4.2: …` lines;
- binding decisions create or refresh their rule.

It returns `{version, applied, created: {ref: id}}`. Discarded drafts are kept for the record.

## 4. Prompts (`core/pm_prompts.py`)

`pm_system_prompt(seats, start_mode, phase)` is built from these layers:

1. **Base:**
   - you are the project manager of a software team of agents;
   - the spec is the contract: requirements with checks, decisions with reasons, and rules;
   - propose changes, never apply them;
   - write plainly, with ids and specifics;
   - mark assumptions;
   - one question at a time, and only when it blocks.
2. **Seats:**
   - **Lead empty:** you are also the technical lead.
     - Make the technical decisions a senior engineer would: stack, structure, data formats,
       testing approach, performance budgets.
     - Record each as a decision with the alternatives you rejected and why, and mark it
       `binding` when builders must follow it.
     - When a decision depends on facts you do not know (library behaviour, standards, prior
       art), do not guess. Add a `research` work item that serves the requirement, say the
       decision is pending on it, and choose a reversible default for now.
     - Prefer boring, testable choices. Turn your decisions into rules builders and reviewers
       can check.
   - **Lead held:** a human leads technical choices.
     - Propose decisions with two or three options and their trade-offs, and recommend one.
     - Do not mark anything binding without the human's tick; the draft shows them unticked.
   - **Reviewer seat on:** the human co-signs code at accept. Keep work items small enough to
     review: one concern per item, a few hundred lines at most.
3. **Start mode:** one paragraph per mode, matching the matrix in §5.
4. **Phase:**
   - `plan` is the conversation;
   - `after_session` turns a finished session's report, review notes and leftovers into a draft
     (spec suggestions, follow-up items, check states the session could not settle);
   - `derive_spec` is for prototype-first after session 1, and research-first after a research
     session.

**Context the PM gets:**
- the project goal;
- the current spec rendered compactly (`render_spec(limit)`);
- the open draft;
- the last 20 plan messages;
- the open backlog;
- the last 3 session summaries, wrapped as untrusted;
- a repo outline from `core/repo_map.py` (host-side use is allowed), capped.

Inputs and files from `inputs/` are wrapped as untrusted too.

**Briefs** (`core/harness/brief.py` and `task_flow.py`) get a "Spec" section:
- for the planner: the requirements served by the selected items, the binding decisions and the
  rules. The planner keeps every `R#.#:` acceptance line on the card that implements it.
- for card roles: the requirements of the card's item, the binding decisions, and the rules that
  apply to the role.
- for reviewers: the same, plus the binding-rule checklist lines (§1 call 6).

Start-mode paragraphs go into the planner's instructions (§5).

## 5. Start modes

| Mode | Team template (meta-agent hint) | Planner instruction | Reviewer lines | Gate |
|---|---|---|---|---|
| `spec_first` | PM, 1–2 builders, reviewer | "every card implements named R checks" | requirement checks | session 1 needs an approved spec |
| `prototype_first` | PM, 1 builder, reviewer | "session 1: thinnest end-to-end slice; skip polish" | light: does it run | none; PM runs `derive_spec` after the first accepted session |
| `test_first` | PM, tester, builders, reviewer | "for each item a tester card writes failing tests for its R checks first; builder cards depend on it" | "tests written before code, now pass" | approved spec |
| `research_first` | PM, researcher (web), reviewer | "research cards produce a decision note with options, evidence and a recommendation; no product code" | "sources cited, options compared" | none; PM runs `derive_spec` after the session |
| `maintain` | PM, builder, reviewer | "smallest change that meets the item; a regression test per fix; do not refactor outside files_hint" | "regression test added; diff limited to the item" | none; default rules "minimal diff", "regression test per fix" |

The mode can be changed in Settings. The change takes effect from the next session.

## 6. API contract (all bearer-protected; `api/routes/plan.py`, `record.py`, `mcp.py`)

```
GET    /api/projects/{id}/spec                         -> {version, requirements[], decisions[], rules[]}
GET    /api/projects/{id}/plan                         -> {messages[], draft|null}
POST   /api/projects/{id}/plan/messages  {text, mode: "update"|"discuss"}
                                                       -> {messages[] (new ones), draft|null}
PATCH  /api/projects/{id}/plan/draft/{cid} {accepted: true|false|null}  -> draft
POST   /api/projects/{id}/plan/draft/approve           -> {version, applied[], created{}}
POST   /api/projects/{id}/plan/draft/discard           -> {}
POST   /api/projects/{id}/spec/edit  {change}          -> {version, ...}   (one-change draft, approved)
GET    /api/projects/{id}/record?ref=R3                -> {ref, events[]}
GET    /api/projects/{id}/record/export?format=md|json&include=spec,decisions,sessions,plan,rules,costs
POST   /api/projects/{id}/runs-review/{run_id}/cosign {verdict: "cosign"|"changes", comments[]}
GET    /api/runs/{run_id}/spec-slice                   -> {requirements[], decisions[], rules[], verdicts{}}
GET/POST/PUT/DELETE /api/mcp/servers[/{name}], POST /api/mcp/servers/{name}/test
```

Project documents accept `seats` and `start_mode`. `POST /runs-review/{run_id}/accept` returns
409 when the reviewer seat is on and the session is not co-signed. A spec-first project's
`POST /api/invoke/{id}` returns 409 when there is no approved spec, unless the request sets
`"force": true`. Errors use the existing `{detail}` shape.

## 7. Studio (matches storyboards v4)

- **Project tabs:** Work, Plan, Sessions, Team, Record, Settings.
  - Inputs moves into Plan as "Files". The roadmap text becomes the PM's notes under Plan.
  - Insights moves into Record.
  - A header **Overview / Technical** switch is stored per project in `localStorage`.
- **New project** (replaces Propose for new projects): description, attach files or a repo, seats,
  five start modes, spend cap and name. "Start planning" creates the project (team from the
  meta-agent, with the mode as a hint) and opens Plan, which asks the PM for draft 1.
- **Plan:**
  - the conversation, with a large growing composer and an Update draft / Just discuss toggle;
  - the draft panel: the diff against the approved spec, ✓/✕ per change, and Approve or
    Discard;
  - the spec itself (requirements, decisions, rules) when no draft is open.
- **Work:**
  - Overview: lanes, Needs you, and spec changes this week;
  - Technical: a table with checks, serves, state, who and result, plus Routed to you by
    seat;
  - the ask box posts to the plan.
- **Sessions:**
  - the cockpit with a **Team / Graph** toggle;
  - the Team view has team cards, the activity feed, and "What the team must follow"
    (spec slice with verdicts);
  - the review panel shows results against the spec, a co-sign when the reviewer seat is on,
    and spec suggestions as a link into the plan.
- **Team:** seats, the agents table (model, capabilities, MCP tools), and the rules panel with an
  adherence summary.
- **Record:** trace by id, export, and keeping `SPEC.md` and `docs/decisions/` in the repo.
- **Settings → MCP servers:** the list, a detail panel with tools and default grants per role
  type, `mcp.json` import, and test.
- **Checkboxes:** one restyled native checkbox (global CSS) and a `Checkbox` component, so every
  checkbox in the app matches the design.

## 8. Stages and work packages

Each package is sized for one Sonnet sub-agent working in its own git worktree, owning a
disjoint set of files, with tests. The orchestrator (this session) merges each branch, runs the
full suites (`pytest -q`, then `cd studio && npm run typecheck && npm test`), fixes integration
problems and commits.

**Stage 0 (orchestrator):** this plan, the storyboard update (five modes, node toggle), and the
checkbox restyle.

**Stage 1 (parallel):**
- **P1 Spec store.** Files: `core/spec.py`, `core/plan_drafts.py`, `work_db.py` (tables and
  helpers), `backlog.py` (`serves`), and tests `tests/core/test_spec.py` and
  `test_plan_drafts.py`. It covers §2 and §3: the change union, validation, draft lifecycle,
  approve and apply, history, and the `R#.#` acceptance copy.
- **P2 MCP backend.** Files: `core/mcp_registry.py`, `core/harness/mcp_proxy.py`, the grant
  plumbing in `capabilities.py` and `mcp_server.py`, `api/routes/mcp.py`, and tests. It covers
  `docs/MCP_PLAN.md`: host-side registry with credentials stored like provider keys, proxying
  tools to granted roles, a board signal per call, and untrusted wrapping.
- **P3 Studio shell.** Files: `views/Studio.tsx` (tab restructure and detail switch),
  `features/project/NewProject.tsx` (replaces the Propose route for new projects), the seats and
  start-mode fields, and the cockpit Team/Graph toggle with a Team view built from existing
  store data. Vitest tests.

**Stage 2 (parallel; needs P1):**
- **P4 Project manager and API.** Files: `core/pm_prompts.py`, `core/project_manager.py`,
  `api/routes/plan.py`, the spec-first gate in invoke, and tests with a scripted LLM. It covers
  §3, §4 and §6 (plan and spec routes).
- **P5 Studio Plan tab.** Files: `features/plan/*`. Built against the §6 contract with mocked
  fetch in tests.

**Stage 3 (parallel; needs P4):**
- **P6 Harness integration.** Files: the spec sections in `brief.py` and `task_flow.py`, the rule
  checklist lines, the start-mode planner instructions, `R#.#` check states updated on accept
  (`project_repo` accept path), the `after_session` and `derive_spec` hooks, and the spec-slice
  route.
- **P7 Studio Work, Sessions and Team.** The Work Overview/Technical views, routed-to-you,
  spec results and co-sign in the review panel, the session adherence panel, and the Team rules
  panel. The co-sign backend route and accept gate go to P6.

**Stage 4 (parallel):**
- **P8 Record.** `core/record.py` (trace from spec history, plan messages, backlog history, run
  states and review signals), export in Markdown and JSON, and `SPEC.md` plus
  `docs/decisions/` written into the project repo on accept (opt-in). Studio Record tab.
- **P9 MCP Studio.** The Settings → MCP servers page and the Team tab MCP tools column.

**Stage 5 (later, not in this pass):**
- mid-session human co-sign as a scheduler gate;
- a Builder seat with a git endpoint;
- line-level review comments mapped to card issues;
- mechanical rule checks (lint or grep from a rule);
- rule-list pruning.

**Done means:**
- every package has its tests;
- the full backend and Studio suites pass;
- the escape suite still passes for changes to `core/harness/`, `worker/`, `sandbox/` or
  `gateway/`. It needs Docker; CI runs it;
- the docs (`CLAUDE.md` project notes, this table) are updated.

## 9. Status

| Package | Status |
|---|---|
| Stage 0 | done (plan, storyboard update, checkbox restyle) |
| P1 Spec store | done (`core/spec.py`, `core/plan_drafts.py`, work.db tables, backlog `serves`) |
| P2 MCP backend | done (registry, host proxy, grants, `/api/mcp/servers`, `MCP_CALL` signal) |
| P3 Studio shell | done (tabs, detail switch, seat chips, New project page, Sessions tab, Team/Graph toggle) |
| P4 Project manager + API | done (`core/pm_prompts.py`, `core/project_manager.py`, `api/routes/plan.py`, spec-first gate) |
| P5 Studio Plan tab | done (`features/plan/*`, mounted in the Plan tab) |
| P6 Harness integration | done (`core/start_modes.py`, `core/harness/spec_brief.py`, `core/spec_adherence.py`; spec sections in `task_flow.py`; check states and rule verdicts on accept; co-sign route and accept gate; spec-slice, adherence and after-session routes) |
| P7 Studio Work/Sessions/Team | done (Work Overview/Technical, routed-to-you, spec results and co-sign in review, follow panel in the session Team view, seats and rules panels with adherence) |
| P8 Record | done (`core/record.py` trace/export/spec files, `api/routes/record.py`, Record tab); `SPEC.md` and `docs/decisions/` committed to main on accept when `record.keep_in_repo` is on (`core/project_runs.write_record_files`) |
| P9 MCP Studio | done (Settings → MCP servers, per-agent MCP tools field) |
| P10 Team changes in drafts | done (`add_role`/`edit_role`/`remove_role` ops, always human-ticked; `core/team_changes.py` applies them to the project doc on approve; overseer team proposals land in the open draft) |
| P11 Jack: persona, memory, naming | done (`jack-project-manager-v1` seed, `core/pm_persona.py`, `core/pm_memory.py` episodes on session and draft outcomes, recall in every PM turn, distillation via the memory writer, `GET /api/pm`, Studio shows the name) |

**Known gaps after this pass:**
- Runs with no planner (no task board) get no spec sections in briefs.
- The trace reads review verdicts from requirement check states, not from every review round
  on the board.
- `SPEC.md` and `docs/decisions/` are not protected paths. An agent edit to them is overwritten
  by the next accept.
- Visual e2e baselines (`studio/e2e/__screenshots__`) predate the new tabs and checkboxes, and
  need regenerating.
- The escape suite needs Docker. It did not run in this environment; CI's Docker job covers it.

## 10. Addendum: Jack, a growing team, and the manager's own memory (2026-10-06)

### 10.1 The project manager is Jack
- The project manager is a persona: `jack-project-manager-v1`, seeded like the other personas in
  `data/_seed/personas/`. Its display name is the persona's profile name, which defaults to
  "Jack" and can be renamed on the persona's profile. There is one Jack per install, working
  across every project.
- Studio shows the persona name wherever it said "Project manager", with the role in small text.
  `GET /api/pm` returns `{persona_id, name, episodes, patterns}`.

### 10.2 Jack can grow the team (P10)
New draft ops: `add_role`, `edit_role` and `remove_role`. They work on the project's `agents`,
which are the same fields as the team approval screen.
- `add_role` carries: role, role_type, description, model_tier, depends_on, `review_of`, and
  optional capability overrides. Defaults come from the role type, as on the approval screen.
- **A human always approves team changes.** Team ops start unticked whatever the seats, so Lead
  being empty does not pre-accept them. This follows hard rule 24 and the overseer's
  `propose_team_change`.
- On approval the project doc's `agents` change. The new team applies from the next session
  (plan §5.7: widening applies from the next spawn), and a running session is never changed.
- **When Jack proposes a change:**
  - the scope grows past the team, for example a new kind of work (frontend, data) or a
    research need;
  - the start mode changes, for example test-first adds a tester;
  - sessions keep failing review in one area;
  - the playbook or Jack's own memory says this kind of project goes better with a role.

  Every team change needs a reason of at least 20 characters.
- The overseer's in-session `propose_team_change` intervention becomes a draft team change, so
  the human sees it in the Plan tab.

### 10.3 Jack's memory (P11)
Rules 15, 16 and 22 apply, as for any persona.
- **Episodes** are written deterministically, with no LLM call, when:
  - a session is accepted or rejected: project type (from the goal), start mode, seats, team
    roles, spec size, items in the session, review rounds, rule issues, checks met/unmet, cost,
    outcome and the human's note;
  - a draft is approved or discarded: how many changes were accepted, rejected or edited by the
    human, and which ops. This is the signal for where Jack's proposals miss.
- **Patterns** are distilled by the existing memory writer once 3+ episodes share a theme, with
  `source_episodes`, recency-weighted confidence and lifecycle states. For example: "web games
  in prototype-first need a renderer builder from session 1", or "humans reject drafts that add
  more than 6 requirements at once".
- **Recall:** each PM turn calls `persona.recall(goal + latest message)` and injects
  `render_for_prompt()` as "What you have learned on earlier projects". It sits below the
  project's own rules and spec, because rules trump memory (rule 24).
- **Reputation:** sessions accepted and rejected, and drafts approved with no human edits. It is
  shown on Jack's profile.

## 11. Addendum: lean handoffs, structured requirements, a modern Plan view (2026-10-06)

This addendum comes from running pacrogue for three sessions (`docs/FIELD_NOTES.md`,
`docs/cost-reports/pacrogue-session3.md`). It is to be done **before pacrogue session 4**.

### 11.1 Card-to-card handoff (replaces `session_reuse` as the default)
**Problem.** Without session reuse, workers re-read the code they had just written. With it
(`core/harness/run.py:165`), each role carries its whole conversation across cards: a peak of 350 k
tokens, 46% of session 3's cost in cache reads, and about $7.50 of $19.30 lost to carry-over
and cold re-sends (F7, F8, F9).

**Design.** A fresh opencode session per card, plus a compact handoff that carries just enough.
- **Worker-written handoff.** `AgentResult` gains `handoff`, written in the same submit call (no
  extra model call) and capped at about 1.5 k tokens. It has fixed sections:
  - `changed`: files and the APIs or contracts they expose;
  - `decisions`: choices and gotchas the next card must respect;
  - `tests`: what is covered and what is not;
  - `next`: what the following card should know.
  
  The validator rejects an empty handoff on a `done` result.
- **Host facts, added by GuudFlow.** Files changed with line counts, test count and pass state,
  and the exported signatures of changed files (a small host-side extractor; the legacy `repo_map`
  is not reused).
- **What goes into the next brief:**
  - the role's own previous handoff;
  - the handoffs of the card's `depends_on` cards (so the renderer gets the logic cards' contracts);
  - handoffs of cards that touched overlapping files.
  
  Handoffs are wrapped as untrusted agent text. Nothing else carries over.
- **When a session is kept.** Only when the next card directly continues the last one (same role,
  depends on it or overlaps its files), the role has been idle for less than the cache lifetime
  (about 4 minutes), and the carried context is under `handoff.max_carry_tokens` (default 60 k).
  Otherwise the next card starts fresh.
- **The reviewer always starts fresh,** with the card, the diff and the builder's handoff.
- **Notebooks** are written in a fresh session from the role's handoffs for the run, never by
  resuming a large session.
- **Safety net.** If one card's own session passes about 150 k tokens, call opencode's session
  summarize once.
- **Measurement.** `llm_costs.jsonl` gains `card`, `carried_tokens` (context at the card's first
  call) and `cold` (true when the call missed the cache after idling). A per-card cost table goes
  into the retrospective and Studio's run summary. Success in session 4 means: cost per card down
  by at least 30% against session 3 at similar card sizes, with no rise in failed reviews.

### 11.2 Structured requirements and finer draft operations
**Problem.** A requirement is one free-text paragraph that mixes behaviour, numbers and how to
check it.
- `edit_requirement` replaces the whole text, so the GUI strikes through whole paragraphs.
- A check keeps its id only if its text is identical (`core/spec.py:260`), so a one-word edit
  gives it a new id and drops its state and test link. This is why numbering drifts (R3.6, R3.9,
  R4.11…).
- Work items copy check text into `acceptance` when they are created, so they go stale when the
  requirement changes.

The SQLite store with id-addressed, validated ops stays the source of truth. Markdown stays a
generated view (see below), because a model editing a markdown file rewrites and renumbers, and
errors only show after parsing.

**Requirement shape.**

| Field | Rule |
|---|---|
| `title` | ≤ 60 chars, names the feature |
| `statement` | 1–2 sentences of behaviour, no tunable numbers |
| `params` | list of `{key, value, unit?, assumed: bool, note?}`; tunables live here |
| `checks` | list of `{id, method: unit\|manual\|visual\|perf, given?, then, test?: "file::name", state}` |
| `rationale` | optional |
| `kind`, `priority`, `status`, `pinned_fields`, `history` | as today |

**Check ids are stable.** A check is edited through its id and never re-matched by text. Numbers
are never reused (`highest_check_number` already reserves them).

**New draft ops,** each one row in the review:
- `set_statement R#`
- `set_param R# key value [assumed]` and `remove_param R# key`
- `add_check R# {…}`, `edit_check R#.n {…}` and `remove_check R#.n`
- `set_title R#`
- `edit_requirement` stays only for kind and priority.

Items: `add_item` and `edit_item` take `acceptance` as **references** (`"R4.8"`) plus the item's
own lines (`{text}`). Studio and briefs resolve references at read time, so items never go stale.

**Grouping.** Every op gets an optional `group` label (for example "Default dash", "Families"),
so a whole theme can be ticked at once.

**Traceability.** A `unit` check with `test` set takes its state from the session's check run
(test results parsed from `bun test`, `pytest` and similar where possible). Otherwise it uses the
reviewer's verdict.

**Generated markdown.** On every approved draft, GuudFlow writes `docs/spec.md` into the project
repo (read-only for agents, protected path). The spec gets git history, and workers read it like
any other file. Later option: a human edits `spec.md` and GuudFlow diffs it into the same ops,
shown as a draft.

**Migration.** A one-time, rule-based split of existing requirements:
- the title comes from the first clause;
- numbers in the text become `params`, marked assumed for a human to confirm;
- the `Bun test:`, `Manual:` and `Manual screenshot:` prefixes become `method`.

Items' copied acceptance lines that match a check text become references. Anything that does
not parse stays as `statement` text and is flagged.

**PM prompt and validation.** The schema is the contract. The PM prompt shows the shape with one
example. `validate_turn` refuses numbers in a statement when params exist, titles over 60
characters, and checks without `then`.

### 11.3 A modern Plan view
- **Chat:**
  - render markdown (today Jack's `**bold**` shows raw);
  - fold history by session and draft, with the current draft's thread open;
  - "Show earlier" loads more.
- **Draft review as a structured diff:**
  - one card per requirement: title, a word-level diff of the statement, a params table
    (old → new per row, assumed rows highlighted for confirmation) and checks marked added, changed
    or removed with their method icon;
  - items show the checks they reference;
  - groups can be collapsed and ticked as a whole;
  - keyboard: j/k to move, y/n to tick.
- **Spec view as a traceability tree:** requirement → checks → items → linked test and last state,
  filterable to unmet and assumed.
- **Assumptions checklist:** every `assumed` param in one list to confirm or edit. A human edit
  pins the field.
- **Deep links:** the API's Studio mount gets an SPA fallback, so `/projects/:id?tab=plan` can be
  reloaded.

### 11.4 The session budget (F5, F6)
A session has one budget: the GuudFlow cap set on the project or raised during the session.
The provider account (API key credit) is the owner's concern and GuudFlow never reads or shows it.
- **Warn before the cap.** At 80% of the session budget, post an approval request: "about to
  stop at the budget: raise it?", shown in the cockpit with the limits control. Nobody has to
  watch the meter to keep a session from stopping mid-card.
- **Tell the team.** When the budget is raised, `RUN_LIMITS_CHANGED` goes into every role's next
  brief, and the overseer's "budget nearly spent" re-brief is withdrawn.

### 11.5 Quips as a work simulator
**Problem.** Quips are flat and repetitive. Across 60 quips in three sessions, "rolling up my sleeves"
or "cracking my knuckles" came up about 15 times. The generator only gets the speaker's character, a
list of coworker names and the event, so nobody knows anything about anyone. Two bugs:
- review quips name the wrong subject ("I gave overseer the green light", "Arlo's card");
- the quip log does not record the speaker.

**Design.** Each quip call gets a small, factual "office state", so workers know about each other
and where they stand. Just enough for some spice, built from data GuudFlow already has (no extra
LLM call):
- **Standing this session**, one line per teammate: cards done, first-try passes, review
  rejections, cost so far, time spent idle or waiting. For example: "Sol: 4 cards, all passed first
  try, most expensive so far; Lena: waited 8 min on Sol twice".
- **Recent interactions** involving the speaker, at most 3: who reviewed or rejected whose card,
  who waited on whom, overseer re-briefs, budget raises.
- **History across projects,** at most 1 line per teammate, from persona reputation and episodes,
  so working relationships carry over. For example: "Bram has rejected Sol's work 3 times across
  projects".
- **Anti-repetition:** the speaker's last 5 quips are passed with "say something new", and a
  short list of banned openers ("rolling up my sleeves", "cracking my knuckles", "grabbing
  coffee").
- **More events worth a line:** a review rejection, waiting on a teammate, an overseer wake, a
  budget raise, the most expensive card, a session going to review, and a teammate's first-try
  pass streak.

**Limits** (unchanged hard rules):
- the quip log never enters agent context;
- the office state contains facts and numbers only, never invented drama;
- tone is light banter, never mean about a named person's competence;
- quips stay on the fast tier.

**Fixes:**
- review events pass the card id and builder name as the subject ("T4, Sol's abilities");
- the speaker's name and role are written in every quip entry.

### 11.6 Time limits that do not cut work off
**Problem.** Two wall clocks:
- the session deadline (`max_run_minutes`) stops the session even mid-card;
- the turn timeout (`turn_timeout`, 20 min) aborts a card attempt.

Neither makes models faster, because workers are never told the time. Money is already guarded
by the session budget, and stalls are guarded by the stuck detectors.

**Design.**
- **Session time limit modes,** set per project (Settings) and per session at start:
  - `none`: no time limit; the budget and stuck detectors are the guards.
  - `soft` (the new default): at the time limit, stop starting new cards, let running cards and
    their reviews finish, then submit for review. A `TIME_LIMIT_REACHED` signal is shown in Studio.
  - `hard`: today's behaviour, for sessions that must end on time (CI, scripted runs, demos).
    Offered in Studio, just not the default.
- **The soft limit wakes the overseer.** A new stuck detector, `time_limit`, fires when the soft
  limit is reached (and once early, at 80% of it). The overseer looks for loops and cards that are
  going nowhere and redirects with its usual menu (`rebrief`, `split`, `restart_role`, `decide`,
  `pause`). For example: "T7 has run 25 min with 3 failed reviews on the same issue; split it."
  If nothing is stuck, it lets the running cards finish. The two `time_limit` wakes do not count
  against the overseer's 5 wakes per session (`OverseerCaps.max_wakes_per_run`), so a session that
  already had stalls can still be checked when time runs out. The per-wake cap (3 interventions)
  still applies. If the overseer is out of wakes for anything else, the session goes to
  `HUMAN_NEEDED` instead of running on.
- **Turn timeout becomes a stall timeout:** a card attempt is aborted only after N minutes with
  no model or tool activity (default 10). A long but busy turn keeps going. An optional absolute
  cap per turn stays available, off by default.
- **Pausing for a human does not count** towards a soft time limit.
- **The Studio limits control** can switch the mode or change the limit on a live session (it
  already extends deadlines).

### 11.7 GuudFlow bugs and gaps found while running pacrogue
These are GuudFlow problems, not game ones. Details are in `docs/FIELD_NOTES.md`.

| # | Problem | Status / plan |
|---|---|---|
| F1 | Jack could not drop backlog items | fixed (`25c1d60`, `drop_item`) |
| F2 | Docker build cache filled the disk; nothing prunes it | worker disk check + prune offer (§11.8 W7) |
| F3 | A session cut off by a host restart needed a manual API salvage | startup salvage offer in Studio; check H4 overlap (W7) |
| F4 | A paused run could only be answered, or given more budget, through the API | fixed (`a49fca9`, respond + limits) |
| F5 | A session can stop at its budget with nobody warned in time | §11.4 |
| F6 | After a budget raise, workers still had the "money nearly gone" re-brief | §11.4 |
| F7 | Sessions carry whole conversations across cards (≈29% of cost) | §11.1 |
| F8 | Idle roles re-send their full context after the cache expires | §11.1 |
| F9 | Notebooks resume the largest session | §11.1 |
| — | Overseer `decide` text is stored HTML-escaped (`T4 &gt; T6`) | fix escaping at the source (W7) |
| — | No board signal says which detector woke the overseer | post `OVERSEER_WAKE` with the detector and numbers (W7) |
| — | Planner makes long serial chains, so the renderer idled about 8 min twice | planner hint: split cards on agreed interfaces so builders can start in parallel (W7) |
| — | Studio chat shows raw markdown; the Plan page grows to about 7,500 px | §11.3 |
| — | Studio deep links 404 when the API serves Studio | §11.3 |
| — | Check ids are re-matched by text, so a one-word edit makes a new check | §11.2 |
| — | Items keep stale copies of check text | §11.2 |
| — | Review quips name the wrong subject ("gave overseer the green light") | §11.5 |
| — | The quip log does not record the speaker | §11.5 |
| — | Quips repeat the same openers and know nothing about teammates | §11.5 |
| — | Session deadline and turn timeout cut work off; there is no "no limit" option | §11.6 |

### 11.8 Work packages (in order)
- **W1 Handoff in results.** `AgentResult.handoff`, the validator, host facts, and brief
  injection (own previous card, `depends_on`, overlapping files). Tests: brief contents, caps,
  untrusted wrapping.
- **W2 Session policy.** A fresh session per card, with the narrow keep rule, a fresh reviewer,
  notebooks built from handoffs, and the summarize safety net. Tests: when sessions are kept or
  rotated, the idle rule, the reviewer is always fresh.
- **W3 Cost per card.** `card`, `carried_tokens` and `cold` in `llm_costs.jsonl`, and a per-card
  table in the retrospective and the run summary.
- **W4 Structured requirements.** Schema, the new ops, stable check ids, items as references,
  migration and generated `docs/spec.md`. Tests: each op, migration of today's pacrogue spec,
  resolving item references.
- **W5 PM prompt and validation** for the new shape and op `group`s. Tests: refusals with clear
  messages.
- **W6 Plan view.** Markdown chat with folded history, the structured diff review, the
  traceability tree, the assumptions checklist, and the SPA fallback. Tests: vitest per component
  plus a Playwright screenshot pass.
- **W7 Smaller fixes:**
  - §11.4 budget warning and briefing;
  - the escaped overseer text;
  - the `OVERSEER_WAKE` signal;
  - the planner hint for parallel cards;
  - the worker disk check;
  - the startup salvage offer.

- **W8 Time limits.** The `none` / `soft` / `hard` session modes, the stall timeout instead of the
  per-turn wall clock, and the Studio controls. Tests: a soft limit finishes running cards,
  starts none and wakes the overseer once; `none` never ends on time; a stall aborts and a busy turn does not.
- **W9 Quips.** The office state, anti-repetition, the new events, and the subject and speaker
  fixes. Tests: office-state contents from a scripted board; review subject; no quip text in briefs.

W1–W3 come first: they are what session 4 measures. W4–W6 can follow before or after session 4.
That is the owner's call; running session 4 on the new spec shape would also test the migration.

### 11.9 Where pacrogue stands (resume point)
- **Sessions 1–3 are accepted.** The game has all 8 session-3 cards and 661 tests. Known game
  issues: Dark hides the board, Magnet is offered but not built, and the HUD shows DOTS: DEAD.
- **Session 4 is drafted, not approved.** Jack's **draft-4** (12 changes):
  - a default dash with 2 charges that eats ghosts, with a power pellet refilling the charges;
  - Dash and Wall Burst rebuilt on it, and Phase cut;
  - Rush replaces Dark, and the boss has 5 HP;
  - a readability rule;
  - built-only offers and the HUD fix;
  - cards new:3 and new:4, with B7 as the stretch card.
  
  The owner wants to review it in Studio before approving.
- **Session 4 settings:** Sonnet 5.5 for all workers (the owner kept Sonnet), a $15 GuudFlow cap,
  75 minutes, review for size M and up.
- **State snapshot:** `snapshots/pacrogue-2026-10-06.tar.gz` holds the project (repo, `work.db`
  with the spec, backlog, drafts and Jack's chat), the personas and the cost logs of the three
  sessions. It contains no keys. To restore, extract it into a data root and point
  `GUUDFLOW_DATA_DIR` there. The API key and token were never stored in it, so set them again.
