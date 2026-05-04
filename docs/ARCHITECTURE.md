# GuudFlow — Architecture Overview

> This is the **current-state** map of the system. It is meant to be read
> first, before `DESIGN.md`. The design doc is still the canonical
> specification of intent (37 sections), but several layers and flows
> have been refactored since it was written; this overview points to the
> diagrams that reflect what actually exists in the repo.

For high-level context, see [`README.md`](../README.md). For the full
intent + rationale, see [`DESIGN.md`](../DESIGN.md). For deviations from
the original design, see the [refactor log](#refactor-log) below.

> **Diagram format.** All diagrams in `docs/diagrams/` are **ASCII art**
> (box-drawing characters + plain arrows) so they render cleanly on
> phones, terminals, plain-text viewers, and any markdown frontend —
> no Mermaid runtime required.

---

## How GuudFlow runs

A run goes through six big phases. Each phase has at least one diagram.

```
   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
   │ 1 · Propose  │───►│ 2 · Approve  │───►│ 3 · Init     │
   │ meta-agent + │    │ user signs   │    │ seed         │
   │ roster       │    │ off team     │    │ workspace +  │
   │              │    │              │    │ git +        │
   │              │    │              │    │ personas     │
   └──────────────┘    └──────────────┘    └──────┬───────┘
                                                   │
                       ┌──────────────┐    ┌───────▼──────┐
                       │ 6 · Synth.   │◄───│ 5 · Merge    │
                       │ memory_writer│    │ branches →   │
                       │ + reputation │    │ main on      │
                       └──────────────┘    │ APPROVED     │
                                           └──────▲───────┘
                                                  │
                                           ┌──────┴────────────────┐
                                           │ 4 · Loop              │
                                           │ scheduler · agents ·  │
                                           │ reviews · escalation  │
                                           └───────────────────────┘
```

| Phase | Diagrams |
|-------|----------|
| 1 — Propose / 2 — Approve | [`06`](diagrams/06-sequence-run-lifecycle.md) |
| 3 — Init | [`10`](diagrams/10-deployment-and-data-flow.md) |
| 4 — Loop | [`02`](diagrams/02-component-architecture.md), [`06`](diagrams/06-sequence-run-lifecycle.md), [`07`](diagrams/07-sequence-handshake-and-escalation.md), [`09`](diagrams/09-state-agent-and-run.md) |
| 5 — Merge | [`06`](diagrams/06-sequence-run-lifecycle.md), [`09`](diagrams/09-state-agent-and-run.md) |
| 6 — Synthesize | [`08`](diagrams/08-sequence-persona-memory.md) |

---

## Diagram index

All diagrams use **ASCII art** so they render anywhere — including on
phones, in terminals, and in any markdown viewer. No Mermaid runtime
required.

| # | Title | What it answers |
|---|-------|-----------------|
| 01 | [System Context](diagrams/01-system-context.md) | What is GuudFlow as a black box? Who talks to it? |
| 02 | [Component Architecture](diagrams/02-component-architecture.md) | What are the layers and the legal arrows between them? |
| 03 | [Class — `core/`](diagrams/03-class-core.md) | The orchestration kernel. `Workspace`, `Scheduler`, `AgentRunner`, signals, protocol. |
| 04 | [Class — `agents/`](diagrams/04-class-agents.md) | `BaseAgent`, archetypes, builtin agents, `Persona`. |
| 05 | [Class — Tools + LLM](diagrams/05-class-tools-and-llm.md) | Tool hierarchy, `ToolManifest`, `LLMClient`, `ModelRegistry`, error taxonomy. |
| 06 | [Sequence — Run Lifecycle](diagrams/06-sequence-run-lifecycle.md) | Goal-to-deliverable, plus rejection / pause / resume / training variants. |
| 07 | [Sequence — Handshake + Escalation](diagrams/07-sequence-handshake-and-escalation.md) | ASK/ANSWER + REQUEST_SKIP + reviewer-rejection escalation. |
| 08 | [Sequence — Persona Memory](diagrams/08-sequence-persona-memory.md) | Memory injection, post-run synthesis, lifecycle states. |
| 09 | [State — Agent + Run](diagrams/09-state-agent-and-run.md) | The two FSMs the scheduler reasons about. |
| 10 | [Deployment + Data Flow](diagrams/10-deployment-and-data-flow.md) | Local dev, docker compose, on-disk run layout. |
| 11 | [Prompt Stack](diagrams/11-prompt-stack.md) | The 12-layer system prompt, cache boundary, where each layer is set. |

---

## Quick map: file → diagram(s)

| If you are reading… | …start with diagram |
|---|---|
| `core/scheduler.py` | 03, 06, 07, 09 |
| `core/runner.py` | 03, 06, 07, 09, 11 |
| `core/workspace.py` / `signals.py` / `protocol.py` | 03, 06 |
| `core/credentials_broker.py` / `tool_manifest.py` | 02, 05 |
| `core/llm/*` | 02, 05 |
| `agents/base.py` | 04, 05, 11 |
| `agents/persona.py` / `persona_desk.py` / `persona_chat.py` | 04, 08 |
| `agents/builtin/*` | 04, 06, 07 |
| `agents/tools/*` | 05 |
| `api/*` | 02, 06 |
| `worker/*` | 02, 10 |

---

## Refactor log

Notable deviations from the originally-written `DESIGN.md`. The design
doc is still the right place for *intent*; this log captures *what
changed in the code* so the diagrams above make sense.

### M1 — Handshake protocol & roster awareness (commit `1ec92e4`)

- Added structured `ASK / ANSWER / REQUEST_SKIP / SKIP_GRANTED /
  SKIP_DENIED / STORY_OPENED / STORY_CLOSED` to `core/protocol.py`.
- `Scheduler.interrupt()` / `resume()` is the one parking mechanism for
  hard pauses, peer waits, and skip-confirmations. `ask_human()` and
  `request_visual_feedback()` were migrated onto it.
- `PEER_TIMEOUT` and `SKIP_TIMEOUT` signals + a deadline watcher.
- Story lifecycle on the workspace (`Workspace.stories`,
  `Workspace.active_story`).
- Layer 3.5 (teammate brief) and Layer 5.5 (protocol voice) added to
  `effective_system_prompt`.
- `core/edit_blocks.py` introduced for SEARCH/REPLACE edit-block syntax
  with closest-match hint on miss.

### M2 — Persona desk, manifesto layer, history tracking (commit `d4516f0`)

- `agents/persona_desk.py` — `DeskIndex` parses YAML-fronted markdown
  from `data/role_templates/{role}/desk/`, serves contextual snippets.
- New tools: `desk-search`, `desk-push`.
- Layer 3.6 (manifesto) injected into the system prompt. Default lives
  at `data/manifestos/default.md`; per-project overrides supported.
- `memory_writer` now appends to `history.json` after every run with
  atomic write + 1000-entry rotation to `history.archive.jsonl`.
- Run initializer materializes `desk_seeds` and writes
  `inputs/README.md`.

### M3 — Multi-model abstraction + error taxonomy + caching (commit `7b1c54f`)

- `core/llm.py` split into `core/llm/` package: `client.py`, `errors.py`,
  `anthropic.py`, `openai.py`, `registry.py`. All public symbols
  re-exported for backwards compatibility.
- New error taxonomy (`RateLimitError`, `AuthError`,
  `ContextLengthError`, `InvalidRequestError`, `ServerError`,
  `NetworkError`, `TimeoutError`) with provider exception mapping.
- `AnthropicLLMClient` gained `cache_system=True` so the stable system
  prompt is wrapped with `cache_control: ephemeral`.
- `LLMResponse` gained `cache_read_tokens` / `cache_creation_tokens`.
- `ModelRegistry` reads `data/models.json` (5 models across Anthropic,
  Groq, DeepSeek + 6 providers). Per-tier resolution, deprecation
  warnings, per-provider client cache. Local endpoints (Ollama, LM
  Studio) treated as available without an API key.
- `MemoryWriter` is now wired through a SOLID-tier client by default.
- Cost estimator pulls per-1k rates from the registry (75/25 input/output
  blend) with static fallback.
- `GET /api/models` exposes the catalog to studio dropdowns and the
  cost estimator.

### Autonomy enforcement + inter-agent board messaging (commit `d2cdfb1`)

- New `AGENT_MESSAGE` signal type for non-blocking directed
  agent-to-agent messages.
- New `board-message` tool — agents post to a named peer; the recipient
  reads the message at the start of its next turn.
- New mandatory `AUTONOMY RULES` block in `effective_system_prompt`
  ("START WORKING NOW. Make at least one tool call. Never present
  options to humans for things you can decide.").
- Tightened task prompt: "call `fs-list` or `git-changed-files` as your
  very first tool call."

### Reviewer file-by-file process (commit `41c26d4`)

- `git-diff` accepts an optional `path` argument and is hard-capped at
  ~6000 chars (~1500 tokens) per call — no more 429s on large
  codebases.
- New `git-changed-files` tool added to `BASELINE_LOCAL_SCOPES` and
  `Reviewer.default_tools`.
- `CodeReviewer` system prompt rewritten to enforce the
  list-then-iterate review process.

### Hyphenated tool names + RBAC scopes (commit `0709f1b`)

- Tool names are hyphenated (`fs-write`, `git-diff`, `code-exec`,
  `desk-search`). RBAC scope strings match 1:1.

### Phase G-prime — Resume after crash (commits `44c40b6`, `fef6f1a`, `4818135`, `3a8f8db`)

- `Workspace._board_persister` writes signals incrementally to
  `data/runs/{run_id}/board.jsonl`.
- `core/run_loader.py::restore_workspace` rebuilds a `Workspace` from
  the persisted board + artifacts; `derive_agent_states` infers state
  from signal history.
- `POST /api/runs/{id}/resume` plus a worker resume path that picks up
  the restored workspace.

### Phase F — Training Camp + persona chat + memory synthesis

Several commits in the F-series:

- `core/training/scenario.py` and `tournament.py` —
  scenarios, rubrics, tournaments, ranking.
- `agents/builtin/rubric_scorer.py` — replaces a human reviewer in
  training mode.
- `agents/persona_chat.py` — `POST /api/personas/{id}/chat` for
  one-off consultation.
- `MemoryWriter` upgraded with LLM-driven pattern distillation
  (`F.3.0/F.3.1`), pattern flip detection (`F.3.2`), and deterministic
  style drift (`F.3.3`).

### Phase K — Project roadmap context

Layer 3.8 of the system prompt — `data/projects/{id}/roadmap.md` is
loaded and injected so each run is aware of the project's larger arc
(continuous app building).

### M3.5 — Task desks + planner pickup (commits `a501574`–`4aef259`)

- `core/workspace.py`: `Task` + `TaskRef` dataclasses; `Workspace.tasks`
  dict + `task_queues` per-role list; `add_task`, `mutate_task`.
- `core/runner.py`: `_handle_task_closures` (closes tasks on `closes:`
  in COMMIT), `_maybe_auto_start_task` (marks next pending task
  IN_PROGRESS), `_handle_user_comment_routing` (drops USER_COMMENT on
  planner's desk when mention detected), planner pickup loop.
- `GET /api/runs/{id}/desks` — reads live workspace or falls back to
  persisted `tasks.jsonl`.
- `worker/job_runner.py::serialise_task` — canonical Task → dict.

### Track 7 — Subtask decomposition (commits `89a3bff`, `6050e34`, `7af5fab`)

- New `decompose-task` tool (`agents/tools/decompose_task.py`). Cheap
  smart-parent gate uses an LLM to reject coupled subtask sets — keeps
  agents from forking unrelated work into a chain that doesn't parallelise.
- `assign-task` accepts subtasks atomically, so the parent task and its
  children land in one call.
- `Workspace.active_subtask: dict[str, str]` records which subtask each
  role is currently advancing.
- Runner advances `active_subtask` automatically when a subtask closes
  (`_handle_task_closures` keys off `closes:` trailers in COMMIT messages).
- New prompt **Layer 4.7 — ACTIVE SUBTASK** in `effective_system_prompt`
  injects the current subtask so the agent stays focused.

### Track 8 — Subtask context hot-swap + recall (commit `dfa8958`)

- New `recall-subtask` tool (`agents/tools/recall_subtask.py`) — per-
  subtask scoped memory recall instead of global persona memory.
- Layer 4.5b (task-relevant files) is rebuilt when the active subtask
  changes, so the prompt swaps the relevance window automatically.

### Track 9 — Protocol cleaner sidecar (commit `f4c2710`)

- New `PROTOCOL_CLEANUP_AUTO` signal type. The runner watches for
  `PROTOCOL_PARSE_FAIL` and auto-fires a cheap `protocol-cleanup`
  sub-LLM call that re-renders the malformed trailing block instead of
  re-prompting the whole agent.
- New `ProtocolCleanupTool` in `agents/tools/llm_dispatch.py`,
  registered as a Track-12 sub-LLM use case.

### Track 11 — Workers cognition view (commit `e450535`)

- New `WorkerCognitionView` component (`studio/src/components/`) — live
  per-agent snapshot showing the current iteration, last tool call
  summary, recall hits, and budget. Wires into the existing
  `RunWsContext`.
- `BaseAgent._summarise_tool_result` produces the ≤120-char one-liner
  the panel displays.

### Track 12 — Sub-LLM dispatch (commit `dfa8958`)

- `LlmDispatchTool` base + concrete `SummariseSubtaskTool`,
  `ProtocolCleanupTool` in `agents/tools/llm_dispatch.py`.
- `ProjectConfig.sub_llm_models: dict[use_case, model_id]` and
  `AgentConfig.sub_llm_models_override` per-use-case overrides; both
  fall back to `ModelRegistry.cheapest_tool_capable()` at dispatch time.
- Used internally by Track 8 (subtask summarisation) and Track 9
  (protocol cleanup) — kept off the main reasoning model to save spend.

### Track 1 — Iteration metrics + cost tracking

- `Workspace.iteration_metrics: list[IterationMetrics]` — one row per
  full `BaseAgent.run()` invocation, with input/output tokens, cache
  hit/creation tokens, recall counts, repo-map / relevant-files /
  diff token breakdown.
- `Workspace.context_log: list[ContextSnapshot]` — per-`get_context()`
  budget vs. used.
- Track 3d.5: `IterationMetrics.estimated_cost_usd` populated from
  `ModelRegistry.cost_per_1k`. `RunAnalytics` view shows a live cost
  column.
- `Workspace.add_iteration_metrics()` and persistence hooks dump
  `iteration_metrics.jsonl`, `agent_turns.jsonl`,
  `llm_calls.jsonl`, `context_log.jsonl` per run.

### ask-peer wrapper (commits `ee0c7f2`, `7e8aa68`, `20ebecb`, `753840a`)

- New `ask-peer` tool (`agents/tools/ask_peer.py`) — a discoverable
  wrapper around the M1 `[ASK]` protocol. Most agents would never
  guess the raw protocol path; the tool gives them an obvious one.
- `board-message` (the non-blocking sibling) was tightened: priority
  demoted, question-shaped messages to completed agents are now
  rejected to stop "ASK cascades".
- `_generic_peer_answer` runs in a bounded tool-use loop so the
  responder can read 1–2 files before answering, with a fallback
  synthesised ANSWER if the budget elapses.

### Hierarchy: `manages` + `escalates_to` (commits `abd0ee4`, `919bbba`)

- `AgentConfig.manages: list[str]` — explicit "I dispatch to these
  roles" list. Replaces the old derived-from-RBAC behaviour.
- `AgentConfig.escalates_to: list[str]` — explicit escalation handler
  list (defaults to `tech_lead` when empty).
- New conditional **Layer 6.7 — DISPATCH RULE** in
  `effective_system_prompt`: only injected when an agent has
  `assign-task` AND `manages` lists ≥ 2 teammates. Forces "one
  `assign-task` per teammate" — fixes the architect anti-pattern of
  bundling all downstream work into a single dev's task.

### CONTEXT BEFORE OUTPUT layer (commit `0b2b4f0`)

- New **Layer 6.6** in `effective_system_prompt` after the autonomy
  block: "read every reference in your task brief; inventory before
  reads; arch describes shape, design describes behaviour — both
  required before code lands." Diagnosed in `run_f741ea78ba` where
  devs read 3 architecture docs but skipped 26 design docs and wrote
  `ghost_ai_system.py` without reading `ghost_ai.md`.

### Tool-call-adverse runtime fixes (commits `b04105f`, `2841f9b`, `efedf9f`, `242fedc`, `696c483`, `561f9a8`)

- `max_tokens` default bumped 4096 → 8192 across `LLMClient`,
  `AnthropicClient`, `BaseAgent`.
- `fs-write-append` chunked-write primitive for files larger than
  `max_tokens` of output.
- `fs_batch.py`: `FsWriteBatch` and `FsEditBatch` with per-file +
  per-batch byte caps so the LLM picks the right tool.
- Runner handles `stop_reason == "max_tokens"` by recovering truncated
  batched tool calls (and salvaging text-as-tool patterns).
- Cleanup model rejects kind-mismatch (specialist VERIFY loop).

### Provider expansion + model picker (commits `fbd8621`, `b2e16f1`)

- 4 Mistral models added to `data/models.json`; provider key typo
  fixed (`mitsral` → `mistral`).
- `AgentConfig.model_id` was added so the studio's ModelPicker can
  persist non-Anthropic selections across saves.

### M4 — Studio UI Pass 1

- `GET /api/tools` — catalog from `LOCAL_TOOL_CLASSES` with baseline +
  archetype-grant metadata.
- `GET /api/dashboard` — aggregates RunRegistry + RunStore + PersonaStore
  into one polling endpoint.
- `POST /api/meta/propose` — optional `meta_model` tier string.
- `studio/src/lib/avatar.ts` — `avatarFor(seed)`: FNV-1a deterministic
  gradient + initials, memoized.
- `WorkerCard` — single component, four modes: `config` (full editable),
  `preview` (read-only), `live` (ActiveTaskChip + gear), `roster` (click
  → PersonaSlideOver).
- `ToolsMultiSelect` — RBAC-aware; disables tools not granted by
  baseline / archetype / project scopes.
- `WorkerSettingsPanel` — right-edge slide-over; optimistic save +
  revert.
- `Dashboard` — new `/` route; 5s poll, pauses on `document.hidden`.
- `RunWsContext` — tiny pub/sub context; Run view populates it from its
  existing WS ref; `useDesks` subscribes and debounce-refetches on
  `TASK_*`/`COMMIT`.
- `ActiveTaskChip` — derives from `useDesks`; shows IN_PROGRESS → PENDING
  → idle; popover shows acceptance criteria + references.
- `PersonaSlideOver` — goal, reputation snapshot, style tendencies, last
  5 episodes; no edit.
- `AgentCard` deleted.

---

## What the diagrams **don't** cover yet

These are intentionally future / deferred per `ROADMAP.md`:

- **Phase E** — production deploy + multi-tenant admin (k8s migration).
- **Phase I** — headless service mode (webhooks, structured
  deliverables, per-consumer API keys).
- **Phase F.5** — persona author depth (publish, scaffolding, curated
  reference URLs, sandboxed pytest checks).
- **Phase J** — knowledge surface (deep-wiki / codebase-doc toolkit).
- **UICanvas** (DESIGN §15).
- Persona sharing / marketplace (DESIGN §26).

When those land, add a diagram or update the relevant existing one.
