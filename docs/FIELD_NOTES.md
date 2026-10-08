# Field notes: interventions during real runs

A backlog of fixes found by running real projects (the `pacrogue` game) end to end.
An entry goes here whenever a run needed an operator to step in **outside what a user can do
from Studio**: editing files by hand, calling an API Studio does not expose, restarting a
process, or patching code because the run was stuck on a bug. Ordinary owner choices (scope,
approving drafts, accepting a session) do not count.

Each entry: what happened, what the operator did, the likely cause, and the proposed fix.
Status is `open`, `fixed (<commit>)` or `wontfix (<why>)`.

## Before session 3

### F1. Jack could not remove backlog items — fixed (`25c1d60`)
- **Seen:** while scoping the final session, Jack folded B9 into the plan but had no change type to drop it, and asked the owner to delete it by hand.
- **Operator:** dropped B9 through `PATCH /backlog/B9`.
- **Fix:** `drop_item` draft change; the PM prompt tells Jack to prune obsolete items.

### F2. Docker build cache filled the disk
- **Seen:** with 5 GB of build cache from sandbox image builds, the disk was too full to run the test suite or a session.
- **Operator:** `docker builder prune -af`, removed the pip cache.
- **Cause:** nothing in GuudFlow prunes what sandbox image builds leave behind.
- **Proposed fix:** the worker checks free disk at startup and before each run, warns in Studio below a threshold, and offers (or runs, when configured) a prune of dangling build cache and stopped sandbox containers. `fixed (plan §11.10: disk report, prune, worker check)`

### F3. A session interrupted by a host restart needed a manual salvage
- **Seen:** session 2 (`run_046488c5ab`) was cut off when the host restarted. It only reached review after the operator stepped in.
- **Operator:** restarted the API, then accepted the run through the API with a note listing what was left undone.
- **Proposed fix:** on startup, the worker finds runs whose process is gone and offers "salvage" in Studio: finalize what merged, stop the rest, and send the run to review. `fixed (plan §11.10: salvage of interrupted sessions)` (check whether H4 reconcile runs covers this)

### F4. A paused run could only be answered, or given more budget or time, through the API
- **Seen:** in earlier sessions, when a run stopped for a human (`HUMAN_NEEDED`) or ran into its budget, Studio only said "answer in the comment box". A comment does not clear the pause, so the run stayed stuck.
- **Operator:** posted `HUMAN_RESPONDED` or raised limits by hand.
- **Fix:** `POST /runs/{id}/respond` and `PATCH /runs/{id}/limits`, an answer box in the HUMAN_NEEDED banner and a limits control next to the budget meter. The wall-clock deadline now also ends a run that is paused for a human. `fixed` (this commit)

## Session 3 (final pacrogue session)

Run `run_e12b4626f7`: 8 cards, all passed review on the first try, 44 minutes, $17.95, 661 tests.
No card was stuck on a GuudFlow bug. The overseer woke twice on `burn_without_output` and handled
it on its own (budget re-briefs and a priority order). What still needed the operator:

### F5. Raising the budget at the provider does not reach the session
- **Seen:** the owner added credit to the Anthropic key twice ($5, then $20), expecting the session to use it. The session's own cap (`token_budget_usd`) stayed at $10 and would have stopped it with credit to spare. The owner had to tell the operator, and the operator called `PATCH /runs/{id}/limits`.
- **Cause:** GuudFlow knows only its own cap. It cannot see the key's balance, and Studio does not say clearly that the two are separate.
- **Proposed fix:** the session budget is the only budget GuudFlow knows; provider credit is the owner's concern. At 80% of the budget, post an approval request ("about to stop: raise it?") with the limits control. `fixed (plan §11.4: budget warning)`

### F6. After a budget raise, the team still worked as if money was nearly gone
- **Seen:** the overseer had told both workers "budget nearly spent, finish fast". Raising the cap did not reach them; the operator had to post a priority comment.
- **Proposed fix:** a `RUN_LIMITS_CHANGED` signal goes into every role's next brief (as notes do), and the overseer's budget re-brief is withdrawn when the cap rises. `fixed (plan §11.4: limits in briefs, guidance withdrawn)`

### F7. Sessions carry context from card to card, about 29% of the cost
- **Seen:** after the session, the owner asked where the money went. Each role keeps one opencode session for the whole run, so its later cards re-read all earlier ones (peak 350 k tokens). Cache reads were 46% of session 3's $19.30. Full analysis: `docs/cost-reports/pacrogue-session3.md`.
- **Proposed fix:** a fresh session per card by default (always for the reviewer), or rotation once carried context passes about 60 k tokens. Knowledge carries forward through the brief, the thread and notes. `fixed (plan §11.1: fresh session per card + handoffs)`

### F8. Idle roles re-send their whole context after the cache expires
- **Seen:** after more than 5 idle minutes the prompt cache is gone, and the next call re-sends 100–220 k tokens at full price (about $1.40 in session 3).
- **Proposed fix:** rotate the session when the idle time exceeds the cache lifetime. `fixed (plan §11.1: idle sessions rotate)`

### F9. Notebooks resume the largest session
- **Seen:** end-of-run notebooks resume each role's session, so one cold 328 k-token re-send (about $0.65) went to a short note.
- **Proposed fix:** write the notebook in a fresh session from a summary of the role's results. `fixed (plan §11.1: notebooks from handoffs)`

### Noticed in passing (no intervention)
- **Studio chat shows raw markdown:** Jack's replies show `**bold**` markers in the Plan tab, and the chat column holds the full history since project start, which makes for a very long page. `fixed` (markdown chat, folded history)
- **Deep links 404 when the API serves Studio:** `GUUDFLOW_STUDIO_DIST` is mounted with no SPA fallback, so reloading `/projects/pacrogue?tab=plan` returns `{"detail":"Not Found"}`. `fixed` (SPA fallback)
- **Escaped text on the board:** the overseer's `decide` summary had HTML entities (`T4 &gt; T6`). The model wrote them itself; overseer results are now unescaped when validated. `fixed`
- **Overseer wake reason not on the board:** the overseer started with no signal saying which detector woke it (here `burn_without_output`). Post the detector and its numbers as a signal so Studio can show why. `fixed` (`OVERSEER_WAKE`)
- **Serial plans leave workers idle:** the planner chained most cards (T3 → T4 → T5 → T7 → T8), so the renderer waited about 8 minutes twice. Worth a planner hint: prefer cards that can start on agreed interfaces. `fixed` (planner hint)

## Session 5 (pacrogue): lives, an easier level 1, Magnet and OVERDRIVE

Builders on Haiku 5.5; tech lead and reviewer on Sonnet 5.5. Three runs, each continuing the one
before, because the first two ended `stuck` (F13): `run_e58d6481ff` $2.04, `run_2f30d76622` $1.06,
`run_d34e8e25dd` $1.02 (complete). That is $4.12 for 7 backlog items and 835 bun tests passing.
Jack's planning cost about $3.20 on top (F10). Haiku builders kept blocking cards on manual browser
checks even when the goal said not to (F11, F12). With the overseer and reopen fixes in, the last
run cleared one such block on its own.

### Planning

Drafting the session-5 plan took five calls to Jack where one should have done. About $3.60 went
to Jack, and roughly $3 of it was waste. The two turns that failed were never recorded (about
$1.80, estimated from the two recorded turns of the same size). Rejected attempts inside turns
that did succeed cost about $0.80, and one turn ($0.68) only re-sent what a merge bug had dropped.
The owner had about $8 left on the key, so a third of it went here.

### F10. Jack cannot repair its own answer; every mistake re-buys the whole turn
- **Seen:** (1) the answer was cut off at the output cap, because thinking at the project's effort counts against it. (2) Items named checks (`R2.4`) where requirements go. (3) Statements said "level 1" and were rejected as "numbers belong in params". (4) A re-sent turn named `R14`–`R17`, ids that only exist after approval, instead of refs. Each time the validator rejected the *whole* turn (20+ changes), Jack regenerated all of it from scratch, and after two attempts the API returned 502 and kept nothing. (5) A follow-up turn dropped every earlier undecided proposal, although the prompt tells Jack to build on the open draft.
- **Operator:** raised the output cap, mapped check ids to requirements, made follow-up turns keep the draft (all `fixed`, commits on `claude/clever-pascal-vpz71c`), restarted the API three times, and told Jack in chat to re-send what was lost.
- **Cause:** a turn is all-or-nothing. One tool call carries every change, one bad change sinks all of them, the retry repeats the full context and output, and a failed turn leaves no trace: no draft changes, no cost, no message.
- **Proposed fix:** `docs/PM_SELF_REPAIR_PLAN.md`: Jack edits the draft change by change and repairs only what failed, a failed turn keeps its valid work and its cost, and repeated mistakes are detected and broken. `open`

### F11. A worker read a denied pipe as "bun test is blocked"
- **Seen:** session 5, T1. Sol (Haiku) ran `bun test 2>&1 | grep … | sed …` and `bun test > /work/.t.txt; echo done`. Both were refused because `sed`, `echo` and redirection are not on the shell allow-list. Sol concluded `bun test` itself was denied and paused the run to ask for access.
- **Operator:** answered the pause from the run (a Studio action): run `bun test` plain, pipe only to grep/head/tail.
- **Cause:** the refusal does not say which part of the command line was denied, and the brief does not list the allowed commands.
- **Proposed fix:** the builder brief lists the allowed shell commands. Where opencode reports a denied bash call, the event bridge names the refused segment ("`sed` is not allowed; `bun test` is"), so the worker can retry on its own without asking a human. `open`

### F12. Workers stop to ask questions the board already answers
- **Seen:** session 5. Kenji (game logic, Haiku) paused the run twice. The first pause was F11. The second asked which card was his: T7, from his brief, or T5, which the board still showed as his while it was blocked. It also asked which families Magnet suits, which the spec had left open.
- **Operator:** answered from the run, which is a Studio action. T7 is his, the overseer had already settled T5, and the suited families are speed and rush.
- **Cause:** the brief names the current card, but a blocked card stays assigned to its worker, and nothing says it is parked. A spec gap the registry test enforces (`suited` must not be empty) reached a worker as a question rather than a decision for the tech lead.
- **Proposed fix:** the brief lists the worker's parked cards as "parked, not yours to work on now", with who they wait on. A question about the spec goes to the tech lead as a `blocked_on: planner` note before it becomes `ask_human`. Only an owner choice reaches a person. `open`

### F13. A run ended "stuck" behind unblock cards nobody would close
- **Seen:** session 5 (`run_e58d6481ff`). Kenji blocked T3 and Lena blocked T2 on the planner, which created unblock cards T8 and T10 owned by the planner. The planner had already finished and is never spawned again. The overseer decided neither card needed to wait and moved both to `todo`, but each still depended on its open unblock card. Nothing could spawn, and the run ended `stuck` with three items unfinished. Nothing on the board said why.
- **Operator:** continued the run (`continue_run`, a Studio action) with a goal saying manual checks are the owner's at acceptance.
- **Fix:** reopening a card (overseer decide, rebrief, reassign, restart; human retry) settles its open unblock cards, which move to done with the reason. `fixed` (`TaskBoard.reopen`).
- **Seen again** in the continuation (`run_2f30d76622`). Lena blocked T3 on the planner for a reference screenshot. T4 depended on T3 because they share files, so it never started, and the run ended `stuck` again with the overseer never woken.
- **Fix 2:** the `blocked_chain` detector also fires when a card is blocked and no role is running. The watchdog runs right before the scheduler would return `stuck`, so the overseer wakes and can decide, and reopening now settles the unblock card. `fixed`
- **Still open:** a run that ends `stuck` should post why: the open cards and what each waits on.

### F14. A continued run re-plans the items its parent already finished
- **Seen:** session 5, last pass (`run_d34e8e25dd`, continuing `run_2f30d76622`). The goal said only two render cards were left. `continue_run` moves all the parent's items to the new run, and the planner check needs at least one card per item, so the tech lead had to plan 8 cards, six of them "confirm X still works". Earlier, the first continuation had re-planned three finished items the same way.
- **Operator:** none. The extra cards are small, and on Haiku they cost little.
- **Proposed fix:** when continuing, carry only the parent's items whose cards did not all finish. Finished items stay with the parent and close when the chain is accepted. Or let the plan list such items as "done in the parent" without a card. `open`
