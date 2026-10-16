---
name: orient
description: Find your way around the project in a few calls before you read or change code. Use at the start of every turn.
---

# Orient in the project

You start at the project root. GuudFlow has already told you most of what you would search for.

1. Read the "Where you are" part of your rules: the layout, the stack, where tests live and the
   check command.
2. Read your card (or the change you review) and the files it names. Start there.
3. Need a requirement, decision, rule or another card? Read `.guudflow/project/SPEC.md`,
   `DESIGN.md` or `CARDS.md`, or call `guudflow_spec` with an id like `R3.2`. Do not grep the
   code for them.
4. Only then search, and narrowly: `glob` for a file name, `grep` inside the folder the card
   names. Never sweep the whole tree, `node_modules`, `.venv` or build output.
5. Stop exploring once you can say which files you will change and why. Five calls is plenty.
