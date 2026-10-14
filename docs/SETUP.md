# Setting up GuudFlow

This guide takes you from a fresh clone to a team of agents working on your project, and covers
running it day to day, configuration, security and the problems you are likely to hit. For what
GuudFlow is and how it works, see the [README](../README.md); for the design, see
[docs/OPENCODE_HARNESS_PLAN.md](OPENCODE_HARNESS_PLAN.md).

- [1. What you are installing](#1-what-you-are-installing)
- [2. Pick a setup](#2-pick-a-setup)
- [3. Prerequisites](#3-prerequisites)
- [4. Install](#4-install) (and [on Windows](#on-windows-wsl2))
- [5. Start it](#5-start-it)
- [6. First project, end to end](#6-first-project-end-to-end)
- [7. Models, keys and costs](#7-models-keys-and-costs)
- [8. Configuration reference](#8-configuration-reference)
- [9. Security notes for whoever runs it](#9-security-notes-for-whoever-runs-it)
- [10. Updating](#10-updating)
- [11. Troubleshooting](#11-troubleshooting)
- [12. Running the tests](#12-running-the-tests)

---

## 1. What you are installing

| Piece | What it is | Where it runs |
|---|---|---|
| **API + worker** | FastAPI app (`api/main.py`). The worker that runs agent teams runs inside the same process by default. | Your machine, port 8000 |
| **Studio** | The web UI (React, `studio/`). In development Vite serves it and proxies `/api` and `/ws` to the API. Or you build it once and the API serves it. | Port 3001 (dev) or 8000 (built) |
| **opencode** | The agent harness: one headless `opencode serve` per role. Pinned to a version (`OPENCODE_VERSION` in `core/harness/server.py`) and installed into `.tools/opencode`. | Inside each sandbox, or on the host in host mode |
| **Sandboxes** | One hardened Docker container per role per run, built from `sandbox/agent-opencode.Dockerfile` on first use (`guudflow/agent-opencode:<version>`). | Docker on your machine |
| **LLM gateway and MCP server** | Started per run inside the API process. The gateway holds the real provider keys and gives each role a virtual key. The MCP server is how agents submit results and use host tools (web search, screenshots, notes). | Your machine, bound to the run's private Docker network |
| **Data** | Projects, runs, personas, model settings and saved keys. Plain files plus SQLite. | `./data` by default (`GUUDFLOW_DATA_DIR`) |

Nothing is hosted for you. Everything, including the provider keys, stays on the machine that runs
the API.

## 2. Pick a setup

| Your machine | Recommended setup | Agent isolation |
|---|---|---|
| **Linux** (workstation, server, VM) | Native install + Docker Engine, or `docker compose up` (§5, option C) | Full: every role in its own hardened container on a private network |
| **Windows** | WSL2 with a Linux distro; follow the Linux steps inside WSL ([§4, Windows](#on-windows-wsl2)). For sandboxes, install Docker Engine *inside* that distro. Without Docker, run in host mode. | With Docker Engine in WSL: same as Linux. Without: none |
| **No Docker at all** (any OS, including macOS) | Native install with `GUUDFLOW_SANDBOX=host` | None: agents run as your user (see host mode below) |

**Docker Desktop is not supported for sandboxes.** For each run, GuudFlow creates a private Docker
network and binds the LLM gateway and MCP server to that network's gateway IP *on the host*, so
sandboxes can reach those two services and nothing else. Docker Desktop keeps its networks inside its
own VM, so that IP is not a local address on your machine. On Windows, use Docker Engine inside WSL2
instead. Anywhere else without Docker Engine, use host mode.

**Native or compose?** Both give full isolation on Linux. Compose needs only Docker on the machine
and puts the API, Studio and opencode in one container. A native install is easier to develop on and
to debug.

**Host mode** runs each opencode server, and the pre-review check, as a normal process on your
machine, with your user's permissions and no network isolation.
- **How to pick it:** set `GUUDFLOW_SANDBOX=host` in `.env` (or pass `--sandbox host` to the CLI).
  Without the setting (`auto`), runs use Docker when the daemon is reachable and fall back to host
  mode when it is not.
- GuudFlow logs `⚠️ DEV MODE` on every spawn.
- Use it only for projects and goals you trust: an agent can do anything your user can.
- It is refused when `GUUDFLOW_ENV=production`.
- `GUUDFLOW_SANDBOX=docker` does the opposite: a run fails rather than fall back to the host.

## 3. Prerequisites

| Need | Version | Why |
|---|---|---|
| Python | 3.11+ | API, worker, CLI |
| Node.js + npm | Node 18+ (CI uses 20; development used 22) | Studio, and installing the pinned opencode binary |
| git | any recent | Project repos, run branches, merges |
| Docker Engine | recent, with your user able to run `docker` | Sandboxes (skip for host mode) |
| A model provider key | e.g. an Anthropic API key | Real runs. Use a key from your Anthropic organisation's **default workspace**: keys tied to another workspace were rejected in testing. |
| Disk | a few GB | Python and Node deps, the sandbox image (a few hundred MB), runs |

Optional:
- `iptables` usable by the API's user, so GuudFlow can stop sandboxes from opening connections to
  other ports on your host (see [§9](#9-security-notes-for-whoever-runs-it)).
- `BRAVE_API_KEY` or `TAVILY_API_KEY` for the researcher role's web search.
- Playwright Chromium (installed by setup) for screenshots and visual review.

## 4. Install

All commands run from the repository root.

```bash
git clone https://github.com/Erik-Iuhas/GuudFlow.git
cd GuudFlow

# Python venv + deps, Playwright Chromium, Studio deps, .env from .env.example,
# and the default personas (first run only).
./scripts/setup.sh                 # Windows PowerShell: ./scripts/setup.ps1

# The pinned opencode binary, into .tools/opencode (git-ignored). Needs npm.
scripts/install_opencode.sh
```

`setup.sh` honours a few switches: `GUUDFLOW_SKIP_PLAYWRIGHT=1`, `GUUDFLOW_SKIP_NPM=1`,
`GUUDFLOW_WITH_EMBEDDINGS=1` (pulls PyTorch, ~600 MB; embeddings are off by default) and
`GUUDFLOW_FORCE_SEED=1`.

**Docker (Linux).** Install Docker Engine for your distro, then let your user use it without sudo
and check it works:

```bash
sudo usermod -aG docker "$USER"    # log out and back in afterwards
docker info                        # must succeed without sudo
```

You do not need to build the sandbox image yourself: the first sandboxed run builds
`guudflow/agent-opencode:<version>` from the pinned binary (no network needed for the build).
`scripts/build_sandbox.sh` builds the *legacy* sandbox image and is not needed.

**Configure `.env`.** `setup.sh` copied `.env.example` to `.env`. Edit it:

```ini
ANTHROPIC_API_KEY=sk-ant-...        # or leave the line out and paste the key in Studio (§7)
GUUDFLOW_API_TOKEN=pick-a-long-random-string
GUUDFLOW_MODE=dev
GUUDFLOW_DATA_DIR=./data
```

> ⚠️ The API loads `.env` with *override*: a value in `.env` replaces the same variable in your
> shell, and an **empty** line (`ANTHROPIC_API_KEY=`) erases a key you exported. Either put the key in
> `.env`, or delete that line and save the key in Studio.

Setting `GUUDFLOW_API_TOKEN` is strongly recommended even locally: with it unset, dev mode leaves the
API open to anything that can reach port 8000.

### On Windows (WSL2)

Run GuudFlow *inside* WSL, as on a Linux machine: install, start and run it from your WSL
distro's terminal, then open it from your Windows browser. These steps follow the standard Linux
setup but have not been verified on WSL yet; please report anything that differs.

1. **Install WSL2 with a distro** (in PowerShell, once): `wsl --install -d Ubuntu`, then open the
   Ubuntu terminal. Everything below runs there.
2. **Docker: use Docker Engine inside the distro, not Docker Desktop.**
   - With Docker Desktop's "WSL integration" on, `docker` inside WSL talks to Docker Desktop's
     daemon, which keeps its networks in its own VM. That is the setup sandboxes cannot use (§2).
   - Turn that integration off for your distro (Docker Desktop → Settings → Resources → WSL
     integration), or quit Docker Desktop.
   - Install Docker Engine in the distro by following
     [docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/), then:

   ```bash
   sudo usermod -aG docker "$USER"     # close and reopen the terminal
   sudo service docker start           # or enable systemd so it starts on its own (below)
   docker info                         # must work without sudo
   ```

   To start Docker automatically, put this in `/etc/wsl.conf`, then run `wsl --shutdown` in
   PowerShell and reopen the terminal:

   ```ini
   [boot]
   systemd=true
   ```

   No Docker at all? Skip this step and set `GUUDFLOW_SANDBOX=host` in `.env` (agents then run
   without containers; trusted work only).
3. **Clone into the Linux filesystem**, e.g. your home directory, **not** under `/mnt/c/...`. The
   Windows drive is slow from WSL and ignores Linux file permissions, which GuudFlow relies on to keep
   saved provider keys owner-only.

   ```bash
   cd ~ && git clone https://github.com/Erik-Iuhas/GuudFlow.git && cd GuudFlow
   ```

4. **Install and start it** the Linux way: the native steps above (`./scripts/setup.sh`,
   `scripts/install_opencode.sh`, `.env`, then [§5](#5-start-it)), or Docker Compose
   ([§5, option C](#option-c-docker-compose-linux-only-docker-needed)). The native route needs Python
   3.11+, Node 18+ and git *inside* WSL; Ubuntu 24.04 ships Python 3.12.
5. **Open it from Windows:** <http://localhost:8000> (or `:3001` for the dev server). WSL2 forwards
   localhost to Windows by default.

Do not use `scripts/setup.ps1` or `scripts/dev.ps1` for agent runs: they set GuudFlow up on Windows
itself, where sandboxes cannot run.

## 5. Start it

Activate the venv first: `source venv/bin/activate` (`venv\Scripts\Activate.ps1` on Windows).

### Option A: everything on one port (simplest for daily use)

```bash
(cd studio && npm run build)                 # once, and after each update
GUUDFLOW_STUDIO_DIST=studio/dist uvicorn api.main:app --port 8000
```

Open <http://localhost:8000>.

### Option B: development (live-reloading Studio)

```bash
# terminal 1
uvicorn api.main:app --port 8000
# terminal 2
cd studio && npm run dev
```

Open <http://localhost:3001>. Vite proxies the API to `http://localhost:8000`. If your API runs
elsewhere, start Vite with `GUUDFLOW_API_URL=http://host:port npm run dev`.

`scripts/dev.sh` starts both with `--reload`. **Do not use `--reload` while agents are running**:
any edit under `api/`, `core/`, `agents/` or `worker/` restarts the API and kills the run. Use
`./scripts/dev.sh --no-reload`, or plain `uvicorn` as above.

### Option C: Docker Compose (Linux, only Docker needed)

The API, Studio and the pinned opencode run in one container. Agent sandboxes are started next to
it on the same Docker daemon.

```bash
git clone https://github.com/Erik-Iuhas/GuudFlow.git && cd GuudFlow
cp .env.example .env               # set GUUDFLOW_API_TOKEN; add ANTHROPIC_API_KEY or use Studio
docker compose up -d --build       # run from the repo root
```

Open <http://localhost:8000> and paste your token. The first run builds the sandbox image, which takes
a minute.

What the compose file sets up, and why:
- **`network_mode: host`.** Each run binds its LLM gateway and MCP server to the run network's gateway
  IP, which only exists on the host. In host mode `ports:` does not apply: `GUUDFLOW_BIND` (default
  `127.0.0.1`) and `GUUDFLOW_PORT` (default `8000`) decide where the API listens. Set
  `GUUDFLOW_BIND=0.0.0.0` only behind a firewall or proxy you control.
- **Data at the same absolute path inside and outside.** Sandboxes bind-mount run folders by path, and
  the host's Docker daemon resolves them on the host. The data directory defaults to `./data` in the
  repo (`${PWD}/data`). Set `GUUDFLOW_HOST_DATA=/srv/guudflow` in `.env` to put it elsewhere (use an
  absolute path). `TMPDIR` lives under it for the same reason.
- **`NET_ADMIN`**, so each run can add the iptables rules that keep sandboxes off other host ports.
- **Startup seeding:** the default personas and training scenarios are copied into a fresh data
  directory; existing files are never overwritten.

The container runs as root, so files under the data directory are owned by root. Sandboxes still run
as an unprivileged user inside their own containers. Logs: `docker compose logs -f`. Stop:
`docker compose down` (data stays). Update: `git pull && docker compose up -d --build`.

### Check it

```bash
curl http://localhost:8000/health          # {"ok": true}
```

In Studio, paste your `GUUDFLOW_API_TOKEN` into the field at the top right. It is kept in your
browser's local storage.

### Stop it

`Ctrl-C` in each terminal. If a port stays busy, find the old process with
`lsof -ti tcp:8000` and kill it.

## 6. First project, end to end

1. **Add a model key and check it.** Open **Settings → Models & providers**:
   - If you did not put a key in `.env`, click **Set key** on *anthropic* and paste it.
   - Click **Test**: you should see "N model(s) available".
   - Check the **Tiers**: heavy (planner, overseer), solid (builders, reviewers), fast (quips). Pick
     cheaper models while you try things out (§7).

2. **Create a project.** **Projects → + New project** creates a blank project. **Propose** lets the
   meta-agent read your goal and inputs and suggest a team. A useful first team is a planner, a
   builder and a reviewer that reviews the builder.

3. **Shape the team.** Open the project's **Team** tab:
   - each worker has a name (picked for you, editable in the worker's settings), a tier, and
     capabilities (what it may do: edit files, run a shell, use the web, install allow-listed
     packages);
   - capabilities are fixed when the run starts and can only narrow during a run.

4. **Tell it how to check the work.** In **Settings → Project work**, set a check command that runs on
   each finished run's code in a sandbox with no network, for example:
   - `python3 -m unittest discover -s tests -q` for Python. The sandbox image has Python 3.11 and the
     standard library only (no pytest).
   - `cd web && bun test` for JavaScript. The sandbox has Bun's test runner as `bun`; there is no
     Node and no npm.

5. **Write the backlog.** On the **Work** tab:
   - add items with one acceptance check per line, and optional "after" dependencies;
   - tick the items for this run and click **Start a run with N items**.

6. **Watch it.** The run page shows:
   - the team as a graph: each node shows its worker's state, current card, last result and cost.
     Click a node for its tool calls and full result;
   - the Board, Quips, Overseer and Cards tabs;
   - the budget meter;
   - a comment box: your comment reaches the agents at their next turn.

7. **Review and accept.** When the run finishes it waits on the **Work** tab with its check result:
   - **View changes** shows the diff;
   - **Accept into the project** merges it into the project's own repo
     (`data/projects/<id>/repo.git`) and closes its finished items;
   - **Reject** leaves the project unchanged and reopens the items.

   The next run starts from the accepted code. To push accepted work to your real repository, set the
   remote in **Settings → Project work**; the token stays in a `GUUDFLOW_GIT_*` variable on the server.

To get the code out of a project at any time:

```bash
git clone data/projects/<project-id>/repo.git my-copy
```

**Quick demo without Studio**, a one-shot run from the command line:

```bash
set -a; source .env; set +a        # the CLI reads the environment, not .env
python -m cli.oneshot "Build a typed Python client for the Open-Meteo API with tests" \
    --example api-client-library --budget 5 --demo
```

`--sandbox auto|docker|host` picks isolation (auto uses Docker when available). `--web off` skips the
researcher. See the README for the API equivalents (`/api/oneshot`, `/api/invoke/<project>`).

## 7. Models, keys and costs

- **Where keys live.** A provider key comes from its environment variable (e.g. `ANTHROPIC_API_KEY`)
  or from **Settings → Models & providers**. Studio stores keys in
  `data/secrets/provider_keys.json`: the folder is 0700, the file is 0600, and the API never returns a
  key. The environment variable wins when both are set. Keys go only to the LLM gateway on the host;
  agents get a virtual key per role that works only through the gateway.
- **Other providers.** Any OpenAI-compatible endpoint works (OpenAI, OpenRouter, Groq, DeepSeek,
  Mistral, a local Ollama/LM Studio/vLLM). Add it under **Add a provider**, add its models with their
  prices, and put them in a tier.
- **Tiers.** Each tier can list several models:
  - the tier's roles are spread across them;
  - a reviewer gets a different model from the work it reviews when the tier has another one;
  - the first model is the default and prices cost estimates.

  A role can also name an exact model in its settings.
- **Prices** are USD per million tokens (input, output, cache read, cache write; a blank cache-write
  price means 1.25× input).
  - They drive the budget meter, cost previews and the per-run spend log
    (`data/runs/<run>/llm_costs.jsonl`).
  - The shipped prices are list prices. Set your own if your account pays differently.
- **Budgets.** Each project has a token budget (USD) in its Settings tab, enforced at the gateway. A
  role or run that hits its cap stops spending.
- **Indicative cost.** With Haiku everywhere, a small two-item run cost about $1.40. With an Opus
  planner and Sonnet builder and reviewer, a 10-card web-game run cost about $4.60. Both figures are at
  list prices.

## 8. Configuration reference

### Environment variables

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Anthropic key (or save it in Studio). Other providers name their own variable in Settings. |
| `GUUDFLOW_API_TOKEN` | unset | Bearer token for the API and Studio. Unset = open in dev mode, refused in production. |
| `GUUDFLOW_MODE` | `dev` | `production` requires the token and Docker. |
| `GUUDFLOW_ENV` | unset | `production` makes the harness refuse host mode (Docker required). Set it together with `GUUDFLOW_MODE=production`. |
| `GUUDFLOW_SANDBOX` | `auto` | Where agents run: `auto` (Docker when available, else the host), `docker` (always; fail without it) or `host` (never in a container; trusted work only). The CLI's `--sandbox` overrides it. |
| `GUUDFLOW_DATA_DIR` | `./data` | Where everything is stored (relative to the directory you start from). |
| `GUUDFLOW_STUDIO_DIST` | unset | A built Studio (`studio/dist`) to serve at `/`. |
| `GUUDFLOW_CORS_ORIGINS` | `*` | Comma-separated allowed origins. |
| `OPENCODE_BIN` | `.tools/opencode/...` | Use an opencode binary elsewhere. Must be the pinned version. A binary on `PATH` is deliberately ignored. |
| `GUUDFLOW_DISABLE_AUTOPILOT` | unset | `1` stops the autopilot loop from starting scheduled runs. |
| `GUUDFLOW_DISABLE_INPROC_WORKER` | unset | `1` disables the in-process worker. The standalone worker does not share the API's queue yet, so leave this unset. |
| `BRAVE_API_KEY` / `TAVILY_API_KEY` | unset | Web search for the researcher role. Read on the host only. |
| `GUUDFLOW_GIT_*` | unset | Tokens for project remotes. A project's remote names which variable to use. |
| `GUUDFLOW_API_URL` | `http://localhost:8000` | Studio dev server only: where to proxy API calls. |

### Ports

| Port | What |
|---|---|
| 8000 | API (and Studio when `GUUDFLOW_STUDIO_DIST` is set) |
| 3001 | Studio dev server (`npm run dev`) |
| random | Per-run LLM gateway and MCP server, bound to the run's Docker network gateway IP (or 127.0.0.1 in host mode). |

### Files under the data directory

| Path | Contents |
|---|---|
| `projects.json`, `projects/<id>/` | Project configs, roadmap, playbook, persona rules, backlog and run history (SQLite) |
| `projects/<id>/repo.git` | The project's own git repo; accepted runs land on its `main` |
| `runs/<run_id>/` | Each run: board (`board.jsonl`), cards, costs (`llm_costs.jsonl`), quips, per-role clones, report |
| `personas/` | Persistent worker personas and their memory |
| `model_config.json` | Your models, prices and tiers (absent = shipped `data/models.json`) |
| `secrets/provider_keys.json` | Provider keys saved from Studio (0600) |

Back up the data directory to back up everything. `model_config.json` and `secrets/` are git-ignored
even when the data directory is the repo's `data/`.

## 9. Security notes for whoever runs it

- **Keep the API private.** Bind it to localhost (uvicorn's default) or put it behind your own
  authenticating proxy. Always set `GUUDFLOW_API_TOKEN`.
  - Known gap: `/api/settings` (storage settings) does not check the token yet.
- **Sandboxes** (Docker mode):
  - each role runs as your host user's uid (or an unprivileged `agent` user when the API runs as
    root), never as root;
  - read-only root filesystem, all capabilities dropped, resource limits;
  - only its own clone mounted at `/work`, and `inputs/` read-only;
  - a private network that can reach only the gateway and the MCP server, with no internet egress;
  - the permission ruleset denies `pip`, `npm`, `curl`, `git push` and the like;
  - packages come only through an allow-listed, host-side `request_package` tool.
- **Host-port lockdown** needs `iptables`. GuudFlow adds rules so sandboxes can open new connections
  only to the gateway and MCP ports on the host. If the API's user cannot run `iptables`, a warning is
  logged and sandboxes could reach other services listening on the host. Run the API as a user that can
  manage `iptables`, or keep nothing sensitive listening on the host.
- **Merges are checked.** Before an agent branch is merged, the runner checks it for protected paths
  (`inputs/`, `.github/`, `opencode.json`, `.opencode/`, `.guudflow/`), symlinks and secrets. Nothing
  reaches a project's `main` until a human accepts the run (or the autopilot does, only when you turn
  that on and the check passes).
- **Production checklist:**
  - `GUUDFLOW_MODE=production`, `GUUDFLOW_ENV=production` and a strong `GUUDFLOW_API_TOKEN`;
  - Docker available;
  - the API reachable only by people you trust;
  - backups of the data directory.

### Custom certificates (TLS behind a company proxy or an internal endpoint)

Some networks re-sign TLS traffic with their own certificate authority, or serve models from an
internal endpoint with a private certificate. Do not turn verification off: give GuudFlow the CA.

1. Save the CA certificate(s) as one PEM file.
2. Set `GUUDFLOW_CA_BUNDLE=/path/to/corp-ca.pem` in `.env`. With Docker Compose, keep the file
   under the data directory (for example `data/tls/corp-ca.pem`), since only that directory is
   mounted, and use its absolute path.
3. Restart the API.

The CA is trusted **in addition to** the public roots by GuudFlow's own HTTP calls (model
providers, the LLM gateway, web search and fetch, MCP servers, GitHub), and by git, npm and
opencode on the host through the usual variables (`SSL_CERT_FILE`, `GIT_SSL_CAINFO`,
`NPM_CONFIG_CAFILE`, `NODE_EXTRA_CA_CERTS`, pointing at a combined bundle written to
`<data>/tls/ca-bundle.pem`). Variables you set yourself win. Sandboxes reach the internet only
through the host, so they need nothing.

## 10. Updating

```bash
git pull
source venv/bin/activate && pip install -r requirements.txt
(cd studio && npm install && npm run build)        # build only if you use Option A
scripts/install_opencode.sh                        # if OPENCODE_VERSION changed
```

The sandbox image is tagged with the opencode version and is built only when missing. If an update
changes `sandbox/agent-opencode.Dockerfile` without changing the version, remove the old image so it is
rebuilt on the next run:

```bash
docker rmi guudflow/agent-opencode:$(python -c "from core.harness.server import OPENCODE_VERSION as v; print(v)")
```

Then restart the API.

## 11. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Studio shows 401 / "Unauthorized" | Paste `GUUDFLOW_API_TOKEN` into the token field at the top right. |
| Studio loads but every request fails (dev) | Vite proxies to `localhost:8000` by default. Start the API there or set `GUUDFLOW_API_URL`. |
| Provider **Test** says "no key", or runs fail with 401 from the provider | No key reached the host: put it in `.env`, or delete the empty `ANTHROPIC_API_KEY=` line and save the key in Studio. An exported shell variable is overridden by `.env` (§4). Anthropic keys must belong to the default workspace. |
| "opencode not installed" | Run `scripts/install_opencode.sh` (needs npm), or set `OPENCODE_BIN` to the pinned version. |
| Runs log `DEV MODE: opencode agent running on host` | Docker is not reachable, so the run fell back to host mode. Check `docker info` works for the API's user, then restart the API. |
| "Docker is required for sandboxed agents but is not available" | Production refuses host mode. Start Docker, or don't set `GUUDFLOW_ENV=production` for local trials. |
| Sandboxed runs fail to start on Docker Desktop | Not supported (§2). On Windows, install Docker Engine inside WSL2; otherwise set `GUUDFLOW_SANDBOX=host`. |
| WSL: `docker info` fails or needs sudo | Start the daemon (`sudo service docker start`, or enable systemd in `/etc/wsl.conf`) and make sure your user is in the `docker` group (reopen the terminal after `usermod`). |
| WSL: `docker` works but sandboxes fail like on Docker Desktop | Your `docker` is Docker Desktop's WSL integration. Turn it off for this distro and install Docker Engine inside it (§4, Windows). |
| WSL: slow, or "permission" warnings about the keys file | The repo is under `/mnt/c`. Clone it into your WSL home directory instead. |
| Compose: runs fail with "no such file or directory" on a mount | The data directory is not at the same path inside and outside. Run `docker compose` from the repo root, or set `GUUDFLOW_HOST_DATA` to an absolute path. |
| Compose: Studio not reachable from another machine | The API listens on `127.0.0.1` by default. Set `GUUDFLOW_BIND=0.0.0.0` (behind your own firewall or proxy). |
| A local model (Ollama, LM Studio) fails with "Illegal header value" | Fixed: the gateway now sends no auth header to a provider without a key. Update and restart. |
| Address already in use on 8000 | An old API is still running: `lsof -ti tcp:8000` and kill it. |
| A run stopped when you edited code | You ran with `--reload`. Use `--no-reload` or plain `uvicorn` while agents run, then **Resume run**. |
| Agents say a tool is missing (pytest, node, npm) | The sandbox has Python 3.11 (standard library), git and Bun only. Use `unittest` or `bun test`, or allow-list the package (pinned version) so roles can `request_package` it. |
| "Compound commands are denied" in an agent's thinking | Fixed (`cd` is now allowed). Restart the API so new runs use the new ruleset. |
| A backlog item stays "in a run" after a run died | Runs that crash now release their items. For an older one, free it through the API: `curl -X PATCH localhost:8000/api/projects/<id>/backlog/<item> -H "Authorization: Bearer $GUUDFLOW_API_TOKEN" -H "Content-Type: application/json" -d '{"status": "open"}'`. |
| Quips say "(quip failed: …)" | The quip model (fast tier) could not be called. Check the fast tier's provider has a key. Quips never affect the run. |
| Budget meter or costs look wrong | Costs use the prices in Settings → Models & providers. Set yours to match your account. Each result's `cost` is the role's running total at the gateway. |
| The sandbox image did not pick up an update | Remove it so it is rebuilt (§10). |

Logs: the API logs to its terminal. Each run keeps its board (`data/runs/<run>/board.jsonl`), costs and
report on disk, and Studio's **Run report** shows them after the run.

## 12. Running the tests

```bash
source venv/bin/activate
pytest                                   # whole backend suite; no API key needed
pytest tests/harness/                    # harness with a fake LLM and the real pinned opencode
pytest tests/harness/test_escape_suite.py tests/harness/test_run_docker.py   # needs Docker
cd studio && npx tsc --noEmit && npx vitest run && npx playwright test
```

Tests that need the opencode binary or Docker skip cleanly when they are missing. The escape suite runs
hostile code in a real sandbox and must pass after any change to `core/harness/`, `worker/`,
`sandbox/` or `gateway/`.
