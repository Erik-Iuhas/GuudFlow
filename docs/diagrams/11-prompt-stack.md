# 11 — The Layered Prompt Stack

The `effective_system_prompt()` method in `agents/base.py` (composer at
~`base.py:1140`) builds the system prompt as an **ordered list of
layers**. The original DESIGN §20 had 7 layers; the live composer now
runs ~14 (some are conditional). Cache boundary matters: the Anthropic
client wraps everything sent as `system` with `cache_control: ephemeral`,
so layers that change rarely cache nicely between iterations.

```
   ┌──────────────────────────────────────────────────────────────────┐
   │ 1 · IDENTITY                                            [cache]  │
   │   persona.identity_layer() or "You are the {role} agent."        │
   ├──────────────────────────────────────────────────────────────────┤
   │ 2 · CHARACTER                                           [cache]  │
   │   persona.character_layer() — working style. Falls back to a     │
   │   default "professional specialist" block when no persona.       │
   ├──────────────────────────────────────────────────────────────────┤
   │ 3 · STAKE FRAMING                                       [cache]  │
   │   one-sentence why-this-matters (persona.stake_layer())          │
   ├──────────────────────────────────────────────────────────────────┤
   │ 3.5 · TEAMMATE BRIEF (M1)                               [cache]  │
   │   "YOUR TEAMMATES:\n\n" + roster + reputation snippet            │
   ├──────────────────────────────────────────────────────────────────┤
   │ 3.6 · MANIFESTO (M2)                                    [cache]  │
   │   data/manifestos/default.md or per-project override             │
   ├──────────────────────────────────────────────────────────────────┤
   │ 3.7 · PROJECT RULES (§36)                               [cache]  │
   │   data/projects/{id}/persona_rules/{persona_id}.md +             │
   │   _shared.md                                                     │
   ├──────────────────────────────────────────────────────────────────┤
   │ 3.8 · PROJECT CONTEXT (Phase K)                         [cache]  │
   │   roadmap.md — the team's shared understanding                   │
   ├──────────────────────────────────────────────────────────────────┤
   │ 3.9 · CONTINUATION BRIEF (Phase G-prime)             [VOLATILE]  │
   │   _continuation_brief — only present when this run resumed       │
   │   from a prior run; tells the agent what's already done.         │
   ├──────────────────────────────────────────────────────────────────┤
   │ 4 · RECALLED MEMORY                                  [VOLATILE]  │
   │   persona.recall(task).render_for_prompt() — patterns +          │
   │   antipatterns + episodes. Only when persona.has_memory()        │
   │   AND _current_task is set.                                      │
   ├──────────────────────────────────────────────────────────────────┤
   │ 4.5a · REPO MAP STRUCTURE (Track 1c)                    [cache]  │
   │   _render_repo_map_layer — PageRank top-N from RepoMap;          │
   │   stable across an iteration so it caches well.                  │
   ├──────────────────────────────────────────────────────────────────┤
   │ 4.5b · TASK-RELEVANT FILES (Track 1d)                [VOLATILE]  │
   │   _render_task_relevance_layer — entries scored against          │
   │   _current_task.                                                 │
   ├──────────────────────────────────────────────────────────────────┤
   │ 4.7 · ACTIVE SUBTASK (Track 7)                       [VOLATILE]  │
   │   _render_active_subtask_layer — current decomposed chunk so     │
   │   the agent stays focused. Skipped when no subtask is in flight  │
   │   or only the synthetic root is active.                          │
   ├──────────────────────────────────────────────────────────────────┤
   │ 5 · BASE PROMPT + TOOL CATALOG                          [cache]  │
   │   self.system_prompt + ToolManifest.render_for_prompt()          │
   ├──────────────────────────────────────────────────────────────────┤
   │ 5.5 · PROTOCOL VOICE (M1)                               [cache]  │
   │   ≤12-word rules for COMMIT / VERIFY_RESULT / DELTA / ACTION /   │
   │   ASK / ANSWER / STATUS / REQUEST_SKIP — verbatim block.         │
   ├──────────────────────────────────────────────────────────────────┤
   │ 6 · CHAIN-OF-THOUGHT PRIMER                             [cache]  │
   │   "Before producing output, briefly consider edge cases…"        │
   ├──────────────────────────────────────────────────────────────────┤
   │ 6.5 · AUTONOMY RULES (commit d2cdfb1)                   [cache]  │
   │   "START WORKING NOW. Make at least one tool call. Never         │
   │   present options to humans for things you can decide."          │
   │   Includes the `ask-peer` / `board-message` escape hatch.        │
   ├──────────────────────────────────────────────────────────────────┤
   │ 6.6 · CONTEXT BEFORE OUTPUT (commit 0b2b4f0)            [cache]  │
   │   Read the spec. Inventory first, batch reads. Architecture =    │
   │   shape; design docs = behaviour — both required before code.    │
   │   Diagnosed in run_f741ea78ba (devs read 3 arch docs, skipped    │
   │   26 design docs, then wrote ghost_ai_system.py without reading  │
   │   ghost_ai.md).                                                  │
   ├──────────────────────────────────────────────────────────────────┤
   │ 6.7 · DISPATCH RULE (conditional)                       [cache]  │
   │   Only injected when (a) the agent has the `assign-task` tool    │
   │   AND (b) `cfg.manages` lists ≥ 2 teammates besides itself.      │
   │   Forces "one assign-task per teammate" — fixes the architect    │
   │   anti-pattern of bundling all downstream work into one task.    │
   ├──────────────────────────────────────────────────────────────────┤
   │ 6.8 · PROTOCOL OUTPUT PRIMER                            [cache]  │
   │   _protocol_output_primer() — concrete shape of the trailing     │
   │   protocol block the runner expects.                             │
   ├──────────────────────────────────────────────────────────────────┤
   │ 7 · POSITIVE FRAMING                                    [cache]  │
   │   "Make this something you are proud of."   ◄─ always last       │
   ╞══════════════════════════════════════════════════════════════════╡
   │ VOLATILE SUFFIX (user message, not system)           [VOLATILE]  │
   │   current task · upstream context block ·                        │
   │   iteration encouragement · pending AGENT_MESSAGE                │
   └──────────────────────────────────────────────────────────────────┘
```

**Legend:**
`[cache]` — the layer is sent with `cache_control: ephemeral` and reused
across iterations. `[VOLATILE]` — the layer changes per call and forces
a (small) cache miss for the suffix.

## Caching boundary

Layers marked `[cache]` are **cacheable** because they are stable across
an iteration and across runs that hire the same persona on the same
project. The Anthropic client opts in via `cache_system=True` (commit
`7b1c54f`). Layers 3.9 / 4 / 4.5b / 4.7 are stable-but-per-call: they
can drift between iterations as the recalled subset / active subtask
changes, so they sit downstream of the cacheable identity block.

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
| 3.9 | `Runner._build_continuation_brief()` | only on resume / continuation |
| 4 | `Persona.recall(task)` | `BaseAgent.effective_system_prompt()` per call |
| 4.5a/b | `Workspace.repo_map` | `_render_repo_map_layer` / `_render_task_relevance_layer` |
| 4.7 | `Workspace.active_subtask` | `_render_active_subtask_layer` (Track 7) |
| 5 | per-class `system_prompt` + `ToolManifest` | builtin or `AgentConfig.system_prompt_overrides` |
| 5.5–6.6 | `BaseAgent.effective_system_prompt()` | hard-coded constants |
| 6.7 | conditional on `cfg.manages` + `assign-task` | hard-coded constant |
| 6.8 | `_protocol_output_primer()` | per-archetype primer |
| 7 | `BaseAgent.effective_system_prompt()` | hard-coded |

## Why ordering matters

- Layer 4 (memory) sits **after** project rules so that "rules trump
  memory on conflict" — a project rule can countermand a remembered
  pattern (CLAUDE.md hard rule 24).
- The autonomy block (6.5, commit `d2cdfb1`) and CONTEXT BEFORE OUTPUT
  block (6.6, commit `0b2b4f0`) are intentionally late so they are
  among the last instructions the model sees before its turn — the
  fixes that stopped agents from acting like chatbots and from skipping
  spec docs.
- The DISPATCH RULE (6.7) is conditional, injected only for agents that
  *can* dispatch — keeping it out of every agent's cache footprint.
- "Make this something you are proud of." (Layer 7) is always the final
  line per CLAUDE.md — never reordered.

## Token accounting

`IterationMetrics` accumulates per-call:

- `repo_map_tokens` — Layer 4.5a structure
- `relevant_files_tokens` — Layer 4.5b + Layer 4.7 (subtask is task-
  relevant by definition, so it rolls into this bucket)
- `context_tokens_total` — sum of the above

Track 3d.5 added `estimated_cost_usd` per iteration so the studio's
`RunAnalytics` view can show a live spend column.
