# 04 — UML Class Diagram: `agents/`

The actor plane. `BaseAgent` is the universal contract. `Reviewer`,
`EscalationHandler`, and `Specialist` are the three abstract archetypes;
every builtin agent is a concrete subclass of one of them.

```
┌──────────────────────────────────────────────────────────────────┐
│ BaseAgent                                                        │
├──────────────────────────────────────────────────────────────────┤
│ + role : str                                                     │
│ + description : str                                              │
│ + depends_on : list[str]                                         │
│ + system_prompt : str                                            │
│ + declared_local_tools : list[str]                               │
│ + declared_mcp_servers : list[str]                               │
│ + persona : Persona                          ◄─ optional         │
│ - _manifest : ToolManifest                                       │
│ - _broker : CredentialsBroker                                    │
│ - _context_manager : ContextManager                              │
│ - _llm : LLMClient                                               │
│ - _scheduler : Scheduler                                         │
│ - _manifesto : str                                               │
│ - _project_rules : str                                           │
│ - _project_context : str                                         │
│ - _teammate_brief : str                                          │
│ - _desk_index : DeskIndex                                        │
├──────────────────────────────────────────────────────────────────┤
│ + run(workspace) → str                              «override»   │
│ + signal(type, summary, detail)                                  │
│ + ask_human(question, context)                                   │
│ + check_user_board(workspace) → list[Signal]                     │
│ + request_visual_feedback(path, question)                        │
│ + request_package(name, reason)                                  │
│ + emit_commit / emit_verify / emit_delta / emit_action           │
│ + emit_ask / emit_answer                                         │
│ + read_protocol(types) → list[ProtocolMessage]                   │
│ + claim_files(paths) · release_files(paths)                      │
│ + call_tool(name, args)                  ◄─ ONE entrypoint       │
│ + request_credential(scope) · invoke_mcp(server, tool, args)     │
│ + get_context(task, max_tokens) → AgentContext                   │
│ + search_codebase(query) → list[SearchResult]                    │
│ + recall(task) → MemoryRecall                                    │
│ + llm(messages, tools) → LLMResponse                             │
│ + effective_system_prompt(brief) → str    ◄─ 12-layer composer   │
└──────────────┬─────────────┬───────────────┬─────────────────────┘
               │             │               │
       ┌───────┴───────┐  ┌──┴──────────┐  ┌─┴────────────┐
       │ «abstract»    │  │ «abstract»  │  │ «abstract»   │
       │ Reviewer      │  │ Escalation- │  │ Specialist   │
       │               │  │ Handler     │  │              │
       │ +emit_review  │  │ +on_escal.. │  │              │
       │ +should_escal.│  │             │  │              │
       └───┬───────────┘  └──────┬──────┘  └─┬────────────┘
           │                     │           │
   ┌───────┴────────┐        ┌───┴────┐    ┌─┴───────────────────────┐
   │ Architect      │        │ TechLead│    │ ITAgent                │
   │ CodeReviewer   │        └────────┘    │ MetaAgent              │
   └────────────────┘                      │ MemoryWriter           │
                                           │ RequirementsWriter     │
                                           │ RubricScorer           │
                                           │ TestRunner             │
                                           │ VisualChecker          │
                                           └────────────────────────┘

┌─────────────────────────────────────┐
│ Registry  «singleton»               │   produces    ┌────────────┐
│ agents/registry.py                  │ ─────────────►│ BaseAgent  │
├─────────────────────────────────────┤               └────────────┘
│ + register(name, cls)               │
│ + get(name) → BaseAgent             │
│ + has(name) → bool                  │
└─────────────────────────────────────┘
```

### Persona — the persistent specialist

```
┌──────────────────────────────────────────────────┐
│ Persona                                          │
├──────────────────────────────────────────────────┤
│ + profile : PersonaProfile                       │
│ + episodes : list[Episode]                       │
│ + patterns : list[Pattern]                       │
│ + antipatterns : list[AntiPattern]               │
│ + style : WorkingStyle                           │
│ + reputation : Reputation                        │
├──────────────────────────────────────────────────┤
│ + has_memory() → bool                            │
│ + recall(task, limit) → MemoryRecall             │
│ + identity_layer() → str                         │
│ + character_layer() → str                        │
│ + stake_layer() → str                            │
│ + log_history(entry)                             │
└────┬────────┬─────┬────────┬────────┬────────────┘
     │ 1      │ *   │ *      │ *      │ 1   produces
     ▼        ▼     ▼        ▼        ▼     ───────►
┌────────────┐  ┌────────┐  ┌────────────┐  ┌─────────────┐  ┌──────────────┐
│ Persona-   │  │Episode │  │ Pattern    │  │ AntiPattern │  │ MemoryRecall │
│ Profile    │  │ +id    │  │ +id        │  │ +id         │  │ + patterns   │
│ +id        │  │ +run_id│  │ +descr     │  │ +avoid      │  │ + antipats   │
│ +name      │  │ +outc. │  │ +applies_  │  │ +confidence │  │ + episodes   │
│ +archetype │  │ +files │  │  when      │  │ +source_eps │  │ + render_for │
│ +model_pref│  │ +pat_  │  │ +confidence│  └─────────────┘  │   _prompt()  │
│ +goal      │  │  appl. │  │ +source_eps│                   └──────────────┘
│ +created_at│  │ +score │  │ +state :   │
└────────────┘  └────────┘  │  emerging/ │
                            │  active/   │
                            │  declining/│
                            │  archived/ │
                            │  superseded│
                            └────────────┘

┌──────────────────────┐    ┌──────────────────────────────────┐
│ WorkingStyle         │    │ Reputation                       │
│ + tendencies :       │    │ + production : ReputationBucket  │
│   dict[str, float]   │    │ + training   : TrainingReputation│
└──────────────────────┘    └──────────────────────────────────┘

   BaseAgent ──► Persona  (optional, bound by runner)
```

### DeskIndex — role-template knowledge handoff (M2)

```
┌──────────────────────────────────────────┐    ┌──────────────────────┐
│ DeskIndex                                │  * │ DeskFile             │
├──────────────────────────────────────────┤◄───├──────────────────────┤
│ + files : list[DeskFile]                 │  1 │ + path : Path        │
│ + search(tags, query) → list[DeskFile]   │    │ + tags : list[str]   │
│ + render_inject_block(role, query) → str │    │ + front_matter : dict│
└──────────────────────────────────────────┘    │ + body : str         │
                                                └──────────────────────┘

   BaseAgent ──► DeskIndex   (injected by runner)
```

## Three abstract archetypes

| Archetype | Purpose | Builtins |
|-----------|---------|----------|
| `Reviewer` | reads work + emits APPROVED / REJECTED | `Architect`, `CodeReviewer` |
| `EscalationHandler` | fires only after a rejection threshold | `TechLead` |
| `Specialist` | does the work | everyone else |

## Persona = persistent specialist

A `Persona` exists across runs in `data/personas/{id}/` (seven files —
`profile.json`, `episodes.jsonl`, `patterns.json`, `antipatterns.json`,
`style.json`, `reputation.json`, `history.json`). When an agent slot has
`persona_id` in its `AgentConfig`, the runner loads and binds a `Persona`
to the `BaseAgent` instance for that run; `effective_system_prompt()`
then injects the persona's identity / character / stake layers and the
contextually recalled memory.

`agents/persona_chat.py` is a thin helper that powers
`POST /api/personas/{id}/chat` — one-off consultation that hydrates the
persona without spinning up a run. It is not modelled in the class
diagram because it has no runtime relationship with the scheduler or
the workspace.

## Agents never read raw memory

Agents call `recall(task)` and receive a `MemoryRecall`. Confidence and
relevance scoring (`compute_confidence`, `_tag_overlap`,
`_recency_weight`) are encapsulated inside `Persona`.
