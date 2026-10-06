# GuudFlow

A managed multi-agent orchestration platform. Describe a goal, a meta-agent
proposes a team, you approve it, and the team runs — with a shared
workspace, a shared git repo, structured inter-agent communication, and a
sandboxed code-execution surface.

**Opencode harness.** Agents now run on [opencode](https://github.com/anomalyco/opencode),
headless, one sandboxed server per role, and `opencode` is the default harness for new projects.
GuudFlow stays the manager: the scheduler, the task board (cards move only on scheduler events),
personas and memory, per-role permissions, an LLM gateway that keeps provider keys on the host, a
host-side MCP server for board and web tools, an overseer for stuck runs, and Studio. Agents finish
every turn with a validated structured result instead of free-text protocol blocks. The original
in-process agent loop is still in the repo behind `harness: "legacy"` until it is retired. To try it,
see [Run the demo](#run-the-demo). Design: [docs/OPENCODE_HARNESS_PLAN.md](docs/OPENCODE_HARNESS_PLAN.md);
task status: [docs/OPENCODE_WORK_BREAKDOWN.md](docs/OPENCODE_WORK_BREAKDOWN.md).

The notes below on the inter-agent protocol, `ask-peer`, tech lead, repo map and the 12-layer prompt
describe the legacy harness.

**Status (legacy harness): phases A, B, C, D-prime, D, F, G-prime, G, K, milestones
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

**Architecture diagrams** — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) has Mermaid diagrams for
the opencode harness (components and data flow, turn lifecycle, sandbox and network layout, card
flow) and ASCII UML, sequence, state, deployment and prompt-stack diagrams for the earlier shape of
the codebase (the legacy harness). Read that before `DESIGN.md` if you want the live picture; read
`DESIGN.md` for original intent + rationale (section 39 summarises the opencode harness).

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

**Multi-model.** Anthropic (Opus 5.5 / Sonnet 5.5 / Haiku 4.5 and older), Groq (Llama 3.1),
DeepSeek, Mistral (Large / Medium / Small / Codestral) — shipped in `data/models.json`, seven providers
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

## Run the demo

The generic one-shot takes a goal and runs a default team on it end to end: an optional researcher
(the only role with web access), a planner that splits the work into task cards, two builders working
in parallel, and a reviewer with a two-round review loop. An overseer is added automatically. See
[plan section 10](docs/OPENCODE_HARNESS_PLAN.md).

### Prerequisites

1. Python deps and the backend setup from [Run it locally](#run-it-locally) (step 1).
2. The pinned opencode binary (needs Node and npm):
   ```bash
   scripts/install_opencode.sh        # installs opencode into .tools/opencode (git-ignored)
   ```
   Set `OPENCODE_BIN` to use a binary somewhere else. A binary on your `PATH` is deliberately not used.
3. A model key on the **host only**: `ANTHROPIC_API_KEY` (or another provider's key from
   `data/models.json`), or paste it into Studio's **Settings → Models & providers**, which stores it
   on the host in `{data_root}/secrets/provider_keys.json` (owner-only; an environment variable wins).
   The same page picks each tier's model, adds providers and models, and sets the prices that budgets,
   cost previews and the gateway's spend use (saved to `{data_root}/model_config.json`; reset returns to
   the shipped defaults). It is read by the LLM gateway on the host. It is never put in a project, a
   sandbox or an agent's environment; agents get a per-role virtual key instead. The API loads `.env`;
   the CLI reads the process environment, so export it first:
   ```bash
   set -a; source .env; set +a
   ```
4. Docker is optional.
   - **With Docker:** every role runs in a hardened container on an internal network that can reach only
     the gateway and the MCP server. The agent image is built on first use. This is the default
     (`--sandbox auto` picks Docker when the daemon is available) and is required when
     `GUUDFLOW_ENV=production`.
   - **Without Docker:** use host mode, `--sandbox host`. Agents run as processes on your machine with
     no isolation, and a warning is logged on every spawn. Use it for trusted local demos only; it is
     refused when `GUUDFLOW_ENV=production`. Note that with `--sandbox auto` and no Docker, a run also
     falls back to host mode outside production.

### From the command line

```bash
python -m cli.oneshot "Build a typed Python client for the Open-Meteo API with tests" \
    --example api-client-library --web researcher --budget 5 --demo
# or: scripts/guudflow oneshot "..." --demo
```

Options: `--web researcher|off` (default `researcher`), `--budget USD` (spend cap enforced at the
gateway, default 5), `--demo` (auto-approve the team and its capabilities), `--example NAME` (seed the
run's `inputs/` from `data/example_projects/NAME`), `--sandbox auto|docker|host`, `--run-dir`,
`--max-minutes`. Board events print live, one line each; at the end you get the run summary and a cost
table per role. Exit status is 0 when the run completed, 1 otherwise, 2 on bad arguments.

### From the API

```bash
uvicorn api.main:app --port 8000
curl -X POST http://localhost:8000/api/oneshot \
  -H "Authorization: Bearer $GUUDFLOW_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"goal": "Build a typed Python client for the Open-Meteo API with tests", "web": "researcher", "budget": 5, "demo": true}'
```

It returns `202` at once with `job_id`, `run_id` and a `status_url` (`/api/invoke/{job_id}/status`) to
poll. Without `"demo": true` the project is stored but not started: the `202` carries
`needs_approval`, a cost estimate and a `confirm_url`; approve the team in Studio (or POST
`{"confirm": true}` to that URL) to start the run. Open Studio to watch the run live (board, quip feed,
task cards, pending approvals). `GUUDFLOW_API_TOKEN` can be unset in dev mode.

### Ongoing work on a project

A project is software under continuous development; each run is one working session on it.
Open the project in Studio: the **Work** tab holds the backlog and the runs waiting for your
review. Add items (one acceptance check per line), pick the ones for the next run and start it.
When the run finishes it waits for you: look at its changes, then accept it into the project
(merged into the project's own repo, `data/projects/{id}/repo.git`; its finished items close) or
reject it (nothing changes; its items reopen). The next run starts from the accepted code. From
the API or CI:

```bash
curl -X POST http://localhost:8000/api/projects/my-app/backlog -H "Authorization: Bearer $GUUDFLOW_API_TOKEN" \
  -H "Content-Type: application/json" -d '{"title": "Rate-limit login", "acceptance": ["429 after 5 tries"]}'
curl -X POST http://localhost:8000/api/invoke/my-app -H "Authorization: Bearer $GUUDFLOW_API_TOKEN" \
  -H "Content-Type: application/json" -d '{"confirm": true, "backlog_items": "top"}'
curl -X POST http://localhost:8000/api/projects/my-app/runs-review/RUN_ID/accept \
  -H "Authorization: Bearer $GUUDFLOW_API_TOKEN"
```

In **Settings → Project work** you can also set:

- a check command (for example `python3 -m unittest discover -s tests -q`, or `bun test` for
  JavaScript), run on each finished run's code in a sandbox with no network; its result shows in
  the review queue;
- the project's real remote: it is synced into the project before every run, and an
  accepted run is pushed as a branch, opened as a GitHub pull request, or pushed to the
  branch. The token stays in a `GUUDFLOW_GIT_*` environment variable on the server;
- the autopilot: start runs on a schedule within per-run and weekly budgets, optionally
  accepting a run by itself when every item finished and the check passed.

See plan §13 and §14 in `docs/OPENCODE_HARNESS_PLAN.md` for how it works.

### Quality gate

An offline check of a finished run (no network, no model):

```bash
scripts/run_quality_gate.sh data/runs/<run_id> api-client-library
```

The second argument is optional and adds that example project's functional checks (available:
`algorithm-with-benchmarks`, `api-client-library`, `bug-hunt-and-fix`, `cli-tool-from-spec`,
`codebase-wiki`, `fullstack-web-app`). It prints a report table and exits 0 when the gate passes, 1
when it fails, 2 on a usage error. Add `--json` for machine-readable output.

### Tests

```bash
pytest tests/harness/    # fake LLM and, if installed, the real pinned opencode; no API key needed
```

Tests that need the opencode binary or Docker skip cleanly when they are missing. The security escape
suite (`tests/harness/test_escape_suite.py`) needs Docker and runs in CI's Docker job.

---

## Run it locally

> **Full guide: [docs/SETUP.md](docs/SETUP.md)**: which setup to pick for your OS, prerequisites,
> install, starting it, a first project end to end, models and keys, configuration, security and
> troubleshooting. The summary below is the short version.

You need Python 3.11+, Node 18+ with npm, git, and Docker Engine for sandboxed agents (Linux, or
WSL2 with Docker Engine inside it; Docker Desktop is not supported for sandboxes, see the guide).

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

Install the pinned opencode binary (needs npm):

```bash
scripts/install_opencode.sh
```

Then start the API + in-process worker (avoid `--reload` while agents run: a code edit restarts
the API and stops the run):

```bash
source venv/bin/activate     # or: venv\Scripts\Activate.ps1 on Windows
uvicorn api.main:app --port 8000
```

The API serves at `http://localhost:8000`. Health check:
`http://localhost:8000/health`.

### 2. Frontend

```bash
cd studio
npm install
npm run dev
```

Studio runs at `http://localhost:3001` and proxies `/api` and `/ws` to
the backend on `:8000` (or set `GUUDFLOW_API_URL`). To serve Studio from the API on one port instead,
run `npm run build` and start the API with `GUUDFLOW_STUDIO_DIST=studio/dist`. Paste your API token (whatever you set in
`GUUDFLOW_API_TOKEN`) in the top-right field.

### 3. Sandboxes

With Docker available, every agent role runs in its own hardened container; the image
(`guudflow/agent-opencode:<version>`) is built automatically on the first run. Without Docker, runs
fall back to host mode (no isolation, trusted demos only). See [docs/SETUP.md](docs/SETUP.md).

The rest of this section describes the **legacy** harness's sandbox, kept for reference.

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

On Linux with Docker Engine, one container runs the API, Studio and the pinned opencode, and starts
each agent's sandbox next to it on the same daemon (Docker Desktop is not supported; see
[docs/SETUP.md](docs/SETUP.md), option C):

```bash
cp .env.example .env          # set GUUDFLOW_API_TOKEN; add ANTHROPIC_API_KEY or paste it in Studio
docker compose up -d --build  # from the repo root; open http://localhost:8000
```

The compose file uses host networking, mounts the data directory at the same absolute path inside
and outside (`GUUDFLOW_HOST_DATA`, default `./data`), and adds `NET_ADMIN` for the per-run firewall
rules. The guide explains why each is needed.


## Project layout

```
guudflow/
├── core/              ← signals, workspace, scheduler, runner, playbook,
│                        oneshot, run_initializer, run_loader, training/, ...
│                        (legacy harness: protocol, llm/, loop_guard,
│                        edit_blocks, context_manager, repo_map)
│   └── harness/       ← opencode harness: OpencodeRun, client, event bridge,
│                        capabilities + permissions, results, task board,
│                        detectors + overseer, git roles, MCP server and
│                        host tools (web, screenshot, visual_diff)
├── gateway/           ← LLM gateway: virtual keys, spend caps, cost log
├── cli/               ← `python -m cli.oneshot`
├── agents/            ← BaseAgent, abstract archetypes, persona,
│                        persona_desk, persona_chat, registry, catalog,
│                        tools/, builtin/
├── api/               ← FastAPI app + routes + WebSocket
├── worker/            ← JobRunner, queue, SandboxManager, DockerManager
├── studio/            ← Vite + React + TS frontend
├── sandbox/           ← agent-opencode image (+ chart variant) and the
│                        legacy per-run sandbox Dockerfile (§29)
├── data/              ← personas, projects, runs, role_templates,
│                        manifestos, models.json, training_camp/
├── tests/             ← pytest suite
├── scripts/           ← setup + sandbox build helpers
├── docs/              ← OPENCODE_HARNESS_PLAN, OPENCODE_WORK_BREAKDOWN,
│                        STUDIO_UI_SPEC, USER_STORIES, worker-archetypes,
│                        ARCHITECTURE.md + diagrams/
├── DESIGN.md          ← full architecture (39 sections; 39 = opencode harness)
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
| `GUUDFLOW_ENV` | unset | Set to `production` to make the opencode harness refuse host mode (Docker required) |
| `OPENCODE_BIN` | `.tools/opencode/...` | Path to the pinned opencode binary (see `scripts/install_opencode.sh`) |
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
