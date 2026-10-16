---
name: write-cards
description: How the tech lead plans a session as cards. Use on every planning turn and when a card is blocked on you.
---

# Write the session's cards

You write every card the team works on. You never write product code.

## A good card
- One concern, size S (about 10 minutes) or M (about 25). Split anything bigger.
- A title that says the outcome, an owner from the team, `kind`, and `files_hint` where the
  work lands (cards that share files do not run at the same time).
- `serves`: the checks (R4.2) or requirements (R4) it works towards, or `supports` the cards it
  helps. GuudFlow adds each served check's `R#.#:` line to the card for you.
- Acceptance items that are concrete and testable from the code or a test run.
- `implements`: the design component or interface it builds.

## A good plan
- Every check of the session's items is served by some card.
- Builders are not chained needlessly: agree an interface (names, data shapes) in the earlier
  card and the plan `notes`, then let cards that only use it start in parallel.
- Testers get the unit checks, each needing a named test.
- Stay inside the stack and the binding rules in your rules file.

## A card blocked on you
Answer it by changing cards: reword the acceptance, split the card, add the missing piece as a
new card, or decide the open question in `notes`. Do not re-plan the whole session.
