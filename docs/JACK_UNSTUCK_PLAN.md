# Jack keeps the project moving

**Status:** plan, 2026-10-17. Builds on `JACK_DOCK_PLAN.md` (the dock) and `RESPONSIVENESS_PLAN.md`
(receipts).

## 1. The role

Today, when a session gets stuck, an operator steps in. They read the board, find the cause, and
press the right Studio button or call the right route (`FIELD_NOTES.md` F1–F14). **That job is
Jack's.** The owner should never need to know which route clears a pause or which card blocks
which.

Jack works one loop, in the dock or the Plan tab:

1. **Notice.** Jack watches for the states that mean "stuck" (§3). He sets the dock's badge and
   adds an *Ask Jack* button where the problem shows.
2. **Diagnose.** Jack reads the **board digest** by default. When he needs more, he fetches one
   thing at a time with read tools (§4): a card's thread, a worker's brief, a turn's console, a
   diff.
3. **Propose.** In two or three lines Jack says what is wrong, why, and what he would do. He
   attaches the fix as **action buttons** (§5).
4. **Act on your press.** Each button calls an existing route. Jack never acts unasked. The one
   standing permission is that he may *read* anything in the project except secrets.
5. **Follow through.** After an action, Jack watches the receipt and the next signals. He reports
   "✓ unblocked: T3 started" or "still stuck: here is the next step". He never leaves the owner
   guessing.

### Decisions (from the owner, 2026-10-17)

- **Model.** Jack keeps his own model (`pm_model`) everywhere: dock, Plan tab, Ask mode. No
  cheaper tier.
- **Approve now in the dock.** Kept, for small drafts with one reason.
- **During a run.** The board digest is the default context. Briefs, consoles, threads and diffs
  come through read tool calls when Jack needs them.

## 2. How Jack differs from the overseer

The **overseer** runs inside a session and is woken by the stall detectors. It acts at once
through its fixed menu (rebrief, decide, reassign, split, extend review, restart role, pause),
and it has no person to ask.

**Jack** works for the owner, across sessions, before and after them and while they run. He
proposes; the owner presses. When an in-run fix is what is needed, Jack's button usually *asks the
overseer*: "Wake the overseer: T3 waits on a finished role, decide it." The overseer stays the
one actor inside a run. Jack also covers what the overseer cannot reach: setup, the team, the
spec, environment problems, a stopped or crashed run, review, and money.

## 3. Where things get stuck, and what Jack does

Each row: what the owner sees today → what Jack reads → what he offers. The F-numbers point to
real incidents.

### 3.1 Before a session

| Stuck state | Jack reads | Jack offers |
|---|---|---|
| **Team cannot work** (`team_problems`: two planners, a builder no card reaches, a role naming a missing one) | the team, the refused start | a team-change draft that fixes it ("make the planner the tech lead") → Approve now |
| **No spec yet in spec-first/test-first** (Start refused "approve the plan first") | spec version, open draft | "Draft the first spec from the goal" (a Jack turn), or open the draft |
| **Stack not set** (stack check inert; F1 of the worker context work: pygame ignored) | the spec stack, decisions, input files | a `set_stack` draft from what the decisions already say → Approve now |
| **Missing test runners** (`missing_runtime` gate) | the runtime check | the install line to run, or "start anyway: these checks stay unmet" |
| **Missing provider key / model not in Settings** | model config (never the keys), the error | "open Settings → Models", and which tier lacks a model |
| **Prepared session out of date** (preflight `stale`) | the basis versus now | "Refresh the plan" (a tech lead repair turn) or "Start anyway" |
| **Prepared session blocked on owner questions** | the open assumptions | each question with Jack's suggested answer; Answer with one press |
| **Another run is still active** (409 "one active run per project") | the queue, the live run | open it, stop it, or start alongside (`allow_parallel`) when the items do not overlap |
| **Backlog item stuck "in run" with no live run** | items, runs | release it (`PATCH status: open`) |
| **Disk full, sandbox image missing** (F2) | the worker's disk/image report | the prune action, or the build command |

### 3.2 Starting

| Stuck state | Jack reads | Jack offers |
|---|---|---|
| **Cost preview confusing** | estimate, budget, average card cost | a plain sentence ("about $2–4, your cap is $10") and Confirm |
| **Run queued but nothing starts** | job status, worker heartbeat | "the worker is not running: start `python -m worker.job_runner`" |
| **Planner produced no valid plan** (validation failures, `no_plan`) | the planner's turns and their rejections (read tool) | what the plan kept getting wrong; a note to the planner, or a spec fix if the spec caused it |
| **Plan checkpoint waiting** | the planned cards against the items and checks | a one-line review ("covers B4, B5; R2.3 has no card") with Approve, or Send back with his note |

### 3.3 During a session

| Stuck state | Jack reads | Jack offers |
|---|---|---|
| **A worker asked a question** (`ask_human`; F11, F12) | the question, the worker's brief, the spec | an answer drafted from the spec, the board and the shell allow-list ("run `bun test` plain"), sent with one press; or "this is yours to decide" with the options |
| **Card blocked on a finished role** (F13) | the blocked chain | Wake the overseer with the reason; or reopen the card, or make the answer a decision |
| **Card in Needs attention** (validation failures, set-aside work) | the card thread, the last result, the console tail | the cause in one line ("summary too short", "touched `.github/`") and Retry with a note to the worker |
| **Same review issue twice / review rounds exhausted** | both reviews, the diff | extend review, split the card, or reassign it, through the overseer or a card edit |
| **Wrong stack in the work** (stack check refusals) | the refusal, the stack | confirm the stack (a decision) or relax it; message the worker |
| **Burning money without output** (`burn_without_output`) | cost per role, the last turns | narrow the card, lower its effort, or stop that role |
| **Budget or time nearly out** (F5, F6) | spend, the cap, the cards left | raise the cap or extend the time (limits route) and say what that buys; or stop cleanly |
| **Merge conflicts keep coming back** | git notes, files in the conflict | serialise the cards (`depends_on`), or give both to one worker |
| **Spec changed mid-run** | the project update and who has been told | who still has to see it; message them now |
| **Package request waiting** | the package, version, allow-list | approve once, add it to the allow-list, or reject with a reason |
| **Paused for the overseer's cap** | the overseer's log | what it tried and why it stopped; the next action |

### 3.4 After a session

| Stuck state | Jack reads | Jack offers |
|---|---|---|
| **Run ended "stuck"** (F13: nothing says why) | open cards and what each waits on | the reason in one line, plus "Continue with a note" carrying the fix |
| **Run stopped by you** | open cards | "Continue with the open cards" with a note drafted from why you stopped |
| **Run interrupted** (host restart, F3) | the salvage report | salvage, then send to review |
| **Waiting for review** | the review card: owner checks, check results, the diff summary | what to look at first and which manual checks are yours; Accept or Reject (always the owner's press) |
| **Checks failed before review** | the check output | which check failed and the card it belongs to; continue with a fix card |
| **Merge into main conflicts at accept** | the marker files | a reconcile run with the files listed |
| **Remote sync or publish failed** | the sync error (never the token) | the cause and the retry; re-enter the token in Settings |
| **Design draft waiting on the Lead seat** | the design draft | a summary and Approve or Discard |

### 3.5 Across sessions

- **The same problem keeps coming back** (a worker blocking on manual checks, F11, F12): Jack proposes a
  rule or a project skill (`add_rule` / `add_skill` drafts) so it stops. This uses his
  cross-project memory too.
- **Cost drift**: "the last 3 sessions cost 40% more per card". He points to where the money went
  (`cost-reports`) and suggests what to change (effort per item, a smaller model for one role).

## 4. What Jack reads

**Default context, every turn the dock or Plan tab sends:** the *board digest*. It is short and
built by the host. It holds the page you are on, the project's state (spec version, open draft,
backlog counts, runs waiting for review) and, for a live or recent run, cards by column with
owner and wait reason, who is running, blocked or parked, the last result per role, open
questions, spend against the cap and time left. Agent-written text in it is wrapped as untrusted.

**Read tools, called only when the answer needs them:** each returns a capped excerpt and
records its cost with the turn.

| Tool | Returns |
|---|---|
| `card(id)` | a card: acceptance, thread tail, handoffs, review issues |
| `brief(role, n?)` | what a worker was told on a turn (A1 brief log) |
| `console(role, tail?)` | the end of a turn's console (tool calls, refusals) |
| `diff(card)` | the card's merged diff, cut to size |
| `result(role, n?)` | a turn's structured result |
| `spec(id)` / `design(id)` | a requirement, check, decision, rule, component or interface |
| `run_report(run)` | retrospective, check results, owner checks |
| `health()` | team problems, missing runtimes, model config gaps, disk, worker status (no secrets) |
| `history(item)` | a backlog item's history across sessions |

Jack never sees provider keys, remote tokens or MCP secrets, and no tool returns them.

## 5. What Jack can offer

Actions are typed in Jack's reply (`actions: [...]`). The dock renders them as buttons. Each one
maps to a route that already exists:

| Action | Route |
|---|---|
| `message_worker(role, text, wake)` | `POST /runs/{id}/roles/{role}/message` |
| `wake_overseer(reason)` | `POST /runs/{id}/overseer/wake` |
| `answer(text)` | `POST /runs/{id}/respond` |
| `plan_review(approve or send_back, note)` | `/runs/{id}/plan/approve`, `/plan/send-back` |
| `edit_card` / `add_card` / `set_priority` | `/runs/{id}/cards…` |
| `limits(budget, minutes)` | `PATCH /runs/{id}/limits` |
| `approve_package(id)` | `POST /runs/{id}/approval/{id}` |
| `stop` | `POST /runs/{id}/stop` |
| `continue_run(run, note)` | `POST /invoke/{project}` with `continue_run` |
| `start_session(items or preflight)` | invoke, or `/preflight/prepare` |
| `release_item(id)` | `PATCH /backlog/{id}` |
| `approve_draft` | `/plan/draft/approve` (dock Approve now) |
| `open(target)` | navigates: a card, a tab, Settings |

`stop`, rejecting a session and raising the budget always ask for one more confirm in the button
itself ("Raise to $15? Confirm"). Accepting a session stays on the review card: Jack can point to
it, but he does not press it.

## 6. Where "Ask Jack" appears

Every stuck surface gets an *Ask Jack* button that opens the dock with the context chip already
set and a starter question filled in. Some examples:

- the HUMAN_NEEDED banner: "Help me answer this";
- the Needs attention column and the card drawer: "Why is this card stuck?";
- the stopped bar: "What should the next session do?";
- a refused Start (team, spec, runtime, 409): "Fix this";
- the budget warning: "Is it worth raising?";
- the review card: "What should I check?".

## 7. Packages

- **U1 Board digest** (host, `core/jack_context.py`): page context + project state + run digest,
  capped, untrusted text wrapped; a test pins its size on a 2,000-signal run.
- **U2 Read tools**: the §4 tools as PM tools, each capped and cost-recorded; repeated
  calls within a turn are cut off at a budget; tests with a scripted client.
- **U3 Actions**: `PlanTurn` (or a sibling `AskTurn` for Ask mode) gains `actions`, validated
  against §5; the dock renders buttons; each press shows its receipt (R1); Jack follows up when the
  receipt settles (a short host-side check, not a new LLM call, unless it is still stuck).
- **U4 Stuck states → badge + Ask Jack**: one Studio selector computes the §3 states from data
  already loaded; the buttons open the dock prefilled.
- **U5 Run-ended-stuck reason** (F13, still open): the run posts the open cards and what each
  waits on; Jack's digest uses it.
- **U6 Health**: `GET /projects/{id}/health` (team, runtimes, model config, disk, worker, queue)
  for Jack and for Studio's own banners.
- **U7 Patterns into rules**: when Jack sees the same stuck kind twice in a project's history, he
  proposes a rule or skill draft; recorded in his memory.

Order: U1 → U3 (with the dock's J1–J2) → U2 → U4 → U5 → U6 → U7. Every package keeps Jack on his
own model and every action behind a press.

## 8. Tests

Scripted Jack turns, where each scenario is a recorded board that must produce the right diagnosis
and action:

- F11, a denied pipe: answer with the allow-list;
- F13, blocked on a finished role: wake the overseer with the reason;
- a team with two planners: a team-change draft;
- a stale preflight: refresh;
- a budget at 85%: raise, with confirm;
- a run ended stuck: continue with a note.

Plus: no action runs without a press, no tool returns a secret, the digest stays under its cap, and
read tools stop at the per-turn budget.
