# Run findings — 2026-10-14 (WSL dev stack, Mistral via openai_compatible)

Setup: Windows 11 + WSL Ubuntu, checkout on `/mnt/c`, `run.ps1 dev` (VS Code "Run dev stack"),
host mode (`DEV MODE: opencode agent running on host`), project `build-excuse-o-matic…`,
all roles on `mistral-medium-latest` (provider `mistral`, client `openai_compatible`).

## F1 — FIXED (a67e8ef): WSL ran a Windows opencode, the agent never worked
- Evidence: `.tools/opencode` held only `opencode-windows-x64`; `.bin/opencode` is npm's
  Windows shim → `exec opencode-ai/bin/opencode.exe`. In WSL that ran through interop as a
  Windows process (WSL pid = `/init`, Windows pid listening on Windows 127.0.0.1).
- WSL → `127.0.0.1:<port>` unreachable (http 000) → `opencode serve not healthy after 180s`.
- From Windows: unsecured (`OPENCODE_SERVER_PASSWORD is not set`), providers = `opencode`
  (hosted Zen, default `big-pickle`), no `guudflow` MCP, HOME = `C:\Users\eriki`.
  Interop drops the whole locked env (config, password, HOME/XDG, flags).
- `llm_costs.jsonl` never created.
- Why the install was wrong: WSL has no Linux node/npm; `dev.sh`/`setup.sh` tested
  `[ -x .tools/opencode ]` (true for any directory) so never reinstalled.
- Fix: Linux uses `opencode-linux-*` and refuses a Windows-only install; `start()` refuses a
  server offering providers outside `enabled_providers`; installer fetches the Linux package
  from the registry (sha512) without npm; scripts ask the harness. Tests:
  `tests/harness/test_server_binary.py` (2 fail before, all pass after).
- After: Linux process, private HOME, password (401 without), providers = `mistral` only,
  `guudflow` MCP connected, agents = role + internal helpers. Model calls reach the gateway.

## F2 — Studio served a stale prebuilt dist (no Console tab)
- `dev.sh` fell back to `studio/dist` (built 2026-10-13 21:01) because WSL has no Linux
  node and `studio/node_modules` is Windows npm's. Repo itself was current (= origin/main).
- Workaround applied: `npm run build` in `studio/` on Windows; dist is platform independent.
- Not fixed in code: `dev.sh` gives no warning that the served dist is older than `studio/src`.

## F3 — PARTLY FIXED (dbd40c4): project test runner (`bun`) not available to agents
- Goal requires "Verify everything with bun test only". WSL host has no `bun` and no `node`
  (only python3, git); the sandbox image has neither.
- Developer spent turns on `which bun`, `bun --version`, `find … -name bun`, `echo $PATH`
  — all refused (only `bun test*` / `bun run *` are allowed), each a paid model call.
- Developer then asked to install `bun@1.0.0` (approval); a grant cannot work: no npm on the
  host (exit 127), and `--ignore-scripts` would not produce a bun binary anyway.
- Fix: in host mode the brief's shell section lists allowed runners missing from the host PATH
  and says to write code/tests anyway and mark unrunnable items unmet.
- Still open: nothing tells the person *before* the run that the project's runner is missing
  (a preflight against `check_command` / the spec's test rule), and the sandbox image has no
  node/bun at all.

## F4 → F7 — FIXED (24a34a6): agents could read GuudFlow's own checkout (host mode)
- Seen: `ls -la /mnt/c/.../GuudFlow-main` → ok, `ls .tools/`, `ls venv/bin`, `cd <root> && ls`.
- Cause: role `work/` had no `.git`, so opencode took the nearest enclosing repo — GuudFlow's
  own checkout, since `data/runs/` lives inside it — as its project. Live `/path`:
  `"worktree": "/mnt/c/.../GuudFlow-main"`. `external_directory: deny` never fired for anything
  in the repo (`.env`, `data/secrets/provider_keys.json`, other projects).
- Probed with the real binary: GIT_CEILING_DIRECTORIES and an empty `work/.git/` don't help; a
  bad gitfile makes worktree `/`.
- Repro test: scripted model `read`s a secret in the enclosing repo → it reached the model.
- Fix: `work/.git` is a file → empty `roles/<role>/boundary.git`; `.git` is a protected path.
  Host git still names the real git dir. Same root cause made agents resolve card paths
  against GuudFlow's root (F10) and would have pointed an agent's own `git commit` at it.

## F6 — OPEN: live cost under-reported in Studio during a turn
- Developer card "25 calls · $0.00", budget bar $0.0773 while the gateway had logged $0.28.
- `runEvents.ts` updates `costs` only on AGENT_RESULT (turn end). Display only; the gateway
  enforces the cap itself.

## F8 — RBAC refusal loops cost money
- Refused discovery commands (`which`, `find`, `echo $PATH`) each cost a model call. F3's
  brief line removes the main cause (hunting for a runtime); F7 removes wrong-root guessing.

## F9 — FIXED (d52fa85): OpenAI-compatible (Mistral) chunks failed the turn
- `Type validation failed`: Mistral streams `delta.content` as a list
  (`[{"type":"reference"},{"type":"text","text":…}]`); opencode's SDK requires a string. T2 had
  no valid result → needs_attention → overseer `restart_role` (more spend).
- Fix: the gateway joins text parts for openai_compatible upstreams (stream and JSON).

## F11 — dev "Run dev stack" (reload) kills live runs
- Any watched file change (even `tests/…`, not a `--reload-dir`) restarts uvicorn; the old
  worker stops listening (gateway + MCP) but hangs on shutdown (open websockets), so the
  run's opencode servers get `Cannot connect to API` and turns stop after 4 tries.
- Workaround: use "Run dev stack (no reload, safe for agent runs)".

## F12 — FIXED (bdb6939): killed sessions were never offered for salvage
- `interrupted_runs` treated any `meta.json` as finished, but the worker writes one at start
  (`running`; API shows `stale`). Goal-only sessions hold no backlog items so were never found.
- After fix: both killed sessions listed; salvage sent T1/T3's 4 commits / 5 files to review.

## F13 — Salvage duplicates backlog intent
- Salvaging a goal-only session added its 7 cards as new backlog items (6 → 13), next to the
  6 existing items with the same intent.

## F14 — Spec adherence shows ✓ without evidence
- Review "Against the spec": ✓ "Testing framework: Use bun test" with "0 met" though bun never
  ran on this machine.

## UX — what the user cannot see
- No live view of code while a role works; code appears only after a merge (card drawer
  "View diff", plain text) or in the review panel (raw diff in a narrow column).
- Design tab hides empty sections, so a thin design (0 interfaces/flows/decisions, as the
  Mistral tech lead produced) looks like a rendering bug.

## Test hygiene
- `tests/api/test_project_work.py::test_remote_sync_endpoint_and_summary` and
  `tests/api/test_runs.py::test_list_tags_returns_review_points` fail in WSL without a global
  git identity (pre-existing, not product bugs).

## F5 — OpenAI-compatible (Mistral) observations
- Every call ~18k–26k input tokens, `cache_read_tokens: 0` (Mistral reports no cached tokens;
  gateway reads `prompt_tokens_details.cached_tokens` correctly) → no prompt-cache savings.
- List-valued content broke turns (F9, fixed in the gateway).
- Overseer once emitted a tool call as text (`guudflow_post_noteprilis{…}`) — model quality.
- Tech lead's design was thin (components + 2 data types; no interfaces/flows/decisions).
- Tool calling, streaming and structured results otherwise worked: T1 and T3 completed
  with valid AgentResults and merged.
