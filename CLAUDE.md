# GuudFlow — Claude Code Instructions

## ⚠️ TOP PRIORITY — Commit after every code change

After every file modification, run `git add` on the changed files and create a commit before ending the turn. Do not batch multiple sessions of changes into one commit. If tests pass, commit. If tests fail, fix them, then commit. Never leave modified files uncommitted when handing back to the user.

---

## Read this first, then DESIGN.md in full before writing any code.

---

## What this project is

A managed multi-agent orchestration platform. Agents run in dependency-aware loops,
communicate via a shared workspace board, and can form working groups. Humans approve
the agent team before any run starts. Projects become callable API endpoints for CI/CD.

Full architecture is in DESIGN.md. Do not infer architecture from existing code —
read the design doc. Every structural decision is intentional.

**Ongoing projects.** A project is software under continuous development; a run is one
working session on it (plan §13). Each project has a long-lived repo (`core/project_repo.py`)
and a backlog (`core/backlog.py`). A run takes backlog items, starts from the project's `main`,
and waits for a human to accept it before anything reaches `main`. The Studio project page
opens on the Work tab (review queue + backlog). Plan §14 adds remote sync/publish
(`core/project_remote.py`, host-only token), checks before review (`core/project_checks.py`),
reconcile runs, item dependencies and grooming, autopilot (`core/autopilot.py`) and SQLite
storage (`core/work_db.py`).

**Named workers.** Every role has a worker name (`core/crew.py`): the name set on the team
(`agents[].name`), the persona's, or a stable pick seeded by project and role (saving a project
fills in missing names). Briefs introduce teammates by name, notes may be addressed by name or role,
commits and quips use the name, and a `TEAM` signal at run start tells Studio who is who.
`from_agent` on signals stays the role.

**Plan, spec, seats and start modes.** See `docs/PROJECT_FLOW_PLAN.md`.
- **Project manager: Jack.** The meta-agent is the project manager, a persona named Jack
  (`jack-project-manager-v1`, `core/pm_persona.py`) with cross-project memory (`core/pm_memory.py`:
  episodes on session and draft outcomes, patterns via the memory writer, recall in every turn).
  Jack can propose team changes in drafts (`add_role`/`edit_role`/`remove_role`); a human always
  ticks them and they apply from the next session (`core/team_changes.py`).
  The PM itself is `core/project_manager.py` (prompts in `core/pm_prompts.py`). It is a host-side LLM call that proposes changes to a
  versioned **spec** through **drafts**. Every spec change goes through a draft (`core/plan_drafts.py`).
- **The spec.** Requirements `R#` with checks `R#.#`, decisions `D#`, and rules `rule-N`
  (`core/spec.py`), all in `work.db`. Work items are the backlog (`B#`, which `serves` requirements).
- **Seats.** Owner, Lead and Reviewer change what the project manager does and who must approve.
- **Start modes.** Five modes (`core/start_modes.py`) preset the team, the planner instructions,
  the reviewer lines and gates.
- **In sessions.** The spec reaches briefs through `core/harness/spec_brief.py`. Binding rules
  become reviewer checklist lines. Check states and rule verdicts are recorded when a session is
  accepted (`core/spec_adherence.py`).
- **Reasons.** Every draft change names a described reason (`PlanTurn.groups`, quotes checked
  against what was said) and cards say which spec checks they serve or which cards they support
  (`PlannedTask.serves` / `supports`). See plan §12.
- **Record.** `core/record.py` gives the trace and export.
- **Next (planned, not built):** role contracts, project kinds, a design artifact owned by a tech
  lead, a shared knowledge base and a verification ledger: `docs/ROLE_CONTRACTS_PLAN.md`.
- **MCP.** Third-party MCP servers are proxied on the host (`core/mcp_registry.py`,
  `core/harness/mcp_proxy.py`; `docs/MCP_PLAN.md`).

**Models & providers.** Studio's Settings page edits providers, models, prices and each tier's
model (`core/model_config.py`, `api/routes/model_config.py`). The install's choices live in
`{data_root}/model_config.json` (the shipped `data/models.json` is the default); provider keys typed
into Studio are stored host-only in `{data_root}/secrets/provider_keys.json` (0600), are never returned
by the API, and an environment variable wins. Always load the registry with
`ModelRegistry.for_data_root()`.

**Opencode harness.** Agent work is done by [opencode](https://github.com/anomalyco/opencode)
running headless, one sandboxed server per role. GuudFlow stays the manager: scheduler,
task board, personas and memory, permissions, credentials, merges and Studio. The design is
in `docs/OPENCODE_HARNESS_PLAN.md`, the task list in `docs/OPENCODE_WORK_BREAKDOWN.md`, and
the Studio spec in `docs/STUDIO_UI_SPEC.md`. `opencode` is the only harness that runs; the
original in-process tool loop was retired (work breakdown S15) and a project that still says
`harness: "legacy"` is refused with a clear error. Old runs stay viewable from their saved boards.
The legacy harness is unsupported: new work does not need to keep it (or its views) working,
and it is not removed just for tidiness (plan §14).
`core/context_manager.py`, `core/repo_map.py`, `core/protocol.py` and `agents/tools/` remain only
because host-side agents (meta-agent, memory writer, persona chat) still use them. Where DESIGN.md and the plan disagree, the plan
wins for the opencode harness.

---

## Token Budget Per Stage

> **Note:** the stages below describe the original build (the legacy harness). The opencode
> harness work is tracked task by task in `docs/OPENCODE_WORK_BREAKDOWN.md`, which also
> records which tasks are done and which are deferred.

Use these estimates to pace the build. At the end of each stage, check your
actual token usage against the estimate. If you're significantly over, stop
and assess why before continuing — it usually means the design was ambiguous
somewhere and you're iterating more than expected.

All estimates assume Claude Sonnet for routine work, Opus for complex reasoning.
If running Opus throughout, multiply output costs by ~3x.

```
STAGE 1 — Foundation Primitives
  Files: core/signals.py, core/protocol.py
  Complexity: Medium — dataclasses and parsers, but everything depends on these
  Estimated tokens: 150-250k
  Estimated cost:   $5-8
  Exit criteria: All signal types defined; protocol parse/render roundtrips cleanly
                 in tests; ProtocolMessage dataclass complete
  STOP if: Over 350k tokens

STAGE 2 — Workspace & State
  Files: core/workspace.py, core/file_workspace.py, core/git_workspace.py,
         core/repo_map.py, core/context_manager.py, data/personas/ structure
  Complexity: Medium-High — includes tree-sitter parser, PageRank ranking,
              AgentContext builder, token budget logic
  Estimated tokens: 350-500k
  Estimated cost:   $10-15
  Exit criteria: Workspace initialises; FileWorkspace path safety enforced;
                 git repo inits; repo_map builds and renders compact signatures;
                 context_manager returns AgentContext within token budget;
                 iteration-aware diff logic works correctly
  STOP if: Over 600k tokens

STAGE 3 — Scheduler ⚠️ Critical Path
  Files: core/scheduler.py, tests/core/test_scheduler.py
  Complexity: Very high — do not rush this
  Estimated tokens: 200-350k
  Estimated cost:   $6-10
  Exit criteria: ALL 6 scheduler tests pass (sequential chain, parallel fan-out,
                 join, human pause, group completion, max iterations cutoff)
  STOP if: Over 450k tokens or any test still failing — do not proceed with
           a broken scheduler under any circumstances. See "The Scheduler is
           the Critical Path" section below.

STAGE 4 — Agent Foundation
  Files: agents/persona.py (with three-layer memory: episodes/patterns/style loaders + recall()),
         agents/base.py, agents/registry.py,
         agents/tools/ (base, code_exec, file_read, web_search, git_tool)
  Complexity: High — memory recall is non-trivial (similarity search, confidence scoring)
  Estimated tokens: 450-650k
  Estimated cost:   $13-19
  Exit criteria: BaseAgent instantiates with persona; recall() returns relevant
                 episodes and patterns by task similarity; all protocol emit methods
                 produce valid parseable output; effective_system_prompt() composes
                 all 7 layers including contextually retrieved memory; tools run
                 without errors in dev mode
  STOP if: Over 800k tokens

STAGE 5 — Runner & Builtin Agents
  Files: core/runner.py, core/quip_engine.py,
         agents/builtin/ (meta, it_agent, tech_lead, visual_checker,
         code_reviewer, test_runner, memory_writer)
  Complexity: Very High — runner wires everything including escalation,
              quip events, AND post-run memory synthesis
  Estimated tokens: 600-850k
  Estimated cost:   $18-26
  Exit criteria: A 2-agent run completes end-to-end with protocol messages on
                 the board, git commits in workspace, quips in quip log;
                 memory_writer creates a new episode in episodes.jsonl,
                 updates pattern confidence if applicable, and refreshes
                 reputation.json post-run
  STOP if: Over 1M tokens

STAGE 6 — API Layer
  Files: api/auth.py, api/main.py, api/websocket.py,
         api/routes/ (projects, runs, meta, personas, invoke)
  Complexity: Medium — wiring, auth, route handlers
  Estimated tokens: 200-300k
  Estimated cost:   $6-9
  Exit criteria: POST a project; start a run via WebSocket; receive board events
                 live; invoke endpoint returns 202 immediately with bearer auth
  STOP if: Over 400k tokens

STAGE 7 — Worker & Queue
  Files: worker/queue.py, worker/docker_manager.py, worker/job_runner.py
  Complexity: Medium — queue abstraction with in-memory default
  Estimated tokens: 150-250k
  Estimated cost:   $5-8
  Exit criteria: Worker picks up jobs from queue; runs AgentRunner; updates
                 status. docker_manager can be a stub in dev mode.
  STOP if: Over 350k tokens

STAGE 8 — Frontend
  Files: studio/src/ (App, all views, all components)
  Complexity: Medium-High — Run view is the most complex
  Estimated tokens: 500-700k
  Estimated cost:   $15-21
  Exit criteria: Create project; select run profile with live cost estimate;
                 approve proposal; watch live run with QuipFeed alongside
                 BoardFeed; post user comment; see screenshots; view git log;
                 inspect persona roster
  STOP if: Over 850k tokens

STAGE 9 — Sandbox (production prerequisite, can defer)
  Files: sandbox/Dockerfile
  Complexity: Low — minimal image
  Estimated tokens: 50-100k
  Estimated cost:   $2-3
  Exit criteria: Image builds; container runs with resource limits;
                 code_exec routes through container in production mode
  STOP if: Over 150k tokens — Dockerfile complexity should be minimal

INTEGRATION DEBUGGING (always budget extra here)
  This is not a stage — it is the buffer at every stage boundary.
  Real overhead from misalignment between modules tends to surface here.
  Estimated tokens: 400-800k spread across stages
  Estimated cost:   $12-25

─────────────────────────────────────────────────────────────────────────
TOTAL ESTIMATED: 2.8M - 4.2M tokens (excluding integration buffer)
                 3.3M - 5.0M tokens (with integration buffer)
TOTAL ESTIMATED COST: $85-135 (Sonnet-primary)
                      $200-320 (Opus-primary)

RECOMMENDED BUDGET: $200 with batch API pricing (50% input discount)
                    $300 on-demand pricing with headroom for debugging

The 9-stage structure means natural checkpoints — stop, verify, then continue.
This is much better than running the whole project in one shot.
─────────────────────────────────────────────────────────────────────────
```

**How to track spend per stage:**

At the start of each stage, note your current token count from the Claude Code
session stats. At the end, subtract to get stage cost. If a stage runs over
budget, post a comment in the relevant file explaining what caused the overrun
so future sessions have context.

If total spend hits $250 before Stage 7 is complete, stop and do a design review
before continuing — overruns at that level usually mean an architectural decision
needs revisiting, not just more coding.

---

## How to work on this project

### Starting a session
1. Read DESIGN.md sections relevant to what you're building
2. Check what already exists before creating anything new
3. Follow the build order in DESIGN.md section 12 — do not skip ahead
4. If something in the design is ambiguous, make the conservative choice and add a TODO comment

### One task at a time
Complete one module fully before starting the next. A complete module means:
- Implementation
- Type hints throughout
- Docstrings on all public classes and functions
- Corresponding test file in `tests/`

### When you're unsure
Add a `# QUESTION:` comment and move on. Do not make architectural guesses.
Structural decisions (especially in core/) affect everything downstream.

---

## Hard constraints — never violate these

These are from DESIGN.md sections 13, 14, and 19. They are not preferences.

1. **Workspace is the only shared state.** Agents never reference each other directly.
2. **Scheduler owns all concurrency.** No `asyncio.gather` outside of `core/scheduler.py`.
3. **Signals are append-only.** Never mutate or delete from `workspace.board`.
4. **BaseAgent is the only contract.** Runner imports agents through `agents/registry.py` only.
5. **Tools are stateless.** Input in, output out. No instance state between calls.
6. **MCP is additive.** Core works with zero MCP config. Never require it.
7. **Human pause does not interrupt running agents.** Sets `pending_human`, stops new spawns only.
8. **The loop is the error handler.** Keep agents simple. Reviewer catches bad output.
9. **Groups are pipeline units.** PipelineScheduler only tracks group-level COMPLETE signals.
10. **Scheduler is one reusable class.** PipelineScheduler and GroupScheduler are same class, different scope.
11. **Workspace artifacts are typed.** Use `Artifact` dataclass — never `dict[str, str]`.
12. **Invoke endpoints return immediately.** Enqueue job, return 202. Never block on pipeline completion.
13. **File I/O: agents write inside their sandbox; GuudFlow goes through FileWorkspace.** (changed for the opencode harness, plan §6.4) Agent file I/O happens inside the sandbox via opencode. GuudFlow-side I/O (runner, API, workspace) still uses `FileWorkspace`, with `_safe_path()` on every operation. The sandbox mount *is* the path-safety boundary: a role can write only its own clone at `/work`, `/inputs` is read-only, and the runner checks every branch (protected paths, symlinks, secrets) before merging.
14. **Signals reference files, not contain them.** Agents post `files_written` paths, others read from disk.
15. **Personas persist across runs.** Profile, memory, history, reputation are independent of any project.
16. **Memory writes are conservative.** Only record lessons backed by specific evidence.
17. **Credentialed calls go through the broker.** Agents never hold API keys, OAuth tokens, or MCP secrets. All credentialed access routes through the Credentials Broker via `request_credential()` or `invoke_mcp()`. RBAC is default-deny. See DESIGN §30.
18. **One tool policy.** (changed for the opencode harness, plan §6.4) The policy is the generated opencode permission ruleset plus MCP RBAC, both compiled from the role's capabilities (`core/harness/capabilities.py`, `permissions.py`). Agents never see credentials (rule 17 is unchanged). The role's MCP identity comes from its bearer token, never from tool arguments. `BaseAgent.call_tool` and `ToolManifest` apply only to the legacy harness. See plan §5.1, §5.6, §5.7.
19. **Scopes keep their names.** (changed for the opencode harness, plan §6.4) The RBAC scope names (`fs:*`, `doc:*`, `code:*`, `web:*`, `git:*`, `vis:*`, `mcp:*`) are unchanged. `core/harness/permissions.py` maps them onto opencode permission keys, and `capabilities.py` onto MCP tool grants. Read access is baseline; anything that writes, runs a shell, uses the web, installs packages or takes screenshots needs an explicit capability. Default-deny.
20. **No file claims: each role has its own clone and branch.** (changed for the opencode harness, plan §6.4) Advisory file claims are dropped, because there is no shared working tree to claim. Overlap is prevented before it happens: the task board never runs two cards with overlapping `files_hint` at the same time (plan §4.6), and the runner merges `agent/{role}` branches into `main`, never the agents.
21. **`inputs/` is read-only at runtime.** Only the runner writes there, once, at initialization. The meta-agent reads `inputs/README.md` first before proposing a team. See DESIGN §33. (changed for ongoing projects, plan §13) Runs are sessions of ongoing project work: every run starts from the project repo's `main` with full history (not a fresh history), its changes reach `main` only when a human accepts the run, and work items live on the persistent project backlog.
22. **Memory is dynamic, not append-only-forever.** Patterns have lifecycle states (emerging/active/declining/archived/superseded). Confidence is recency-weighted (90d half-life default). Failure clusters narrow `applies_when` before decaying global confidence. Patterns can flip to anti-patterns. Reconciliation runs in the memory_writer at end-of-run. See DESIGN §34.
23. **Training and production reputation are segregated; episodes are unified.** Both feed `recall()`; reputation aggregates split. Training mode is a flag, not a separate runner. Graduation is a soft gate. See DESIGN §35.
24. **Project rules live with the project, not the persona.** `data/projects/{id}/persona_rules/{persona_id}.md` and `_shared.md`. Loaded at Layer 3.7 of `effective_system_prompt()`, ABOVE memory. Rules trump memory on conflict. Auto-suggestion never auto-applies. See DESIGN §36.

Rules 1–12, 14–17 and 21–24 are unchanged in substance. Notes on how they read under the opencode harness:
- Rule 4 (`BaseAgent`) and rule 5 (stateless tools) describe the legacy harness. In the opencode harness the equivalent contract is `OpencodeRun` driven by the scheduler (`core/harness/run.py`); host-side MCP tools stay stateless apart from per-run state created in their `tools(ctx)` closure.
- Rule 6 (MCP is additive) is about third-party MCP servers: core still works with none configured. GuudFlow's own MCP server (`core/harness/mcp_server.py`) is part of the opencode harness, because it is how agents submit results.
- Rule 8 (the loop is the error handler): a bad turn becomes a failed card or role, and the reviewer or the overseer deals with it.
- Rule 14 (signals reference files) holds: results list `files_changed`; the diff lives in git.

---

## Opencode harness rules (added for the opencode harness, plan §6.4)

These apply to everything under `core/harness/`, `gateway/`, `worker/sandbox_manager.py` and the sandbox images. Section numbers refer to `docs/OPENCODE_HARNESS_PLAN.md`.

- **Agents finish with `guudflow_submit_result`.** One spawn is one opencode session and one validated structured result (§4.1–§4.3). GuudFlow does not use opencode's `format` / structured-output setting (§2.2 #12).
- **No synchronous peer communication.** No ASK/ANSWER, no waiting on another agent. Agents leave notes (`post_note`, `notes_for`) or return `blocked` with `blocked_on`; the scheduler re-arms the role that must act. Cycles go to `HUMAN_NEEDED` (§4.4).
- **Only the scheduler moves cards.** Task cards change column on scheduler events, never because an agent wrote text. The planner's validated result is the task list; the runner creates the cards (§4.6).
- **Credentials never enter sandboxes.** Real provider keys stay on the host in the LLM gateway; each sandbox gets a per-(run, role) virtual key. MCP tokens map to a role and are revoked at run end. Search-provider keys are read on the host from an allow-listed set of environment variables and never reach a sandbox (§5.4, §5.6, §5.8).
- **Host tools via MCP.** Web search and fetch, screenshots, `visual_diff`, package installs and board tools run on the host behind the MCP server. opencode's own `webfetch` / `websearch` stay denied for every role, and no sandbox has internet egress (§5.8, §5.9).
- **Capabilities decide what a role may do.** Set on the team approval screen before the run, recorded as a `CAPABILITIES` signal at every spawn, narrowing only at runtime. Widening needs a human and applies from the next spawn (§5.7).
- **Protected paths.** `inputs/`, `.github/`, `opencode.json`, `.opencode/` and `.guudflow/` can never be changed by an agent branch. The runner fails the turn and does not merge (§5.5).
- **The escape suite must pass.** `tests/harness/test_escape_suite.py` (with `test_run_docker.py`) runs hostile code in a real sandbox and asserts on the host. Run it on every change to `core/harness/`, `worker/`, `sandbox/` or `gateway/`; CI runs it in its Docker job (§7 P3, §8).
- **Host mode is for trusted local demos only.** `sandbox: host_dev` / `--sandbox host` runs agents on the host with a warning on every spawn, and is refused when `GUUDFLOW_ENV=production`. Production requires Docker.
- **No concurrency outside the scheduler.** The harness adds no `asyncio.gather`. The overseer decides and the scheduler acts (§11).

---

## Code standards

**Language:** Python 3.11+ for backend, React + Vite for frontend

**Types:** Full type hints everywhere. No `Any` unless genuinely unavoidable with a comment explaining why.

**Async:** Everything that could block is async. Use `asyncio` throughout.

**Data structures:** Dataclasses for internal data, Pydantic models for API boundary only.

**No global state** except the job queue instance and registry — both explicitly initialized at startup.

**Imports:** Absolute imports only. No relative imports in core/.

**Error handling:** Agents catch their own exceptions and post a FAILED signal.
Core modules (scheduler, runner) let exceptions propagate to the runner which handles them.
Never swallow exceptions silently.

---

## File structure

Follow DESIGN.md section 8 exactly. Key files:

```
core/signals.py          ← build first, everything imports from here
core/workspace.py        ← build second
core/scheduler.py        ← most important file in the project
agents/base.py           ← third
agents/tools/base.py     ← before any tool implementations
agents/registry.py       ← simple dict, populated at startup
worker/queue.py          ← abstraction layer, in-memory default
worker/job_runner.py     ← reads queue, runs AgentRunner
api/main.py              ← mounts all routers, initializes queue
```

---

## Python dependencies to add

Add to `requirements.txt`:
- `gitpython==3.1.43` — git operations from Python
- `docker==7.1.0` — Docker SDK for Python (worker container management)
- `tree-sitter==0.21.3` — repo map parsing, zero external deps
- `tree-sitter-python==0.21.0` — Python language grammar for tree-sitter
- `tree-sitter-javascript==0.21.0` — JS/TS grammar for tree-sitter
- `numpy==1.26.4` — embedding storage and similarity search (if embeddings enabled)
- `sentence-transformers==2.7.0` — local embedding model, no API call needed (optional, only if embeddings enabled, lazy-loaded)

---

## What NOT to build yet

- Auth / user accounts
- Payment / usage tracking  
- UICanvas (section 15 — future only)
- Full Docker deployment config (section 16) — skip for now
- Persona sharing / marketplace (section 26) — future
- Persona cloning UI — data model supports it, UI can wait
- The `extensions/` folder already has README and example — do not add more there yet

## Security rules — enforced from day one

See DESIGN.md section 28 for full detail. Short version:

- **Dev mode**: `code_exec` may run on host. Log a warning every time it does.
  `⚠️  DEV MODE: code_exec running on host. Do not use with untrusted input.`
- **Production**: `code_exec` MUST run inside the sandbox container. The worker
  must refuse to start in production mode without Docker available.
- **FileWorkspace**: `_safe_path()` check is mandatory on every operation.
  Never skip it. Never trust agent-provided paths.
- **Prompt injection**: external file content is always wrapped in delimiters
  before being included in any prompt. Never f-string file contents directly.
- **API keys**: never passed into containers. Runner makes all LLM calls on
  the host side. Containers have `network_mode: none`.

## What to act on from day one even though full feature is later

These are foundational decisions that are painful to retrofit:

1. **Workspace artifacts are typed** (`Artifact` dataclass, not `dict[str, str]`)
   — Required for UICanvas and other structured artifact types later.

2. **FileWorkspace exists from the start** — Even basic agents in MVP write
   their output to the run's working directory. Don't pass file contents
   through the artifact string.

3. **Personas are persistent entities** — `data/personas/{persona_id}/` with
   profile.json, memory.json, history.json, reputation.json. Even if the
   first runs use freshly-created personas with empty memory, the storage
   structure must exist from day one. Migrating later is much harder.

4. **The memory_writer agent runs after every successful run** — Even if its
   first version just appends raw feedback to memory.json without sophisticated
   extraction, the hook must exist from day one so memory accumulates from
   the very first run.

---

## Testing

Every file in `core/` needs a test file in `tests/core/`.
Every file in `agents/` needs a test file in `tests/agents/`.

Scheduler tests are the most critical. Cover:
- Sequential chain: A → B → C
- Parallel fan-out: A → (B + C simultaneously)
- Join: (A + B) → C (C waits for both)
- Human pause: mid-run HUMAN_NEEDED stops new spawns, running agents finish
- Group completion: group COMPLETE unblocks downstream pipeline agents
- Max iterations cutoff

The opencode harness adds scheduler tests in `tests/core/test_scheduler_harness.py`: review edge and
exhausted rounds, blocked re-arm and blocked cycle → human, parking, `max_parallel`, `has_work`
gating and the stall watchdog.

Use `pytest` + `pytest-asyncio`. Mock LLM calls — never make real API calls in tests. The harness
tests under `tests/harness/` use a scripted fake OpenAI-compatible LLM (`tests/harness/fake_llm.py`)
and, where marked, a real pinned `opencode` binary (`scripts/install_opencode.sh`); they skip cleanly
when the binary or Docker is missing and need no API key.

---

## Token efficiency rules

These keep costs down during development and keep Claude Code sessions focused:

- **Never rewrite a file that only needs a small edit.** Use targeted edits.
- **Never reproduce large existing files in full when referencing them.** Cite by filename and line number.
- **Build core/ first** — it has no dependencies and can be tested without the API or frontend running.
- **Test incrementally.** Run `pytest tests/core/` after each core module before moving on.
- **Don't generate placeholder implementations.** If something isn't being built yet, leave it absent — not stubbed with `pass` and a comment.
- **Consolidate related changes.** If editing workspace.py and scheduler.py in the same session, do both before running tests — don't round-trip after each file.

---

## Environment setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
scripts/install_opencode.sh   # pinned opencode binary into .tools/opencode (needs node/npm)

cp .env.example .env
# fill in ANTHROPIC_API_KEY at minimum

# run backend
uvicorn api.main:app --reload --port 8000

# run worker (separate terminal)
python -m worker.job_runner

# run frontend
cd studio && npm install && npm run dev
```

---

## Session startup prompt (use this to start each Claude Code session)

> Read CLAUDE.md then DESIGN.md fully. Check what files currently exist in the project.
> Tell me which section of the build order we are at and what the next logical task is.
> Do not write any code until you have confirmed the current state.

---

## Model Tier Rules

From DESIGN.md section 22.

- Every agent config must have a `model_tier` field: `heavy` | `solid` | `fast`
- `escalation_model` is optional — if absent, escalation upgrades one tier automatically
- Run profiles (BUDGET/BALANCED/QUALITY/CUSTOM) assign models across all agents — implement as a preset that populates individual agent model fields, not a global override
- Live cost estimate in studio updates on every model change — use estimated tokens per role type x max_iterations, not real billing
- Dynamic escalation is handled by the runner — agents never change their own model
- Quip generator always uses FAST tier regardless of run profile — no exceptions
- Overseer always uses HEAVY tier — it activates rarely and needs genuine reasoning (changed for the opencode harness, plan §6.4). It replaces the tech lead (plan §11.1); the legacy tech lead had the same rule.

## Escalation & Quip Board Rules

From DESIGN.md section 23.

- Escalation is a scheduler review edge (changed for the opencode harness, plan §6.4): rework is `review_of` + `max_review_rounds`. When the rounds run out an `ESCALATION` signal is posted and the overseer wakes (plan §4.5, §11). The legacy 2/3/4 rejection thresholds in project config apply to the legacy harness only.
- The overseer replaces the tech lead (changed for the opencode harness, plan §6.4). It is event-driven, has no `depends_on`, and is woken by the scheduler's deterministic stuck detectors (no progress, same review issue, blocked chain, burn without output, permission thrash, repeated validation failure, card aging, escalation). It has no side channels and writes nothing but its structured result (plan §11.1, §11.2).
- The overseer decides, the scheduler acts. Its fixed intervention menu is `rebrief`, `decide`, `reassign`, `split`, `extend_review`, `restart_role`, `pause`, `propose_team_change`; only `propose_team_change` needs a human. Wakes and interventions are capped per wake and per run, and if the overseer fails or its wakes run out the run goes to `HUMAN_NEEDED`. Every intervention is posted as an `OVERSEER_INTERVENTION` signal with its rationale (plan §11.3).
- After each run the runner writes a deterministic retrospective (`core/harness/retrospective.py`), a carry-forward for the next run, and an evidence-backed update to the project playbook (`data/projects/{id}/playbook.json`). Playbook entries follow rules 16 and 22 (evidence runs, recency-weighted confidence, lifecycle). They only inform the meta-agent's next proposal and never change a team without human approval (rule 24) (plan §11.4).
- Quip board is a completely separate log — never included in agent context, never read by agents
- Quip generation is a secondary LLM call triggered by the runner after significant events — not inside agent run() methods
- Teammate brief is generated at run start from reputation.json stats only — no subjective content
- Teammate brief is Layer 3.5 in effective_system_prompt() between character and stake framing
- The quip log is surfaced in the Run view as a secondary feed — always visible, auto-scrolling

---

## LLM Psychology Rules — Persona Prompts

From DESIGN.md section 20. These are functional requirements, not decoration.

- `effective_system_prompt()` MUST build in the exact 7-layer order defined in section 20
- Layer order is: identity → character → stake framing → memory → base prompt → CoT primer → positive framing
- Never access `self.system_prompt` directly — always call `effective_system_prompt()`
- `stake_framing` is injected as-is — keep it one sentence, present tense, specific to role type
- Memory is rendered via `persona.memory.render_for_prompt()` — structured bullets, never prose dump
- The encouragement line between iterations is injected by the runner, not the agent — agents do not self-encourage
- "Make this something you are proud of." is the final line of every effective system prompt, always
- Character descriptions should describe working style, not just knowledge — "methodical and thorough" not "knows React"
- Chain of thought primer is always included — it costs tokens but reduces iteration count which saves tokens overall
- Opencode harness (plan §5.11): a persona-backed role's system prompt is built by `core/harness/persona_prompt.py: build_agent_prompt()`. It reuses the persona layers (identity, character, stake framing, teammate brief, manifesto, project rules, project context, recalled memory, base prompt with the legacy protocol instructions stripped, CoT primer, positive framing) and drops what opencode or the harness now owns (repo map, tool catalog, protocol voice, autonomy rules). "Make this something you are proud of." is still the last line. The board records the prompt as a hash plus layer names, never the memory text.

---

## Persona Memory Rules — The Three-Layer Model

From DESIGN.md section 26. This is the moat — get the structure right from day one.

- Persona storage: `profile.json`, `episodes.jsonl`, `patterns.json`, `style.json`, `reputation.json`, `history.json` — six files per persona, never collapsed into one
- `episodes.jsonl` is JSONL (line-delimited), append-only — every run produces an episode entry
- `patterns.json` entries MUST include `source_episodes` array — patterns without source episodes are invalid
- Pattern confidence decays on contradiction: applied + succeeded = +0.05, applied + failed = -0.15, below 0.5 flag for review
- Agents call `recall(task_description)` to access memory — NEVER read raw files directly
- The injected memory subset is contextual, retrieved by similarity to current task — never inject the full patterns.json
- `style.json` is updated incrementally per run — tendency scores drift toward observed behaviour
- The `memory_writer` is SOLID tier (Sonnet-class) — it does genuine synthesis, not templated extraction
- Episodes accumulate quickly (raw truth), patterns accumulate slowly (only when 3+ episodes share a theme)
- Embeddings on episodes are optional — tag-based + keyword similarity is the default retrieval mechanism

---

## Context Management Rules — Token Cost Control (changed for the opencode harness, plan §6.4)

From plan §5.9 (replaces DESIGN.md section 28 for the opencode harness).

- opencode owns context: file reading, compaction (`compaction` config), LSP diagnostics, provider retries and todo tracking. GuudFlow does not build agent context.
- `core/context_manager.py` and `core/repo_map.py` (`get_context()`, repo map, relevance ranking, `include_diff`) belong to the legacy harness and retire with it. Do not use them from `core/harness/`.
- The brief carries only what opencode cannot know: the goal, the card (title, acceptance checklist, dependency results, thread, `visual_refs`), notes addressed to the role, review issues and the carry-forward brief. It ends with how to finish (`guudflow_submit_result`).
- Card to card (plan §11.1): each card starts a fresh opencode session; what it needs from earlier cards comes from handoffs (`core/harness/handoff.py`: the worker's `AgentResult.handoff` plus git facts), stored on the card thread. A session is kept only when the next card continues the last one, the cache is warm and the context is small (`core/harness/session_policy.py`). Reviewers always start fresh.
- Anything that did not come from GuudFlow or the human (web research, `research/notes.md`, other agents' free text, fetched pages, images) is wrapped in untrusted-content delimiters before it enters a brief.
- Cost is measured at the gateway, not estimated from iterations: each role's tokens and cost (image tokens separately) go to the run's `llm_costs.jsonl` and into `AGENT_RESULT`. Per-turn limits are opencode `steps`, `doom_loop: deny`, a per-turn wall clock that aborts the session, the gateway spend cap per role and run, and the scheduler's `max_iterations` and deadline.
- `explore` subagents (capability `subagents: explore_only`, depth 1) do cheap read-only search in the role's own clone; their cost still flows through the gateway.
- Embeddings are OFF by default and stay optional for persona memory recall.

---

## Result & Communication Rules — Structured Results (changed for the opencode harness, plan §6.4)

From plan §4.2–§4.6 (replaces the Protocol Rules, DESIGN.md section 18, for the opencode harness).

**The rule:** agents work in natural language inside opencode. They report to GuudFlow in typed results and leave async notes. Nothing parses free text.

- Results are Pydantic models in `core/harness/results.py`, `tasks.py` and `overseer.py`: `AgentResult`, `ReviewResult`, `PlanResult`, `OverseerResult`. They replace the `[COMMIT]` / `[VERIFY]` / `[ASK]` blocks. `core/protocol.py` and `emit_commit()` / `emit_verify()` / `read_protocol()` belong to the legacy harness.
- A role submits its result by calling the MCP tool `guudflow_submit_result`. The payload is validated inside the tool call, and validation errors go straight back to the model in the same turn.
- A turn that ends with no accepted result gets exactly one follow-up message in the same session. A second miss fails the turn with the validation error as its detail (rule 8).
- A failing verdict or unmet acceptance item MUST carry specifics: `Issue.detail` and an unmet `ItemVerdict.detail` need at least 20 characters, and a `pass` cannot have unmet items. This is a validator, not a regex.
- No synchronous peer communication. Notes go on the card thread (`post_note`, `notes_for`) and are read in the recipient's next brief. A role that cannot continue returns `status: "blocked"` with `blocked_on`; the scheduler re-arms the role it needs, bounded by `max_iterations`.
- Review is a scheduler edge, not an agent-to-agent exchange.
- The event bridge (`core/harness/event_bridge.py`) turns opencode events into board signals; agents do not post signals themselves. Studio renders structured results and cards, so users never see raw JSON or protocol text.
- What goes to humans (`ask_human`, visual feedback, deliverable descriptions) stays in plain language.
- The schemas check shape and required specifics. They do not replace review: the reviewer and the overseer catch bad output.

---

## Git & IT Rules — Non-Negotiable (changed for the opencode harness, plan §6.4)

From plan §5.4 and §5.5 (replaces DESIGN.md sections 19 and 21 for the opencode harness).

**Git:**
- Every project has one long-lived repo (`data/projects/{id}/repo.git`, plan §13). A run's repo is a clone of its `main`; at run end the run is stored as `runs/{run_id}` and merged into the project's `main` only when a human accepts it. Agents and runs never push to the project repo.
- Every run has one run repo. Every role works in its own checkout under `<run_root>/roles/<role>/` on its own branch `agent/{role}`. The worktree (`work/`, mounted at `/work`) has no `.git`; the git directory is host-only and never mounted, so nothing the agent writes can configure git on the host.
- Agents only ever change their own branch. They have no remote credentials, `git push` is denied, and they never merge or touch `main` or another role's branch.
- After each turn the runner (host side) commits leftover changes with a conventional message, fetches only `agent/{role}`, checks it (protected paths, symlinks, `core/secret_scan.py`), and merges it into `main`. A failed check means no merge and a failed turn with the reason; the branch is then *set aside* (reset to `main`, patch kept at `.guudflow/set_aside/`, reason in the next brief) so it never poisons later turns.
- Each spawn starts with `main` merged into the role's branch. A conflict there, or when merging the branch home, goes back to the agent: the markers stay in the worktree and the agent resolves them (in the brief, or in one follow-up in the same session). Markers are never merged; unresolved work is set aside.
- Commit messages use conventional commits format (`feat:`, `fix:`, `test:`, `docs:`).
- The full `.git/` repo is included in the run's deliverable output.

**Packages (the IT agent's job moved to a host tool):**
- No role may run `pip install`, `npm install`, `curl` or `wget`. The generated ruleset denies them, and sandboxes have no internet anyway.
- Roles with `packages: via_it_agent` call the host-side MCP tool `request_package(manager, name, version)`. Packages on the project allow-list with pinned versions are installed on the host into the role's tool directory (`pip install --only-binary=:all:`, `npm install --ignore-scripts`, so no install script runs). Anything else becomes an approval request for a human.
- Installed files go into the host-written tool directory `.guudflow/` (read-only to the agent, git-excluded). Every request, approval and rejection is a `PACKAGE_REQUEST` / `PACKAGE_APPROVED` / `PACKAGE_REJECTED` board signal, and each run starts clean.
- The `it_agent` role type still exists but is not needed for allow-listed installs.

---

## User Interaction Rules — Three Modes, Never Confuse Them

From DESIGN.md section 6. This distinction must be maintained everywhere.

| Mode | Method | Urgency | Effect |
|------|--------|---------|--------|
| Hard pause | `ask_human()` | Blocks new spawns | Agent suspends, waits for answer |
| Visual review | `request_visual_feedback()` | Blocks calling agent | Screenshot taken on host, user annotates |
| Ambient comment | `check_user_board()` | Non-blocking | Absorbed at next step boundary |

**Rules:**
- `check_user_board()` must be called between every major step in every agent's `run()` method
- `request_visual_feedback()` triggers Playwright on the **host runner**, never inside the container
- Screenshots are served to the browser via FastAPI static file endpoint — never base64 in WebSocket
- `seen_by` on USER_COMMENT signals must be updated when an agent absorbs a comment — never process the same comment twice
- The Run view must always show the persistent comment input — never hide it while agents are running
- High priority comments (`USER_COMMENT_PRIORITY`) are checked before expensive LLM calls mid-step

---

## The Scheduler is the Critical Path

This section exists because it needs more than a one-line mention.

`core/scheduler.py` is the single most load-bearing file in the project.
Every execution pattern — sequential chains, parallel fan-out, joins, working
group scoping, human pause, max iteration cutoff — is implemented entirely here.
If the dependency resolution logic is wrong, all of those patterns break.
Not some of them. All of them.

### Treat it as a hard gate

Do not move to `agents/` until all of the following are true:

- [ ] Sequential chain test passes: A → B → C, each waits for previous
- [ ] Parallel fan-out test passes: A → (B + C), both start simultaneously when A completes
- [ ] Join test passes: (A + B) → C, C waits for both A and B
- [ ] Human pause test passes: HUMAN_NEEDED stops new spawns, running agents finish, resumes on HUMAN_RESPONDED
- [ ] Group completion test passes: group-scoped COMPLETE unblocks pipeline-level dependents
- [ ] Max iterations test passes: run halts cleanly when limit is reached
- [ ] No asyncio.gather calls exist outside this file

If any test fails, fix the scheduler before proceeding. Do not work around a
broken scheduler by adding logic elsewhere. The fix belongs here.

### Why getting this right first saves everything downstream

Every agent, every tool, every API route assumes the scheduler works correctly.
If you build agents on top of a broken scheduler you will see mysterious failures
that look like agent bugs but are actually orchestration bugs. They are extremely
hard to trace back to the scheduler once the rest of the codebase exists.

One clean scheduler + full test suite = confident foundation.
Proceed only when you have that.
