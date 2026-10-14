# Thin update: Jack manages the team, and the design can be reviewed before a run

**Why.** A person hand-editing the team graph ends up with a generic node (`Bea · agent_3`, no
role type, a stray "manages" edge), and nothing in the project writes a design unless a tech lead
runs inside a session. The owner wants Jack to do both, in the Plan area, with the person
ticking. Almost all the machinery exists; this plan adds the thin layer on top and changes
no scheduler, validation or approval logic.

**Already there (reused as is):** team ops in drafts (`add_role` / `edit_role` / `remove_role`,
always human-ticked, `core/team_changes.py` and `team_problems` refuse a broken team); the draft
panel; the typed design ops and `validate_design_turn` (`core/design.py`); design drafts and the
Design tab (`api/routes/design.py`, `studio/src/features/design/`); Jack's prompt already says
"no tech lead: propose one".

## 0. A run that never started (found on the first live run)

> **Correction (after trying to build S1).** `TaskFlow.build` returns `None` when the team has no
> planner, which means *plain dependency mode*: the developer runs straight from the goal, with no
> cards. A planner-less team is therefore **valid** (the full-journey test runs one to
> completion), so "no planner means no cards" is not the cause of the stall below, and S1 as
> written (refuse such teams) would break legitimate simple teams. S1 and S2 are on hold until
> the run's `board.jsonl` shows why Bram never spawned: candidates are the model or key (no
> Mistral key reaching the gateway, a spawn error before any call), the run having been started
> with a spec but no items, or the scheduler finding nothing runnable. S3 (overseer visible),
> S4 (honest status), S5 (don't skip a ready backlog) and S7 (Jack proposes a sensible team) stand.

### Reframed: the goal is visibility (done)

The first live run ended `stuck` with $0.00 and no explanation, and the code reading did not
settle why. So the first step is not another guess; it is making the next such run explain
itself.

| Step | Work | Status |
|---|---|---|
| C1 | **Run console.** `core/harness/run_console.py`: while a run is active, host-side log lines (core, gateway, worker, api loggers, with tracebacks) are written to the run's `console.jsonl`. The runner also writes the team at start and, at the end, `describe_end`: mode (task cards or plain roles), backlog items taken, and for every role how often it spawned or why it never started (what it waits on). | done |
| C2 | **Role server logs.** `read_console` also returns the tail of each role's `opencode_home/<role>/opencode-serve.log`. | done |
| C3 | **`GET /api/runs/{id}/console`** | done |
| C4 | **Studio Console tab** on the run view: log lines by source, errors in red, a source picker that includes each role's server log, polling every 3 s while open. | done |

With this, the stuck Excuse-o-matic run would have shown either a spawn error or "no role was ever
spawned" with the reason beside it.

Also done:
- **S3.** Under the graph, an overseer bar: "watching" with a **Wake overseer** button
  (`POST /runs/{id}/overseer/wake` posts an owner ESCALATION, which the escalation detector turns
  into a wake), or, when the run has none, why: the overseer only starts when the team has a
  planner (task cards). The console notes the same at run start. This is what the stuck
  Excuse-o-matic run lacked: a builder-and-reviewer team runs in plain-roles mode, which has no
  overseer at all.
- **S4** (small): the waiting line points to the Console tab.
- **S5.** *Run on the goal only* asks first when backlog items are open.
- **S6.** A repair round that re-sends changes adds "Fixed N changes in a repair round", not a
  second copy of the summary.
- **S7.** Jack's team context ends with a computed "Minimum team" line (planner, builder,
  reviewer), and his prompt tells him to propose what is missing in the same turn.

Not done: S1/S2 (dropped: planner-less teams are valid), and sections 1 and 2 below (team graph
diff, design conversation).

### Original notes: A run with nobody to plan it just sits there (found on the first live run)

**Seen (Excuse-o-matic, `run_85824de61c`).** The team was a developer (Bram) and a reviewer
(Uma) and nothing else. The run showed "Waiting for the first agent…", then `stuck` at 1:03 with
2 signals, no cards and $0.00. Comments then failed with "This run is no longer active". The
overseer did not appear in the graph and never woke.

**What the project record adds** (`Excuser-o-matic-record.md`). The session line reads
`run_85824de61c no_changes (0 commits, items none)`, while B1–B5 are all still `open`. So the run
was started **on the goal only, with no backlog items selected**, even though the spec and backlog
were fully drafted and approved (9 requirements, 4 decisions, 5 items, spec v7, plan cost $0.08).
That makes two independent faults: nothing told the owner that open items were being skipped, and
a team without a planner cannot turn even a goal into cards. Fixing either one alone would have
avoided this run.

**Causes (read from the code and the record).**
0. **Run started with open items unselected.** "Run on the goal only" and "Start a run with N
   items" sit side by side; with five open, spec-linked items and none ticked, the run quietly
   used neither.
1. **Nobody creates the cards.** Cards come from the role that owns `session_plan` (a planner or
   tech lead). `team_problems` returns early when the team has *no* planner
   (`core/team_changes.py`: `if not planners: return problems`), so a builder-and-reviewer team
   passes every check and starts. With no cards, nothing is spawnable, and the scheduler ends
   `STUCK` within a minute.
2. **The overseer has nothing to watch.** Its detectors need cards, a blocked chain or ten idle
   minutes (`no_progress_minutes`), and the scheduler returns `STUCK` first. An empty board is
   the one stall no detector covers. The overseer is also event-driven and not a team node, so
   the graph never shows it, which reads as "it was never created".
3. **The status text is wrong for the case.** "Waiting for the first agent…" stays on screen
   after the run has stopped, and a stopped run only says comments can't land.

| Step | Work | Where |
|---|---|---|
| S1 | **A team that builds needs a planner.** `team_problems` reports "no role plans the session, so no builder gets cards" when there are builders and no `session_plan` owner. The start-run check refuses (it is a blocker like the cycle case), and the Approve-the-team screen shows it with a one-click "Add a planner" (adds a `planner` after nothing and before the builders; a tech lead when the kind has one). Jack's plan prompt gets the same line. | `core/team_changes.py`, `core/runner.py`, `studio/src/views/Studio.tsx` and the approve screen |
| S2 | **Empty board is a stop with a reason.** When the scheduler is about to end `STUCK` with zero cards ever created, post `RUN_STOPPED` with "no card was created: no role plans the session" (or "the planner finished without a plan"), not a silent `stuck`. This reuses `run_stop.py`. | `core/harness/run_stop.py`, `core/runner.py` |
| S3 | **Overseer in the graph.** A small node beside the team: asleep, woke N times, last decision and why, and a "Wake overseer" button that posts a manual wake with the operator's text. The comment box on a stopped run offers "Continue this run" in place of the dead-end error. | `studio/src/features/cockpit/`, `core/harness/run.py` (manual wake), run view |
| S5 | **Don't skip a ready backlog silently.** *Run on the goal only* with open items present asks "B1–B5 are open and ready. Run them?" (items ticked by default, the goal-only path one click away). The review card for a `no_changes` session says which items were not run. | `studio/src/features/work/`, `studio/src/views/Studio.tsx` |
| S6 | **Jack's repair note repeats his reply.** The first plan message in the record says the same summary twice (the original text, then "Repairs: I've drafted the first spec…"). When a repair only re-sends corrected changes, append one line ("Fixed 2 changes"), not a second full summary. | `core/project_manager.py` (`repairs` reply assembly) |
| S7 | **Jack owns a minimum viable team.** Jack's first plan (`/plan/start`) never built one: Excuse-o-matic kept a developer and a reviewer and nothing planned the session. Three parts: (a) **prompt:** a "Minimum team" block in `pm_prompts.py`, stated as a rule and not as advice: a plan-owning role (`planner`, or a `tech_lead` when the kind has one), at least one builder after it, and a reviewer checking the builders; Jack checks the *Current team* section against it on every first draft and in `update` turns, and proposes `add_role` / `edit_role` ops for what is missing, with the reason; (b) **context:** `_team_context` ends with a computed line, "Minimum team: missing a role that plans sessions", from the same check as S1, so Jack is told, not left to notice; (c) **validation:** the turn validator already runs `team_problems` on team ops; with S1 it blocks a final team that has builders and no planner, so a draft that removes or never adds the planner is repaired (or parked with a suggestion) instead of accepted. | `core/pm_prompts.py`, `core/project_manager.py` (`_team_context`), `core/plan_drafts.py` |
| S4 | **Honest status.** Replace "Waiting for the first agent…" with the live state: "No cards yet, waiting for the planner", and after a stop the `RUN_STOPPED` reason. | `studio/src/features/cockpit/` |

Done when: starting a builder-and-reviewer team is refused with the reason and a fix button; a
run that still ends with no cards says why; the overseer is visible on the graph with its wake
count and can be woken by hand. Add `tests/harness/test_flow_hardening.py` cases for the empty
board stop, a `team_problems` case for the planner-less team, and a scripted-LLM test that
Jack's first draft for a project whose team has no planner contains an `add_role` for one.

**Order:** S1, S2, S5 and S7 first (they stop the failure), then S3, S4 and S6. These come before the
Team and Design steps below, because a new project's default team should never fail like this.

## 1. The team belongs to Jack

| Step | Work | Where |
|---|---|---|
| T1 | **"+ Add agent" opens the Plan tab with the team box**, not a blank node. The Team tab link ("Ask Jack to change the team", done) becomes a small composer: a text box that posts to the Plan chat with `team:` context ("change the team: …") and jumps there. The manual node editor stays under an "Advanced: edit by hand" fold. | `studio/src/views/Studio.tsx` |
| T2 | **Graph diff in the draft.** When a draft holds team ops, `DraftPanel` renders `TeamGraph` twice (now and after) with added nodes green, edited amber, removed struck through, and the dependency and review edges Jack changes drawn solid or dashed. Read-only: ticking stays on the change list. | `studio/src/features/plan/DraftPanel.tsx`, `team/TeamGraph.tsx`, a pure `applyTeamOps(team, ops)` in `team/model.ts` |
| T3 | **Jack tidies on request.** One prompt line: a message starting `team:` (or "reorganize the team") asks for a *complete* proposal in one turn, with every role typed, a name-able purpose, and review links, instead of a single op. No new tool; it uses the existing ops. | `core/pm_prompts.py` |
| T4 | **No generic nodes.** Studio's manual "+ Add agent" and the API both require a `role_type`; the default `agent_N` role name is replaced by a type-based name (`developer`, `reviewer`, …). | `studio/src/views/Studio.tsx`, `api/routes/projects.py` |

Done when: asking Jack for "a researcher before the builder, Uma reviews Bram" produces a draft
whose graph diff shows exactly that, ticking and approving it changes the team for the next
session, and a broken proposal (cycle, builder no card reaches) is refused by `team_problems`
with the reason on the card.

## 2. Design before the run (technical mode)

The design ops and drafts exist; what is missing is a way to *start a design turn on the host*,
without a session. Thin version: reuse Jack's loop with a design phase.

| Step | Work | Where |
|---|---|---|
| D1 | **`DesignManager`**: the same turn loop as `ProjectManager` (validate, keep valid changes, repair only the bad ones, record cost), but the tool payload is `DesignTurn`, the context is the spec plus the current design plus a short repo summary (file tree and the knowledge base's generated facts), and the persona is the tech lead's contract. Changes land in a design draft. | new `core/design_manager.py`; factors the shared turn loop out of `project_manager.py` (no behaviour change) |
| D2 | **Routes:** `POST /design/start` (first design from the spec), `POST /design/messages` (a person talks to the designer), reusing the draft approve/discard routes. | `api/routes/design.py` |
| D3 | **Design tab gets the conversation:** the same `Conversation` component as the Plan tab, labelled "Designer", beside the existing design view and draft panel. Shown when *technical mode* is on (project setting, off by default); otherwise the tab stays read-only as today. | `studio/src/features/design/DesignTab.tsx`, project settings |
| D4 | **Who is the designer when there is no tech lead:** the host-side designer uses the `tech_lead` contract regardless of the team, so a design can exist before a tech lead does. A design with no tech lead on the team shows a note: "Add a tech lead so sessions plan against this design." Jack's existing prompt line covers proposing one. | `core/design_manager.py`, `DesignTab.tsx` |
| D5 | **Gate before the run (optional, same switch as technical mode):** *Start a run* asks "Review the design first?" when a design draft is open, and refuses to start while one is, which matches the existing rule that accept waits on an open design draft. | `core/project_runs.py` or the invoke route, `studio/src/features/work/` |

Done when: with technical mode on and no tech lead on the team, the person can open the Design
tab, ask for a design, tick its changes, approve it, and then start a run whose briefs carry the
approved design.

## 3. Order and size

| Order | Steps | Size | Risk |
|---|---|---|---|
| 0 | S1–S7 (planner-less team, skipped backlog, empty-board stop, overseer node, repair note, Jack's minimum team) | M | S1 changes what `team_problems` refuses: check existing team fixtures |
| 1 | T1, T4 | S | none: UI wiring and a default name |
| 2 | T2, T3 | M | the diff view is new UI; no backend |
| 3 | D1, D2 | M | the shared turn loop refactor must not change Jack: run `tests/core/test_project_manager.py` unchanged before and after |
| 4 | D3, D4, D5 | M | needs the technical-mode switch |

Steps 1 and 2 answer the screenshot (a generic `Bea · agent_3` node) and can ship alone. Steps 3
and 4 are the design conversation.

## 4. Not in this update

- **Dragging nodes and drawing edges in the graph.** Edges come from Jack's ops, which the team
  check validates. Hand-drawn edges would need their own validation UI; the "Advanced" fold keeps
  today's manual editor for anyone who wants it.
- **Streaming Jack's items as they validate.** Separate: a "N changes so far" counter during a
  turn is the cheap version (the draft already fills as changes pass validation).
- **A distinct "designer" role type.** The tech lead contract is the designer; no new role.

## 5. Tests

- `applyTeamOps` (pure): add, edit, remove, a rename of a dependency; vitest for the diff colours.
- Jack: a `team:` message yields one complete team proposal (scripted LLM).
- `DesignManager`: a scripted turn with one bad change keeps the good ones and repairs the bad;
  the design draft opens; approval stores the design; no tech lead on the team still works.
- Route: `design/start` returns 409 when a design draft is already open.
- Studio: Add agent without a role type is refused; the Design conversation appears only in
  technical mode.
