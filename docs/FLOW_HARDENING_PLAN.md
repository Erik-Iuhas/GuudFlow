# Flow hardening: sessions that finish without an operator

Session 5 of pacrogue (field notes F10–F14) needed the operator nine times: three code patches
mid-session, three server restarts, two answers to worker questions, and two `continue_run`
calls after a run ended `stuck`. None of that is the game's fault. Each one is a place where
GuudFlow's own flow let the agents get stuck, waste money, or ask a person something the system
already knew. This plan fixes the flow so that a session on any project, with any model, finishes
on its own, and so that when it can't, it says why and offers the next step in Studio.

**Done means:** a scripted replay of session 5 (§9) ends `complete` with no human answer, no
`stuck`, no operator action, and no card re-planned. Every remaining owner action is a Studio
button.

Already fixed during session 5 (not repeated here): the project manager's output cap, follow-up
turns keep the draft, check ids serve their requirement, partial turns and repairs (J1–J3),
reopening a card settles its unblock cards, a blocked card with nothing running wakes the overseer.

## 1. What went wrong, by cause

| # | What happened | Cause in GuudFlow | Section |
|---|---|---|---|
| 1 | Workers blocked cards on "a human playtest" or "a browser screenshot", four times, even when the goal said not to | Manual checks sit on the card's checklist like any other line, so a worker can't meet them and blocks | §2 |
| 2 | A blocked card waited on the tech lead, who had finished; the run ended `stuck` twice | An unblock card can go to a role that will never run again | §3 |
| 3 | The run ended `stuck` with nothing on the board saying why | The scheduler's end state isn't explained to the owner | §4 |
| 4 | Continuing a run re-planned 6 finished items | `continue_run` carries every item, and the plan check needs a card for each | §5 |
| 5 | A worker read a denied `sed` as "`bun test` is blocked" and paused the run | The refusal doesn't say what was refused or what is allowed | §6 |
| 6 | A worker asked which card was its own, and asked a spec question a person had to answer | Briefs don't show parked cards; spec gaps go to `ask_human` | §6 |
| 7 | About $3 of Jack's $3.60 bought nothing | All-or-nothing turns (J1–J3 fixed); learning and replay still open | §7 |
| 8 | The fast-tier builders broke instructions the larger model followed | Rules that matter live only in prose | §2, §6, §8 |

## 2. Owner checks never block a card

A check with `method: manual` (a playtest, a look in a browser) can only be met by the owner at
acceptance, so it must never sit on a worker's checklist as a line they have to meet.

- **Cards split their checklist.** `card_serves` (`core/harness/tasks.py`) tags each acceptance
  line with its check's method. The brief (`TaskBoard.brief_for`) shows two lists: *You must
  meet* (unit, perf, and visual with a reference) and *The owner checks at acceptance* (manual).
  The second list says what to leave ready ("the level loads from the pick screen").
- **The result schema enforces it.** `AgentResult` (`core/harness/results.py`) refuses
  `status: blocked` when the need names only owner checks. The model gets the error in the same
  turn: "owner checks are done at acceptance; submit `done` and list them in `owner_checks`".
  A new field `owner_checks: [check id → what to try]` carries the hand-off.
- **Review follows the split.** Reviewer checklist lines cover only *must meet*. An owner check
  never fails a review.
- **Acceptance asks the owner.** The review panel in Studio lists the session's owner checks
  with the workers' notes. Ticking them records their state (`core/spec_adherence.py`), and an
  unticked one stays `unknown` instead of blocking acceptance.
- **The planner can't put one on a card.** `check_link_problems` / the plan check warn when a
  card's own acceptance line is a manual check, and suggest an `owner_checks` entry instead.

## 3. Nobody waits on a role that has finished

The tech lead plans once and is done, so a card blocked on it sits there until the run ends.

- **Route by who can answer.** In `TaskFlow._blocked` (`core/harness/task_flow.py`):
  - if `on_role` will run again (it has cards left, or it is a builder or reviewer), make the
    unblock card as now;
  - if it has finished for the run (the tech lead after planning, a role marked complete with no
    cards), make no card for it. Wake the overseer at once with a new detector,
    `blocked_on_finished`, and put the question to it. Its `decide` reopens the card (which
    settles the unblock card, already fixed).
- **Spec and design questions go to the tech lead's notes, not to a person.** A `blocked_on:
  planner` need is recorded as a decision request. The overseer answers within the spec and
  design, and only a choice the spec leaves to the owner becomes `ask_human`.
- **Belt and braces.** The `nothing running` case in `blocked_chain` (fixed) stays as the
  fallback.

## 4. A stopped run says why and offers the next step

- **Explain the end state.** When the scheduler ends `stuck`, `max_iterations`, `time_limit` or
  `budget`, the runner posts `RUN_STOPPED`, listing each open card with what it waits on:
  dependency, unblock card, spawn cap, budget. `core/harness/run.py` builds it from the board,
  and the retrospective includes it.
- **Studio shows it with one action.** The review panel shows the reason and a button, *Continue
  with the open cards*. That is a `continue_run` with the open items only (§5), with the budget
  prefilled from what is left of the session cap.
- **No silent `stuck`.** Before the scheduler returns `stuck`, the watchdog must have woken the
  overseer at least once for that situation. If its wakes are used up, the run pauses with
  `HUMAN_NEEDED` instead, so the owner can answer without restarting anything.

## 5. Continuing a run carries only unfinished work

- **Transfer only what is unfinished.** `Backlog.transfer` (`core/backlog.py`) moves an item to
  the continuing run only if one of its cards didn't finish (from the parent's `tasks.jsonl`).
  Finished items stay with the parent.
- **Accepting a continuation accepts the chain.** Accepting it accepts its parents too
  (`core/project_runs.decide_run`, `core/project_repo.py`): their finished items close, and
  `main` gets the last run's head, which already contains the parents.
- **The plan check counts what the run holds.** The planner's coverage check in
  `core/harness/tasks.py` already counts only the run's own items; with the transfer fixed, it
  stops forcing "confirm X still works" cards.
- **The goal can't fight the check.** If a goal says an item is done but the item is still in
  the run, the plan check tells the tech lead how to mark it done-in-parent instead of making
  it plan a card.

## 6. Workers know what they may do and what is theirs

- **Allowed commands in the brief.** Rendered from the compiled capabilities
  (`core/harness/capabilities.py`, `PermissionInputs.bash_allow`), the brief says: "Shell: you may
  run `bun test …`, `git add/commit`, `ls`, `cat`, `grep`, `head`, `tail`, `wc`; anything else is
  refused; use the read/grep tools to look at output."
- **Refusals name the refused part.** When opencode rejects a bash call
  (`core/harness/opencode_runner.py`, `approvals.py`), the tool error back to the model and the
  board signal say which segment was refused and which part is allowed: "`sed` is not allowed;
  `bun test` is". A small parser splits pipes, `;`, `&&` and redirection, and matches each
  segment against the ruleset.
- **Parked cards in the brief.** The brief lists the worker's other cards: *parked, waiting on
  X, not yours to work on now* and *queued after this one*. A card the overseer settled says so.
- **Questions with a known answer are answered from the spec.** `ask_human` from a worker is
  first checked against the open spec gaps. A spec question goes to the overseer (§3), and only
  owner choices reach a person.

## 7. Jack: finish the self-repair plan

From `docs/PM_SELF_REPAIR_PLAN.md`, the remaining tasks:

- **J4:** a worked example per problem code, shown the second time a code comes back.
- **J5:** suggestions as data on a left-out change, with *Accept the suggestion* in Studio.
- **J6:** problem codes in Jack's memory episodes, so recall warns him about the mistakes he
  repeats.
- **J7:** replay the five real session-5 turns (their payloads saved as fixtures) against the
  scripted LLM. Each must keep its valid work, cost at most one repair call, and record every
  call.
- **Spend visibility:** the Plan tab shows the PM's spend for the day next to the session
  budget, from `pm_costs.jsonl`, so planning cost is never invisible again.

## 8. Small models get the same guarantees as large ones

Rules a fast-tier model ignored in prose are enforced by schema or by flow (§2, §3, §6). In
addition:

- **Measure it.** The retrospective counts, per role: blocked results, `ask_human` calls, denied
  commands, re-plans of finished work, overseer wakes. The project playbook records them by
  model, so the PM can see "Haiku builders block twice as often" before choosing a team.
- **A shorter brief for small models.** When a role's model has a small working context or a
  fast tier, the brief puts the must-meet list, the allowed commands and "how to finish" first,
  and the history last.

## 9. The replay test that proves it

`tests/harness/test_session5_replay.py` uses the scripted fake LLM (`tests/harness/fake_llm.py`),
with no opencode or Docker needed for the flow parts. It replays session 5:

1. A builder card with a manual check: the builder tries to block on the planner, gets the
   schema error, and submits `done` with `owner_checks`. The card goes to review, and review
   passes.
2. A builder blocks on the finished tech lead with a real spec question: the overseer wakes at
   once and decides, the card reopens, and nothing stays open.
3. A builder's command is refused: the error names the segment, and the builder retries without
   asking.
4. A run is stopped by its budget: `RUN_STOPPED` lists the open cards, and a continuation carries
   only those items and plans no card for finished ones.

All four end `complete` with zero `HUMAN_NEEDED` and zero `stuck`.

## 10. Order and size

| Step | Work | Size |
|---|---|---|
| 1 | §2 owner checks (schema, brief split, review, acceptance list) | M |
| 2 | §3 route blocks on finished roles to the overseer | S |
| 3 | §4 `RUN_STOPPED` + Studio *Continue with the open cards* | M |
| 4 | §5 continuation carries only unfinished items, chain acceptance | M |
| 5 | §6 allowed commands, refused segment, parked cards | S–M |
| 6 | §9 replay test (written alongside steps 1–5, green at the end) | M |
| 7 | §7 J4–J7, §8 measures | M |

Steps 1–4 remove every operator action from session 5. Step 5 removes the two questions a worker
should never have asked, and steps 6 and 7 keep it that way. Every change under `core/harness/`
also runs the escape suite (CLAUDE.md).

## Status

- **§2 done.** Each acceptance line is classed by its spec check's method or its wording
  (`tasks.is_owner_check`). The brief lists *must meet* and *The owner checks these at
  acceptance* separately. A `done` result answers only the must-meet lines. A `blocked` result
  that names an owner check, a screenshot or a manual verdict is refused in the turn
  (`check_not_blocked_on_owner`). The `owner_checks` hand-off goes to `owner_checks.json`.
  Reviewers judge only the must-meet lines. Studio's review card lists the owner checks with
  *Try:* and tick boxes that record `R#.#` ids as confirmed. The plan-check warning for manual
  lines on cards is not done (the split makes it harmless).
- **§3 done, simpler.** No new routing: the `blocked_chain` detector fires when a card waits
  on a role that has finished for the run (unblock cards excluded), even while others work,
  and the overseer's `decide` reopens the card, which settles the unblock card.
- **§4 done.** `core/harness/run_stop.py` writes `stopped.json` and posts `RUN_STOPPED` with
  each open card's wait. Studio shows it on the review card; *Continue this run* now carries
  only unfinished items. A forced overseer wake before `stuck` is covered by the detector
  above, not by a scheduler change.
- **§5 done.** `Backlog.transfer` keeps `pending: done`, `record_outcomes` leaves those items
  alone, and the planner's coverage and brief skip them ("DONE in the run this one continues").
  They close when the continuation is accepted.
- **§6 done, partly.** A *Shell commands* section is rendered from the compiled capabilities.
  The brief lists the worker's other cards (parked, queued, in review). The how-to-work text
  says what never justifies a block, and that spec questions block on the tech lead. opencode
  writes the refusal text itself, so it can't be rewritten to name the refused segment; the
  brief's rule ("run the allowed part alone") replaces that.
- **§7:** J4 (a worked example per problem code in repair calls), J6 (problem codes as
  tagged episodes in Jack's memory) and the planning-spend line in the Plan tab are done.
  J5 (*Accept the suggestion*) is done (PM_SELF_REPAIR_PLAN J5).
- **§8 partly done:** the retrospective's per-role `friction` (blocked, asked a person,
  refused, model) and three new playbook metrics. The shorter brief for small models is open.
- **§9 done:** `tests/harness/test_session5_replay.py` (owner check, block on the finished
  tech lead, stop report and continuation), plus `tests/harness/test_flow_hardening.py`.
