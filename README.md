# GuudFlow

A managed multi-agent orchestration platform. Describe a goal, a meta-agent
proposes a team, you approve it, and the team runs — with a shared
workspace, a shared git repo, structured inter-agent communication, and a
sandboxed code-execution surface.

**Status: phases A, B, C, D-prime, D, F, G-prime, G, K, milestones
M0–M6, and Tracks 1, 3, 4, 7, 8, 9, 11, 12 shipped — backend tests +
studio typecheck/build green.**
Recent: subtask decomposition with active-subtask prompt layer
(Track 7), per-subtask context hot-swap + recall (Track 8), protocol
cleaner sidecar that auto-fires on parse failure (Track 9), live
per-agent Workers cognition view (Track 11), sub-LLM dispatch for
cheap-model use cases (Track 12), explicit `manages` / `escalates_to`
hierarchy with a conditional DISPATCH RULE prompt layer, ask-peer
wrapper around the M1 [ASK] protocol, `CONTEXT BEFORE OUTPUT` prompt
layer, `estimated_cost_usd` per iteration, four Mistral models added
(9 models total across 7 providers). Phases E (production deploy +
multi-tenant), I (headless service), F.5 (persona author depth), J
(knowledge surface) registered as future work; see
[ROADMAP.md](ROADMAP.md). User stories driving priorities are in
[docs/USER_STORIES.md](docs/USER_STORIES.md).

**Architecture diagrams** — ASCII UML, sequence, state, deployment, and
prompt-stack diagrams reflecting the current (post-M4) shape of the
codebase live in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Diagrams
render anywhere — phones, terminals, plain markdown viewers — without
needing a Mermaid runtime. Read that before `DESIGN.md` if you want the
live picture; read `DESIGN.md` for original intent + rationale.

## What it actually is

```
You              "Add JWT auth to my web app, with refresh tokens"
   ↓
Meta-agent       proposes a team: developer + reviewer + tech_lead-on-escalation
                 with model tiers, tools, RBAC scopes, manages /
                 escalates_to hierarchy
   ↓
You approve      tweak agents, model tiers, see live cost estimate
   ↓
Run starts       agents work in parallel where deps allow, share a
                 board, commit to per-agent git branches, escalate on
                 conflicts, ask you (or each other via `ask-peer`) for
                 input when genuinely blocked. Planners decompose work
                 into subtasks (Track 7); each agent sees only its
                 active subtask's context (Track 8). Live pipeline
                 graph, Kanban-style task desks, per-agent cognition
                 view, and a quip stream in the studio.
   ↓
Reviewer         approves → branches merge → memory_writer records what
                 worked into the personas' files for next time. Each
                 APPROVED merge is auto-tagged as a review point.
   ↓
If a run fails   Resume from the persisted state — completed agents
                 don't re-run. Per-iteration cost + token telemetry is
                 written to `iteration_metrics.jsonl` for postmortems.
```

**Beyond the run loop:** training camp (toy scenarios + tournaments
+ ranking board), persona chat for one-off consultation, a roster
review UI with reputation + graduation status, persona desks
(role-template knowledge handoff), and an office-wide manifesto layer
that prepends to every agent's system prompt. First-timers get seed
personas (Mike, Sarah, Alex) and a "Try demo" button.

**Inter-agent protocol.** Agents communicate through a structured
handshake — `COMMIT`, `VERIFY_RESULT`, `ASK / ANSWER`,
`REQUEST_SKIP / SKIP_GRANTED / SKIP_DENIED`, `STORY_OPENED / CLOSED`.
The scheduler enforces one-outstanding-ASK and parks agents via
`Scheduler.interrupt()` / `resume()`. Peer waits and skip
confirmations have wall-clock deadlines (`PEER_TIMEOUT`,
`SKIP_TIMEOUT`).

**Multi-model.** Nine models across Anthropic (Opus 4.7 / Sonnet 4.6 /
Haiku 4.5), Groq (Llama 3.1), DeepSeek, Mistral (Large / Medium /
Small / Codestral) — see `data/models.json`, seven providers total
including local endpoints (Ollama, LM Studio). Tier resolution
(`heavy` / `solid` / `fast`) plus per-agent `model_id` override
(persisted by the studio's ModelPicker). Sub-LLM dispatch (Track 12)
routes cheap-model use cases like protocol-cleanup and
summarise-subtask through their own model picks. Anthropic prompt-
cache hits across iterations on the stable system-prompt prefix; the
Anthropic `max_tokens` default was bumped 4096 → 8192 to make
batched tool calls survive `stop_reason="max_tokens"`.

The high-level architecture is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (ASCII diagrams,
current-state). The full intent / rationale is in
[DESIGN.md](DESIGN.md) (37 sections, plus a refactor addendum at the
end). Build plan and stage-by-stage progress is in
[`.claude/progress/`](.claude/progress/).

## Run it locally

You need Python 3.11+, Node 18+, and (for production sandbox)
Docker. Three commands:

### 1. Backend

```bash
# Linux / macOS / WSL
./scripts/setup.sh

# Windows PowerShell
./scripts/setup.ps1
```

This creates a venv, installs Python deps, runs `playwright install
chromium`, and copies `.env.example` → `.env` if it doesn't already
exist. Open `.env` and add your `ANTHROPIC_API_KEY`.

> **Optional:** semantic embeddings (PyTorch + sentence-transformers,
> ~600 MB) are off by default per DESIGN §28. To enable, set
> `GUUDFLOW_WITH_EMBEDDINGS=1` before running setup, or
> `pip install -r requirements-optional.txt` separately later.

Then start the API + in-process worker:

```bash
source venv/bin/activate     # or: venv\Scripts\Activate.ps1 on Windows
uvicorn api.main:app --reload --port 8000
```

The API serves at `http://localhost:8000`. Health check:
`http://localhost:8000/health`.

### 2. Frontend

```bash
cd studio
npm install
npm run dev
```

Studio runs at `http://localhost:3000` and proxies `/api` and `/ws` to
the backend on `:8000`. Paste your API token (whatever you set in
`GUUDFLOW_API_TOKEN`) in the top-right field.

### 3. (Optional) Sandbox image

In dev mode this is optional — `code-exec` runs on the host with §29
hardening (env scrub, cwd pin, audit log, POSIX rlimits, mode
enforcement).

For production-style runs, build the per-run sandbox image:

```bash
./scripts/build_sandbox.sh
```

Then set `GUUDFLOW_MODE=production`. The worker now routes every
`code-exec` invocation through `DockerManager.exec_in()` against the
run's ephemeral container — `network_mode: none`, read-only root,
512 MiB memory cap, 1 CPU, 50 PID cap, no-new-privileges, tmpfs `/tmp`
(64 MiB). See [DESIGN.md §29](DESIGN.md) for the full security model.

Misconfigured production deploys fail closed: if the worker reaches
production mode without a Docker daemon (or without the sandbox image),
it refuses to start. If somehow `GUUDFLOW_MODE=production` is set
without a `DockerManager`, `code-exec` itself refuses to run on the
host.

## Run it with Docker

If you'd rather not install Python/Node directly:

```bash
# 1. Build the per-run sandbox image (one-time)
./scripts/build_sandbox.sh

# 2. Start the host stack
docker compose up --build
```

The compose stack:
- Builds the host image (multi-stage: builds studio, then runs the API
  with the bundle at `/`)
- Mounts `./data` as the persistent run/persona directory
- Mounts `/var/run/docker.sock` so the host container can spawn sandbox
  containers as siblings (NOT nested — see DESIGN §29 / k8s notes in
  [ROADMAP.md](ROADMAP.md))
- Exposes the studio + API at `http://localhost:8000`

Set environment variables in `.env` (the compose file reads it):
- `ANTHROPIC_API_KEY` (required for real LLM calls)
- `GUUDFLOW_API_TOKEN` (optional — required for production)

## Project layout

```
guudflow/
├── core/              ← signals, protocol, workspace, scheduler, runner,
│                        llm/ (split package), loop_guard, edit_blocks,
│                        run_initializer, run_loader, training/, ...
├── agents/            ← BaseAgent, abstract archetypes, persona,
│                        persona_desk, persona_chat, registry, catalog,
│                        tools/, builtin/
├── api/               ← FastAPI app + routes + WebSocket
├── worker/            ← JobRunner, queue, DockerManager
├── studio/            ← Vite + React + TS frontend
├── sandbox/           ← per-run sandbox Dockerfile (§29)
├── data/              ← personas, projects, runs, role_templates,
│                        manifestos, models.json, training_camp/
├── tests/             ← pytest suite
├── scripts/           ← setup + sandbox build helpers
├── docs/              ← USER_STORIES, worker-archetypes,
│                        ARCHITECTURE.md + diagrams/
├── DESIGN.md          ← full architecture (37 sections + refactor addendum)
├── CLAUDE.md          ← operating instructions for AI assistants
└── ROADMAP.md         ← what's next
```

## Configuration

Most behaviour is environment-driven. Reasonable defaults; override what
you need.

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | (unset) | Required for real LLM calls |
| `GUUDFLOW_API_TOKEN` | (unset) | Bearer token for credentialed routes. Unset = dev-permissive; required in production |
| `GUUDFLOW_MODE` | `dev` | `dev` or `production`. Production refuses to start without Docker; gates `code-exec` to container path |
| `GUUDFLOW_DATA_DIR` | `./data` | Where runs, personas, and projects are stored |
| `GUUDFLOW_DISABLE_INPROC_WORKER` | unset | Set to `1` to disable the in-process worker (use a separate worker process instead) |
| `GUUDFLOW_CORS_ORIGINS` | `*` | Comma-separated CORS origins |
| `GUUDFLOW_STUDIO_DIST` | unset | If set to a directory of static files, the API serves them at `/` |

## Testing

```bash
# Backend
pytest

# Frontend typecheck
cd studio && npm run typecheck
```

Backend suite runs in ~35 seconds. No real LLM calls (all mocked). No
Docker required (DockerManager dev-mode is a stub). Test gate after the
M1 milestone landed at 980 passed / 2 skipped / 0 failures; current
counts are tracked in the latest milestone commit messages.

## What's missing / what's next

**[ROADMAP.md](ROADMAP.md)** has the prioritized list. Mode A (the
interactive studio for the developer-as-user) is the headline product;
Mode B (headless service) is a future tack-on per
[docs/USER_STORIES.md](docs/USER_STORIES.md).

Shipped:
- ✅ Phase B — production `code-exec` through the sandbox container
- ✅ Phase C — live runs (comments, quip stream, escalation, architect review, approval gates)
- ✅ Phase D — real meta-agent proposal (reads inputs + roster + 16-archetype catalog)
- ✅ Phase F — Training Camp + persona chat + memory-writer LLM synthesis
- ✅ Phase G-prime + G — run resume + studio polish (roster, pipeline graph, onboarding, training tab, review-point tags)
- ✅ Phase K — project roadmap context (Layer 3.8 of the system prompt)
- ✅ M0 — persona bootstrap skeleton + degraded roster entries
- ✅ M1 — handshake protocol (ASK/ANSWER/SKIP/STORY) + roster awareness + edit-block parser
- ✅ M2 — persona desk + manifesto layer + history tracking
- ✅ M3 — multi-model abstraction + error taxonomy + prompt caching
- ✅ M3.5 — per-agent task desks + scope envelopes
- ✅ M4 — Studio UI pass 1 (dashboard, pipeline graph, roster, WorkerCard, QuipFeed)
- ✅ M5 — backend pass (reject loop, escalation, desk API, hierarchy graph endpoint)
- ✅ M5.5 — task completion records + in-flight compaction
- ✅ M6 — Studio UI pass 2 (hierarchy graph edges, GitGraph, KanbanBoard, file preview, desk files UI, WorkerCard refactor, quip avatars)
- ✅ Track 1 / 1b / 1c / 1d — iteration metrics + repo_map prompt-layer wiring + cache-stable rebuilds + task-aware relevance
- ✅ Track 3a / 3c / 3d.4 / 3e — outline-first funnel, RTK-style tool output compression, manifest auto-render with priority/when_to_use, batch tool calls (`fs-edit-batch`, `fs-write-batch`, `code-outline-batch`)
- ✅ Track 4 / 4.2 / 4.3 / 4.4 — `.flow/`-in-repo storage mode, per-project `pr_mode` (manual vs auto), in-place working tree via Source Repo, settings hot-reload + gitignore-aware fs-list/repo_map
- ✅ Track 7 / 8 / 9 — subtask decomposition with active-subtask prompt layer, per-subtask context hot-swap + `recall-subtask`, protocol cleaner sidecar that auto-fires on `PROTOCOL_PARSE_FAIL`
- ✅ Track 11 — Workers cognition view (live per-agent thinking snapshot)
- ✅ Track 12 — sub-LLM dispatch primitive (`llm-dispatch`, `summarise-subtask`, `protocol-cleanup`) with per-use-case + per-agent model picker

Active execution queue (`.claude/plan_agent_buff/`) — small, scoped tracks ready to pick up:
- Track 3d (parts) — agent I/O hygiene + drift fixes
- Track 6 — peer rework redispatch loop
- Track 3g — strip duplicate `description` fields from the API tools array (~2–3K token savings)

Strategic / longer-horizon (`.claude/future_buff/`) — items captured but not on the immediate queue:
- **Cost ceiling** — `max_dollars_per_run` on `ProjectConfig`. `estimated_cost_usd` already tracked per iteration; this turns it from telemetry into a guardrail (BUDGET_WARNING at 80%, halt at 100%).
- **Runner refactor** — `core/runner.py` is ~4150 lines and growing. Pure refactor to extract persisters, RBAC enforcement, escalation logic, signal subscribers, repo-map rebuild, tool registry building, continuation loading, auto-commit, quip dispatch into focused modules.
- **Production sandbox** — Stage 9 from the original plan. Container per `code-exec` invocation, `network_mode: none`, resource limits, `workspace/working` mounted r/w, no host env vars or secrets in the container. Required before running against any non-toy / untrusted repo.
- **Functional verification gate** — automated "does the produced game actually run?" checks (imports clean, entry point runs, tests pass, spec-adherence greps). Captured for regression safety, deprioritised because manual verification is currently catching everything.
- **CLI v1** — headless terminal surface (`aflow run "..."`), reuses the engine in-process. Skip the React/WebSocket layer; `rich`-based live terminal UI replaces BoardFeed/QuipFeed. Lowest priority in the future folder.
- **Bug backlog** — `.claude/future_buff/bugs/` holds captured failure patterns from real runs (e.g. `run_632863526b_circular_ask_cascade.md`).

Phase-level future work (per `ROADMAP.md`):
- Phase F.5 — persona author depth (publish, scaffolding, curated reference URLs, sandboxed pytest checks)
- Phase E — production deploy + multi-tenant admin
- Phase I — headless service (webhooks, structured deliverables, per-consumer API keys)
- Phase J — knowledge surface (deep-wiki / codebase-doc toolkit)

## License

[Apache License 2.0](LICENSE). Copy, modify, and redistribute under
the terms in `LICENSE`. Commit `2d46bb1` landed the license file.
