# Circular ASK cascade — Diana / Sarah handshake tangle

**Severity:** live (corrupts continuation runs; produces silently-empty answers)
**First observed:** [run_632863526b](data/runs/run_632863526b/) (RougePack continuation)
**Status:** captured, not fixed

## Symptom

UI Quip Feed showed an unhealthy peer-comm tangle:

```
Sarah   agent_started   "running through what could go wrong with Diana's
                         architecture decisions before I crack my knuckles"
Sarah   peer_question  → Diana: "What is the expected structure of src/state/?"
Diana   agent_started   "rolling up my sleeves to map out architecture"
Diana   peer_question  → Sarah: "Can you proceed with game logic once
                                   architecture is finalized?"
Sarah   peer_answer    → Diana: "Game logic development can proceed.
                                   Movement, wall-breaking, power-up systems
                                   are implemented."  (← hallucinated)
Diana   peer_answer    → Sarah: ""  (← empty)
```

Sarah eventually receives an empty answer to her real architectural question. The conversation looks like Diana abdicating her architect role.

## Root cause

Four bugs stacked. Each enables the next.

### 1. Continuation no-op COMMIT (root)

`run_632863526b` is a continuation. `inputs/source_repo/docs/design/*.md` from the prior run got seeded into `working/` at run start. Diana's main turn called `fs-create` × 5 with full architecture content — **identical** to what was already on disk. Git saw zero diff. `_auto_commit_locked` returned `None`. Runner posted `PROTOCOL: Diana: COMMIT (no-op)`.

Diana **thinks** she shipped 5 files. She emitted `[COMMIT] summary: design docs created`. But there is no `agent/game_architect` commit. Sarah arrives, sees pre-existing seeded docs but no real attribution, doesn't trust them, asks Diana for clarification.

**Evidence:**
- [data/runs/run_632863526b/llm_calls.jsonl] — Diana call #2 has 5 fs-create tool_calls with real content
- Git log shows only `chore: seed working/ from inputs/source_repo/` (commit a98e9c5) — no game_architect commit
- Board has zero `FILE_CLAIM` signals (separate gap — fs-create isn't auto-claiming on this path)
- PROTOCOL signal at 02:46:27 — `Diana: COMMIT (no-op)`

### 2. `ask-peer` allowed inside respond loop

When Sarah's `[ASK]` woke Diana on a respond task, Diana's first respond turn called:

```python
ask-peer(to: "game_logic_dev",
         question: "Can you proceed with the game logic development
                    once the architecture is finalized?")
```

The bounded peer-answer loop in [`core/runner.py` `_generic_peer_answer`](core/runner.py) allows ANY tool from the agent's manifest, including `ask-peer`. So Diana's "answer" was actually a counter-ASK that suspended Diana waiting for Sarah, while Sarah was already suspended waiting for Diana. Off-loop dispatch broke the literal deadlock but created a circular handshake.

**Evidence:**
- llm_calls.jsonl Diana call #4: `ask-peer` tool_call inside the respond turn
- AGENT_MESSAGE at 02:46:39 — Diana's ASK to Sarah, *new* ask_id `ad294e7e...` (different from Sarah's original `0973e26f...`)

### 3. Sarah's respond hallucinated completion

Sarah's respond task answered Diana's counter-ASK with:

> "Game logic development can proceed. Movement, wall-breaking, and power-up systems are implemented."

Sarah's main task had been running for **9 seconds** at this point. She had implemented zero systems. The own-work context (`_render_responder_own_work`) returned empty (no commits). The model still confabulated.

**Evidence:**
- Sarah's main run hadn't reached the implementation tools yet — only fs-list / fs-read in the lead-up
- AGENT_MESSAGE at 02:46:41 — Sarah's ANSWER signal contains the made-up completion claim

### 4. Codestral repetition collapse → 16k token blow → empty answer extracted

Diana's second respond turn (after receiving Sarah's hallucinated answer) emitted **72,671 characters** of text and hit `stop_reason: max_tokens` at the new 16,384 cap.

The text starts coherent (a real answer about state-module structure), then degenerates:

```
... [ANSWER] ask_id: 0973e26f... answer: game state module defines core
classes... [ACTION] target: game_logic_dev verb: CLARIFY... 12 words. No
marketing language. No apology. No preamble. ... 12 words. No marketing
language. No apology. No preamble. ... [×~200 repetitions for ~70k chars]
```

The system-prompt PROTOCOL VOICE rules got echoed back as output verbatim, in a tight repetition loop, until truncation. Causes:

- The first `[ANSWER]` block is at offset 384, immediately followed by `[ACTION]`.
- `protocol.extract_trailing()` returns the LAST trailing block, which (per parser semantics) is somewhere in the garbage tail or fails to parse.
- `_dispatch_peer_response` falls through with `answer_body = ""`.
- AGENT_MESSAGE posted at 02:48:12 with `"answer": ""`.

The bounded-loop fallback synthesis (committed in `20ebecb`) didn't fire because the model DID produce text — it just produced unparseable text.

## Recommended fixes (ordered by smallest effective change first)

### Fix 1 — Forbid `ask-peer` (and other dispatch tools) in respond turns

**Effort:** 15 min
**File:** [`core/runner.py` `_generic_peer_answer`](core/runner.py)

When building the tool list for a respond LLM call, filter out `ask-peer`, `assign-task`, `decompose-task`, and any other tool whose effect would be "spawn another agent." Respond is supposed to be terminal: read some files, then answer.

```python
RESPOND_DISALLOWED_TOOLS = {"ask-peer", "assign-task", "decompose-task"}
tools = [t for t in agent._tool_schemas_for_llm()
         if t.get("name") not in RESPOND_DISALLOWED_TOOLS]
```

This single change breaks the circular-ASK cascade. Diana would have to actually answer or fail. No more counter-ASK. Eliminates ~80% of the symptom.

### Fix 2 — Emit a real signal when a continuation produces a no-op COMMIT

**Effort:** 30 min
**File:** [`core/runner.py` `_auto_commit_locked`](core/runner.py)

When `auto_commit` returns None AND the role's COMMIT block claims `files: ...`, the runner currently emits the misleading `PROTOCOL: Diana: COMMIT (no-op)`. Replace with:

```python
if files_claimed and not commit_hash:
    # Detect: agent says they wrote files but git is clean. Most likely
    # this is a continuation re-create — the seeded inputs already had
    # identical content. Emit a clear signal so downstream agents know
    # the work is already on disk (just not from THIS run's commits).
    signal_type = SignalType.COMMIT_NO_OP_CONTINUATION
    summary = f"{role}: claimed files unchanged from continuation seed"
```

Add the new signal type to `core/signals.py`. Downstream agents (Sarah) seeing this signal know "the architect's docs are on disk, just from a prior run — trust them."

### Fix 3 — Repetition-collapse detector

**Effort:** 1 hour
**File:** [`core/runner.py` `_generic_tool_use_loop`](core/runner.py) and `_generic_peer_answer`

After receiving an LLM response, check `response.text` for repetition collapse:

```python
def _looks_repetition_collapsed(text: str, threshold: int = 5) -> bool:
    """Detect Codestral's repetition-loop failure mode. If a 30-char
    span appears more than `threshold` times, the model is stuck."""
    if len(text) < 1000:
        return False
    spans = [text[i:i+30] for i in range(0, len(text)-30, 30)]
    counts = Counter(spans)
    return any(n > threshold for n in counts.values())
```

When detected, treat as parse failure → fall through to the existing protocol-cleanup or fallback-synthesis path. Stops Codestral from burning the full output budget on garbage.

### Fix 4 — Empty-own-work prompt nudge

**Effort:** 10 min
**File:** [`core/runner.py` `_render_responder_own_work`](core/runner.py)

When the responder has NO commits and the own-work block would be empty, instead inject:

```
YOUR RECENT WORK: (none yet — your main task has not produced commits.
Answer factually about what you KNOW from the spec and your task brief,
not about what you have BUILT.)
```

This nudges Sarah to say "I haven't built it yet, I'd build X by doing Y" rather than confabulating a completion claim.

## Priority

Ship in this order: **Fix 1 first** (breaks the cascade, smallest change). Then Fix 2 (root cause for continuation runs). Fix 3 and Fix 4 are layered defenses for less-frequent paths.

Combined effort: ~2 hours including tests. Each fix is independent — can ship one at a time.

## Cross-references

- `core/runner.py` `_generic_peer_answer` — bounded loop introduced in commit `20ebecb`
- `core/runner.py` `_auto_commit_locked` — claim scoping introduced in `f20f751`
- `core/signals.py` — would need new `COMMIT_NO_OP_CONTINUATION` enum value for Fix 2
- DESIGN.md §32 (file-claim semantics) — relates to Fix 2's continuation handling
