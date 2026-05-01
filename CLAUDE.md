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

---

## Token Budget Per Stage

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
13. **All file I/O goes through FileWorkspace.** No raw filesystem APIs in agents.
14. **Signals reference files, not contain them.** Agents post `files_written` paths, others read from disk.
15. **Personas persist across runs.** Profile, memory, history, reputation are independent of any project.
16. **Memory writes are conservative.** Only record lessons backed by specific evidence.
17. **Credentialed calls go through the broker.** Agents never hold API keys, OAuth tokens, or MCP secrets. All credentialed access routes through the Credentials Broker via `request_credential()` or `invoke_mcp()`. RBAC is default-deny. See DESIGN §30.
18. **One tool entrypoint.** Every tool call goes through `BaseAgent.call_tool(name, arguments)`. Agents never import tool classes directly and never call MCP transports themselves. The agent's `ToolManifest` is the source of truth at runtime. See DESIGN §31.
19. **Built-in tools are namespaced.** `fs:*`, `doc:*`, `code:*`, `web:*`, `git:*`, `vis:*`, `mcp:*`. RBAC scope names match tool names 1:1. Read tools (`fs-read`, `fs-list`, `git-diff`, `doc:*`, `web-fetch`, `web-search`) are baseline; write tools require explicit RBAC scope. See DESIGN §31.
20. **File claims are advisory; branches are the safety net.** Write tools (`fs-write`, `fs-edit`, etc.) auto-claim before the operation and release after commit. Default behaviour when another agent's claim is active is to wait. Crash → synthetic release. See DESIGN §32.
21. **`inputs/` is read-only at runtime.** Only the runner writes there, once, at initialization. Continuation runs preserve persona memory, not git history. The meta-agent reads `inputs/README.md` first before proposing a team. See DESIGN §33.
22. **Memory is dynamic, not append-only-forever.** Patterns have lifecycle states (emerging/active/declining/archived/superseded). Confidence is recency-weighted (90d half-life default). Failure clusters narrow `applies_when` before decaying global confidence. Patterns can flip to anti-patterns. Reconciliation runs in the memory_writer at end-of-run. See DESIGN §34.
23. **Training and production reputation are segregated; episodes are unified.** Both feed `recall()`; reputation aggregates split. Training mode is a flag, not a separate runner. Graduation is a soft gate. See DESIGN §35.
24. **Project rules live with the project, not the persona.** `data/projects/{id}/persona_rules/{persona_id}.md` and `_shared.md`. Loaded at Layer 3.7 of `effective_system_prompt()`, ABOVE memory. Rules trump memory on conflict. Auto-suggestion never auto-applies. See DESIGN §36.

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

Use `pytest` + `pytest-asyncio`. Mock LLM calls — never make real API calls in tests.

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
- Tech lead always uses HEAVY tier — it activates rarely and needs genuine reasoning

## Escalation & Quip Board Rules

From DESIGN.md section 23.

- Escalation thresholds are stored in project config with defaults: 2/3/4 rejections
- Tech lead is event-driven — same pattern as IT agent, wakes on escalation signal, no depends_on
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

## Context Management Rules — Token Cost Control

From DESIGN.md section 28. This is the most important cost control mechanism.

- Agents NEVER call `file_read` directly to understand the codebase — always `get_context()`
- `get_context(task_description, max_tokens)` is called ONCE at the start of each reasoning step
- Repo map is ALWAYS included in every context — it is cheap and always useful, never skip it
- Full file contents are included only up to the token budget — highest relevance first
- On iteration 2+, `include_diff=True` by default — diffs replace full file re-reads
- Token counts returned by `get_context()` MUST be logged by the runner per agent per call
- Embeddings are OFF by default — symbol search handles most cases without extra setup
- `core/repo_map.py` is rebuilt incrementally after every GIT_COMMIT — not from scratch
- Token budget defaults: HEAVY=12k, SOLID=6k, FAST=2k — agents can request more for specific steps
- `search_codebase(query)` works without embeddings via symbol search — never require embeddings

---

## Protocol Rules — Inter-Agent Communication

From DESIGN.md section 18. This is the most important efficiency decision in the system.

**The rule:** Agents reason internally in natural language. They communicate externally in protocol.

- `core/protocol.py` is built alongside `core/signals.py` at step 1 — they are peers
- Every agent output posted to the board MUST use a protocol render helper
- `emit_commit()`, `emit_verify()`, `emit_delta()`, `emit_action()` are the only valid ways to post work-related board messages
- Agents read the board via `read_protocol()` which returns parsed objects — never raw text parsing
- `FAIL` results in `emit_verify()` MUST include a specific detail string — "doesn't work" is invalid, Claude Code should raise a ValueError if detail is missing on FAIL
- Protocol messages are rendered as structured visuals in the UI — users never see raw protocol text
- What goes to humans (`ask_human`, visual feedback, deliverable descriptions) stays in plain language
- The protocol does NOT prevent bad outputs — the multi-stage review architecture does. Do not add verification logic inside the protocol layer itself.

---

## Git & IT Rules — Non-Negotiable

From DESIGN.md sections 18, 19, and 21.

**Git:**
- Every run initialises a git repo in `workspace/working/` at run start
- Every agent role gets exactly one branch: `agent/{role}`
- Agents only commit to their own branch — never to main or another agent's branch
- Agents read other branches via `git_read()` only — never checkout
- Reviewer agents use `git_diff()` in iteration 2+ — never re-read the full codebase
- The runner handles all merges — never agents
- Commit messages use conventional commits format (`feat:`, `fix:`, `test:`, `docs:`)
- The full `.git/` repo is included in the run's deliverable output

**IT Agent:**
- No agent may call `pip install`, `npm install`, or any package manager directly
- All installs go through `self.request_package()` which routes to the IT agent
- The IT agent is event-driven — no `depends_on`, wakes on `PACKAGE_REQUEST`
- Auto-approve packages on the project allow-list with pinned versions
- Packages not on the allow-list route to human approval
- Every approved install is recorded in workspace state
- Runtime installs do not persist to the sandbox image — each run starts clean

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
