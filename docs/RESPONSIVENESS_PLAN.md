# Responsive interaction: no message into the void

**Status:** built 2026-10-17 (R1–R6). Receipts: `studio/src/features/cockpit/receipts.ts`, `ReceiptLine.tsx`, `AnswerReceipt.tsx`.

Every time a person acts on an agent (writes to it, wakes it, answers it, stops it) Studio should
show, from real signals, what happened to that action: sent, waiting for the worker, being read,
acted on. Nothing is faked with timers; the only clock shown is how long a real wait has lasted.

## 1. Where it falls short today

| Interaction | What the person sees now | Gap |
|---|---|---|
| Message one worker (agent panel) | "Sent. X reads it at the start of its next turn." | A static sentence. No sign of when X actually starts, reads it, or answers. |
| Comment to everyone / one worker (run view) | The box clears; a "who has been told" row appears once the board signal arrives | Between clearing and the board echo the message is nowhere; no "sending"; no sign of a reply. |
| Wake the overseer | "Woken. Its decision appears below." | Nothing until the decision; no "the overseer is looking now". |
| Answer an agent's question | The banner disappears | No confirmation of what was sent or that the agent resumed. |
| Plan checkpoint: approve / send back | The bar disappears | After "send back", nothing says the planner is re-planning with the note. |
| Stop | The stopped bar appears at once | Running workers finish their turn first; the bar does not say who is still finishing. |
| Jack, the tech lead (preflight), grooming | "is drafting…" / "is planning…" | Long LLM waits with no elapsed time; the preflight has no live state. |
| Live connection | Only an empty-run hint mentions it | If the websocket drops, receipts silently stop; the person thinks agents ignore them. |

## 2. Packages

- **R1 Receipts.** One shared model (`features/cockpit/receipts.ts`): given the board and an action
  (who, when, the signal it created) it returns the stage from real signals:
  `sending → sent → waiting (worker mid-turn) → waking → reading (worker started) → got it
  (DELIVERED) → answered (the worker's result)`. A small `Receipt` line renders it with the time
  since sending. Used by the worker message box and the run's comment box (an outbox row shows
  "sending" until the board echo lands, then joins the "who has been told" list).
- **R2 Wake the overseer** shows "waking → looking (overseer started) → decided (intervention or
  result)".
- **R3 After answering.** The run view keeps a short confirmation of the last answer and checkpoint
  decision ("You answered planner: … · planner resumed", "Plan sent back · planner is re-planning
  with your note") until the next relevant signal makes it stale.
- **R4 Stopping.** The stopped bar says "Stopping: Juno and Nia are finishing their current turn"
  until they end, then "Stopped".
- **R5 Elapsed time on long waits.** A `useElapsed` clock on every host-side LLM wait (Jack's
  turn, the tech lead's preflight turns, grooming): "Jack is drafting · 0:42".
- **R6 Connection state.** A live/reconnecting pill next to the comment box; while reconnecting,
  receipts say they will catch up.

## 3. Tests

Vitest for the receipt model (each stage from a scripted board), the message box and comment box
receipts, overseer wake states, the stopping list, the answer confirmation and the elapsed clock.
