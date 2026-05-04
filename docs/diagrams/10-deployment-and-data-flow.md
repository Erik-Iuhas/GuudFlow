# 10 — Deployment + Data Flow

How the boxes from diagrams 01–02 actually run, and what flows between
them at runtime.

## 10a · Local dev deployment (default)

```
   ┌──────────────────────────┐
   │ browser  localhost:3000  │
   └────────────┬─────────────┘
                │
                ▼
   ┌──────────────────────────────────────┐
   │ Node 18+ — Vite dev server           │
   │ ──────────────────────────────────── │
   │ npm run dev                          │
   │ HMR + proxy /api → :8000             │
   └────────────┬─────────────────────────┘
                │
                ▼
   ┌──────────────────────────────────────────────────┐
   │ Python 3.11+ — uvicorn process                   │
   │ ──────────────────────────────────────────────── │
   │   ┌──────────────────────────┐                   │
   │   │ api.main                 │                   │
   │   │ FastAPI app + lifespan   │                   │
   │   └─────────┬────────────────┘                   │
   │             │ enqueue                            │
   │             ▼                                    │
   │   ┌──────────────────────────┐                   │
   │   │ InMemoryJobQueue         │                   │
   │   └─────────┬────────────────┘                   │
   │             │                                    │
   │             ▼                                    │
   │   ┌──────────────────────────┐                   │
   │   │ in-process Worker        │                   │
   │   │ JobRunner.start_consumer │                   │
   │   └─────────┬────────────────┘                   │
   │             │                                    │
   │             ▼                                    │
   │   ┌──────────────────────────┐                   │
   │   │ AgentRunner per active   │                   │
   │   │ run                      │                   │
   │   └────┬────────────┬────────┘                   │
   └────────┼────────────┼─────────────────────────────┘
            │            │
            ▼            ▼
   ┌──────────────────┐  ┌────────────────────────┐
   │ host code-exec   │  │ Playwright             │
   │ subprocess       │  │ chromium headless      │
   │ POSIX rlimits +  │  └────────────────────────┘
   │ env scrub        │
   └──────────────────┘

   AgentRunner ── LLMClient ──► Anthropic / OpenAI / Groq / Ollama

   FastAPI + AgentRunner  ◄────►  ./data
                                   personas · projects · runs ·
                                   role_templates · manifestos
```

Key points:

- `GUUDFLOW_MODE=dev` (default) — `code-exec` runs on the host with
  hardening (audit log, env scrub, cwd pin, POSIX rlimits, mode
  enforcement). Logs a warning every invocation per CLAUDE.md.
- Worker runs **in-process** so a single `uvicorn` command boots the
  whole backend. Set `GUUDFLOW_DISABLE_INPROC_WORKER=1` to split.

## 10b · Docker compose (host + per-run sandbox)

```
   ┌──────────┐
   │ browser  │
   └─────┬────┘
         │
         ▼
   ┌────────────────────────────────────────────────────────┐
   │ host container — multi-stage image                     │
   │ ────────────────────────────────────────────────────── │
   │   ┌────────────────────────────┐                       │
   │   │ uvicorn api.main           │                       │
   │   │ + in-proc Worker           │                       │
   │   │ + static studio bundle at /│                       │
   │   └─────────────┬──────────────┘                       │
   │                 │                                       │
   │                 ▼                                       │
   │   ┌────────────────────────────┐                       │
   │   │ /var/run/docker.sock       │  mounted from host    │
   │   │ (sibling-launch socket)    │                       │
   │   └─────────────┬──────────────┘                       │
   └─────────────────┼───────────────────────────────────────┘
                     │ DockerManager.exec_in()
                     ▼
   ┌────────────────────────────────────────────────────────┐
   │ per-run sandbox container (sibling)                    │
   │ ────────────────────────────────────────────────────── │
   │ network_mode: none                                     │
   │ read-only root · 512 MiB · 1 CPU                       │
   │ tmpfs /tmp 64 MiB · 50 PID cap                         │
   │ no-new-privileges                                      │
   └────────────────────────────────┬───────────────────────┘
                                    │
                                    ▼
                            ┌──────────────────┐
                            │ ./data volume    │
                            │ persisted        │
                            └──────────────────┘

   host container ── LLMClient ──► Anthropic / OpenAI / ...
   API keys live in the host environment, never in sandbox containers.
```

- The host container does **not** run sandboxes nested. Sandboxes are
  spawned as **siblings** through the mounted Docker socket (see
  ROADMAP.md k8s notes for the production migration plan).
- `code-exec` is the only tool routed through `DockerManager.exec_in()`.
- API keys live in the host environment, never in sandbox containers.

## 10c · Data flow during a single iteration

```
   ┌───────────┐  spawn   ┌───────────────┐
   │ Scheduler │─────────►│ BaseAgent.run │
   └───────────┘          └───┬───┬───┬───┘
                              │   │   │
                              │   │   │ effective_system_prompt
                              │   │   ▼
                              │   │   ┌────────────────┐
                              │   │   │ LLMClient      │
                              │   │   │ (cache_system) │
                              │   │   └───────┬────────┘
                              │   │           │ tool_calls
                              │   │           ▼
                              │   │   ┌────────────────────┐
                              │   │   │ ToolManifest       │  ──► ToolDescriptor
                              │   │   └─────┬──────────────┘
                              │   │         │
                              │   │         ▼
                              │   │   ┌──────────────┐
                              │   │   │ Tools layer  │
                              │   │   └──┬──┬──┬─────┘
                              │   │      │  │  │
                              │   │      ▼  ▼  ▼
                              │   │  ┌──────┐ ┌──────────┐ ┌────────┐
                              │   │  │ FS   │ │ Git      │ │ Broker │
                              │   │  └──────┘ └──────────┘ └───┬────┘
                              │   │                            │
                              │   │                            ▼
                              │   │                       ┌──────────┐
                              │   │                       │ MCP svr  │
                              │   │                       └──────────┘
                              │   │
                              │   │ get_context(task)
                              │   ▼
                              │  ┌────────────────────┐
                              │  │ ContextManager     │── compact map ──► RepoMap
                              │  └────────┬───────────┘
                              │           │ ranked files
                              │           ▼
                              │       (back to BaseAgent)
                              │
                              │ emit_commit / signal
                              ▼
                          ┌────────────────┐  subscribers   ┌───────────────────┐
                          │ Workspace.board│───────────────►│ API WebSocket bridge
                          └─────┬──────────┘                └───────────────────┘
                                │ mark_seen
                                ▲
                                │
                         (back to BaseAgent)
```

## 10d · On-disk run layout

```
data/
├── personas/{persona_id}/
│   ├── profile.json
│   ├── episodes.jsonl          ← append-only, raw truth
│   ├── patterns.json           ← lifecycle states · confidence
│   ├── antipatterns.json       ← flips from patterns
│   ├── style.json              ← drifted tendencies
│   ├── reputation.json         ← split: production + training
│   └── history.json            ← rotated to history.archive.jsonl @1000
│
├── projects/{project_id}/
│   ├── project.json            ← ProjectConfig
│   ├── persona_rules/          ← project rules per persona (DESIGN §36)
│   │   ├── _shared.md
│   │   └── {persona_id}.md
│   ├── manifesto.md            ← Layer 3.6
│   ├── roadmap.md              ← Layer 3.8 (Phase K)
│   └── inputs/                 ← read-only at runtime
│
├── runs/{run_id}/
│   ├── workspace.json          ← serialised Workspace
│   ├── board.jsonl             ← incremental signal log (Phase G-prime.0)
│   ├── artifacts/{role}/...
│   ├── working/                ← live workspace + .git/
│   ├── inputs/README.md        ← generated per-run by run_initializer
│   ├── screenshots/...
│   └── deliverables.zip
│
├── role_templates/{archetype}/
│   └── desk/*.md               ← persona-desk seed templates (M2)
│
├── manifestos/default.md       ← office-wide rules of engagement
├── models.json                 ← ModelRegistry source
└── training_camp/scenarios/{name}/
    ├── scenario.yaml
    └── rubric.yaml
```
