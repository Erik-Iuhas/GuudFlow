# 02 — Component Architecture

The layers inside the host process and the legal arrows between them. The
project follows the hard constraints in `CLAUDE.md` — most notably that the
**Scheduler owns concurrency**, the **Workspace is the only shared state**,
and **agents reach the outside world only through tools and the broker**.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  studio/  — React frontend                                              │
│  ─────────────────────────────────────────────────────────────────────  │
│  views   Dashboard · Projects · Propose · Run · Roster · Training ·     │
│          Tournament · Studio · Settings · Inputs                        │
│  components                                                             │
│    run     BoardFeed · QuipFeed · PipelineGraph · GitGraph ·            │
│            KanbanBoard · RunAnalytics · CommentBox · ScreenshotPane ·   │
│            ApprovalCard · ResumeButton · CostStrip · TagsPanel ·        │
│            ActiveTaskChip · WorkerCognitionView (Track 11)              │
│    files   FileTreeTab · FilePreviewPanel · DeskFilesSection ·          │
│            UploadDropzone · GitDiffSideOver                             │
│    config  WorkerCard (4 modes) · WorkerSettingsPanel ·                 │
│            ToolsMultiSelect · SettingsDrawer · SourceRepoField ·        │
│            FolderPicker · PriorRunPicker                                │
│    roster  PersonaSlideOver · PersonaChatBox · RankingsPanel ·          │
│            ScenarioCard · AgentStateCard · RoadmapTab · Toaster         │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │ HTTP / WS
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  api/  — FastAPI surface                                                │
│  ─────────────────────────────────────────────────────────────────────  │
│  routes/  projects · runs · meta · personas · agents · invoke ·         │
│           models · training · inputs · settings · tools · dashboard ·   │
│           fs                                                            │
│  websocket.py    RunRegistry · QuipRegistry                             │
│  auth.py         require_bearer                                         │
│  storage.py      ProjectStore · RunStore · PersonaStore                 │
└──────┬─────────────────────────┬─────────────────────────────────────────┘
       │ enqueue                 │ subscribes
       ▼                         │
┌────────────────────────────┐   │
│  worker/                   │   │
│  ────────────────────────  │   │
│  queue.py     InMemory…    │   │
│  job_runner.py             │   │
│  docker_manager.py         │   │
└────────────┬───────────────┘   │
             │ build + run        │
             ▼                    │
┌──────────────────────────────────────────────────────────────────────────┐
│  core/  — orchestration kernel                                          │
│  ─────────────────────────────────────────────────────────────────────  │
│  scheduler.py     Scheduler · InterruptToken                            │
│  runner.py        AgentRunner · ProjectConfig · AgentConfig             │
│  workspace.py     Workspace · Artifact          ◄────┐ posts signals    │
│  signals.py / protocol.py                            │                  │
│  credentials_broker.py    CredentialsBroker          │                  │
│  tool_manifest.py         ToolManifest               │                  │
│  context_manager.py · repo_map.py · repo_walk.py ·   │                  │
│  edit_blocks.py · secret_scan.py                     │                  │
│  run_initializer · run_loader · source_repo ·        │                  │
│  meta_proposal · cost_estimate · loop_guard ·        │                  │
│  quip_engine · task_summarizer · story_tags ·        │                  │
│  archetype_doc · file_workspace · git_workspace      │                  │
│  storage · flow_dir · user_settings                  │                  │
│  training/    scenario · tournament                  │                  │
└────────────┬─────────────────────────────────────────┘                  │
             │                                                            │
             ▼                                                            │
┌──────────────────────────────────────────────────────────────────────────┐
│  agents/  — actor plane                                                 │
│  ─────────────────────────────────────────────────────────────────────  │
│  base.py        BaseAgent                                               │
│  abstract.py    Reviewer · EscalationHandler · Specialist               │
│  builtin/       architect · code_reviewer · tech_lead · it_agent ·      │
│                 meta · memory_writer · requirements_writer ·            │
│                 rubric_scorer · test_runner · visual_checker            │
│  persona.py · persona_chat.py · persona_desk.py · catalog · registry    │
└────────┬───────────────────────────────────────────┬─────────────────────┘
         │                                            │ effective prompt
         ▼                                            ▼ + tool calls
┌────────────────────────────────────┐  ┌────────────────────────────────┐
│  agents/tools/                     │  │  core/llm/  — provider plane   │
│  ────────────────────────────────  │  │  ────────────────────────────  │
│  fs (+ fs_batch · output_compress) │  │  registry.py    ModelRegistry  │
│  git · code (+ code_batch) · doc   │  │  client.py      LLMClient ·    │
│  web · data · text · math · time   │  │                 MockLLMClient  │
│  board · ask_peer (M1 wrapper)     │  │  anthropic.py    AnthropicClient│
│  desk_search · desk_push           │  │  openai.py       OpenAIClient   │
│  decompose_task · recall_subtask   │  │   (covers Groq · DeepSeek ·    │
│  story · llm_dispatch              │  │    Mistral · Ollama · LM-Studio)│
│   (sub-LLM: summarise-subtask ·    │  │  errors.py       taxonomy       │
│    protocol-cleanup ─ Track 9)     │  │                                │
└────────┬───────────────────────────┘  └─────────▲──────────────────────┘
         │ mediated by manifest                    │
         ▼                                          │
┌────────────────────────────────────┐              │
│  CredentialsBroker                 │──────────────┘ credentialed calls
│  RBAC default-deny · MCP transport │
└────────────────────────────────────┘
                                  ▲
                                  │ posts signals
                                  │
                  ┌───────────────┴────────────────┐
                  │  AgentRunner owns + drives     │
                  │  Scheduler;                    │
                  │  Scheduler invokes the runner  │
                  │  per spec.                     │
                  └────────────────────────────────┘

         All persistent state lives under data/
         (personas · projects · runs · role_templates ·
          manifestos · models.json)
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
