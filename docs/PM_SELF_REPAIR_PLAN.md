# Jack repairs his own drafts

Field note F10. Drafting the pacrogue session-5 plan cost about $3.60 in calls to Jack. Roughly
$3 of that bought nothing: two failed turns, rejected attempts inside turns that did succeed, and
a turn that only re-sent what a merge bug had dropped. Every recovery came from the operator
(patching code, restarting the API, telling Jack what was lost). A user in Studio can do none of
that. The goal is that Jack gets himself out of a rut, and when he cannot, nothing is lost and
the user has a button that finishes the job.

## 1. What went wrong

| # | Failure | What it cost |
|---|---|---|
| 1 | Answer cut off at the output cap (thinking counts against it) | the whole turn, twice |
| 2 | Items named a check (`R2.4`) where a requirement goes | the whole turn (502, nothing kept) |
| 3 | "level 1" in a statement rejected as a tunable number | an attempt in 3 of 5 turns |
| 4 | Re-sent turn used `R14`–`R17`, ids that exist only after approval | one attempt |
| 5 | A follow-up turn dropped every earlier undecided change | a whole extra turn |
| 6 | Failed turns recorded no cost, message or partial draft | the spend was invisible |

Items 1, 2 and 5 are already fixed: a larger output cap, check ids mapped to their requirement,
and follow-up turns that keep the draft. They were point fixes, though. The underlying problem is
that **a turn is all-or-nothing**. One tool call carries every change. One bad change sinks all
of them. The retry re-sends the full context and regenerates the full output, and after two tries
everything is thrown away.

## 2. Principles

1. **Valid work is never thrown away.** Every change that passes validation lands in the draft
   as soon as it is made.
2. **Repair costs what the mistake costs.** A bad change is fixed by resending that change, not
   the turn.
3. **Every call is accounted for.** A failed attempt is recorded with its tokens and cost.
4. **Only real breakage blocks.** A problem blocks only if approving the change would break the
   spec or the backlog. Style rules become warnings that a person sees.
5. **Ruts are detected, not retried.** If the same problem comes back on the same change, the
   loop stops retrying it and hands that change on with a concrete suggestion.
6. **Whatever the operator did, the user can do.** Each recovery in F10 maps to something Jack
   does automatically or a Studio action (§7).

## 3. Jack edits the draft change by change (tool loop)

Replace the single `submit_plan_turn` call with a short tool loop over the open draft. The
loop lives in `core/project_manager.py`, and each tool is validated by `core/plan_drafts.py`.

| Tool | Effect | Returns |
|---|---|---|
| `propose_changes(changes[≤8])` | validates each change on its own and adds the valid ones to the draft | per change: `cid` and `ref`, or problems with suggestions |
| `revise_change(cid, change)` | replaces one of Jack's undecided changes | `ok` or problems |
| `withdraw_change(cid)` | removes one of Jack's undecided changes | `ok` |
| `finish_turn(reply, groups, question?)` | ends the turn | the turn's summary |

- Each tool result is small: ids and problems. Earlier proposals are never regenerated. The
  growing conversation is read from the prompt cache, so a repair costs a few hundred output
  tokens plus cached input.
- Changes Jack has proposed but nobody has decided stay his to revise or withdraw. Changes a
  human ticked, rejected or wrote can't be touched (the same rule as `merge_pm_turn`).
- The loop is bounded by tool steps (default 24), by a per-turn budget (the gate
  `pm_turn_budget_usd`, default $1), and by a stop condition: `finish_turn` was called, or
  the model ended without a tool call. Hitting a bound is a *partial* turn (§5), never an
  exception.
- When the model stops at the output cap mid-call, the changes from completed calls are
  already saved. The loop sends "continue from where you stopped" once, with a lower
  `propose_changes` batch size.
- The output cap is set from effort: room for thinking at that level plus the batch. It is
  not a fixed constant.
- `merge_pm_turn` remains for the after-session phase and for tests. It is the same validation
  applied to one batch.

## 4. Validation that helps instead of rejecting

`validate_turn` becomes `validate_change(change, draft, spec, backlog)`, which returns
`Problem(code, severity, message, suggestion)`.

- **Codes.** Each problem has a stable code (`unknown_requirement`, `check_not_requirement`,
  `ref_not_in_draft`, `numbers_in_statement`, `pinned_field`, `dependency_cycle`,
  `reason_too_short`, …). Codes feed rut detection (§6) and memory (§8).
- **Severity.**
  - `block`: the change would break approval (unknown id, bad ref, cycle, pinned field,
    missing check). It is not added.
  - `warn`: a style rule (`numbers_in_statement`, a short reason). The change is added with
    the warning on it, and Studio shows the warning on its card. A person decides.
- **Suggestions are computed, not guessed by the model.**
  - `R14` (not yet approved) gets: "use ref `new:11` (Lives and hit buffer), the requirement
    this draft adds".
  - `R2.4` is mapped automatically (already done).
  - An unknown item gets the closest open item by title.
- **Auto-fix only when unambiguous.** A suggestion is applied without asking only when exactly
  one candidate exists and the fix keeps the change's meaning. The change records `autofixed`
  with the original value, and Studio shows it.
- **Refs span the whole draft.** A ref is valid in `serves`/`depends_on` if it belongs to any
  change in the open draft, not only the current batch. This removes "I couldn't link the two
  cards formally".
- **Fix the false positive.** `_NUMBER` skips counts and ordinals that name things, such as
  "level 1", "the first 3 pellets", or ids. Since `numbers_in_statement` is now a `warn`, a
  miss costs a warning, not a turn.

## 5. A turn that fails keeps what it made

- A turn ends `complete`, `partial` (some changes in, some problems open, or a bound reached)
  or `failed` (no changes, e.g. the provider is down).
- Jack's message is stored in every case, with `cost_usd`, `tokens`, `status` and the open
  problems. `POST /plan/messages` returns 200 with `status` and stops answering a model mistake
  with 502. 5xx is kept for provider and server errors.
- Every PM call is appended to `projects/{id}/pm_costs.jsonl` (model, tokens, cost, attempt,
  outcome) whether or not the turn succeeds. The Plan tab shows each turn's cost and how much
  went to repairs.

## 6. Rut detection

Per turn, track `(code, target)` for each problem (target = cid or the change's target key,
`plan_drafts._target`).

- **Second time a code hits the same target.** Apply the suggestion if one exists and is
  unambiguous. Otherwise park the change as `needs_person`: it goes into the draft
  undecided, flagged with the problem and the suggestion. Jack is told not to retry it.
- **Third repeat of any code in a turn.** Jack gets that code's worked example (a short fixed
  text for each code, e.g. how `params` and a `{key}` placeholder replace a number).
- **The per-turn budget hits 80%.** Jack is told to finish with what he has. At 100% the loop
  stops and the turn is `partial`.

The loop stays out of an endless retry by construction, and the money a rut can burn is capped
per turn.

## 7. What the user can do (instead of the operator)

| Operator did this in F10 | Now |
|---|---|
| Raised the output cap and restarted the API | the cap follows effort; a cut-off continues on its own (§3) |
| Patched the validator for `R2.4` | the validator suggests the fix, or applies it when unambiguous (§4) |
| Told Jack in chat to re-send dropped changes | nothing is dropped (`merge_pm_turn` fix, §3) |
| Read server logs to see why a turn failed | the Plan tab shows the turn's status, problems and cost (§5) |
| (none: a 502 left nothing) | **"Ask Jack to fix these"** sends a repair turn with only the open problems, in the same loop |
| (none) | **"Accept the suggestion"** on a parked change applies the computed fix |

## 8. Jack learns which mistakes he makes

Each turn writes a `pm_memory` episode with the problem codes it hit and how each one was
resolved (repaired, auto-fixed, parked). The memory writer forms a pattern only once 3+ episodes
share a code (rule 16), e.g. "statements with tunables: use params with a placeholder". Recall
puts the relevant patterns into Jack's prompt, so a mistake that cost money twice is less likely
the third time. No pattern changes validation; they only shape Jack's prompt.

## 9. Tasks

| # | Task | Files |
|---|---|---|
| J1 | Problem codes, severity, suggestions; `validate_change`; draft-wide refs; `_NUMBER` fix | `core/plan_drafts.py` |
| J2 | Turn status and cost on every message; `pm_costs.jsonl`; 200 with `partial` instead of 502 | `core/project_manager.py`, `api/routes/plan.py` |
| J3 | Tool loop (`propose_changes`, `revise_change`, `withdraw_change`, `finish_turn`), bounds, continue after cut-off, cap from effort | `core/project_manager.py`, `core/pm_prompts.py` |
| J4 | Rut detection, parking, worked examples, per-turn budget gate | `core/project_manager.py`, `core/plan_drafts.py` |
| J5 | Studio: turn status and cost, warnings and parked changes on cards, "Ask Jack to fix these", "Accept the suggestion" | `studio/src/features/plan/` |
| J6 | Problem codes in `pm_memory` episodes and recall | `core/pm_memory.py` |
| J7 | Replay tests: the six F10 failures as scripted-LLM fixtures. Each must end with the valid work kept, no 502, bounded calls and recorded cost | `tests/core/test_project_manager.py`, `tests/api/test_plan_routes.py` |

Order: J1 → J2 → J7 (for J1/J2) → J3 → J4 → J5 → J6. J1 and J2 alone stop the money disappearing.
J3 and J4 make the repair cheap.

**Measure of done:** the five F10 turns replayed against the scripted LLM produce the same
draft. Every call is recorded. Repairs cost less than 20% of the first attempt, and no turn is
lost.
