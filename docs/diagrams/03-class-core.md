# 03 — UML Class Diagram: `core/`

The orchestration kernel. Everything an agent does flows through these
classes. Field lists are abridged to the public surface — see the source
modules for the full dataclasses.

```
┌────────────────────────────────────────────────────────┐
│ Workspace                                              │
├────────────────────────────────────────────────────────┤
│ + run_id : str                                         │
│ + goal : str                                           │
│ + phase : str                                          │
│ + max_iterations : int                                 │
│ + board : list[Signal]              ◄─ append-only     │
│ + user_board : list[Signal]                            │
│ + artifacts : dict[str, Artifact]                      │
│ + files : FileWorkspace                                │
│ + agent_states : dict[str, AgentState]                 │
│ + hired_personas : dict[str, str]                      │
│ + pending_human : HumanRequest                         │
│ + pending_screenshots : list[PendingScreenshot]        │
│ + pending_approvals : list[ApprovalRequest]            │
│ + git : GitState                                       │
│ + stories : dict[str, StoryState]                      │
│ + active_story : dict[str, str]                        │
│ + deadline_at : float                                  │
│ + iteration : int                                      │
│ + completed_agents : list[str]                         │
│ + repo_map : RepoMap                                   │
│ + tasks : dict[str, Task]                              │
│ + task_queues : dict[str, list[TaskRef]]               │
├────────────────────────────────────────────────────────┤
│ + post_signal(signal)                                  │
│ + set_artifact(role, artifact)                         │
│ + mark_seen(signal_id, agent)                          │
│ + subscribe(callback)                                  │
│ + add_task(...) · mutate_task(...)                     │
└──────────┬─────────────────────────────────────────────┘
           │  1
           │
           │  *
           ▼
┌──────────────────────────────────────┐    ┌────────────────────────┐
│ Signal  «append-only»                │    │ Artifact               │
├──────────────────────────────────────┤    ├────────────────────────┤
│ + id : UUID                          │    │ + agent_role : str     │
│ + type : SignalType                  │───▶│ + type : str           │
│ + from_agent : str                   │    │ + content : str|dict   │
│ + from_user : bool                   │    │ + iteration : int      │
│ + priority : Priority                │    │ + timestamp : datetime │
│ + summary : str                      │    └────────────────────────┘
│ + detail : str                       │
│ + iteration : int                    │
│ + seen_by : list[str]                │
│ + timestamp : datetime               │
└──────────────────────────────────────┘
              │
              ▼ when type==PROTOCOL
┌──────────────────────────────────────────────────┐
│ ProtocolMessage                                  │
│ « COMMIT · VERIFY · DELTA · ACTION ·             │
│   ASK · ANSWER · STATUS · SKIP · STORY ·         │
│   ASSIGN_TASK · TASK_STARTED/COMPLETED/...»      │
├──────────────────────────────────────────────────┤
│ + kind : str                                     │
│ + from_agent : str                               │
│ + commit_hash : str                              │
│ + files : list[FileChange]                       │
│ + checkpoints : list[str]                        │
│ + results : list[VerifyResult]                   │
│ + ask_id : str                                   │
│ + target_role : str                              │
└──────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ SignalType  «enum»                                  │
├─────────────────────────────────────────────────────┤
│   STARTED · THINKING · COMPLETE · FAILED · BLOCKED  │
│   APPROVED · REJECTED · ESCALATION                  │
│   HUMAN_NEEDED · HUMAN_RESPONDED                    │
│   APPROVAL_NEEDED · _GRANTED · _DENIED              │
│   USER_COMMENT · USER_VISUAL_FEEDBACK               │
│   GIT_COMMIT · GIT_MERGE · GIT_CONFLICT             │
│   PACKAGE_REQUEST · _APPROVED · _REJECTED           │
│   CREDENTIAL_REQUEST · _GRANTED · _DENIED           │
│   FILE_CLAIM · FILE_RELEASE                         │
│   MANIFEST_INVALIDATED                              │
│   TASK_ASSIGNED · TASK_STARTED · TASK_COMPLETED     │
│   TASK_BLOCKED · TASK_CANCELLED · TASK_OUT_OF_SCOPE │
│   RUBRIC_SCORE · TOURNAMENT_RESULT                  │
│   PROTOCOL · PROTOCOL_PARSE_FAIL · AGENT_MESSAGE    │
│   PEER_TIMEOUT · SKIP_TIMEOUT · PR_READY            │
└─────────────────────────────────────────────────────┘
```

### Scheduler + AgentRunner

```
┌──────────────────────────────────────────┐
│ Scheduler                                │
├──────────────────────────────────────────┤
│ + agents : dict[str, AgentSpec]          │
│ + workspace : Workspace                  │
│ + scope : str                            │
│ + max_iterations : int                   │
├──────────────────────────────────────────┤
│ + run() → SchedulerResult                │
│ + interrupt(role, kind, payload) → Token │
│ + resume(token, value)                   │
│ - _spawn(spec)                           │
│ - _tasks : dict[str, asyncio.Task]       │
└─────┬───────────────┬────────────────────┘
      │ owns *        │ produces
      ▼               ▼
┌──────────────┐  ┌────────────────────────┐
│ AgentSpec    │  │ InterruptToken         │
│ +role        │  │ + agent_role : str     │
│ +runner      │  │ + kind : human/peer/   │
│ +depends_on  │  │          skip          │
└──────────────┘  │ + id : str             │
                  └────────────────────────┘

┌────────────────────────────────────────┐
│ SchedulerStatus  «enum»                │
│ COMPLETE · MAX_ITERATIONS · STUCK ·    │
│ DEADLINE_EXCEEDED                      │
└────────────────────────────────────────┘

┌──────────────────────────────────────────────────┐
│ AgentRunner   (core/runner.py)                   │
├──────────────────────────────────────────────────┤
│ + cfg : ProjectConfig                            │
│ + workspace : Workspace                          │
│ + scheduler : Scheduler           ◄─ owns        │
│ + broker : CredentialsBroker                     │
│ + manifest_builder : ToolManifestBuilder         │
│ + context_manager : ContextManager               │
│ + quip_engine : QuipEngine                       │
├──────────────────────────────────────────────────┤
│ + llm_for_tier(tier) → LLMClient                 │
│ + run() → RunResult                              │
│ + on_signal(signal)                              │
│ + handle_screenshot(req)                         │
│ + handle_escalation()                            │
│ + finalize_run()                                 │
└─────┬────────────────────────────────────────────┘
      │ configured by
      ▼
┌──────────────────────────────────────────┐    ┌─────────────────────────┐
│ ProjectConfig                            │    │ AgentConfig             │
├──────────────────────────────────────────┤  * ├─────────────────────────┤
│ + project_id : str                       │◄───│ + role : str            │
│ + run_id : str                           │  1 │ + archetype : str       │
│ + goal : str                             │    │ + persona_id : str      │
│ + agents : list[AgentConfig]             │    │ + depends_on : list     │
│ + max_iterations : int                   │    │ + model_tier : tier     │
│ + deadline_seconds : float               │    │ + model_preference : str│
│ + run_profile : BUDGET/BALANCED/QUALITY  │    │ + tools : list[str]     │
│ + source_repo : SourceRepoSpec           │    │ + mcp_servers : list    │
│ + inputs : list[Path]                    │    │ + rbac_scopes : list    │
└──────────────────────────────────────────┘    │ + system_prompt_overrides│
                                                └─────────────────────────┘
```

### Broker, manifest, context, file/git, helpers

```
┌──────────────────────────────────────┐  ┌───────────────────────────────┐
│ CredentialsBroker                    │  │ ToolManifest                  │
├──────────────────────────────────────┤  ├───────────────────────────────┤
│ + rbac : dict[str, RoleRBAC]         │  │ + role : str                  │
│ + request_credential(role, scope)    │  │ + tools : list[ToolDescriptor]│
│ + invoke_mcp(role, server, tool, …)  │  │ + mcp : list[MCPToolEntry]    │
│ + redact(text)                       │  │ + has(name) → bool            │
│ + audit() → list[AuditEntry]         │  │ + render_for_prompt() → str   │
└─────────────▲────────────────────────┘  └────────────▲──────────────────┘
              │ asks RBAC                              │ builds
              │                                        │
              │                                ┌───────┴──────────────┐
              │                                │ ToolManifestBuilder  │
              └────────────────────────────────┤ + build(cfg, broker) │
                                               │ + invalidate(role)   │
                                               └──────────────────────┘

┌──────────────────────────────────────┐    ┌──────────────────────────┐
│ ContextManager                       │ ─► │ RepoMap                  │
├──────────────────────────────────────┤    ├──────────────────────────┤
│ + repo_map : RepoMap                 │    │ + entries                │
│ + get_context(task, max_tokens)      │    │ + render_compact()       │
│ + search_codebase(query)             │    │ + rebuild_incremental()  │
│ + mark_iteration(agent, iteration)   │    └──────────────────────────┘
└──────────────────────────────────────┘

┌──────────────────────────────────────┐  ┌──────────────────────────────┐
│ FileWorkspace                        │  │ GitWorkspace                 │
├──────────────────────────────────────┤  ├──────────────────────────────┤
│ + root : Path                        │  │ + repo_path : Path           │
│ + read(path) · write(path, content)  │  │ + commit(branch, message)    │
│ + edit(path, search, replace)        │  │ + checkout(branch)           │
│ + list(prefix) · grep(pattern)       │  │ + log(branch) · diff(a, b)   │
│ + claim(role, path) · release(...)   │  │ + changed_files(branch)      │
│ + _safe_path(path)   ◄─ DESIGN §28   │  │ + merge(source, target)      │
└──────────────────────────────────────┘  └──────────────────────────────┘

┌──────────────────────────────────────┐  ┌──────────────────────────────┐
│ LoopGuard                            │  │ QuipEngine                   │
│ + verdict_for(agent, calls)          │  │ + on_signal(signal)          │
│ + classify_error(exc)                │  │ + finalize_run(result)       │
└──────────────────────────────────────┘  └──────────────────────────────┘
```

## Key invariants encoded above

- `Workspace.board` is a `list[Signal]`, but only `post_signal` and
  `mark_seen` mutate it — append-only.
- `Scheduler.run()` is the single re-entry into orchestration; it returns a
  `SchedulerResult` keyed by `SchedulerStatus`.
- `AgentRunner` is the only object that holds references to *all* the
  shared singletons for a run (workspace, scheduler, broker, manifest
  builder, context manager, quip engine).
- `ToolManifest` is the per-agent runtime contract; agents call tools by
  name through it, never by direct import.
