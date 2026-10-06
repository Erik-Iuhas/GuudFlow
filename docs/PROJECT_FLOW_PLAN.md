# Plan, spec, seats and start modes: implementation plan

**Status:** written 2026-10-06; in progress. The per-package status table is at the end (§9).
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
| Stage 0 | in progress |
| P1 Spec store | not started |
| P2 MCP backend | not started |
| P3 Studio shell | not started |
| P4 Project manager + API | not started |
| P5 Studio Plan tab | not started |
| P6 Harness integration | not started |
| P7 Studio Work/Sessions/Team | not started |
| P8 Record | not started |
| P9 MCP Studio | not started |
