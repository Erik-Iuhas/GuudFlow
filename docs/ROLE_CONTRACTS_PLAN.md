# Role contracts, project kinds and a shared knowledge base

Status: RC1–RC9 built (2026-10-08); see §10 for where each piece lives. Builds on
`PROJECT_FLOW_PLAN.md` (§10 Jack, §11 handoffs and structured requirements, §12 reasons) and the
opencode harness plan. Pacrogue session 4, the first `adopt` run, has not been run yet.

## 0. Why

Pacrogue reached a playable game in three sessions, but mostly because the domain is familiar, the
scope is small, three early decisions acted as an architecture, and the owner reviewed every session
by hand. Where nothing held the structure, it slipped:

- **No design.** Requirements went straight to cards. The planner reinvented the structure every
  session; builders guessed each other's interfaces. Nobody owned the dash, so it ended up inside
  Wall Burst. The offer table and the ability registry had no contract, so Magnet was offered
  without being built. `game.js` grew to 932 lines because it was the one place both sides met.
- **The interface doc exists, by accident.** The header of `game.js` and Sol's notebook are a
  real design and knowledge base. Sol wrote them for himself; nobody else reads them, and nothing
  keeps them true.
- **"Met" is a claim.** A check is met when the reviewer says so. Nothing runs the test a check
  names, so a green check proves the reviewer agreed, not that the behaviour holds.
- **Roles are a prompt and a hope.** A role is a name, a role type (capabilities) and a free-text
  responsibility. Nothing says what it must hand over, to whom, or what "done" means for it. Jack
  picks a team from archetype descriptions written for the retired harness.

The goal: roles that behave like a real project team (each owns something, hands something over,
and is checked by someone else), common artifacts that carry knowledge between roles and sessions,
and proof that work was done. Software first, without baking software into the core: a research
report or a docs project should be able to use the same machinery.

## 1. Four pieces

| Piece | What it is | Owned by |
|---|---|---|
| **Role contract** | What a role owns, reads, produces, hands off, how its work is checked, when it escalates, what it may not do. Versioned data, not prose. | The contract library (GuudFlow ships a set; a project may adapt) |
| **Project kind** | A template for a kind of work: lifecycle phases, the artifacts each phase needs, the contracts that fill it, the gates between phases. | The library; Jack picks one with the owner |
| **Shared artifacts** | The common ground roles hand work through: spec, design, knowledge base, verification ledger. Typed, versioned, each with one owner. | Per artifact (below) |
| **Learning loop** | How contracts and kinds get better across projects: retrospectives measure each contract, evidence-backed changes are proposed, a human approves. | Jack proposes; a human ticks |

## 2. Role contracts

### 2.1 Shape

A contract is YAML in `data/contracts/{id}.yaml` (shipped) with project overrides in
`data/projects/{id}/contracts/{id}.yaml`. Loaded by `core/contracts.py` into a dataclass.

```yaml
id: tech_lead
version: 1
title: Tech lead
purpose: Turns the spec into a design the builders can follow, and plans each session against it.
role_type: architect            # capabilities preset (core/harness/capabilities.py)
model_tier: heavy
owns: [design, session_plan]    # artifacts only this role may change (through drafts)
reads: [spec, design, knowledge_base, verification_ledger, code]
produces:
  - artifact: design            # proposed as a design draft, ticked by the Lead seat or auto
  - artifact: session_plan      # the card list (today's PlanResult)
hands_off:
  - to: builder
    what: cards that name the design element they implement and the checks they serve
  - to: product_manager
    what: feasibility notes on spec drafts (a check that cannot be tested, a missing requirement)
done_when:
  - every card in the plan implements a named component or interface, or supports another card
  - every interface a card touches is declared in the design before the card starts
  - every requirement the session serves is reachable from some card
checked_by: reviewer             # who verifies this role's output, and against what
escalates_when:
  - a spec requirement cannot be met without a decision the owner must make
may_not:
  - write product code
  - change requirements (proposes through Jack instead)
rules:                           # pre-assigned lines in the role's prompt (persona layer 3.7)
  - Name every interface before a builder depends on it.
  - Prefer a small number of modules with narrow interfaces over a central file everyone edits.
measures:                        # what the retrospective scores (§6)
  - interface_drift: exported names that differ from the declared interface
  - plan_churn: cards split, reassigned or added mid-session
```

The fields that change behaviour:

- `owns` decides who may propose changes to which artifact. A draft change to an artifact the
  proposer does not own is refused, the way a pinned field is refused today.
- `produces` and `hands_off` become the role's result schema and its brief's "how to finish"
  section. A builder's handoff (§11.1) is already one of these; contracts make every role have one.
- `done_when` becomes acceptance lines the role's result must answer, like card acceptance.
- `checked_by` wires the review edge: the reviewer of a tech lead's design is not the tech lead.
- `rules` are the pre-prompt: injected as the project-rules layer, above memory (hard rule 24).
- `measures` are computed by the retrospective, never self-reported.

### 2.2 The software set (first release)

| Contract | Owns | Hands off | Checked by |
|---|---|---|---|
| `product_manager` (Jack) | spec, backlog | requirements and checks to the tech lead | the Owner (drafts) |
| `tech_lead` | design, session plan | cards against the design to builders; feasibility to Jack | reviewer (cards vs design), Lead seat (design) |
| `builder` | its cards' code | a handoff per card | reviewer, test run |
| `tester` | tests for checks | a test per unit check, named `file::name` | test run (red before, green after) |
| `reviewer` | review verdicts | verdicts per check and per interface | the Owner at acceptance |
| `researcher` | research notes | a decision note with options and evidence | tech lead or Jack (who asked) |
| `overseer` | interventions | interventions with rationale | caps, the Owner |

Changes to today's roles:

- **Jack stops writing technical decisions.** "Logic and drawing are kept apart" becomes a design
  decision owned by the tech lead. Jack keeps product decisions ("Rush replaces Dark"). The
  `LEAD_EMPTY` prompt that makes Jack the technical lead goes away; the tech lead contract fills
  the empty Lead seat instead, and a human Lead approves its drafts when the seat is held.
- **The planner becomes part of the tech lead.** Breaking a session into cards against the design
  is the tech lead's job. `planner` stays as a role type for kinds without a tech lead.
- **The overseer stays separate.** It is the in-session incident responder; the tech lead is the
  architect between and at the start of sessions.
- **The tech lead runs in a sandbox with a checkout.** Jack is a host-side call that sees file
  names only; the design needs someone who can read the code.

### 2.3 Beyond software (to prove the shape, not to ship first)

The same fields describe non-code teams. Two sets are written as part of the first release so the
schema is tested against them, and shipped behind the project-kind picker:

- **Research report:** `editor` (owns the outline and the brief), `researcher` (owns sources and
  notes), `writer` (owns sections), `fact_checker` (checks each claim against a cited source).
- **Docs:** `docs_lead` (owns the information architecture), `writer`, `reviewer` (accuracy against
  the code), `tester` (every code sample runs).

What stays software-only: sandboxes with a repo, merges, the test runner. A kind declares which of
these it uses; nothing in the core assumes them.

### 2.4 Migration from archetypes

`docs/worker-archetypes.yaml` is the legacy harness's catalog (responsibility prompts in the old
APPROVED/REJECTED voice). Its `when_to_use` texts move into contracts as `purpose`, its tools map to
`role_type`, and the meta proposal (`core/meta_proposal.py`) reads contracts instead. The YAML stays
for old runs until the legacy views go.

## 3. Project kinds

A kind is YAML in `data/kinds/{id}.yaml`:

```yaml
id: software_app
title: Software you keep building
phases:
  - id: discover
    owner: product_manager
    produces: [spec]
    gate: owner approves the first draft
  - id: design
    owner: tech_lead
    needs: [spec]
    produces: [design]
    gate: Lead seat approves (or auto when empty)
  - id: build
    owner: tech_lead            # plans; builders and testers do the cards
    needs: [spec, design]
    produces: [code, tests, handoffs]
    gate: every served check verified or claimed with evidence
  - id: accept
    owner: owner
    needs: [verification_ledger]
    gate: owner accepts the session
  - id: learn
    owner: product_manager
    produces: [knowledge_base, retrospective]
team: [product_manager, tech_lead, builder, builder, tester, reviewer]
start_modes: [spec_first, prototype_first, test_first, research_first, maintain]
uses: [sandbox, repo, test_runner]
```

- Start modes (`core/start_modes.py`) become variations within a kind: `test_first` adds a
  tester-before-builder edge, `prototype_first` lets design be a sketch, `maintain` skips design
  unless a card changes an interface.
- Kinds in the first release: `software_app`, `library`, `game` (a `software_app` with visual and
  feel checks), `maintenance`, plus `research_report` and `docs` (§2.3).
- Jack picks the kind with the owner in the first plan turn, proposes the team from its `team`,
  and fills names and personas as today. Team changes still go through drafts (`core/team_changes.py`).

The lifecycle is visible: Studio's Work tab shows the phase each item is in and what gate it waits on.

## 4. Shared artifacts (the common ground)

Every artifact has one owner contract, a version, and changes only through drafts with reasons
(§12 of the flow plan). Readers get it in their brief as a section, never as free text from another
agent.

### 4.1 Spec (exists)
Requirements, checks, product decisions, rules. Owner: `product_manager`.

### 4.2 Design (new)
Owner: `tech_lead`. Stored in `work.db` like the spec, rendered to `DESIGN.md` in the project repo.

- **Components:** id (`C3`), name, paths it owns (`web/src/logic/abilities/**`), responsibility,
  the requirements it serves.
- **Interfaces:** id (`I5`), the component that provides it, a signature or event or data shape
  (`step(input: {dir, actions}) -> Event[]`), which components use it.
- **Data model:** the shapes that cross interfaces (game state, events), with field meanings.
- **Flows:** short Mermaid diagrams (sequence or state) for the paths that matter: one step of
  play, a dash, a level transition. Studio renders them; they stay text so drafts can diff them.
- **Technical decisions:** what Jack's D# technical decisions become (`TD1`), with options rejected.

Links: cards say `implements: [I5]` alongside `serves: [R4.2]`; `files_hint` comes from the
component's paths, so overlap detection follows the design.

Drift check (host side, cheap): after each merge, the exported names GuudFlow already extracts for
handoffs (§11.1) are compared with the declared interfaces of the component that owns those files.
A new export not in the design, or a declared one that disappeared, is flagged on the card and
counts toward the tech lead's `interface_drift` measure.

### 4.3 Knowledge base (new, grows every session)
Owner: `tech_lead` for software kinds (the editor for a report). Three layers, kept apart because
they are trusted differently:

1. **Facts** (generated by GuudFlow, always true): a project map of modules and their exports, the
   test map (check → test → last result), commands that work, file sizes. Rebuilt after each
   accepted session. No model writes this layer.
2. **Notes** (proposed by agents, approved): conventions, gotchas, how-tos. Today these live in
   per-role notebooks (Sol's notebook is the best knowledge of pacrogue's code that exists). At
   session end, notebook entries that more than one role would need are proposed for promotion; the
   tech lead (or the Lead seat) ticks them like draft changes. Stale notes are flagged when the files
   they name change.
3. **History** (the record): decisions, sessions, reasons. Already in `core/record.py`.

Every brief gets the parts of the knowledge base that touch the card's component, wrapped as GuudFlow
content (layer 1) or as approved notes (layer 2, approved by a person or the tech lead, so trusted).
Notebooks stay as each role's scratch pad.

### 4.4 Verification ledger (new)
Owner: GuudFlow (no model writes it). Per check: its state and the evidence behind it.

- **verified**: its linked test ran on the host after the merge and passed (and, in `test_first`,
  failed before the change);
- **claimed**: the reviewer said met, with no test run (manual checks, or no test linked);
- **failing**: its test failed;
- **confirmed by a person**: a manual or visual check the owner ticked at acceptance.

Unit checks must name a test; the tester contract owns writing it. The project's `check_command`
(plan §14.3) gains a way to report per-test results (JUnit XML or `bun test --reporter`), and
`core/spec_adherence.py` records evidence, not just the reviewer's word. Acceptance shows the ledger.

## 5. How the pieces meet in a session

1. **Jack** (spec owner) has the drafts approved. The kind says the design phase comes next.
2. **Tech lead** reads the spec, the design, the knowledge base and the code; proposes design changes
   with reasons (new interface I7 for the dash, component C2 split); a human Lead or the auto-tick
   approves. Then it plans the session: cards that implement design elements and serve checks.
3. **Tester** writes the tests named by the session's unit checks, where the mode asks for tests first.
4. **Builders** get cards with: the checks they serve, the interfaces they implement and use (from
   the design), the knowledge-base notes for their component, and handoffs from earlier cards.
5. **After each merge** GuudFlow runs the linked tests (ledger) and the drift check (design).
6. **Reviewer** checks the card against its checks and against the interfaces it touches.
7. **Acceptance** shows the ledger: verified, claimed, failing, needs your eyes.
8. **Learn:** the retrospective scores each contract's measures; notebook notes are proposed for the
   knowledge base; Jack proposes contract or kind changes when the evidence supports them.

## 6. Learning across projects

- Each contract's `measures` are computed per session by the retrospective
  (`core/harness/retrospective.py`): interface drift, plan churn, checks verified vs claimed,
  review rounds, reopened cards.
- Evidence-backed changes to a contract (a rule that would have prevented a repeated failure) are
  proposed as drafts against the library or the project's override, following rules 16 and 22
  (evidence runs, recency-weighted confidence, lifecycle). Never applied without a human.
- Jack's memory (`core/pm_memory.py`) records which kind and team worked for which goal, so the next
  project starts from experience. This is the "grows across projects" part; the knowledge base is
  the "grows within a project" part.

## 7. Work packages

Ordered so each is useful on its own. Each lands with tests; Studio parts get a storyboard first.

| # | Package | Main files | Depends on |
|---|---|---|---|
| RC1 | Contract schema and loader; the software set as YAML; contract rules injected as the role-rules layer | `core/contracts.py`, `data/contracts/*.yaml`, `core/harness/persona_prompt.py` | — |
| RC2 | Verification ledger: per-test results from the check command, test ↔ check mapping, evidence states, acceptance view | `core/project_checks.py`, `core/spec_adherence.py`, Studio review | — |
| RC3 | Tech lead role: sandboxed, owns the session plan (planner folded in), feasibility notes on drafts; Jack's `LEAD_EMPTY` removed | `core/harness/task_flow.py`, `core/pm_prompts.py`, team proposal | RC1 |
| RC4 | Design artifact: components, interfaces, data model, Mermaid flows, technical decisions; design drafts with reasons; `DESIGN.md` record | `core/design.py`, `core/plan_drafts.py`, `core/record.py` | RC3 |
| RC5 | Cards link to design (`implements`), files_hint from components, drift check after merge, reviewer checks interfaces | `core/harness/tasks.py`, `handoff.py`, `run.py` | RC4 |
| RC6 | Knowledge base: facts layer generated, notebook promotion as drafts, KB sections in briefs, stale-note flags | `core/knowledge.py`, `brief.py` | RC4 |
| RC7 | Project kinds: YAML, phases and gates, Jack picks a kind and proposes the team from it; Work tab shows phases | `core/kinds.py`, `core/project_manager.py`, Studio | RC1, RC3 |
| RC8 | Non-software sets (research report, docs) to prove the schema; kinds declare `uses` | `data/contracts`, `data/kinds` | RC7 |
| RC9 | Contract measures in the retrospective; evidence-backed contract proposals | `retrospective.py`, `pm_memory.py` | RC1, RC5 |

RC1 and RC2 do not depend on each other and help the next pacrogue session even alone: RC2 makes
session 4's results provable. RC3 to RC5 are the design layer. RC6 turns notebooks into shared
knowledge. RC7 to RC9 generalise.

## 8. Joining an existing codebase (and pacrogue session 4)

Many projects arrive as an existing repo with no design: code first, structure implied. That is a
start mode of its own, `adopt`, available in every software kind:

1. **Facts first.** GuudFlow generates the knowledge base's facts layer from the repo (module map,
   exports, test map, commands that work) and runs the test suite once as the ledger's baseline.
2. **Design recovery.** The tech lead's first session reads the code and writes the design *as it
   is*: components, the interfaces actually used, the data that crosses them, and the debt it sees
   (two owners of one behaviour, a hub file, dead code from dropped ideas). Existing notes (code
   header comments, role notebooks) are promoted into the knowledge base. No product code changes.
3. **Target design.** For the work at hand, the tech lead proposes design changes against the
   recovered design, with reasons: what moves where, which interface is added or narrowed.
4. **Build against it.** Sessions after that are planned from the target design as usual.

Pacrogue session 4 is the first run of this. The recovered design should name the debt already
known (the dash lives in two abilities, the offer table has no contract with the registry, Dark is
still wired into drawing, `game.js` and `draw.js` are where everything gathers). The target design
then gives the default dash one owner before draft-4's cards are built. The tech lead reviews
draft-4 for feasibility first and may send changes back to Jack. Session 3 ($19.30 for 8 cards) is
the cost baseline to compare against.

Order: RC1–RC5 and RC6's facts layer, then the `adopt` sessions on pacrogue, then a greenfield
project to test design before code.

## 9. Open questions

1. ~~Who approves design changes when no human holds the Lead seat?~~ Decided (2026-10-08): the
   tech lead approves its own design drafts then; a human Lead approves them when the seat is held.
2. ~~Should a project be able to run without a design phase?~~ Decided in RC7: the kind decides
   (`maintenance` has no design phase), the start mode can defer it (`prototype_first`,
   `maintain`), and a team without the design owner skips it.
3. ~~How many non-software kinds?~~ Two (`research_report`, `docs`), shipped behind the kind
   picker (RC8).
4. ~~Session 4 before or after RC1–RC2?~~ Decided (2026-10-08): after; session 4 is the first
   `adopt` run (§8).

## 10. Implementation (2026-10-08)

| # | What was built | Where |
|---|---|---|
| RC1 | Contract schema, library of 14 contracts, project overrides (fields replace, rules append), "Your role contract" brief section, contract on team rows and in `TEAM` | `core/contracts.py`, `data/contracts/`, `core/harness/brief.py`, `api/routes/contracts.py` |
| RC2 | JUnit parsing (pytest, bun, vitest, jest), test ↔ check matching, ledger states, test-first base check, owner confirmations at accept | `core/ledger.py`, `core/project_checks.py`, `core/spec_adherence.py` |
| RC3 | The planner is whoever owns `session_plan`; a tech lead plans with design sections and feasibility notes (`spec_notes`, posted to the plan conversation as `tech_lead`); Jack's prompt splits product and technical decisions | `core/harness/task_flow.py`, `tasks.py`, `run.py`, `core/pm_prompts.py` |
| RC4 | Design document in `work.db`, design turns with reasons, design drafts (auto-applied when the Lead seat is empty, else a human decides), `DESIGN.md` in the record, accept waits on an open design draft | `core/design.py`, `core/work_db.py`, `api/routes/design.py` |
| RC5 | `PlannedTask.implements`, `files_hint` from component paths, reviewer `design-I#` / `design-TD#` lines, drift check after each merge (`DESIGN_DRIFT`) | `core/harness/task_flow.py`, `handoff.py`, `task_board.py` |
| RC6 | Facts from `main` (modules, exports, owning component, tests, test map, command), notes proposed/approved/stale, notes in briefs (only person-approved notes are trusted), refresh after accept | `core/knowledge.py`, `core/project_runs.py` |
| RC7 | Kinds with phases, gates, team and uses; `adopt` start mode; `set_kind` draft op; Jack's kind context; meta proposal assigns the kind's contracts (a tech lead replaces the default planner); phase strip and item phases | `core/kinds.py`, `data/kinds/`, `core/start_modes.py`, `core/meta_proposal.py`, `GET /api/kinds`, `GET /api/projects/{id}/phases` |
| RC8 | `research_report` and `docs` kinds over the editor, writer, fact checker and docs lead contracts | `data/kinds/`, `data/contracts/` |
| RC9 | Measures per contract scored when a session is accepted; a measure bad in 2 of the last 3 sessions proposes a fixed rule as an undecided `add_contract_rule` change; approving appends it to the project override; Jack sees the measures; kind tags on Jack's episodes | `core/contract_measures.py`, `core/pm_memory.py` |
| Studio | Design tab (design, design drafts by reason, knowledge base), phase strip and item chips on Work, ledger with confirm boxes at review, role contracts on Team, kind picker and adopt mode on New project, labels for the new draft ops | `studio/src/features/design/`, `work/Ledger.tsx`, `team/ContractsPanel.tsx` |

Not built: `escaped_issues` and `spec_churn` measures (they need findings recorded at acceptance),
Mermaid rendering in Studio (flows show their source), and the migration of
`docs/worker-archetypes.yaml` into contracts (§2.4).


### 10.1 Moving an older project over (2026-10-08)

Projects created before kinds (pacrogue) have no kind, a `planner` role and no design. Found while
preparing pacrogue session 4; what now covers it:

| Problem | Now |
|---|---|
| Swapping the planner for a new `tech_lead` role left the builders depending on `planner`; replacing it with `add_role` left two planning roles. Either way no card reached the builders. | `core/team_changes.team_problems()`: a role naming one not on the team, two roles whose contract owns the session plan, a builder not after the planner. A save refuses the first and returns the others as `team_warnings`; a draft's team changes may not introduce any; a run with any stops before it spends anything (`FAILED`). |
| Jack proposed `add_role` to reach the kind's team. | Jack is told the project's accepted sessions and whether a design exists; an older project gets kind + `start_mode: adopt`, and the existing planner is changed with `edit_role` (`architect` / `tech_lead`, same name). |
| No way in Studio to change kind, start mode, role type or contract on an existing project. | Team tab: Project kind card (kind, start mode, team warnings); worker panel: Role type and Contract. |
| Tests written before the ledger proved nothing on record. | The recovery turn lists unit checks with no test; the tech lead links existing tests in `PlanResult.check_links` (checked against the spec and the repo's test files). The links join the session's ledger, the check run proves them, and accepting the session records the state and the link on the check. |
