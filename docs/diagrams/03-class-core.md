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
│ + pending_package_requests : list[PackageRequest]      │
│ + pending_approvals : list[ApprovalRequest]            │
│ + git : GitState                                       │
│ + stories : dict[str, StoryState]                      │
│ + active_story : dict[str, str]                        │
│ + active_subtask : dict[str, str]   ◄─ Track 7         │
│ + deadline_at : float                                  │
│ + iteration : int                                      │
│ + completed_agents : list[str]                         │
│ + repo_map : RepoMap                                   │
│ + tasks : dict[str, Task]                              │
│ + task_queues : dict[str, list[TaskRef]]               │
│ + iteration_metrics : list[IterationMetrics]           │
│ + context_log : list[ContextSnapshot]                  │
│ + embeddings_enabled : bool                            │
├────────────────────────────────────────────────────────┤
│ + post_signal(signal)                                  │
│ + set_artifact(role, artifact)                         │
│ + mark_seen(signal_id, agent)                          │
│ + subscribe(callback)                                  │
│ + add_task(...) · mutate_task(...)                     │
│ + record_approval(...) · find_pending_approval(...)    │
│ + add_iteration_metrics(m) · record_context_snapshot() │
│ + signals_of_type(type) · unseen_user_comments()       │
│ + child_tasks(parent_id) · desk_for(role)              │
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

┌─────────────────────────────────────────────────────────┐
│ SignalType  «enum»                                      │
├─────────────────────────────────────────────────────────┤
│   STARTED · THINKING · COMPLETE · FAILED · BLOCKED      │
│   APPROVED · REJECTED · ESCALATION                      │
│   HUMAN_NEEDED · HUMAN_RESPONDED                        │
│   APPROVAL_NEEDED · _GRANTED · _DENIED                  │
│   USER_COMMENT · USER_COMMENT_PRIORITY                  │
│   SCREENSHOT_READY · USER_VISUAL_FEEDBACK               │
│   GIT_COMMIT · GIT_MERGE · GIT_CONFLICT                 │
│   PACKAGE_REQUEST · _APPROVED · _REJECTED               │
│   CREDENTIAL_REQUEST · _GRANTED · _DENIED               │
│   FILE_CLAIM · FILE_RELEASE                             │
│   MANIFEST_INVALIDATED                                  │
│   TASK_ASSIGNED · TASK_STARTED · TASK_COMPLETED         │
│   TASK_BLOCKED · TASK_CANCELLED · TASK_OUT_OF_SCOPE     │
│   RUBRIC_SCORE · TOURNAMENT_RESULT                      │
│   PROTOCOL · PROTOCOL_PARSE_FAIL ·                      │
│   PROTOCOL_CLEANUP_AUTO          ◄─ Track 9 sidecar     │
│   AGENT_MESSAGE                                         │
│   PEER_TIMEOUT · SKIP_TIMEOUT · PR_READY                │
└─────────────────────────────────────────────────────────┘
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
│ DEADLINE_EXCEEDED · STOPPED            │
└────────────────────────────────────────┘
   STOPPED ─ user pressed Stop / force_cancel()

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
┌──────────────────────────────────────────┐    ┌──────────────────────────────────┐
│ ProjectConfig                            │    │ AgentConfig                      │
├──────────────────────────────────────────┤  * ├──────────────────────────────────┤
│ + project_id : str                       │◄───│ + role : str                     │
│ + goal : str                             │  1 │ + description : str              │
│ + agents : list[AgentConfig]             │    │ + depends_on : list[str]         │
│ + rbac : dict[str, RoleRBAC]             │    │ + system_prompt : str            │
│ + mcp_servers : list[dict]               │    │ + declared_local_tools : list    │
│ + max_iterations : int                   │    │ + declared_mcp_servers : list    │
│ + escalation_thresholds : dict[str, int] │    │ + persona_id : str | None        │
│ + approval_gates : dict[str, Any]        │    │ + persona_goal : str             │
│   pre_run_cost_preview · iter_breach_at  │    │ + model_tier : heavy/solid/fast  │
│ + mode : production | training           │    │ + model_id : str   ◄─ explicit   │
│ + stories : list[dict]    (M1 §7b seeds) │    │ + agent_class_role_type :        │
│ + desk_seeds : dict[id, list[seed]] (M2) │    │    str | None                    │
│ + training_scenario_id : str | None      │    │ + is_reviewer : bool             │
│ + consecutive_fail_threshold : int (M5)  │    │ + is_final_reviewer : bool       │
│ + pr_mode : manual | auto  (Track 4.2)   │    │ + max_tool_iterations : int      │
│ + sub_llm_models : dict[use_case, model] │    │ + loop_guard : LoopGuardConfig   │
│   (Track 12)                             │    │ + max_protocol_retries : int     │
└──────────────────────────────────────────┘    │ + sub_llm_models_override : dict │
                                                │   (Track 12 per-agent)           │
                                                │ + excluded_baseline_tools : list │
                                                │ + manages : list[str]    ◄── §23 │
                                                │ + escalates_to : list[str]       │
                                                └──────────────────────────────────┘
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

### Workspace sub-records

```
┌────────────────────────────┐    ┌─────────────────────────────────────┐
│ IterationMetrics           │    │ ContextSnapshot                     │
│ (Track 1 — per-iteration)  │    │ (Track 1d — per get_context() call) │
├────────────────────────────┤    ├─────────────────────────────────────┤
│ + run_id · role            │    │ + role · iteration                  │
│ + iteration · model        │    │ + repo_map_tokens                   │
│ + input_tokens             │    │ + relevant_files_tokens             │
│ + output_tokens            │    │ + git_diff_tokens                   │
│ + cache_read_tokens        │    │ + total_tokens                      │
│ + cache_creation_tokens    │    │ + budget · used                     │
│ + llm_calls                │    └─────────────────────────────────────┘
│ + final_message_tokens     │
│ + recall_episodes_count    │    ┌─────────────────────────────────────┐
│ + recall_patterns_count    │    │ Task / TaskRef  (M3.5 desks)        │
│ + repo_map_tokens          │    ├─────────────────────────────────────┤
│ + relevant_files_tokens    │    │ + id · parent_id · assignee_role    │
│ + git_diff_tokens          │    │ + goal · acceptance_criteria        │
│ + context_tokens_total     │    │ + state : pending/in_progress/      │
│ + direct_file_read_tokens  │    │           done/blocked/cancelled    │
│ + tool_token_breakdown     │    │ + closed_by_commit · iteration      │
│ + estimated_cost_usd       │    │ + created_by · created_at           │
│   (Track 3d.5)             │    └─────────────────────────────────────┘
│ + started_at · ended_at    │
│ + elapsed_seconds          │    ┌─────────────────────────────────────┐
└────────────────────────────┘    │ StoryState  (M1 §7b)                │
                                  │ + id · name · goal · owner_role     │
┌────────────────────────────┐    │ + parent_story_id                   │
│ CommitRecord               │    │ + opened_at · closed_at             │
│ + hash · branch · agent    │    │ + state : open/closed               │
│ + message · iteration      │    └─────────────────────────────────────┘
│ + files · timestamp        │
└────────────────────────────┘    ┌─────────────────────────────────────┐
                                  │ ApprovalRequest  (Phase C.0.5)      │
┌────────────────────────────┐    │ + id · gate_name · created_at       │
│ PackageRequest             │    │ + payload : dict (cost preview,     │
│ + name · reason            │    │              tier upgrade, etc.)    │
│ + iteration · status       │    │ + state : pending/granted/denied    │
└────────────────────────────┘    └─────────────────────────────────────┘
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
