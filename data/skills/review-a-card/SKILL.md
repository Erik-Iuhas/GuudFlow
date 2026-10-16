---
name: review-a-card
description: How to review one card's change quickly and specifically. Use on every review turn.
---

# Review a card

1. Start from the change: the changed files and diff are in your brief (or call
   `guudflow_changes` with the card id). Do not survey the repository.
2. Run the check command from your rules once. Note what failed.
3. Answer every line of the checklist with its own entry: the card's acceptance items, each
   `R#.#` check, each `rule-N` and the stack line. `met` needs evidence you saw (a test that
   passed, the code at a file and line). An unmet line needs a specific detail: the file, what
   is wrong, what would fix it.
4. A binding rule or the stack broken is a failure, however good the rest is.
5. Do not edit files. Do not fail for style the rules do not ask for.
