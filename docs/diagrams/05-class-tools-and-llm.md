# 05 — UML Class Diagram: Tools + LLM Plane

Two parallel hierarchies that hang off `BaseAgent`:

- **Tools** — local, in-process, namespaced (`fs:*`, `git:*`, `code:*`,
  `web:*`, `doc:*`, `desk:*`, `board:*`). Mediated by `ToolManifest` so
  RBAC and MCP entries look the same to the agent.
- **LLM clients** — abstracted behind `LLMClient`, dispatched per agent
  by `ModelRegistry` based on tier + preference.

```
┌─────────────────────────────────────────────────────┐
│ «abstract» BaseTool                                 │
├─────────────────────────────────────────────────────┤
│ + name : str                                        │
│ + description : str                                 │
│ + rbac_scope : str                                  │
│ + spec() → dict                                     │
│ + invoke(args, workspace, context) → str            │
└──────────────────────┬──────────────────────────────┘
                       │
   ┌───────────────────┼─────────────────────────┐
   │                   │                         │
   ▼ fs:*              ▼ git:*                   ▼ code:*
┌──────────────┐   ┌─────────────────┐   ┌──────────────────────┐
│ FsRead       │   │ GitDiff         │   │ CodeExec             │
│ FsList       │   │ GitChangedFiles │   │  «sandbox in prod»   │
│ FsExists     │   │ GitLog          │   │ CodeOutline          │
│ FsReadRange  │   │ GitStatus       │   └──────────────────────┘
│ FsGrep       │   │ GitRead         │
│ FsWrite      │   └─────────────────┘
│ FsCreate     │
│ FsEdit       │   ┌────────────────┐    ┌──────────────────────┐
│  «claiming · │   │ WebSearch      │    │ DocRead · DocWrite   │
│   SEARCH/    │   │ WebFetch       │    └──────────────────────┘
│   REPLACE»   │   └────────────────┘
│ FsDelete     │
│ FsRename     │   ┌────────────────┐    ┌──────────────────────┐
└──────────────┘   │ DeskSearch     │    │ BoardMessage         │
                   │ DeskPush       │    │  «inter-agent        │
                   └────────────────┘    │   AGENT_MESSAGE»     │
                                          └──────────────────────┘

   ┌────────────────────────────────────────┐
   │ TextTool · DataTool · MathTool · TimeTool│  (utility tools)
   └────────────────────────────────────────┘
```

### Manifest layer

```
┌──────────────────────────────────────┐    ┌──────────────────────┐
│ ToolManifest                         │  * │ ToolDescriptor       │
├──────────────────────────────────────┤◄───├──────────────────────┤
│ + role : str                         │    │ + name : str         │
│ + tools : list[ToolDescriptor]       │    │ + rbac_scope : str   │
│ + mcp   : list[MCPToolEntry]         │    │ + description : str  │
├──────────────────────────────────────┤    │ + schema : dict      │
│ + has(name) → bool                   │    └──────────┬───────────┘
│ + descriptor(name) → ToolDescriptor  │               │ refers to
│ + render_for_prompt() → str          │               ▼
└────────┬─────────────────────────────┘    ┌──────────────────────┐
         │ 1                                │ BaseTool subclass    │
         │                                  └──────────────────────┘
         │ *
         ▼
┌──────────────────────┐
│ MCPToolEntry         │
│ + server : str       │
│ + tool   : str       │
│ + rbac_scope : str   │
└──────────────────────┘
```

### LLM plane

```
┌────────────────────────────────────────────────────────┐
│ «protocol» LLMClient                                   │
├────────────────────────────────────────────────────────┤
│ + complete(messages, tools, system, cache_system)      │
│   → LLMResponse                                        │
│ + stream(messages, tools)                              │
└──────────────┬─────────────────────────────────────────┘
               │ implemented by
       ┌───────┼────────────────────┐
       ▼       ▼                    ▼
┌─────────────────────┐ ┌────────────────────┐ ┌─────────────────────┐
│ AnthropicLLMClient  │ │ OpenAILLMClient    │ │ MockLLMClient       │
│ + cache_system: bool│ │ + complete(...)    │ │ + scripted_responses│
│ + complete(...)     │ └────────────────────┘ └─────────────────────┘
└─────────────────────┘

┌──────────────────────────────────────┐    ┌────────────────────┐
│ LLMResponse                          │ * │ ToolCall           │
├──────────────────────────────────────┤◄──├────────────────────┤
│ + text : str                         │  1 │ + id : str         │
│ + tool_calls : list[ToolCall]        │    │ + name : str       │
│ + stop_reason : str                  │    │ + input : dict     │
│ + usage : dict                       │    └────────────────────┘
│ + cache_read_tokens : int            │
│ + cache_creation_tokens : int        │
└──────────────────────────────────────┘

┌──────────────────────────────────────┐
│ LLMMessage                           │
│ + role : user/assistant/tool         │
│ + content : list                     │
└──────────────────────────────────────┘

   LLMClient  ─── raises ──►  LLMError
                                    │
                ┌───────────────────┼───────────────────┐
                ▼                   ▼                   ▼
       RateLimitError        AuthError         ContextLengthError
       InvalidRequestError   ServerError       NetworkError
                                                TimeoutError

┌──────────────────────────────────────────────────────┐
│ ModelRegistry                                        │
├──────────────────────────────────────────────────────┤
│ + models    : dict[str, ModelInfo]                   │
│ + providers : dict[str, ProviderInfo]                │
├──────────────────────────────────────────────────────┤
│ + resolve(tier, preference) → ModelInfo              │
│ + client_for(model) → LLMClient                      │
│ + cost_per_1k(model) → tuple[float, float]           │
└────────┬───────────────────────────┬─────────────────┘
         │ 1                       1 │
         │                           │ caches per provider
         │ * ModelInfo               ▼
         ▼                       LLMClient
   ┌──────────────────────┐
   │ ModelInfo            │       ┌──────────────────────┐
   │ + id : str           │       │ ProviderInfo         │
   │ + tier : heavy/solid/│       │ + name : str         │
   │   fast               │       │ + base_url : str     │
   │ + provider : str     │       │ + api_key_env : str  │
   │ + cost_per_1k_in/out │       │ + supports_caching   │
   │ + deprecated : bool  │       └──────────────────────┘
   └──────────────────────┘
```

## How an agent reaches a tool

```
   BaseAgent          ToolManifest        BaseTool subclass        Broker
       │                   │                      │                    │
       │ descriptor("fs-write")                   │                    │
       ├──────────────────►│                      │                    │
       │                   │                      │                    │
       │   ┌── if local tool ────────────────────────────┐             │
       │   │ ToolDescriptor                              │             │
       │   ◄─────────────────                            │             │
       │   │                  │ invoke(args, ws, ctx)    │             │
       │   ├─────────────────────────────────────────────►             │
       │   │                  │                          │ result      │
       │   ◄─────────────────────────────────────────────┤             │
       │   └─────────────────────────────────────────────┘             │
       │                                                               │
       │   ┌── if MCP tool ────────────────────────────────────────┐   │
       │ MCPToolEntry                                              │   │
       │   ◄────────────────                                       │   │
       │   │                  invoke_mcp(server, tool, args)       │   │
       │   ├──────────────────────────────────────────────────────►│   │
       │   │                  result (redacted)                    │   │
       │   ◄──────────────────────────────────────────────────────┤   │
       │   └──────────────────────────────────────────────────────┘
```

## Hyphenated names everywhere

Tool names are hyphen-separated (`fs-write`, `git-diff`, `code-exec`,
`desk-search`). They map 1:1 to RBAC scope strings — see commit
`0709f1b refactor: hyphen-separated tool names + RBAC scopes`.
