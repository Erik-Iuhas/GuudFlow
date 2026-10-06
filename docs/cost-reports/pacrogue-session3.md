# Cost report: pacrogue session 3 (`run_e12b4626f7`)

8 cards, 4 roles on Claude Sonnet 5.5 (overseer on Opus 5.5), 280 model calls, 44 minutes.
Source: the run's `llm_costs.jsonl` (gateway-measured) and `board.jsonl`. Prices: Sonnet
$2 in / $10 out / $0.20 cache read per million tokens; Opus $4 / $20 / $0.20.

## Totals

| | Tokens | Cost | Share |
|---|---:|---:|---:|
| Cache reads (context re-sent on every call) | 44.6 M | $8.92 | 46% |
| Output (code, tests, results) | 0.47 M | $4.67 | 24% |
| Uncached input (briefs, tool output, cold re-sends) | 2.25 M | $5.71 | 30% |
| **Total** | | **$19.30** | |

The $19.30 includes $1.35 of end-of-run notebooks written after the session finished (Studio showed $17.95 at submit).

## By phase

| Phase | Cost | Share |
|---|---:|---:|
| Building the 8 cards | $13.95 | 72% |
| Reviewing the 8 cards | $3.45 | 18% |
| Notebooks (memory for the next session) | $1.35 | 7% |
| Planning | $0.34 | 2% |
| Overseer (2 wakes) | $0.21 | 1% |

## By card (build + review)

| Card | Build | Review | Peak context at build |
|---|---:|---:|---:|
| T1 run module | $0.55 | $0.21 | 57 k |
| T2 death, pops, banner | $1.15 | $0.17 | 118 k |
| T3 families, templates | $2.22 | $0.43 | 187 k |
| T4 dash, burst, blast | $2.06 | $0.42 | 281 k |
| T5 chain, combo | $1.18 | $0.41 | 325 k |
| T6 run flow UI | $2.12 | $0.43 | 212 k |
| T7 abilities on screen | $2.56 | $0.66 | 287 k |
| T8 feel pass | $2.11 | $0.72 | 350 k |

A worker's later cards cost more for similar-sized work, because the context it carries keeps growing.

## Where money was lost

1. **Session carry-over: about $5.50 (29%).** Each role keeps one opencode session for the whole
   run (`session_reuse`, rotated only after 8 turns, and no role did more than 4). Every call
   on a later card re-reads all earlier cards' conversation. Sol's T5 started with 262 k tokens
   carried in, and Lena's T8 with 266 k. The reviewer's session also carries over, so reviewing T8
   began with 183 k tokens of earlier reviews. Auto-compaction never triggers, because Sonnet's
   window is 1 M tokens. (This is an upper bound: carry-over also saves some re-reading of files.)
2. **Cold re-sends after the prompt cache expired: about $1.40 (7%).** The cache lives 5 minutes.
   When a role waited longer (the renderer waited for logic cards, the reviewer between reviews),
   its next call re-sent the whole carried context at full input price: 219 k for T7, 208 k for
   the T8 review, 188 k for the T7 review.
3. **Notebook on a cold, huge session: about $0.65.** Sol's notebook ran 14 minutes after his last card.
   The cache had expired, so 328 k tokens were re-sent uncached to write a short note.

Together that is about **$7.50 of $19.30 (≈ 39%)** that a fresh session per card would mostly avoid.

## What testability and traceability cost

Less than expected:
- **Running tests:** `bun test` ran 22 times (3 s each, short output). Negligible.
- **Writing tests:** this is part of the output cost. The suite grew from 328 to 661 tests, so
  roughly half of the $4.67 output went to tests.
- **Review:** $3.45 (18%). Each review re-ran the tests and read the diff. Most of a review's cost
  is the carried and cold context (above), not the checking itself.
- **Traceability:** acceptance ticks, notes and requirement ids in results are a few hundred
  output tokens per card, well under $0.50 in total. Notebooks are $1.35.

So the requirements workflow costs roughly $3–4 (review plus test writing beyond the bare
minimum). The bigger leak is how context is managed, not the verification.

## Across sessions

| Session | Calls | Cost | Cache-read share | Peak context |
|---|---:|---:|---:|---:|
| 1 (`run_aef4b1e1be`) | 239 | $6.58 | 25% | 86 k |
| 2 (`run_046488c5ab`) | 187 | $6.51 | 32% | 130 k |
| 3 (`run_e12b4626f7`) | 280 | $19.30 | 46% | 353 k |

The more cards a role does in one run, the larger the share of cost spent re-reading context.

## Proposed fixes (see FIELD_NOTES F7–F9)

- Start a fresh session per card by default (or rotate when carried context passes about 60 k
  tokens), and carry knowledge forward through the brief, the card thread and the role's notes.
  Always do this for the reviewer.
- Rotate the session when a role has been idle longer than the cache lifetime, since a cold re-send
  costs as much as a fresh start.
- Write notebooks from a short summary in a fresh session, not by resuming the large one.
