# 01 — System Context

A top-down view of GuudFlow as a deployed system. Boxes outside the dashed
boundary are actors or third-party services; everything inside is code in this
repository.

```mermaid
flowchart LR
    classDef actor fill:#fdf6e3,stroke:#586e75,stroke-width:1px,color:#073642
    classDef external fill:#eee8d5,stroke:#93a1a1,stroke-dasharray:4 2,color:#073642
    classDef internal fill:#e3f2fd,stroke:#1976d2,color:#0d47a1
    classDef store fill:#f3e5f5,stroke:#6a1b9a,color:#311b92

    User([Developer / PM<br/>browser]):::actor
    CI([CI/CD pipeline<br/>or external script]):::actor

    subgraph anth[" "]
      direction TB
      Anthropic[Anthropic API<br/>Claude Opus / Sonnet / Haiku]:::external
      OpenAI[OpenAI / Groq /<br/>DeepSeek / Ollama]:::external
      MCP[Connected MCP servers<br/>per-project]:::external
      Web[Web search /<br/>web fetch]:::external
    end

    subgraph host["GuudFlow host process"]
      direction TB
      Studio[studio/<br/>Vite + React UI]:::internal
      API[api/<br/>FastAPI app + WS]:::internal
      Worker[worker/<br/>JobRunner + queue]:::internal
      Core[core/<br/>scheduler · runner · workspace ·<br/>protocol · llm · context]:::internal
      Agents[agents/<br/>BaseAgent · personas ·<br/>builtin · tools]:::internal
    end

    Sandbox[per-run sandbox container<br/>code-exec only]:::external
    Data[(data/<br/>personas · projects · runs ·<br/>role_templates · manifestos)]:::store

    User -->|HTTPS| Studio
    Studio -->|/api · /ws| API
    CI -->|POST /api/invoke<br/>bearer token| API

    API --> Worker
    API --> Core
    Worker --> Core
    Core --> Agents
    Agents -->|LLMClient| Anthropic
    Agents -->|LLMClient| OpenAI
    Agents -->|broker| MCP
    Agents -->|web tools| Web
    Worker -->|production mode| Sandbox

    Core --> Data
    API --> Data
    Worker --> Data
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
