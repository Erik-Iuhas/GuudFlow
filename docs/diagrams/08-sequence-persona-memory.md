# 08 — Sequence: Persona Memory Lifecycle

The three-layer memory model from DESIGN §26 / §34, end-to-end. Memory is
**dynamic, not append-only-forever**: patterns have lifecycle states,
confidence is recency-weighted, and reconciliation runs after every run.

## 8a · Memory injection at run start

```
Runner         Disk            Persona         BaseAgent         LLM
  │             │                 │                │              │
  │ read profile.json, episodes.jsonl, patterns.json,
  │      antipatterns.json, style.json, reputation.json, history.json
  ├────────────►│                 │                │              │
  │ Persona.from_files(...)                        │              │
  ├────────────────────────────►│                  │              │
  │ bind persona → agent slot                      │              │
  ├────────────────────────────────────────────────►              │
  │             │                 │ identity_layer / character_layer / stake_layer
  │             │                 ◄──────────────┤                │
  │             │                 │ recall(current_task)
  │             │                 ◄──────────────┤                │
  │             │                 │ tag-overlap + recency-weighted confidence
  │             │                 │   (compute_confidence, half_life)
  │             │                 │ MemoryRecall(patterns, antipatterns, episodes)
  │             │                 ├──────────────►                │
  │             │                 │ effective_system_prompt() composes layers
  │             │                 │   1 identity · 2 character · 3 stake ·
  │             │                 │   3.5 teammate brief · 3.6 manifesto ·
  │             │                 │   3.7 project_rules · 3.8 project_context ·
  │             │                 │   4 memory · 5 base + tools · 5.5 protocol voice ·
  │             │                 │   6 CoT primer · 6.5 autonomy · 7 positive framing
  │             │                 │ complete(system, messages, tools, cache_system=True)
  │             │                 │                ├─────────────►│
  │             │                 │                │ response (cache_read_tokens > 0
  │             │                 │                │            on iter 2+)
  │             │                 │                ◄─────────────┤
```

The system prompt is sent with `cache_system=True`. Because the stable
layers (identity, character, manifesto, project rules, project context,
base prompt, tool catalog, primers) come **before** the volatile layer
(recalled memory + current task), the Anthropic prompt cache hits on
every iteration after the first.

## 8b · Post-run synthesis (memory_writer)

```
Runner       MW         WS         Persona        LLM (SOLID)        Disk
  │           │          │            │                │                │
  │ spawn at end-of-run with run summary
  ├──────────►│          │            │                │                │
  │           │ read board (commits, verifies, approvals, rejects, scores)
  │           ├─────────►│            │                │                │
  │           │ read git log + final artifacts
  │           ├─────────►│            │                │                │
  │           │ distil patterns (DESIGN §F.3.0)
  │           ├──────────────────────────────────────►│                │
  │           │ candidate Pattern objects with source_episodes
  │           ◄──────────────────────────────────────┤                │
  │           │ detect pattern flips (§F.3.2)
  │           ├──────────────────────────────────────►│                │
  │           │ patterns to flip → anti-patterns
  │           ◄──────────────────────────────────────┤                │
  │           │ append Episode(run_id, outcome, files, score)
  │           ├─────────────────────►│                │                │
  │           │ update Pattern.confidence by lifecycle rules
  │           │   +0.05 applied & succeeded
  │           │   −0.15 applied & failed
  │           ├─────────────────────►│                │                │
  │           │ WorkingStyle drift from observed behaviour
  │           ├─────────────────────►│                │                │
  │           │ Reputation bucket update (production vs training)
  │           ├─────────────────────►│                │                │
  │           │ history.json append (atomic + rotation)
  │           ├─────────────────────►│                │                │
  │           │            │ persist all six files                    │
  │           │            ├───────────────────────────────────────►│
  │ synthesis complete signal
  ◄──────────┤
```

## 8c · Pattern lifecycle states

```
                         ┌── first observation ──┐
                         ▼                       │
                    ┌───────────┐                │
                    │ emerging  │                │
                    └─────┬─────┘                │
              3+ source_episodes,                │
              confidence ≥ 0.6 │                 │
                              ▼                  │
   ┌─── no reinforcement ──┐ ┌───────────┐       │
   │   after N runs        ├►│  active   │◄──────┤
   ▼                       │ └─────┬─────┘ regains confidence
┌──────────┐               │       │ confidence < 0.5 (after recency
│ archived │               │       │ decay or contradictions)
└──────────┘               │       ▼
       ▲                   │ ┌───────────┐
       │ stays below       │ │ declining │
       │ 0.4 for M runs    │ └─────┬─────┘
       │                   │       │
       │                   │       │ a more specific pattern covers
       │                   │       │ same applies_when
       │                   │       ▼
       │                   │ ┌────────────┐
       │                   │ │ superseded │
       │                   │ └────────────┘
       │                   │
       │                   │ flip detected (§F.3.2 LLM-driven)
       │                   ▼
       │             ┌────────────┐
       │             │ antipattern│
       │             └────────────┘
       │
       └─── (terminal) end-state for archived/superseded/antipattern
```

States are stored on `Pattern.state`. Confidence is decayed using a
recency half-life (90d default — see `compute_confidence` in
`agents/persona.py`).

## 8d · Two reputation buckets, one episode log

```
   ┌───────────────────────────────┐
   │ Reputation                    │
   ├───────────────────────────────┤
   │ + production : ReputationBucket │
   │ + training   : TrainingReputation│
   └────┬──────────────────────┬───┘
        │                      │
        ▼                      ▼
   ┌──────────────────┐   ┌──────────────────────┐
   │ ReputationBucket │   │ TrainingReputation   │
   │ + runs           │   │  (extends bucket)    │
   │ + approvals      │   │ + scenarios_completed│
   │ + rejections     │   │ + tournament_wins    │
   │ + avg_iterations │   │ + graduated : bool   │
   │ + per_domain :   │   └──────────────────────┘
   │   dict[str,score]│
   └──────────────────┘
```

Production runs and training runs **share** `episodes.jsonl` (the raw
truth) but **split** the reputation buckets. Graduation is a soft gate
in the studio — it does not lock the persona out of production.
