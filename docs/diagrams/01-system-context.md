# 01 — System Context

A top-down view of GuudFlow as a deployed system. Boxes outside the dashed
boundary are actors or third-party services; everything inside is code in this
repository.

```
                       ┌──────────────────────────┐
                       │  Developer / PM (browser)│  actor
                       └──────────────┬───────────┘
                                      │ HTTPS
                                      ▼
   ┌──────────────┐        ┌──────────────────────┐
   │  CI / script │ ─────► │     studio/         │ ◄─── /ws messages
   │  (POST       │ bearer │  Vite + React UI    │
   │  /api/invoke)│        └──────────┬───────────┘
   └──────┬───────┘                   │ /api · /ws
          │                           ▼
          │                ┌────────────────────────────────────────┐
          ▼                │  GuudFlow host process                 │
   ┌────────────────────┐  │  ┌────────────┐  ┌──────────────────┐  │
   │  HTTPS bearer      │  │  │  api/      │  │  worker/         │  │
   │  POST /api/invoke  │─►│  │  FastAPI   │◄─┤  JobRunner       │  │
   └────────────────────┘  │  │  + WS      │  │  + queue         │  │
                           │  └────┬───────┘  └────────┬─────────┘  │
                           │       │                   │            │
                           │       ▼                   ▼            │
                           │  ┌──────────────────────────────────┐  │
                           │  │  core/                           │  │
                           │  │  scheduler · runner · workspace  │  │
                           │  │  protocol · llm · context        │  │
                           │  └──────────────┬───────────────────┘  │
                           │                 ▼                      │
                           │  ┌──────────────────────────────────┐  │
                           │  │  agents/                         │  │
                           │  │  BaseAgent · personas ·          │  │
                           │  │  builtin · tools                 │  │
                           │  └─┬───────┬───────┬───────┬────────┘  │
                           └────┼───────┼───────┼───────┼───────────┘
                                │       │       │       │
                  LLMClient─────┘   LLMClient   broker  web tools
                                │       │       │       │
                                ▼       ▼       ▼       ▼
                        ┌─────────────────────────────────────┐
                        │  External services                  │
                        │  ─────────────────────────────────  │
                        │  Anthropic (Opus / Sonnet / Haiku)  │
                        │  OpenAI / Groq / DeepSeek / Ollama  │
                        │  Connected MCP servers (per-project)│
                        │  Web search / Web fetch             │
                        └─────────────────────────────────────┘

   Worker  ── production mode ──►  per-run sandbox container
                                    (code-exec only)

   core/, api/, worker/  ◄──────►  data/   personas · projects ·
                                          runs · role_templates ·
                                          manifestos
```

## Notes

- The **API** and **Worker** can run as one process (default — in-process
  worker, started by `api.main.lifespan`) or as two (set
  `GUUDFLOW_DISABLE_INPROC_WORKER=1`).
- `code-exec` is the only tool that crosses into the sandbox; every other
  tool runs in-process so file I/O can stay path-safe inside `FileWorkspace`.
- LLM calls always go through the runner-side `LLMClient` abstraction.
  Containers never see API keys (DESIGN §28).
- MCP and external HTTP go through the `CredentialsBroker`; agents never
  hold secrets.
