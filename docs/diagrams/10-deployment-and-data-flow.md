# 10 — Deployment + Data Flow

How the boxes from diagrams 01–02 actually run, and what flows between
them at runtime.

## 10a · Local dev deployment (default)

```mermaid
flowchart LR
    classDef proc fill:#e3f2fd,stroke:#1976d2,color:#0d47a1
    classDef ext fill:#eee8d5,stroke:#93a1a1,color:#073642
    classDef store fill:#fffde7,stroke:#f9a825,color:#827717

    Browser([browser<br/>localhost:3000]):::ext

    subgraph node["Node 18+ — Vite dev server"]
      Vite[npm run dev<br/>HMR + proxy /api → :8000]:::proc
    end

    subgraph py["Python 3.11+ — uvicorn process"]
      direction TB
      FastAPI[api.main<br/>FastAPI app + lifespan]:::proc
      InProc[in-process Worker<br/>JobRunner.start_consumer]:::proc
      Queue[InMemoryJobQueue]:::proc
      RunnerProc[AgentRunner per active run]:::proc
    end

    HostExec[host code-exec subprocess<br/>POSIX rlimits + env scrub]:::proc
    Playwright[Playwright<br/>chromium headless]:::proc

    DataDir[(./data<br/>personas · projects · runs ·<br/>role_templates · manifestos)]:::store

    Browser <--> Vite
    Vite <--> FastAPI
    FastAPI --> Queue
    Queue --> InProc
    InProc --> RunnerProc
    RunnerProc --> HostExec
    RunnerProc --> Playwright

    FastAPI <--> DataDir
    RunnerProc <--> DataDir

    Anth[Anthropic / OpenAI / Groq / Ollama]:::ext
    RunnerProc -. LLMClient .-> Anth
```

Key points:

- `GUUDFLOW_MODE=dev` (default) — `code-exec` runs on the host with
  hardening (audit log, env scrub, cwd pin, POSIX rlimits, mode
  enforcement). Logs a warning every invocation per CLAUDE.md.
- Worker runs **in-process** so a single `uvicorn` command boots the
  whole backend. Set `GUUDFLOW_DISABLE_INPROC_WORKER=1` to split.

## 10b · Docker compose (host + per-run sandbox)

```mermaid
flowchart LR
    classDef proc fill:#e3f2fd,stroke:#1976d2,color:#0d47a1
    classDef ext fill:#eee8d5,stroke:#93a1a1,color:#073642
    classDef store fill:#fffde7,stroke:#f9a825,color:#827717

    Browser([browser]):::ext

    subgraph hostc["host container — multi-stage image"]
      direction TB
      App[uvicorn api.main<br/>+ in-proc Worker<br/>+ static studio bundle at /]:::proc
      Sock["/var/run/docker.sock<br/>mounted from host"]:::proc
    end

    subgraph sandbox["per-run sandbox container (sibling)"]
      direction TB
      Sbox[network_mode: none<br/>read-only root · 512 MiB · 1 CPU<br/>tmpfs /tmp 64 MiB · 50 PID cap<br/>no-new-privileges]:::proc
    end

    Vol[(./data volume<br/>persisted)]:::store

    Browser <--> App
    App --> Sock
    Sock --> Sbox
    Sbox --> Vol
    App --> Vol

    Anth[Anthropic / OpenAI / ...]:::ext
    App -. LLMClient .-> Anth
```

- The host container does **not** run sandboxes nested. Sandboxes are
  spawned as **siblings** through the mounted Docker socket (see
  ROADMAP.md k8s notes for the production migration plan).
- `code-exec` is the only tool routed through `DockerManager.exec_in()`.
- API keys live in the host environment, never in sandbox containers.

## 10c · Data flow during a single iteration

```mermaid
flowchart LR
    classDef agent fill:#ede7f6,stroke:#5e35b1,color:#311b92
    classDef core fill:#fce4ec,stroke:#c2185b,color:#880e4f
    classDef tool fill:#f3e5f5,stroke:#6a1b9a,color:#311b92
    classDef llm fill:#e0f7fa,stroke:#00838f,color:#006064

    Sched[Scheduler]:::core
    Agent[BaseAgent.run]:::agent
    Manifest[ToolManifest]:::core
    Ctx[ContextManager]:::core
    Repo[RepoMap]:::core
    LLM[LLMClient]:::llm
    FS[FileWorkspace]:::core
    Git[GitWorkspace]:::core
    Tools[Tools layer]:::tool
    Board[Workspace.board]:::core
    Broker[CredentialsBroker]:::core
    MCP[MCP server]:::tool

    Sched -- spawn --> Agent
    Agent -- get_context(task) --> Ctx
    Ctx -- ranked files --> Agent
    Ctx -- compact map --> Repo
    Agent -- effective_system_prompt --> LLM
    LLM -- tool_calls --> Agent
    Agent -- call_tool(name) --> Manifest
    Manifest -- ToolDescriptor --> Tools
    Tools <--> FS
    Tools <--> Git
    Tools -- mcp:* --> Broker
    Broker -- credentialed --> MCP
    Agent -- emit_commit / signal --> Board
    Board -- mark_seen --> Agent
    Board -- subscribers --> WS_API[API WebSocket bridge]
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
