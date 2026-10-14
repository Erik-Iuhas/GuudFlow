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
