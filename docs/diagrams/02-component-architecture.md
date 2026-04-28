# 02 — Component Architecture

The layers inside the host process and the legal arrows between them. The
project follows the hard constraints in `CLAUDE.md` — most notably that the
**Scheduler owns concurrency**, the **Workspace is the only shared state**,
and **agents reach the outside world only through tools and the broker**.

```mermaid
flowchart TB
    classDef ui fill:#fff3e0,stroke:#e65100,color:#bf360c
    classDef api fill:#e3f2fd,stroke:#1976d2,color:#0d47a1
    classDef worker fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20
    classDef core fill:#fce4ec,stroke:#c2185b,color:#880e4f
    classDef agents fill:#ede7f6,stroke:#5e35b1,color:#311b92
    classDef tools fill:#f3e5f5,stroke:#6a1b9a,color:#311b92
    classDef llm fill:#e0f7fa,stroke:#00838f,color:#006064
    classDef storage fill:#fffde7,stroke:#f9a825,color:#827717

    subgraph UI["studio/ — React frontend"]
      direction LR
      RunView[RunView · BoardFeed · QuipFeed · GraphView]:::ui
      Roster[Persona Roster · Chat · Rankings]:::ui
      Training[Training Camp · Tournaments]:::ui
      Onboarding[Onboarding · Demo · Project Wizard]:::ui
    end

    subgraph APILayer["api/ — FastAPI surface"]
      direction LR
      Routes[routes/ · projects · runs · meta · personas ·<br/>agents · invoke · models · training · inputs]:::api
      WS[websocket.py<br/>RunRegistry · QuipRegistry]:::api
      Auth[auth.py · require_bearer]:::api
      Storage[storage.py · ProjectStore ·<br/>RunStore · PersonaStore]:::api
    end

    subgraph WorkerLayer["worker/"]
      direction LR
      Queue[queue.py · InMemoryJobQueue]:::worker
      JobRunner[job_runner.py · default_runner_factory]:::worker
      Docker[docker_manager.py]:::worker
    end

    subgraph CoreLayer["core/ — orchestration kernel"]
      direction LR
      Sched[scheduler.py · Scheduler · InterruptToken]:::core
      Run[runner.py · AgentRunner · ProjectConfig]:::core
      WS_obj[workspace.py · Workspace · Artifact ·<br/>signals.py · protocol.py]:::core
      Broker[credentials_broker.py · CredentialsBroker]:::core
      Manifest[tool_manifest.py · ToolManifest]:::core
      Context[context_manager.py · repo_map.py · edit_blocks.py]:::core
      RunIO[run_initializer.py · run_loader.py ·<br/>source_repo.py · meta_proposal.py ·<br/>cost_estimate.py · loop_guard.py · quip_engine.py]:::core
      Train[training/ · scenario · tournament]:::core
    end

    subgraph LLM["core/llm/ — provider plane"]
      direction LR
      Reg[registry.py · ModelRegistry]:::llm
      Client[client.py · LLMClient · MockLLMClient]:::llm
      Anth[anthropic.py]:::llm
      OAI[openai.py]:::llm
      Err[errors.py · taxonomy]:::llm
    end

    subgraph AgentsLayer["agents/ — actor plane"]
      direction LR
      Base[base.py · BaseAgent]:::agents
      Abs[abstract.py · Reviewer ·<br/>EscalationHandler · Specialist]:::agents
      Builtin[builtin/ · architect · code_reviewer ·<br/>tech_lead · it_agent · meta · memory_writer ·<br/>requirements_writer · rubric_scorer ·<br/>test_runner · visual_checker]:::agents
      Persona[persona.py · persona_chat.py ·<br/>persona_desk.py · catalog.py · registry.py]:::agents
    end

    subgraph ToolsLayer["agents/tools/"]
      direction LR
      Tools[fs · git · code · doc · web ·<br/>data · text · math · time ·<br/>board · desk_search · desk_push]:::tools
    end

    Storage_DB[(data/<br/>personas · projects · runs ·<br/>role_templates · manifestos · models.json)]:::storage

    UI -->|HTTP/WS| APILayer
    APILayer -->|enqueue| WorkerLayer
    WorkerLayer -->|build + run| CoreLayer
    APILayer -->|read| Storage
    Storage --> Storage_DB
    APILayer -.->|subscribes| WS_obj

    CoreLayer --> AgentsLayer
    AgentsLayer --> ToolsLayer
    AgentsLayer -->|effective prompt + tool calls| LLM
    ToolsLayer -->|mediated by manifest| Broker
    Broker -->|credentialed calls| LLM

    Run -->|owns + drives| Sched
    Sched -->|invokes runner per spec| Run
    Run -->|posts signals| WS_obj
    AgentsLayer -->|posts signals| WS_obj
```

## Legal arrows

| From | To | Allowed because |
|------|----|-----------------|
| API → Worker | enqueue / status | `api/routes/invoke.py` |
| Worker → Core | builds `AgentRunner` per job | `worker/job_runner.py` |
| Core → Agents | via `agents/registry.py` only | DESIGN §13 rule 4 |
| Agents → Tools | via `BaseAgent.call_tool()` only | DESIGN §31 rule 18 |
| Tools → Broker | for credentialed calls | DESIGN §30 rule 17 |
| Agents → Workspace | append-only signals | DESIGN §13 rule 3 |
| Scheduler → asyncio | sole owner of concurrency | DESIGN §13 rule 2 |

## Forbidden arrows

- Agents importing other agents directly — registry only.
- Anything outside `core/scheduler.py` calling `asyncio.gather` or
  `asyncio.create_task` for agent execution.
- Agents calling LLM provider SDKs directly — `LLMClient` only.
- Tools holding credentials — broker only.
