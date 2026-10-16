# Worker context, orientation and run controls

**Status:** plan, written 2026-10-16. Nothing here is built yet.

Testing with smaller models showed:

- workers ignored the owner's stack (Python with pygame) and wrote TypeScript;
- the tech lead did not act as if writing the cards were always its job;
- requirements were closed out wrongly or not at all;
- reviewers spent about 20 tool calls finding their way before doing anything useful.

Small models make these worse, but a read of the code shows the context itself has gaps. Each
finding below names the code that causes it.

## 1. Findings

### What reaches a worker today

A card turn's brief (`core/harness/brief.py`) holds:

- the goal;
- the team;
- "Your job" (the role's description);
- the role contract;
- the card;
- the extra sections `TaskFlow.begin_turn` adds (`core/harness/task_flow.py:205`): the spec, the
  design, project knowledge and handoffs.

The spec section comes from `SpecBrief.card_section` (`core/harness/spec_brief.py:135`).

### F1. Plain decisions never reach a worker

`SpecBrief._shared` lists only **binding** decisions (`spec_brief.py:163`).
`design.render_for_brief` lists only **binding** technical decisions (`core/design.py:827`).

A decision such as "D2 Language: Python 3.12 with pygame" that is recorded but not marked
binding appears in no brief: not the builders', not the reviewers', not the tech lead's
(`planner_section` uses the same `_shared`). Only Studio's spec view shows it.

### F2. With a tech lead on the team, Jack is told not to record technical choices

`TECH_LEAD` in `core/pm_prompts.py:139` says: "Record only product decisions … Leave technical
choices to the tech lead." The game kind's team has a tech lead (`data/kinds/game.yaml`).

So an owner who tells Jack "use pygame" gets no rule and no decision. The choice stays in the
chat, which no worker reads. The prompt does not separate a choice Jack would make from a
constraint the owner has already set.

### F3. A rule can silently apply to nobody

`Rule.applies_to` holds role *types*. `Spec.rules_for` matches it exactly (`core/spec.py:285`).
Nothing validates it (`core/plan_drafts.py:278`).

A rule written for `["developer"]` (a role name) or `["python devs"]` reaches only the planner
(who sees every rule). It never reaches a builder, and the reviewer never checks it.

### F4. No single place says "this is the stack"

The stack lives, if anywhere:

- in the goal text;
- in a decision (dropped unless binding, F1);
- in a design decision (dropped unless binding);
- in a rule (dropped if `applies_to` is wrong, F3).

Nothing guarantees it is in every brief, and nothing checks the code against it.

### F5. Requirement checks close only if the planner copies their text word for word

A check is closed only when a reviewer answers an acceptance line starting with `R#.#:`
(`core/spec_adherence.py:36`, `check_verdicts`). The planner is told to copy those lines word for
word (`KEEP_LINES`, `spec_brief.py:32`).

When it does not, two things go wrong:

- **The plan still passes.** A missing check is only a THINKING "spec coverage warning"
  (`task_flow.py:457`), so the check can never be closed in that session.
- **`serves` alone closes nothing.** A card whose `serves` names `R4.2` without the line on its
  acceptance list gives the reviewer no `R4.2` line to answer.

The builder is told only "This card is for R4.2. Say in your summary how each one is met."
`AgentResult` has no structured place to answer per check.

### F6. Goal-only sessions see no requirements

`planner_section` lists only the requirements of the session's backlog items
(`spec_brief.py:117`). A session started "on the goal only" has none. The planner plans with no
requirement in view, and no card can close one.

### F7. The tech lead's card-writing job is only stated in its turn's instructions

The `tech_lead` contract (`data/contracts/tech_lead.yaml`) says it "plans each session". "Your
job" is whatever description the team carries, which Jack may have edited. Only the turn
section "Your output: the task list" (`_planner_instructions`, `task_flow.py:807`) says to write
cards, and nothing structural stops the planner from editing product code.

If a team edit gives the tech lead a reviewer-like description, it reads two conflicting jobs.

### F8. Workers start blind about the project layout

- **No layout.** No brief says where the worker is (the repo root), what the top-level layout is,
  where the tests live or what the check command is.
- **Narrow module list.** Builders get "Modules on `main`" only for their card's `files_hint`
  (`knowledge.render_for_brief`), built from the *project's* `main` at session start, not from
  the run's `main` as cards merge.
- **Reviewers get nothing.** Their sections are spec, design and handoff (`task_flow.py:262`):
  no file list, no check command, no diff, only "call `guudflow_changes`".

The roughly 20 calls before useful work are the agent rebuilding what GuudFlow already knows.

### F9. Nobody can see what a worker was told

Briefs are not saved. The board records the system prompt as a hash and layer names, and the
brief not at all. So "was the rule in the brief?" cannot be answered after a run, only
suspected.

### F10. Plain-roles teams get no spec at all

A team with no planner (no task board) gets no spec sections (known gap,
`PROJECT_FLOW_PLAN.md` §9).

### Studio

- **S1. Jack's chat looks like the message was not sent.** `Conversation.send` clears the box
  only after `onSend` resolves, which is after Jack's whole turn
  (`studio/src/features/plan/Conversation.tsx:137`). The typed text stays and nothing shows
  the message was sent.
- **S2. Adding a card is awkward.** One modal with owner, title, acceptance rows, kind and size.
  It has no link to spec checks and no dependencies, and it is far from where cards are read.
- **S3. Workers cannot be messaged.** A comment can target a role (`_comment_target`), but it is
  read only at that role's *next* spawn. A role that is idle, finished or parked never spawns
  again, so the message never arrives. There is no way to wake one worker.
- **S4. The Overseer tab has no wake button.** It is only in the bar under the graph
  (`OverseerBar.tsx`).

## 2. Work packages

They are in order. A comes first so every later fix can be checked against what workers were
really told. R comes next because it decides where B, C, D and E put their text.

### A. Record what every worker was told

1. **Save the brief.** At every spawn, write the rendered brief and the system prompt's layers
   to `<run_root>/briefs/<role>/<nnn>-<card>.md`. Board `PROMPT` signals keep their hash and
   link to the file.

   The memory text is not saved (the rule in CLAUDE.md stays). Notes are already wrapped as
   untrusted.
2. **Read it.** `GET /api/runs/{id}/roles/{role}/briefs[/{n}]`.
3. **Show it.** Studio adds a "What it was told" tab in the worker detail and the card drawer.
4. **Brief contract test** (`tests/harness/test_brief_contract.py`). From a fixture spec, render
   every role's brief (tech lead, builder, tester, reviewer, plain-roles builder) and assert each
   contains:
   - the stack;
   - every binding rule that applies to the role;
   - the card's check lines;
   - the orientation section.

   This is the regression net for F1–F8.

### R. Deliver context the agentic way: rules and skills

Today everything goes into one brief, the first user message of the turn. opencode has two
better channels, and the pinned version (1.18.34) supports both:

- **Rules** (the `instructions` config, loaded with `AGENTS.md`): files that sit in the system
  prompt on every model call. They survive compaction, are cached as a stable prefix (cheaper
  than repeating them in each brief), and are where coding agents expect standing rules.
- **Skills** (`SKILL.md` folders, loaded on demand by the `skill` tool): playbooks the model
  sees as a short list (name and description) and opens only when the task calls for one. Long
  how-to text stops crowding every turn.

Today GuudFlow switches both off for safety: `OPENCODE_DISABLE_PROJECT_CONFIG`,
`OPENCODE_DISABLE_CLAUDE_CODE` and `OPENCODE_DISABLE_EXTERNAL_SKILLS` (`core/harness/server.py`),
and no `skill` permission. The plan keeps those switches. GuudFlow supplies its own rules and
skills from a host-only, read-only place, so nothing the agent writes can become an instruction.

1. **Three layers, each with one job.**

   | Layer | Holds | Changes |
   |---|---|---|
   | Rules (system prompt) | ground rules (B3), the role's job (C1), "Where you are" (E1), how to finish, the definition of done | per spawn |
   | Skills (on demand) | how to do recurring kinds of work well | per release, per stack |
   | Brief (first message) | this turn only: the card, its checks (D4), review findings, notes, handoffs | per turn |

2. **Rules files.** At spawn, the runner writes `rules/guudflow.md` (and `rules/project.md` for
   the project's approved conventions) into the role's host-only tool directory. That directory
   is mounted read-only (`/work/.guudflow` in a sandbox), and the config lists the files under
   `instructions` by absolute path.

   They stay short: under about 150 lines. A rule that a check enforces says so ("the merge
   refuses .ts files"), so a small model learns the boundary instead of finding it by failing.
3. **A GuudFlow skill library.** Versioned in the repo under `data/skills/<name>/SKILL.md`, with
   text only and no scripts. Per role, the runner copies the skills its contract, the project
   kind and the stack call for into a read-only skills folder in the role's private opencode
   config: `opencode_home/<role>/.config/opencode/skills/` on the host, and a read-only mount
   there in a sandbox.

   `permission.skill` allows exactly those names and denies the rest. First set, from agentic
   coding practice:
   - **`orient`**: read "Where you are" first; list before you grep; search inside the card's
     files and folders; never sweep the whole tree or `node_modules`.
   - **`plan-then-edit`**: write a short todo list (opencode's todo tool) before the first edit;
     keep diffs small and focused on the card.
   - **`test-first`**: write or extend the failing test, run it, implement, run the check
     command; never claim a test you did not run.
   - **`close-a-check`**: how to verify each `R#.#` by its method and report evidence in
     `checks` (D4).
   - **`write-cards`** (tech lead): card anatomy, sizing, `serves`/`supports`, acceptance lines,
     interfaces agreed before parallel cards, answering a blocked card.
   - **`review-a-card`** (reviewer): start from the diff in the brief, run the check command,
     one verdict per line with evidence, what counts as a specific finding.
   - **`resolve-conflict`** and **`when-to-block`**: the existing brief text, moved out of every
     turn into skills.
   - **Stack skills**, picked by `spec.stack` (B1): `python-pygame` (layout, pytest, headless
     runs with `SDL_VIDEODRIVER=dummy`), `typescript-bun`, `node-vitest` and others as needed.
     The stack skill is what keeps a small model from writing the wrong language by habit.
4. **The project's own `AGENTS.md`.** On accept, GuudFlow writes an `AGENTS.md` into the project
   repo from the stack, the approved knowledge notes and the check command, like `SPEC.md` today.
   The code then explains itself to any agent, including tools outside GuudFlow.

   It is a protected path: agents propose changes through knowledge notes, and the tech lead or
   a person approves them.
5. **Visibility and learning.**
   - Every skill load is a board signal (event bridge), so Studio and the retrospective show which
     skills a worker opened.
   - The "What it was told" view (A3) shows the rules files and the skills offered.
   - The playbook records which skills went with fewer review rounds and orientation calls.
   - Jack or the tech lead may propose a project skill (for example, how this game's levels are
     built). A person approves it, as with rules (CLAUDE.md rule 24), and it lands in
     `data/projects/{id}/skills/`.
6. **Verify with the real binary first.** A harness test with the pinned opencode checks:
   - the `instructions` files reach the system prompt;
   - only the allowed skills are listed and loadable;
   - an `AGENTS.md` or `.opencode/skills` written into the worktree by the agent is **not**
     loaded, with the locked flags on;
   - the read-only mounts hold in a sandbox (escape suite).

   If `OPENCODE_DISABLE_PROJECT_CONFIG` also stops the global skills folder, the skills go in
   through the config document instead. That will be settled by the test, not assumed.

Packages B, C, D and E below say *what* the workers need to know. R says *how* it reaches them:
standing facts become rules, know-how becomes skills, and the brief keeps only the turn.

### B. The owner's ground rules reach everyone (F1–F4, F10)

1. **A stack on the spec.** `spec.stack` holds:
   - language and version;
   - runtime and framework;
   - package manager;
   - test command;
   - run or entry command;
   - source and test folders.

   Jack proposes it with a `set_stack` draft change. A person can edit it in the Spec tab. Fields
   the owner set are pinned, so Jack cannot change them.
2. **Owner constraints are always recorded.** In every seat, including with a tech lead: a
   technical constraint the owner states ("use pygame", "TypeScript only") is recorded as the
   stack or a binding rule with `decided_by: human`, never left to the tech lead.

   `TECH_LEAD`, `LEAD_EMPTY` and `LEAD_HELD` in `pm_prompts.py` say so. Rejecting an owner
   constraint is a turn problem.
3. **Ground rules in the role's rules file (R2), not the brief.** It goes to every role, in every mode
   (plain-roles too), and in short form on continued sessions. It holds:
   - the stack;
   - every binding rule that applies to the role;
   - the binding decisions.

   As a rule it is in the system prompt on every call, so it outlasts compaction and a small
   model sees it before the card. The brief keeps one line pointing at it.
4. **Plain decisions reach workers as context.** A card's brief lists:
   - the accepted decisions that serve its requirements;
   - all technical decisions of the design, binding or not, one line each.

   "Binding" keeps its one meaning: the reviewer checks it.
5. **`applies_to` is validated.** Draft validation accepts only known role types or `*`. A role
   *name* is mapped to its type, and anything else is a problem Jack repairs.

   On load, existing rules whose `applies_to` matches no role type on the team are treated as
   `*`, and the console warns.
6. **A mechanical stack check.** At merge, the runner flags files whose language is outside the
   stack (for example `.ts` or `.tsx` in a Python stack, or a `package.json` in a pygame project).
   The turn is sent back with the reason, as `unverified_claims` already does. It is not a prompt.

   The check knows a small table of extensions per language. Docs, config and assets pass.
7. **Reviewer line.** Every review checklist gets `stack: the change uses <stack>` as a line with
   its own verdict.

### C. The tech lead always writes the cards (F7)

1. **"Your job" for the planning role is fixed text from its contract, in its rules file**, not the team's free
   description, which is appended after it as "Also". It says:
   - every session starts with your turn;
   - you write every card the team works on, with the checks it closes;
   - you never write product code;
   - when a card is blocked on you, you answer by changing cards (add, split, reword, unblock).
2. **The planner cannot write product code.** Its capabilities allow edits only under `docs/`
   and the design. A planner branch that changes other files is set aside, with "you write
   cards, not code" in its next brief.
3. **Later planner turns get a brief of their own.** When the planner is re-armed (an unblock
   card, the overseer's `decide`), its brief says which card waits on it and that its result is
   card changes. It does not re-plan the whole session.
4. **Team check.** `team_problems` already refuses two planners. It also refuses a team edit that
   gives the planning role a reviewer or builder type, or removes it while builders remain.

### D. Requirements close out properly (F5, F6)

1. **GuudFlow writes the check lines.** When cards are created, the host puts an
   `R#.#: <current text>` acceptance line on each card for:
   - every check in its `serves`;
   - every check of its backlog item that no other card carries.

   Models no longer copy check text, so a paraphrase cannot lose a check.
2. **Coverage is a plan problem.** Every check of the session's items must be on some card. A
   missing check is returned to the planner in the same turn (repaired like any other plan
   problem), not posted as a warning.
3. **Goal-only sessions see the open requirements.** The planner sees the requirements whose
   checks are not met yet, and every card must say what it serves. Cards that serve checks
   carry their lines (D1).
4. **A "Checks this card closes" section in the builder's brief.** For each check it gives:
   - the text;
   - how it is verified (unit, visual, manual = owner);
   - the expected evidence (a named test for unit checks).

   `AgentResult` gains `checks: [{id, met, evidence}]`, validated so that every non-owner check
   on the card is answered. A `met` unit check names a test file that exists.

   This joins the existing `unverified_claims` guard: a claim with no test run is turned unmet.
5. **The reviewer confirms each check**, as today, and now sees the builder's evidence next to
   each line.
6. **Studio** shows on each card which checks it closes and their state, and at acceptance
   which checks the session closed, left unmet or left to the owner.

### E. Orientation for every worker (F8)

1. **"Where you are" in the role's rules file (R2).** GuudFlow generates it on the host at spawn from
   the role's worktree (cheap: `git ls-files`), and gives:
   - "you are at the project root";
   - the top-level layout to depth two (capped at about 40 lines);
   - the stack and manifest;
   - where the tests are;
   - the check command;
   - the entry point.
2. **Card turns get a starting point.** The brief lists, as files to read first:
   - the card's `files_hint`;
   - the files its dependency cards changed;
   - their module exports.

   Facts come from the *run's* `main` at spawn, not the project's `main` at session start.
3. **Reviewers start with the change in hand.** The reviewer brief includes:
   - the changed files with line counts;
   - the card's diff, when under about 300 lines (otherwise the file list and
     `guudflow_changes`);
   - the check command;
   - "start from these files; do not survey the repository".
4. **Shorter briefs for small models** (open in `FLOW_HARDENING_PLAN.md` §8). For models marked
   small in the registry, "How to work" becomes a numbered checklist of five steps. The long
   how-to text moves into skills (R3) that every model opens only when needed.
5. **Measure it.** The retrospective records orientation calls: tool calls before a builder's
   first edit, or before a reviewer's first test run or verdict. It shows per role in the run
   report and as a playbook metric. Target: under 5 for reviewers.

### F. Studio controls (S1–S4)

1. **Jack's chat sends at once.** The box clears on send and the message shows in the thread
   with "Jack is thinking…". On failure the text comes back into the box with the error.
2. **Adding cards.**
   - An "Add card" row at the top of each lane, already set to that worker. One title field;
     Enter adds the card. "More" opens the full card.
   - Acceptance is a textarea, one item per line.
   - A picker for spec checks (`serves`) adds their lines (D1).
   - A picker for `depends_on`.
   - The card drawer can edit a card that has not started.
3. **Message a worker.** Clicking a worker (team card, graph node, worker detail) opens a box
   with **Send** and **Send and wake**.

   The API is `POST /api/runs/{id}/roles/{role}/message {text, wake}`:
   - it posts a targeted priority comment;
   - with `wake`, an idle, finished or parked role is re-armed (`scheduler.rearm`). If it has no
     card, its turn takes the message as its task;
   - a running role gets the message when its current turn ends, as one follow-up in the same
     session, so it is not lost.

   The board shows "you → Bram" and when it was read.
4. **Overseer tab: Wake overseer.** The tab gets a "Wake overseer" button with an optional
   reason, using `POST /runs/{id}/overseer/wake`. When the run has no overseer, the tab says why,
   as the bar does.

## 3. Tests and checks

- **A4 brief contract test**, for every role and mode, as above, over the brief *and* the
  rules files.
- **R6 real-binary test**: the rules files reach the system prompt, only the allowed skills
  load, and agent-written `AGENTS.md` or skills are ignored.
- **Scripted small-model replay** (`tests/harness/fake_llm.py`):
  - a builder that writes `.ts` in a Python stack is sent back (B6);
  - a planner that drops a check line still produces a card carrying it (D1–D2);
  - a goal-only session's cards close their checks (D3);
  - a pinged idle worker spawns once with the message (F3).
- **Unit tests** for `applies_to` validation, `set_stack`, the "Where you are" renderer (caps,
  no files outside the worktree) and the reviewer diff cut-off.
- **Vitest tests** for the chat send, inline card add, the worker message box and the wake button.
- **The escape suite** for R2, R3, B2, B6 and E1, because they touch `core/harness/` and
  sandbox mounts.
- **Owner run.** A real small-model session (Haiku or Mistral) on the pygame project. Expected:
  - Python only;
  - every selected check closed or marked unmet with a reason;
  - reviewer orientation under 5 calls.

  It needs an API key, so it is the owner's run.

## 4. Open questions

- **Card adding.** "Making cards inside the app is awkward": the plan assumes the friction is the
  modal and the missing check and dependency pickers. If it is something else (editing, moving,
  where the button is), F2 changes.
- **"Tech reviewer".** This plan reads it as the tech lead. If a separate reviewer role was meant
  to write cards, C changes.
- **Where "pygame" was said.** In the goal, in the chat with Jack, or in the spec? A1 shows it on
  the next run. F1–F4 cover all three.
