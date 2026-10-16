# Changelog

## Unreleased

## 0.1.3 (2026-10-16)

The Code tab, shared project dependencies, and fixes from the WSL + Mistral runs.

- **Code tab.** Read the project's code in Studio, on the project page and in the run view
  (a Code button in the run header): the accepted `main`, this session's merged code, or one
  worker's checkout with its unmerged edits. The file tree marks added, modified and deleted
  files, and each file has a File / Diff toggle. While a worker runs, "Follow edits" opens what
  it just wrote. File names on a card open it.
- **Shared dependencies.** A worker pins packages in `package.json` or `requirements.txt` and
  calls `sync_dependencies`. The host installs each manifest once (no install scripts, binary
  wheels only), packages off the allow-list need one approval, and every worker sees the
  environment read-only at `/deps`. The Code tab lists each package, who added it and who
  approved it.
- **One code folder per project** (`data/projects/<id>/code`), kept at the accepted `main` and
  shown in the Work tab.
- **Honest results.** A turn whose tests could not run (`bun: command not found`) cannot claim
  them met. Studio says before a session which test runners the agents cannot run, and a rule
  no review answered shows as not checked.
- **Harness fixes.** WSL never starts a Windows opencode. A role's work folder is its own git
  boundary, so agents cannot read the enclosing checkout. List-valued content from
  OpenAI-compatible providers (Mistral) no longer fails turns. Roles can look around and move
  files inside their own clone. End-of-run notebooks end once saved, and the console says when
  they are being written.
- **Studio.** Live spend follows the gateway during a turn. Each role has a live Changes tab.
  Drawers are wider and can expand. Artifacts can be downloaded. A new Studio build shows up
  without a forced reload.
- **Salvage and dev.** Interrupted sessions are found for salvage, a goal-only session's
  leftover cards go to Jack's draft instead of the backlog, and the dev stack's reload no longer
  kills live runs.

## 0.1.2 (2026-10-14)

- Runs no longer hang after "opencode server listening" behind a company proxy: GuudFlow's calls
  to each worker's local opencode server ignore `HTTP(S)_PROXY`, and opencode always has
  localhost in `NO_PROXY`, so its calls to the gateway and MCP server stay on this machine.
- Each worker's opencode server is only GuudFlow's harness: `enabled_providers` limits it to
  GuudFlow's providers (opencode's own hosted models are gone), and opencode's stock agents
  (build, plan, general) are switched off. The worker's own agent does all the work, with the
  read-only explore helper.

## 0.1.1 (2026-10-14)

Setup behind a company proxy, prebuilt Studio and Python wheels, certificates, and runs that
no longer stall silently when model calls fail.

- Settings → Models & providers → Test now makes one streamed chat call with a tool for an
  OpenAI-compatible provider, the way an agent turn does, and names what fails: a rejected
  parameter (with the provider's message), no tool calling, no streaming, or a proxy that holds
  the stream back until the end.
- A team run no longer sits silent when model calls fail: opencode's retries are logged in the
  run console with the provider's message, and after `GUUDFLOW_MODEL_RETRIES` (default 4) failed
  tries in a row the turn stops with that reason. The gateway logs what the provider answered for
  any error (status and message) and the full reason when it cannot reach it.
- Certificate: `GUUDFLOW_CA_BUNDLE` takes a PEM or a binary `.cer`, and an intermediate CA works
  as the trust anchor. Studio's Settings has a **Certificate** field as a backup: used when `.env`
  has no path or its file is not found, applied from the next connection without a restart. The
  provider test shows the real TLS error and what to do.
- `GUUDFLOW_CA_BUNDLE` (and `HTTPS_PROXY`, `NO_PROXY`, `GUUDFLOW_PIP_INDEX`, ...) in `.env` now
  reach the setup tasks too, not just the API: uv, pip, npm, curl and git trust the certificate on
  top of the system roots (`scripts/net_env.sh`, and the same in `run.ps1`). A Windows path like
  `C:\certs\corp.pem` works from WSL. The Python step no longer replaces the public roots with
  the certificate alone.
- WSL mode: tasks drop the Windows folders WSL adds to `PATH` (`/mnt/c/...`), so a Windows npm,
  node or Python never runs in place of the Linux one (the cause of "'vite' is not recognized";
  `GUUDFLOW_KEEP_WINDOWS_PATH=1` keeps them). Studio's dev server is used only with a Linux npm and
  a `node_modules` made for this OS; otherwise the prebuilt Studio on :8000.
- Run dev stack on native Windows: the API window starts the venv's Python directly (a new window
  could block `Activate.ps1` and fall back to a Python without uvicorn), and Studio's dev server is
  used only when vite is installed; a partial `node_modules` from a failed npm install now means
  the prebuilt Studio on :8000 instead of "vite is not recognized".
- One Python step (`python-env`, task **Python environment**) shared by Prepare, Setup, Run dev
  stack, api and check on Linux, WSL and native Windows: it makes `./venv` and installs the
  packages, does nothing when they are already there, and stops with a clear message instead of
  "uvicorn: command not found". Behind a proxy: `GUUDFLOW_CA_BUNDLE`, `GUUDFLOW_PIP_INDEX`,
  `GUUDFLOW_PYTHON`, uv trusts the system certificate store, and the **Python wheels** GitHub Action
  builds wheels per OS to drop in `prebuilt/` for installs with no network. Prepare goes on when uv
  cannot be installed.
- Prebuilt Studio has one drop folder, `prebuilt/`, and one Studio step (`studio-ensure`) shared by
  Prepare, Setup and Run dev stack on Linux, WSL and native Windows: an archive there means npm is
  never run. The Build tasks (`build studio`, `build native`) use it too. Any `.tar.gz` or `.zip`
  name works. `studio-fetch` saves into `prebuilt/`.
- Studio without npm trouble: `studio-deps` retries and relaxes versions, `GUUDFLOW_NPM_REGISTRY`
  for a mirror, prebuilt `studio-pack` / `studio-unpack`, CI keeps the build as an artifact, and the
  dev stack serves the prebuilt Studio when the npm packages are missing.
- opencode telemetry explicitly off for every agent: `experimental.openTelemetry: false` in its
  config and `OTEL_SDK_DISABLED=true` (with sharing, auto-update and model-list fetching already off).
- Tasks and scripts across setups: one entry point (`scripts/run.sh`, `scripts/run.ps1` on Windows),
  `GUUDFLOW_WINDOWS_MODE=wsl|native`, scripts always run through bash, LF line endings enforced.
- Agent turns that crash are logged and posted (were silent); opencode startup waits 180 s, ignores
  proxies and reports its log.
- `GUUDFLOW_CA_BUNDLE`: trust a custom certificate authority (public roots kept, verification
  never off) in GuudFlow's HTTP calls, git, npm and opencode.
- Plan tab streams Jack's changes while he drafts.
- Run console: each run keeps GuudFlow's own log lines (`console.jsonl`), explains at the end who
  ran and who never started and why, and returns each role's server log; Studio's run view has a
  Console tab.
- Overseer bar under the run graph with a Wake overseer button, or the reason a run has none.
- Jack: picks his model and effort per project, sees a "Minimum team" line and proposes missing
  roles, parks left-out changes with computed fixes (*Accept the suggestion*), and repair rounds
  no longer repeat his summary. Fixed: host-side calls ignored the configured models and always
  used Anthropic.
- Studio: confirm before a goal-only run skips open backlog items; Ask Jack to change the team.
- Setup: uv for the Python packages, `install_uv.sh`, `doctor.sh`, `stop.sh`, `build.sh`,
  `check.sh`, VS Code tasks (run through bash; on Windows that is WSL).

## 0.1.0

First tagged release. GuudFlow runs a named team of agents on an ongoing project: you plan with
the project manager, approve the team, and accept or reject each finished session.

**Agents and runs**
- opencode harness: one sandboxed server per role, a scheduler, a task board with review loops,
  and an overseer that wakes on stuck detectors. Keys stay on the host behind an LLM gateway.
- Ongoing projects: a long-lived repo and a backlog; a run starts from `main` and reaches it only
  when a person accepts. Continuing a run carries only unfinished items.
- Flow hardening (`docs/FLOW_HARDENING_PLAN.md`): manual checks are the owner's and never block a
  card, a card blocked on a finished role wakes the overseer, a stopped run writes `stopped.json`
  and says why, and workers see the shell commands they may run.

**Planning**
- Jack, the project manager, edits a versioned spec through drafts a person ticks. A turn that
  has a bad change keeps its good ones, repairs only the bad ones, records every call's cost, and
  parks what it cannot fix with a computed fix to accept (`docs/PM_SELF_REPAIR_PLAN.md`).
- Role contracts, a design artifact, a verification ledger, a knowledge base and project kinds.

**Models**
- Providers, models, prices and effort levels are edited in Studio. Host-side calls send effort.

**Studio**
- Team approval, live run graph, review queue with owner checks and the stop reason, plan
  conversation with spend, backlog, settings.

**Known limits**
- Replaying the real session-5 planning turns as fixtures (J7) is not done; unit tests cover each
  failure shape.
- Briefs are the same length for small and large models.
- opencode writes its own refusal text, so a refused shell command is not named by segment.
- The legacy harness is unsupported.
