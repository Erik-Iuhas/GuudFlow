# 03 — UML Class Diagram: `core/`

The orchestration kernel. Everything an agent does flows through these
classes. Field lists are abridged to the public surface — see the source
modules for the full dataclasses.

```mermaid
classDiagram
    direction LR

    class Workspace {
        +run_id: str
        +goal: str
        +phase: str
        +max_iterations: int
        +board: list~Signal~
        +user_board: list~Signal~
        +artifacts: dict~str, Artifact~
        +files: FileWorkspace
        +agent_states: dict~str, AgentState~
        +hired_personas: dict~str, str~
        +pending_human: HumanRequest
        +pending_screenshots: list~PendingScreenshot~
        +pending_approvals: list~ApprovalRequest~
        +git: GitState
        +stories: dict~str, StoryState~
        +active_story: dict~str, str~
        +deadline_at: float
        +iteration: int
        +completed_agents: list~str~
        +repo_map: RepoMap
        +post_signal(signal)
        +set_artifact(role, artifact)
        +mark_seen(signal_id, agent)
        +subscribe(callback)
    }

    class Signal {
        <<append-only>>
        +id: UUID
        +type: SignalType
        +from_agent: str
        +from_user: bool
        +priority: Priority
        +summary: str
        +detail: str
        +iteration: int
        +seen_by: list~str~
        +timestamp: datetime
    }

    class SignalType {
        <<enum>>
        STARTED · COMPLETE · FAILED
        APPROVED · REJECTED · ESCALATION
        HUMAN_NEEDED · HUMAN_RESPONDED
        APPROVAL_NEEDED · _GRANTED · _DENIED
        USER_COMMENT · USER_VISUAL_FEEDBACK
        GIT_COMMIT · GIT_MERGE · GIT_CONFLICT
        PACKAGE_REQUEST · _APPROVED · _REJECTED
        CREDENTIAL_REQUEST · _GRANTED · _DENIED
        FILE_CLAIM · FILE_RELEASE
        MANIFEST_INVALIDATED
        RUBRIC_SCORE · TOURNAMENT_RESULT
        PROTOCOL · AGENT_MESSAGE
        PEER_TIMEOUT · SKIP_TIMEOUT
    }

    class Artifact {
        +role: str
        +kind: str
        +summary: str
        +files: list~str~
        +commit: str
    }

    class Scheduler {
        +agents: dict~str, AgentSpec~
        +workspace: Workspace
        +scope: str
        +max_iterations: int
        +run() SchedulerResult
        +interrupt(role, kind, payload) InterruptToken
        +resume(token, value)
        -_spawn(spec)
        -_tasks: dict~str, Task~
    }

    class AgentSpec {
        +role: str
        +runner: AgentRunner
        +depends_on: list~str~
    }

    class InterruptToken {
        +agent_role: str
        +kind: human/peer/skip
        +id: str
    }

    class SchedulerStatus {
        <<enum>>
        COMPLETE
        MAX_ITERATIONS
        STUCK
        DEADLINE_EXCEEDED
    }

    class AgentRunner {
        <<core/runner.py>>
        +cfg: ProjectConfig
        +workspace: Workspace
        +scheduler: Scheduler
        +broker: CredentialsBroker
        +manifest_builder: ToolManifestBuilder
        +context_manager: ContextManager
        +llm_for_tier(tier) LLMClient
        +run() RunResult
        +on_signal(signal)
        +handle_screenshot(req)
        +handle_escalation()
        +finalize_run()
    }

    class ProjectConfig {
        +project_id: str
        +run_id: str
        +goal: str
        +agents: list~AgentConfig~
        +max_iterations: int
        +deadline_seconds: float
        +run_profile: BUDGET/BALANCED/QUALITY
        +source_repo: SourceRepoSpec
        +inputs: list~Path~
    }

    class AgentConfig {
        +role: str
        +archetype: str
        +persona_id: str
        +depends_on: list~str~
        +model_tier: heavy/solid/fast
        +model_preference: str
        +tools: list~str~
        +mcp_servers: list~str~
        +rbac_scopes: list~str~
        +system_prompt_overrides: str
    }

    class ProtocolMessage {
        <<COMMIT/VERIFY/DELTA/ACTION/ASK/ANSWER/STATUS/SKIP/STORY>>
        +kind: str
        +from_agent: str
        +commit_hash: str
        +files: list~FileChange~
        +checkpoints: list~str~
        +results: list~VerifyResult~
        +ask_id: str
        +target_role: str
    }

    class CredentialsBroker {
        +rbac: dict~str, RoleRBAC~
        +request_credential(role, scope) CredentialResult
        +invoke_mcp(role, server, tool, args)
        +redact(text) str
        +audit() list~AuditEntry~
    }

    class ToolManifest {
        +role: str
        +tools: list~ToolDescriptor~
        +mcp: list~MCPToolEntry~
        +has(name) bool
        +render_for_prompt() str
    }

    class ToolManifestBuilder {
        +build(agent_cfg, broker) ToolManifest
        +invalidate(role)
    }

    class ContextManager {
        +repo_map: RepoMap
        +get_context(task, max_tokens) AgentContext
        +search_codebase(query) list~SearchResult~
        +mark_iteration(agent, iteration)
    }

    class FileWorkspace {
        +root: Path
        +read(path) str
        +write(path, content)
        +edit(path, search, replace)
        +list(prefix) list~Path~
        +grep(pattern) list~Match~
        +claim(role, path)
        +release(role, path)
    }

    class GitWorkspace {
        +repo_path: Path
        +commit(branch, message) str
        +checkout(branch)
        +log(branch) list~GitLogEntry~
        +diff(commit_a, commit_b, path) str
        +changed_files(branch) list~str~
        +merge(source, target)
    }

    class LoopGuard {
        +verdict_for(agent, calls) LoopGuardVerdict
        +classify_error(exc) ErrorClass
    }

    class QuipEngine {
        +on_signal(signal)
        +finalize_run(result)
    }

    Workspace "1" o-- "*" Signal : board
    Workspace "1" o-- "*" Artifact
    Workspace "1" --> "1" FileWorkspace
    Workspace "1" --> "1" GitWorkspace : git.repo_path
    Workspace "1" --> "1" RepoMap : repo_map

    Scheduler "1" o-- "*" AgentSpec
    Scheduler ..> Workspace : observes / spawns
    Scheduler ..> InterruptToken : produces
    Scheduler ..> SchedulerStatus : returns

    AgentRunner "1" --> "1" Scheduler : owns
    AgentRunner "1" --> "1" Workspace
    AgentRunner "1" --> "1" CredentialsBroker
    AgentRunner "1" --> "1" ToolManifestBuilder
    AgentRunner "1" --> "1" ContextManager
    AgentRunner "1" --> "1" QuipEngine
    AgentRunner ..> ProjectConfig : configured by

    ProjectConfig "1" o-- "*" AgentConfig

    Signal --> SignalType
    Signal ..> ProtocolMessage : detail when type==PROTOCOL

    ToolManifestBuilder ..> CredentialsBroker : asks RBAC
    ContextManager --> RepoMap
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
