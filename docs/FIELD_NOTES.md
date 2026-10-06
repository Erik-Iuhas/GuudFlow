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
- **Proposed fix:** the worker checks free disk at startup and before each run, warns in Studio below a threshold, and offers (or runs, when configured) a prune of dangling build cache and stopped sandbox containers. `open`

### F3. A session interrupted by a host restart needed a manual salvage
- **Seen:** session 2 (`run_046488c5ab`) was cut off when the host restarted. It only reached review after the operator stepped in.
- **Operator:** restarted the API, then accepted the run through the API with a note listing what was left undone.
- **Proposed fix:** on startup, the worker finds runs whose process is gone and offers "salvage" in Studio: finalize what merged, stop the rest, and send the run to review. `open` (check whether H4 reconcile runs covers this)

### F4. A paused run could only be answered, or given more budget or time, through the API
- **Seen:** in earlier sessions, when a run stopped for a human (`HUMAN_NEEDED`) or ran into its budget, Studio only said "answer in the comment box". A comment does not clear the pause, so the run stayed stuck.
- **Operator:** posted `HUMAN_RESPONDED` or raised limits by hand.
- **Fix:** `POST /runs/{id}/respond` and `PATCH /runs/{id}/limits`, an answer box in the HUMAN_NEEDED banner and a limits control next to the budget meter. The wall-clock deadline now also ends a run that is paused for a human. `fixed` (this commit)

## Session 3 (final pacrogue session)

_Entries are added while the session runs._
