# Jack everywhere: a chat dock on every page

**Status:** storyboard, 2026-10-17. Not built.

Today you talk to Jack only in the Plan tab. The idea is a small chat dock, always one click away
on every page, into the **same** conversation. The Plan tab stays the full view of that
conversation and its draft. The dock adds where you are as context, so "why is this stuck?" means
the card you are looking at.

## Principles

1. **One Jack, one thread per project.** The dock and the Plan tab are two windows on the same
   conversation (`PlanDrafts.messages`). Nothing said in the dock is lost when you open the Plan tab.
2. **Jack proposes, you act.** Spec changes still land in the draft and wait for your tick. Jack
   never messages a worker, wakes anyone or starts a run himself: he offers a button, and you press it.
3. **Context is visible and removable.** The page you are on goes with your message as a chip
   ("Looking at: run_x · card T3"). You can remove the chip before sending. Nothing is sent silently.
4. **Same responsiveness rules** (`RESPONSIVENESS_PLAN.md`): the box clears at once, the message
   shows "sent", the clock and the live preview run while Jack drafts, and an error puts the text back.
5. **It never pops open by itself.** Jack shows a badge (draft waiting, a session needs you, a
   session is ready for review). You open the dock.

## Storyboard

**Frame 1: closed, on the Work tab.**
A round "J" launcher sits bottom right. A small dot means something waits on you; its tooltip
says what ("2 changes drafted"). The keyboard shortcut is `Ctrl/Cmd+J`.

```
┌──────────────────────────────────────────────┐
│ Work · Backlog                               │
│  B4 Draw the board        for Juno           │
│  B5 Score keeping                            │
│                                         (J)• │
└──────────────────────────────────────────────┘
```

**Frame 2: open on a run, card T3 selected.**
A 380px panel slides up: the latest messages, the context chip and the input. The mode toggle
sits under the input: **Ask** (discuss, the default here) or **Change the plan** (update).

```
                         ┌───────────────────────┐
                         │ Jack · project pacrogue│
                         │ …last 3 messages…      │
                         │                        │
                         │ [Looking at: run_x·T3 ×]│
                         │ Why is T3 stuck?    ⏎  │
                         │ ( Ask | Change plan )  │
                         └───────────────────────┘
```

**Frame 3: Jack answers with the facts and buttons.**
Jack reads the run as data: the card, its thread, who is blocked on whom, the last results. He
answers in two lines and offers *actions you confirm*:

> T3 waits on Xia (tech lead) for the sprite size. Xia finished an hour ago and has not been woken.
> [Message Xia] [Wake the overseer] [Open T3]

Each button uses the existing route (worker message, overseer wake), so the receipt line from
R1 follows it in the dock: "waking Xia… 0:04 → ✓ Xia has it".

**Frame 4: a change, without leaving the page.**
"Add a rule: no TypeScript anywhere." This runs in Change-the-plan mode. The dock shows Jack
drafting with the live preview, then a compact draft card:

```
│ Drafted 1 change · rule: No TypeScript        │
│ [Approve now] [Review in Plan →]              │
```

**Approve now** applies a draft whose changes all share one reason (the same check as the
Plan tab). A bigger draft only offers **Review in Plan**. If a run is live, the project update
from §3 of the worker context work tells every worker in their next turn. The dock says so:
"Workers are told at their next turn."

**Frame 5: moving around.**
Close the dock, go to the Design tab, open it again: the same thread, with a new chip
("Looking at: Design · C2 Board renderer"). A reply that arrives while the dock is closed sets
the badge.

**Frame 6: outside a project (the projects list, Settings).**
The dock asks which project to talk about (a picker of recent projects). It can also start a new
one: "Describe what you want to build". That is the existing new-project flow, opened with your
text.

**Frame 7: phone width.**
The dock is a full-screen sheet with a back arrow. Everything else is the same.

## What it needs

- **J1 Dock shell.** A launcher plus a panel in the app layout, open state kept per tab
  (localStorage), `Ctrl/Cmd+J`, and the phone sheet. It reuses `Conversation` (bubbles, optimistic
  send, drafting clock, preview) in a compact layout.
- **J2 Page context.** Each page registers what it shows (`useJackContext({page, refs})`: project,
  run, card, item, requirement, design element). The chip renders it. `POST /plan/messages`
  takes an optional `context` (ids only). The host resolves the ids to text (card title, column,
  thread tail, blocked-on) and adds it to Jack's prompt as a "Where the owner is looking" block.
  Agent-written text is wrapped as untrusted.
- **J3 Run awareness.** When the context is a run, the host adds a short run summary (cards by
  column, who is running or blocked, last results, open questions). Jack's reply may carry
  `actions`: a typed list (`message_worker`, `wake_overseer`, `open`) that the dock renders as
  buttons. Jack never calls them.
- **J4 Inline draft card.** A compact draft summary with Approve now (only when the draft is
  small and has one reason) and Review in Plan.
- **J5 Badge and nudges.** Computed from data Studio already loads: an open draft, a run waiting
  on you (HUMAN_NEEDED, plan checkpoint), a session waiting for review, an unread Jack reply.
- **J6 Project picker and new project** for pages outside a project.

## Decisions (2026-10-17)

- Jack keeps his own model everywhere, including Ask mode.
- Approve now in the dock stays (small drafts with one reason).
- In a run Jack reads the board digest by default and fetches briefs, consoles and diffs with read
  tools when needed. Jack's job of getting sessions unstuck: `JACK_UNSTUCK_PLAN.md`.
