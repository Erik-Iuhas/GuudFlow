# GuudFlow

**A team of AI agents that works on your software, with you as the manager.**

Describe a goal or pick items from a backlog. GuudFlow proposes a team (a planner, builders and a
reviewer, each with a name), you approve it, and the team gets to work. Each agent runs in its own
sandbox. They share a task board and a git repo, and they review each other's work. Nothing reaches
your project until you accept the run.

![A finished run: the team graph with each worker's status and cost, and the live board below it](docs/images/run.png)

---

## What you get

- **A team you approve before anything runs.** You pick the roles, models and capabilities, and see
  a cost estimate up front. Every worker has a name and a role, and they refer to each other by name.

  ![The team graph on the project page: Arlo the planner, Jules the builder and Bram the reviewer](docs/images/team.png)

- **A live view of each run.** The graph shows who is working on what, what it has cost and where
  the review loop is. Click a worker to see its tool calls, its latest result and what it is allowed
  to do.

  ![Clicking a worker opens its tool calls and its latest result](docs/images/node-details.png)

- **Ongoing work, not one-off runs.** Each project keeps a backlog and its own repo. A run takes
  backlog items, starts from the project's `main`, and waits in **Waiting for your review** until
  you accept it (merged) or reject it (nothing changes).

  ![The Work tab: runs waiting for review and the backlog, with acceptance checks per item](docs/images/work.png)

- **Any model, priced properly.** Anthropic, OpenAI-compatible providers and local models (Ollama,
  LM Studio). Each tier (heavy, solid, fast) can use several models. Prices drive budgets and spend
  caps. Keys stay on your machine and never go into a sandbox.

  ![Settings: model tiers, providers and keys](docs/images/settings.png)

- **Guardrails.** Spend caps per role and per run, protected paths, secret scanning before any
  merge, sandboxes with no internet access, and an overseer that steps in when a run gets stuck.

---

## Before you start

The laundry list. `scripts/doctor.sh` checks all of it and tells you what to fix.

**Always**
- [ ] **A model API key**: Anthropic, or any OpenAI-compatible provider or local model you add in
      Studio. You can paste it in Studio after starting, so it does not have to be in a file.
- [ ] **An API token** you make up: any long random string (`openssl rand -hex 24`). It protects the
      API and Studio. It goes in `.env` as `GUUDFLOW_API_TOKEN`.
- [ ] **git** (any recent version).
- [ ] **Free port 8000** (Studio dev server uses 3001).
- [ ] **A budget in mind.** Runs spend real money. Set per-run and weekly caps before the first run.

**For the Docker path**
- [ ] **Docker Engine** on Linux, or on Windows inside WSL2. Docker Desktop cannot host the
      sandboxes. The Compose file also needs `/var/run/docker.sock` and `NET_ADMIN`.
- [ ] A few GB of disk for the image and per-run containers.

**For the uv / Python path**
- [ ] **[uv](https://docs.astral.sh/uv/)** installs the Python packages, and Python itself if you
      lack 3.11+ (see [Installing uv](#installing-uv)).
- [ ] **Node 18+ and npm**, to build Studio once.
- [ ] **Docker Engine** if you want sandboxed agents. Without it, set `GUUDFLOW_SANDBOX=host` (agents
      then run as your user, so use it only for work you trust).

**Optional**
- [ ] `BRAVE_API_KEY` or `TAVILY_API_KEY` for the researcher role's web search.
- [ ] `GUUDFLOW_GIT_TOKEN` to push accepted runs to a real remote.
- [ ] Playwright Chromium for screenshots (setup installs it; `GUUDFLOW_SKIP_PLAYWRIGHT=1` skips it).

### Installing uv

| Platform | Command |
|---|---|
| Linux, macOS, WSL | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| macOS (Homebrew) | `brew install uv` |
| Windows (PowerShell) | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| Any (pipx or pip) | `pipx install uv` or `pip install uv` |

`scripts/install_uv.sh` (or the VS Code task **Install uv**) does the Linux/WSL one and skips if uv exists; the GuudFlow scripts find uv on their own afterwards. Open a new terminal afterwards in other shells so `uv` is on your `PATH`, then check with `uv --version`. No
Python 3.11 on the machine is fine: `uv venv` downloads one. Details: <https://docs.astral.sh/uv/getting-started/installation/>.

## Get started

Pick one. Both end at <http://localhost:8000>, where you paste your token at the top right and add
your model key under **Settings → Models & providers**.

### A. Docker Compose (the easy one, with full sandboxing)

```bash
git clone https://github.com/Erik-Iuhas/GuudFlow.git && cd GuudFlow
cp .env.example .env            # set GUUDFLOW_API_TOKEN (openssl rand -hex 24); ANTHROPIC_API_KEY is optional here
docker compose up -d --build
docker compose logs -f          # wait for "Application startup complete"
```

Stop with `docker compose down`. Your data stays in `./data` (set `GUUDFLOW_HOST_DATA` to move it).
Update with `git pull && docker compose up -d --build`.

### B. uv and Python (no container for the app)

```bash
git clone https://github.com/Erik-Iuhas/GuudFlow.git && cd GuudFlow
scripts/setup.sh                # venv + packages via uv, Chromium, Studio packages, .env, seed data
scripts/install_opencode.sh     # the pinned agent runtime into .tools/opencode
scripts/build.sh studio         # build Studio into studio/dist
$EDITOR .env                    # set GUUDFLOW_API_TOKEN (and GUUDFLOW_SANDBOX=host if no Docker)
scripts/doctor.sh               # tells you if anything is still missing

source venv/bin/activate
GUUDFLOW_STUDIO_DIST=studio/dist uvicorn api.main:app --port 8000
```

By hand, without the scripts: `uv venv venv && uv pip install -r requirements.txt`, then
`python -m playwright install chromium` (with the venv active) and `cd studio && npm ci && npm run build`.
Windows without WSL: `scripts/setup.ps1` (still pip-based; WSL2 is the better route on Windows).

For development, `scripts/dev.sh` starts the API with reload on :8000 and the Studio dev server on
:3001. Use `scripts/dev.sh --no-reload` while agents are running, so a code edit does not restart
the worker under them.

[docs/SETUP.md](docs/SETUP.md) is the full guide: WSL, keys, configuration, security and
troubleshooting.

---

## Working on GuudFlow

| Script | What it does |
|---|---|
| `scripts/doctor.sh` | Checks tools, Docker, `.env`, built pieces and the port |
| `scripts/setup.sh` | First-time install (packages via uv) |
| `scripts/dev.sh [--no-reload]` | API on :8000 and Studio dev server on :3001 |
| `scripts/build.sh native\|docker\|studio\|opencode\|sandbox` | `native` = Studio + opencode for the Python path; `docker` = the Compose image (builds its own, independent of the others) |
| `scripts/check.sh [backend\|studio\|escape]` | Tests: pytest, Studio typecheck and vitest, or the sandbox escape suite |

Run `scripts/check.sh escape` too when you touch `core/harness/`, `worker/`, `sandbox/` or `gateway/`.

**VS Code:** the tasks run through `bash`, so open the folder in WSL (Remote - WSL) or have Git Bash on your PATH. Then run **Tasks: Run Task**. The tasks in `.vscode/tasks.json` cover
setup, doctor, the dev stack, Compose up/logs/down, builds, and each check. `.vscode/launch.json` debugs the API, the current pytest file, and a one-shot run.
Run **Tasks: Run Test Task** for the fast checks.

---

### Your first run

1. **Projects → New project.** Write the goal. Add a `README.md` under **Inputs** if you have a spec.
2. On the **Work** tab, add a few backlog items. Put one acceptance check per line.
3. Tick the items and click **Start a run**. Approve the proposed team.
4. Watch the run. Post a comment at any time if you want to steer it.
5. When it finishes, review the diff under **Waiting for your review**, then accept or reject it.

[docs/SETUP.md §6](docs/SETUP.md#6-first-project-end-to-end) walks through this in more detail.

---

## Other ways to drive it

**Command line:** a one-shot run with a default team.

```bash
python -m cli.oneshot "Build a typed Python client for the Open-Meteo API with tests" \
    --budget 5 --demo           # --demo auto-approves the team; --sandbox auto|docker|host
```

**API / CI:** add a backlog item, start a run with it, and accept the result.

```bash
H=(-H "Authorization: Bearer $GUUDFLOW_API_TOKEN" -H "Content-Type: application/json")
curl -X POST localhost:8000/api/projects/my-app/backlog "${H[@]}" \
  -d '{"title": "Rate-limit login", "acceptance": ["429 after 5 tries"]}'
curl -X POST localhost:8000/api/invoke/my-app "${H[@]}" -d '{"confirm": true, "backlog_items": "top"}'
curl -X POST localhost:8000/api/projects/my-app/runs-review/RUN_ID/accept "${H[@]}"
```

Invoke routes return `202` straight away with a status URL to poll.

**Per-project settings** (**Settings → Project work**):

- **Check command:** run on each finished run before you review it, for example `bun test`.
- **Real remote:** synced before each run. An accepted run is pushed as a branch or opened as a pull
  request.
- **Autopilot:** runs on a schedule within per-run and weekly budgets.

---

## How it works

GuudFlow is the manager. It runs the scheduler, the task board, personas and memory, permissions,
credentials, merges and the Studio UI. Each agent role is a headless
[opencode](https://github.com/anomalyco/opencode) server in its own sandbox, on its own git branch.

- **Keys stay on the host.** Agents reach models through an LLM gateway on the host, which holds the
  real keys and gives each role a virtual key with a spend cap.
- **Tools that need the outside world run on the host.** Web search, screenshots, package installs
  and board tools go through GuudFlow's MCP server.
- **Structured results.** Agents finish each turn with a validated result.
- **The runner merges.** It checks each role's branch (protected paths, symlinks, secrets) and
  merges it into the run's `main`.
- **Review loop and overseer.** Review is a loop between builder and reviewer with a round limit. A
  heavy-tier overseer wakes when deterministic detectors see the run stalling.

More detail:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): diagrams of the components, the turn lifecycle,
  the sandbox network and how cards move.
- [docs/OPENCODE_HARNESS_PLAN.md](docs/OPENCODE_HARNESS_PLAN.md): the design.
- [DESIGN.md](DESIGN.md): the original intent and rationale.
- [ROADMAP.md](ROADMAP.md): what's next.
- [CHANGELOG.md](CHANGELOG.md): what is in each release and its known limits.

## Configuration

These environment variables matter most. The full list is in
[docs/SETUP.md §8](docs/SETUP.md#8-configuration-reference).

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Model key. You can also paste keys in Studio; an env var wins |
| `GUUDFLOW_API_TOKEN` | unset | Bearer token for the API and Studio. Required in production |
| `GUUDFLOW_SANDBOX` | `auto` | `auto` (Docker if available, else host), `docker`, or `host` (no isolation) |
| `GUUDFLOW_ENV` | unset | `production` refuses host mode (Docker required) |
| `GUUDFLOW_DATA_DIR` | `./data` | Projects, runs, personas, model config |
| `GUUDFLOW_STUDIO_DIST` | unset | Serve the built Studio from the API on one port |

## Project layout

```
core/        scheduler, runner, project repo, backlog, autopilot, model config
  harness/   opencode harness: runs, task board, overseer, MCP server, host tools
gateway/     LLM gateway: virtual keys, spend caps, cost log
api/         FastAPI app, routes, WebSocket
worker/      job runner, sandbox manager
studio/      React + Vite frontend
sandbox/     agent sandbox images
agents/      personas, memory, host-side agents (meta-agent, memory writer)
cli/         python -m cli.oneshot
data/        shipped defaults (models, personas, example projects); runtime data goes here too
tests/       pytest suite (no real LLM calls)
docs/        setup guide, architecture, design plans
```

## Tests

```bash
pytest                           # backend; mocked LLMs, no key needed
cd studio && npm run typecheck && npm test
```

Tests that need Docker or the opencode binary skip cleanly when either is missing.

## License

[Apache License 2.0](LICENSE).
