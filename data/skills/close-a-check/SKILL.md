---
name: close-a-check
description: How to meet and report a spec check (an acceptance line starting with R#.#). Use when your card has R#.# lines.
---

# Close a spec check

A line like `R3.2: The score resets when a level starts` is a spec check. The session's
requirements close through these lines, so each needs its own answer.

1. Read the full requirement in `.guudflow/project/SPEC.md` (or `guudflow_spec R3`): the text,
   its other checks and how each is verified (`unit`, `visual`, `manual`).
2. `unit`: write a test named after the behaviour and run it. Your evidence is the test file and
   test name, and that it passed.
3. `visual`: produce the screenshot with the screenshot tool. Your evidence is the image path.
4. `manual`: the owner checks it at acceptance. Do not block on it; list it in `owner_checks`.
5. In your result, answer every `R#.#` line of the card in `checks`: `met` with evidence, or not
   met with the reason. Keep the id exactly as written. Never paraphrase a check away.
