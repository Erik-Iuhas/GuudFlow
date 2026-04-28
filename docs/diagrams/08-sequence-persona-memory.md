# 08 — Sequence: Persona Memory Lifecycle

The three-layer memory model from DESIGN §26 / §34, end-to-end. Memory is
**dynamic, not append-only-forever**: patterns have lifecycle states,
confidence is recency-weighted, and reconciliation runs after every run.

## 8a · Memory injection at run start

```mermaid
sequenceDiagram
    autonumber
    participant Runner as AgentRunner
    participant Disk as data/personas/{id}/
    participant P as Persona
    participant Agent as BaseAgent
    participant LLM as LLMClient

    Runner->>Disk: read profile.json, episodes.jsonl, patterns.json,<br/>antipatterns.json, style.json, reputation.json, history.json
    Runner->>P: Persona.from_files(...)
    Runner->>Agent: bind persona → agent slot
    Agent->>P: identity_layer / character_layer / stake_layer
    Agent->>P: recall(current_task)
    P->>P: tag-overlap + recency-weighted confidence<br/>(compute_confidence, half_life)
    P-->>Agent: MemoryRecall(patterns, antipatterns, episodes)
    Agent->>Agent: effective_system_prompt() composes layers<br/>1 identity · 2 character · 3 stake ·<br/>3.5 teammate brief · 3.6 manifesto ·<br/>3.7 project_rules · 3.8 project_context ·<br/>4 memory · 5 base + tools · 5.5 protocol voice ·<br/>6 CoT primer · 7 positive framing
    Agent->>LLM: complete(system, messages, tools, cache_system=True)
    LLM-->>Agent: response (cache_read_tokens > 0 on iter 2+)
```

The system prompt is sent with `cache_system=True`. Because the stable
layers (identity, character, manifesto, project rules, project context,
base prompt, tool catalog, primers) come **before** the volatile layer
(recalled memory + current task), the Anthropic prompt cache hits on
every iteration after the first.

## 8b · Post-run synthesis (memory_writer)

```mermaid
sequenceDiagram
    autonumber
    participant Runner as AgentRunner
    participant MW as memory_writer
    participant WS as Workspace
    participant P as Persona
    participant LLM as LLMClient (SOLID tier)
    participant Disk as data/personas/{id}/

    Runner->>MW: spawn at end-of-run with run summary
    MW->>WS: read board (commits, verifies, approvals, rejects, scores)
    MW->>WS: read git log + final artifacts

    MW->>LLM: distil patterns (DESIGN §F.3.0)
    LLM-->>MW: candidate Pattern objects with source_episodes

    MW->>LLM: detect pattern flips (§F.3.2)
    LLM-->>MW: patterns to flip → anti-patterns

    MW->>P: append Episode(run_id, outcome, files, score)
    MW->>P: update Pattern.confidence by lifecycle rules<br/>+0.05 applied&succeeded · -0.15 applied&failed
    MW->>P: WorkingStyle drift from observed behaviour
    MW->>P: Reputation bucket update (production vs training)
    MW->>P: history.json append (atomic + rotation)

    P->>Disk: persist all six files
    MW-->>Runner: synthesis complete signal
```

## 8c · Pattern lifecycle states

```mermaid
stateDiagram-v2
    [*] --> emerging: first observation

    emerging --> active: 3+ source_episodes,<br/>confidence ≥ 0.6
    emerging --> archived: no reinforcement<br/>after N runs

    active --> declining: confidence drops below 0.5<br/>(after recency decay or contradictions)
    active --> superseded: a more specific pattern<br/>covers same applies_when

    declining --> active: regains confidence<br/>(applied & succeeded)
    declining --> archived: stays below 0.4 for M runs

    superseded --> [*]
    archived --> [*]

    active --> antipattern: flip detected<br/>(§F.3.2 LLM-driven)
    antipattern --> [*]
```

States are stored on `Pattern.state`. Confidence is decayed using a
recency half-life (90d default — see `compute_confidence` in
`agents/persona.py`).

## 8d · Two reputation buckets, one episode log

```mermaid
classDiagram
    class Reputation {
        +production: ReputationBucket
        +training: TrainingReputation
    }
    class ReputationBucket {
        +runs: int
        +approvals: int
        +rejections: int
        +avg_iterations: float
        +per_domain: dict~str, score~
    }
    class TrainingReputation {
        +scenarios_completed: int
        +tournament_wins: int
        +graduated: bool
    }
    Reputation --> ReputationBucket
    ReputationBucket <|-- TrainingReputation
```

Production runs and training runs **share** `episodes.jsonl` (the raw
truth) but **split** the reputation buckets. Graduation is a soft gate
in the studio — it does not lock the persona out of production.
