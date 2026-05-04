# 11 — The 12-Layer Prompt Stack

The `effective_system_prompt()` method in `agents/base.py` composes a
specific layer order. Layer count has grown beyond the original 7 (see
DESIGN §20) — the live shape is captured here. Cache boundary matters: a
provider that supports caching (Anthropic) caches everything **above**
the volatile suffix.

```
   ┌──────────────────────────────────────────────────────────────────┐
   │ 1 · IDENTITY                                            [cache]  │
   │   persona.identity_layer() or "You are the {role} agent."        │
   ├──────────────────────────────────────────────────────────────────┤
   │ 2 · CHARACTER                                           [cache]  │
   │   working style — methodical, terse, etc.                        │
   ├──────────────────────────────────────────────────────────────────┤
   │ 3 · STAKE FRAMING                                       [cache]  │
   │   one-sentence why-this-matters                                  │
   ├──────────────────────────────────────────────────────────────────┤
   │ 3.5 · TEAMMATE BRIEF (M1)                               [cache]  │
   │   roster + reputation snippet                                    │
   ├──────────────────────────────────────────────────────────────────┤
   │ 3.6 · MANIFESTO (M2)                                    [cache]  │
   │   data/manifestos/default.md or per-project                      │
   ├──────────────────────────────────────────────────────────────────┤
   │ 3.7 · PROJECT RULES (§36)                               [cache]  │
   │   data/projects/{id}/persona_rules/{persona_id}.md +             │
   │   _shared.md                                                     │
   ├──────────────────────────────────────────────────────────────────┤
   │ 3.8 · PROJECT CONTEXT (Phase K)                         [cache]  │
   │   roadmap.md — the team's shared understanding                   │
   ├──────────────────────────────────────────────────────────────────┤
   │ 4 · RECALLED MEMORY                                  [VOLATILE]  │
   │   persona.recall(task).render_for_prompt()                       │
   ├──────────────────────────────────────────────────────────────────┤
   │ 5 · BASE PROMPT + TOOL CATALOG                          [cache]  │
   │   system_prompt + ToolManifest.render_for_prompt()               │
   ├──────────────────────────────────────────────────────────────────┤
   │ 5.5 · PROTOCOL VOICE (M1)                               [cache]  │
   │   verbatim ≤12-word rules for COMMIT/VERIFY/etc.                 │
   ├──────────────────────────────────────────────────────────────────┤
   │ 6 · CHAIN-OF-THOUGHT PRIMER                             [cache]  │
   ├──────────────────────────────────────────────────────────────────┤
   │ 6.5 · AUTONOMY RULES (commit d2cdfb1)                   [cache]  │
   │   "START WORKING NOW. Make at least one tool call."              │
   ├──────────────────────────────────────────────────────────────────┤
   │ 7 · POSITIVE FRAMING                                    [cache]  │
   │   "Make this something you are proud of."                        │
   ╞══════════════════════════════════════════════════════════════════╡
   │ VOLATILE SUFFIX                                      [VOLATILE]  │
   │   current task · upstream context block ·                        │
   │   iteration encouragement · pending AGENT_MESSAGE                │
   └──────────────────────────────────────────────────────────────────┘
```

**Legend:**
`[cache]` — the layer is sent with `cache_control: ephemeral` and reused
across iterations. `[VOLATILE]` — the layer changes per call and forces
a (small) cache miss for the suffix.

## Caching boundary

Layers marked `[cache]` are **cacheable** because they are stable across an
iteration and across runs that hire the same persona on the same
project. The Anthropic client opts in via `cache_system=True` (commit
`7b1c54f`). Layer 4 (memory) is the only "stable but per-call" insertion
— it can drift between iterations as the recalled subset changes, so
keeping it after the cacheable block is correct.

The volatile suffix (current task, upstream context, encouragement,
pending peer messages) is appended to the **user** message, not the
system prompt, so cache hits remain cheap.

## Where each layer is set

| Layer | Source | Setter |
|-------|--------|--------|
| 1–3 | `Persona` | `Runner.bind_persona()` at run start |
| 3.5 | reputation aggregator | `Runner._build_teammate_brief()` |
| 3.6 | `data/manifestos/default.md` | `BaseAgent.load_manifesto(project_id)` |
| 3.7 | `data/projects/{id}/persona_rules/` | `BaseAgent.load_project_rules(project_id, persona_id)` |
| 3.8 | `data/projects/{id}/roadmap.md` | `Runner._inject_project_context()` |
| 4 | `Persona.recall(task)` | `BaseAgent.effective_system_prompt()` per call |
| 5 | per-class `system_prompt` + `ToolManifest` | builtin or `AgentConfig.system_prompt_overrides` |
| 5.5–7 | `BaseAgent.effective_system_prompt()` | hard-coded constants |

## Why ordering matters

Layer 4 (memory) sits **after** project rules so that "rules trump
memory on conflict" — a project rule can countermand a remembered
pattern (CLAUDE.md hard rule 24).

The autonomy block (commit `d2cdfb1`) is intentionally late so it is the
last instruction the model sees before its turn — the fix that stopped
agents from acting like chatbots.
