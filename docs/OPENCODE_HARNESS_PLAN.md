# GuudFlow on opencode — the thin-orchestrator rebuild

**Status:** plan, not started. Written 2026-10-04.
**Goal:** keep what is distinctive about GuudFlow (scheduler, board, personas, Studio,
quips, example projects) and replace the home-built agent harness with
[opencode](https://github.com/anomalyco/opencode) running headless, one sandboxed
instance per agent. GuudFlow becomes the manager: it decides *who* runs *when*, with
*what permissions*, and records *what happened*. opencode does the actual agent work.

---

## 1. Why (the short version)

The scheduler (`core/scheduler.py`) is sound and well tested. What failed in practice
is the layer underneath it:

| Problem | Where | Effect in real runs |
|---|---|---|
| Home-built agent loop | `core/runner.py:3253` `_generic_tool_use_loop` (~520 lines) inside a 5,518-line runner | Every model quirk became a runner patch; 26 of 57 commits are `fix(...)` |
| Free-text completion contract | `[COMMIT]/[VERIFY]/[ASK]` blocks parsed by `core/protocol.py` (1,248 lines) | Retries, a cleanup model, and silently empty answers (see `.claude/future_buff/bugs/`) |
| Synchronous peer ASK | ASK suspends the asker for 90–120s (`core/runner.py:2040`, `:2098`); timeout → FAILED (`core/scheduler.py:326`) | Circular ASK cascades and stalls |
| Off-scheduler side tasks | `asyncio.create_task` at `core/runner.py:4642, 5011, 5286, 5414` (+ `ensure_future` at 4358) | Breaks hard constraint #2; work happens where the scheduler can't see it |
| Mocked-only tests | 1,773 tests, all LLM calls mocked | Proves the plumbing, not the behaviour |

The fix is not "patch the loop harder". It is: **stop owning the loop.**

---

## 2. Research findings (verified against opencode 1.18.34)

Everything here was checked against a real `opencode serve` (installed from npm
`opencode-ai@1.18.34`), its live OpenAPI spec (`GET /doc`), and a scripted fake
OpenAI-compatible LLM that issued exact tool calls. Nothing below comes only from docs
or memory.

### 2.1 What opencode gives us for free

| Need | opencode feature | Notes |
|---|---|---|
| Run agents headless | `opencode serve --port --hostname` | One server can serve many directories via `?directory=` on every request |
| Start an agent turn | `POST /session` then `POST /session/{id}/message` (sync) or `/prompt_async` | Body takes `agent`, `model`, `system`, `tools` (per-call on/off), `format` |
| **Structured completion** | `format: {type: "json_schema", schema, retryCount}` | Opencode forces `tool_choice: required` and injects a `StructuredOutput` tool; result lands in `AssistantMessage.structured` |
| Per-session permissions | `POST /session` accepts `permission: [{permission, pattern, action}]` | Lets GuudFlow attach a role's ruleset to each session at creation |
| Live events | `GET /event` (SSE): 89 event types incl. `message.part.updated`, `session.next.tool.called/success/failed`, `permission.asked`, `session.idle`, `session.error`, `session.compacted`, `file.edited`, `todo.updated` | Feeds the board, quip feed, and Studio with no polling |
| Human-in-the-loop on tool use | `permission.asked` event + `POST /permission/{id}/reply {reply: once\|always\|reject}` | Maps directly onto the existing approval gate |
| Stop a runaway agent | `POST /session/{id}/abort` | |
| Context management | Auto-compaction (`compaction: {auto, prune, tail_turns, preserve_recent_tokens, reserved}`) + `POST /session/{id}/summarize` | Replaces GuudFlow's `_compact_messages_after_close` |
| Cost & tokens | `AssistantMessage.cost`, `.tokens {input, output, reasoning, cache}` | Replaces iteration-metrics plumbing |
| Diffs | `GET /session/{id}/diff`, `GET /vcs/diff` | |
| Agent definitions | `agent.<name>: {model, prompt, permission, steps, temperature, mode}` | Personas compile to these |
| Any model | `provider.<id>.options.baseURL` + any AI-SDK provider | Point it at GuudFlow's LLM gateway (see §5.4) |
| Server auth | `OPENCODE_SERVER_PASSWORD` → HTTP Basic (verified: 401 without creds) | |

### 2.2 Gotchas that the design must handle (all reproduced)

1. **Default permissions are wide open.** Every built-in agent ships with
   `{"*": "*", action: "allow"}`, including unrestricted bash.
2. **Project config is loaded from the working directory.** An `opencode.json` that an
   agent writes into its own worktree was loaded by the server: it added a new agent
   with `"*": "allow"`. That is a self-escalation path.
   **Mitigation (verified):** `OPENCODE_DISABLE_PROJECT_CONFIG=1` + inject config
   through `OPENCODE_CONFIG_CONTENT`. The injected `evil` agent no longer appeared.
3. **Host config leaks in.** On a default launch opencode picked up skills from the
   host's `~/.claude/skills`. **Mitigation:** isolated `HOME`/`XDG_*` dirs,
   `OPENCODE_DISABLE_CLAUDE_CODE=1`, `OPENCODE_DISABLE_EXTERNAL_SKILLS=1`.
4. **Bash permissions are guardrails, not a boundary.** The bash checker is good: it
   splits compound commands (`git status && python3 -c …` → `python3` denied) and
   catches `$(…)` substitution. But **redirections are not checked**: with only
   `"echo *": "allow"` and `external_directory: "deny"`, these all succeeded and wrote
   outside the worktree:
   - `echo owned > ../outside_redirect.txt`
   - `echo owned > /abs/path/outside_abs.txt`
   - `git status --porcelain > /abs/path/outside_git.txt`

   And any "allowed" test runner (`pytest`, `npm test`) executes arbitrary project code
   anyway. **Conclusion: real isolation must be OS-level (§5).** opencode permissions
   are the *policy* layer that keeps cooperative agents on-task.
5. **`"*": "deny"` hides the `StructuredOutput` tool.** With a deny-all base the tool
   disappeared from the model's tool list and every structured call failed. The role
   ruleset must explicitly include `"StructuredOutput": "allow"`.
6. **Structured output is not schema-validated.** `{"status": "maybe"}` was accepted
   for a schema with `enum: [done, blocked]` and a required `summary`. GuudFlow must
   validate `structured` itself (Pydantic) and re-prompt on failure.
7. **A plain-text reply fails with no retry.** When the model answered in text instead
   of calling `StructuredOutput`, the message ended with
   `StructuredOutputError {retries: 0}` despite `retryCount: 2`. GuudFlow's runner
   owns exactly one bounded retry (§4.3).
8. **Memory footprint.** One idle-ish `opencode serve` measured ~500–600 MB RSS. Six
   agents with one server each is ~3.5 GB. Budget containers at 1 GB and cap parallelism
   (§5.6).
9. **Under `"*": "deny"` the internal `compaction`/`title`/`summary` agents also inherit
   deny.** They don't use tools, so this should be harmless; P0 must confirm that
   auto-compaction still works under the locked config.

---

## 3. Target architecture

```
┌──────────────────────────── host (GuudFlow) ─────────────────────────────┐
│                                                                           │
│  Studio (React) ◄─ws─ api/ ◄── AgentRunner (thin) ──► Scheduler (as-is)   │
│                                   │      ▲                                │
│                 spawn/abort/prompt│      │SSE events → board signals      │
│                                   ▼      │                                │
│   ┌──────────── per-agent sandbox (container, one per role) ───────────┐  │
│   │  opencode serve  (pinned 1.18.x, locked config, password)          │  │
│   │  /work  = this role's own git clone (rw)                           │  │
│   │  /inputs = run inputs (ro)                                         │  │
│   │  network: internal only ──► gateway:4000, guudflow-mcp:4100        │  │
│   └────────────────────────────────────────────────────────────────────┘  │
│                                   │                                       │
│   LLM gateway (host) ◄────────────┘  holds real API keys; per-agent       │
│                                      virtual key; model allowlist; budget │
│   GuudFlow MCP server (host) ◄────── board tools; token → role; RBAC      │
│                                                                           │
│   Git: bare run repo on host. Runner fetches agent/{role} from each       │
│        sandbox clone after a turn, checks it, merges. Agents never        │
│        touch main or anyone else's branch.                                │
└───────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Responsibilities after the rebuild

| Concern | Owner |
|---|---|
| Who runs when, deps, joins, groups, human pause, max iterations | **GuudFlow scheduler** (unchanged) |
| Team proposal, personas, memory, reputation, quips | **GuudFlow** (unchanged) |
| Permission profile per role, sandbox lifecycle, credentials | **GuudFlow** (new: `core/harness/`) |
| Branch merges, conflict handling, deliverables | **GuudFlow runner** (simplified) |
| Tool-use loop, file edits, shell, LSP, compaction, retries, provider quirks | **opencode** |
| Completion contract | **opencode structured output** + GuudFlow Pydantic validation |

---

## 4. The agent turn contract

### 4.1 One spawn = one opencode session = one structured result

The scheduler's runner contract is already `Callable[[Workspace], Awaitable[None]]`
(`core/scheduler.py:34`). The new `OpencodeAgentRunner` implements exactly that:

1. Ensure the role's sandbox is up (§5) and its clone is synced to the latest merged base.
2. `POST /session` with `{title, agent: <role>, permission: <role ruleset>}`.
3. `POST /session/{id}/prompt_async` with the task brief + `format: json_schema(AgentResult)`.
4. Bridge SSE events to board signals until `session.idle` or `session.error`.
5. Read the final `AssistantMessage.structured`, validate with Pydantic (§4.3).
6. Fetch `agent/{role}` from the sandbox clone, run the branch checks (§5.5), then post
   `GIT_COMMIT` / `COMPLETE` / `BLOCKED` / `FAILED` to the board.

There is no tool loop, no protocol parser, and no nudges in GuudFlow.

### 4.2 Result schemas (replace `[COMMIT]/[VERIFY]/[ASK]`)

```python
class AgentResult(BaseModel):          # builders, architects, testers
    status: Literal["done", "blocked", "failed"]
    summary: str = Field(min_length=20)        # what was done, in one paragraph
    files_changed: list[str] = []
    blocked_on: BlockedOn | None = None        # required when status == "blocked"
    notes_for: dict[str, str] = {}             # role -> async note (posted to board)

class BlockedOn(BaseModel):
    role: str            # which upstream role must act
    need: str            # concrete ask, >= 1 sentence

class ReviewResult(BaseModel):          # reviewers
    verdict: Literal["pass", "fail"]
    issues: list[Issue] = []                   # required non-empty when verdict == "fail"

class Issue(BaseModel):
    file: str
    detail: str = Field(min_length=20)         # keeps the "FAIL must cite specifics" rule
```

The existing rule that a FAIL verdict must carry specific detail is kept, as a validator
instead of a regex.

### 4.3 Validation and the single retry

- `structured` is missing (`StructuredOutputError`), or Pydantic validation fails →
  send **one** follow-up message in the same session: the validation error plus "call
  StructuredOutput again with a corrected result". Same schema.
- It fails a second time → the role is `FAILED` with the validation error as detail. The
  loop is the error handler (hard constraint #8): the reviewer or escalation path deals
  with it.

### 4.4 No synchronous peer communication

The ASK/ANSWER handshake, `WAITING_FOR_PEER`, `_generic_peer_answer`, and the off-loop
peer tasks are removed. Instead:

- **Async notes:** `notes_for` and the `post_note` MCP tool write board signals. A role
  sees notes addressed to it in its brief the next time it is spawned. Nobody waits.
- **Blocked:** a role that genuinely can't continue returns `status: "blocked"` with
  `blocked_on`. The scheduler re-arms `blocked_on.role` with the `need` prepended to its
  brief, and re-arms the blocked role once that completes. Bounded by `max_iterations`.
  A cycle (A blocked on B, B blocked on A) is detected and goes to `HUMAN_NEEDED`.

This removes every path that produced the circular-ASK bug.

### 4.5 Reviewer loop as a scheduler edge

Today a rejection re-runs the author from a board subscriber
(`_on_signal_for_rejection_redispatch`). Instead, the scheduler gets a declared
**review edge**: `review_of: <author_role>, max_rounds: 3`. When the reviewer returns
`fail`, the scheduler re-arms the author with the issues as its brief, then re-arms the
reviewer. When rounds run out → escalation (tech lead) → human. All of it is visible to,
and counted by, the scheduler. Scheduler tests are extended for it (§8).

---

## 5. RBAC and sandboxing — defence in depth

Each layer assumes the ones above it have failed.

| # | Layer | Stops | Enforced by |
|---|---|---|---|
| 1 | opencode permission ruleset | A cooperative model wandering off-task (wrong tool, wrong path) | opencode, per session |
| 2 | Locked opencode config | Agent rewriting its own permissions or loading host skills | env flags (§5.2) |
| 3 | Container | Bash/redirect/test-runner escapes reaching the host | Docker (+ gVisor where available) |
| 4 | Network policy | Exfiltration, untrusted downloads, calling other agents' servers | internal Docker network + egress proxy |
| 5 | Credentials | Agents holding real API keys or tokens | LLM gateway + per-agent virtual keys |
| 6 | GuudFlow MCP RBAC | An agent posting as another role or using board tools it wasn't granted | Per-agent bearer token → role, checked server-side |
| 7 | Git branch ownership | Changing `main` or another role's branch, touching protected paths | Runner-side fetch + checks (§5.5) |
| 8 | Budgets | Runaway cost or time | Gateway spend caps, opencode `steps`, `doom_loop: deny`, scheduler deadline |

### 5.1 Role permission profiles (layer 1)

Profiles are generated from the role's existing RBAC scopes (`AgentConfig` +
`core/tool_manifest.py` `allowed_scopes`), so the RBAC vocabulary stays the same.
Every profile starts deny-all and adds:

```jsonc
// common base, all roles
{ "*": "deny",
  "StructuredOutput": "allow",     // gotcha #5
  "read": "allow", "glob": "allow", "grep": "allow", "list": "allow", "lsp": "allow",
  "todowrite": "allow",
  "external_directory": "deny",
  "webfetch": "deny", "websearch": "deny",
  "task": "deny",                  // no opencode subagents in v1; GuudFlow owns fan-out
  "question": "deny",              // humans are reached via the ask_human MCP tool instead
  "doom_loop": "deny",             // a looping agent fails fast; the scheduler handles it
  "guudflow_*": "allow" }          // MCP tools; real RBAC is enforced server-side (layer 6).
                                   // Not yet verified: the exact permission key opencode uses
                                   // for MCP tools. P0 confirms it.
```

| Role type | `edit` | `bash` (last match wins) | Extra |
|---|---|---|---|
| architect / planner | `{"*":"deny", "docs/**":"allow", "*.md":"allow"}` | `{"*":"deny", "git status*":"allow", "git diff*":"allow", "git log*":"allow"}` | — |
| builder (backend/frontend) | `{"*":"allow", "inputs/**":"deny", ".github/**":"deny", "opencode.json":"deny", ".opencode/**":"deny"}` | `{"*":"ask", "git *":"allow", "git push*":"deny", "pytest*":"allow", "python -m pytest*":"allow", "npm test*":"allow", "npm run *":"allow", "ls*":"allow", "cat *":"allow", "pip *":"deny", "npm install*":"deny", "curl *":"deny", "wget *":"deny"}` | Installs go through the IT agent |
| tester | `{"*":"deny", "tests/**":"allow", "**/*.test.*":"allow", "**/test_*.py":"allow"}` | same test-runner allow-list as builder | — |
| reviewer | `"deny"` | `{"*":"deny", "git diff*":"allow", "git log*":"allow", "git show*":"allow", "pytest*":"allow"}` | Read-only by construction |
| IT agent | `{"*":"deny", "requirements*.txt":"allow", "package.json":"allow"}` | `{"*":"deny", "pip install *":"allow", "npm install *":"allow"}` | Only role with package-mirror egress (§5.4) |

`ask` resolves through the existing approval gate (`_request_approval`): GuudFlow
receives `permission.asked` on the event stream, posts `APPROVAL_NEEDED`, and replies
`once` or `reject` from the human's decision. A per-project allow-list can auto-reply
`once` for known-safe patterns, the same way packages are auto-approved today.

### 5.2 Locked opencode launch (layer 2)

```bash
HOME=/home/agent  XDG_CONFIG_HOME=/home/agent/.config  XDG_DATA_HOME=/home/agent/.data
OPENCODE_CONFIG_CONTENT='<generated JSON: provider→gateway, agent defs, base permission>'
OPENCODE_DISABLE_PROJECT_CONFIG=1     # gotcha #2
OPENCODE_DISABLE_CLAUDE_CODE=1        # gotcha #3
OPENCODE_DISABLE_EXTERNAL_SKILLS=1
OPENCODE_DISABLE_AUTOUPDATE=1
OPENCODE_DISABLE_SHARE=1
OPENCODE_DISABLE_MODELS_FETCH=1
OPENCODE_SERVER_PASSWORD=<random per sandbox>
opencode serve --hostname 0.0.0.0 --port 4096
```

Plus `"share": "disabled"` and `"autoupdate": false` in the config itself. The version
is pinned in the image; a contract test diffs `GET /doc` against a checked-in snapshot
so upgrades are deliberate.

### 5.3 Container hardening (layer 3)

Extends what `worker/docker_manager.py` already does for `code:exec`:

- Image `guudflow/agent-opencode:<pin>`: Debian slim + git + python + node + the pinned
  opencode binary. Non-root `agent` user (uid 10001).
- `read_only: true` root filesystem; writable only at `/work` (bind mount) and
  `/tmp`, `/home/agent` (sized tmpfs).
- `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, default seccomp profile.
- `mem_limit: 1g` (gotcha #8), `pids_limit: 256` (test runners and LSPs fork), `cpus: 1`.
- `runtime: runsc` (gVisor) when the host has it; plain runc otherwise, with a startup
  log line saying which.
- Mounts: `/work` = this role's clone (rw), `/inputs` (ro). **Never** the host Docker
  socket, the GuudFlow repo, `data/personas`, or another role's clone.
- Production refuses to start without Docker, as `worker/` already does today.
- Dev mode without Docker: allowed only with `GUUDFLOW_UNSANDBOXED=1`, logged on every
  spawn as `⚠️ DEV MODE: opencode agent running on host. Do not use with untrusted input.`
  This matches the existing `code_exec` dev-mode rule.

### 5.4 Network and credentials (layers 4 and 5)

- Each run gets a Docker network with `internal: true`. Sandboxes reach exactly two
  hosts: the **LLM gateway** and the **GuudFlow MCP server**. Sandboxes cannot reach
  each other: per-sandbox `iptables`/network-policy rules, or a separate network per
  sandbox shared only with the two services.
- **LLM gateway**: a small FastAPI passthrough (or LiteLLM proxy) on the host. It holds
  the real provider keys. Each sandbox gets a **per-agent virtual key** that encodes
  `(run_id, role)`, an allowed model list, and a spend cap. The opencode config sets
  `provider.<id>.options.baseURL = http://gateway:4000/v1` and `apiKey = <virtual key>`.
  Keys are revoked at run end. This keeps the existing rule that real API keys never
  enter containers, and gives exact per-agent cost accounting.
- **IT agent only**: additional egress to a package mirror/proxy that enforces the
  project allow-list (pinned versions). No other role has any internet egress.
- `webfetch`/`websearch` stay denied in v1. A later version can route them through a
  gateway endpoint with a URL allow-list.

### 5.5 Git ownership (layer 7)

opencode git worktrees share the parent `.git`, so a mounted worktree would expose every
branch. Instead:

- The host holds a **bare run repo**. Each sandbox gets its own `git clone` of it,
  checked out on `agent/{role}`. The sandbox has no remote credentials; `git push` is
  denied by policy and has nowhere to go anyway.
- After each turn the **runner** does `git fetch <sandbox clone> agent/{role}` into the
  bare repo, then checks it:
  - only `agent/{role}` moved (other refs in the clone are ignored);
  - no changes under protected paths (`inputs/`, `.github/`, `opencode.json`,
    `.opencode/`, `.guudflow/`);
  - `core/secret_scan.py` passes on the diff;
  - if the agent left uncommitted changes, the runner commits them itself with a
    conventional message (replaces the old auto-commit/claim machinery).
- A check failure → the turn counts as `failed` with the reason, and the branch is not
  merged. The runner merges into `main` as today; agents never merge.

### 5.6 MCP RBAC and budgets (layers 6 and 8)

- The **GuudFlow MCP server** (remote MCP over HTTP) exposes `post_note`, `read_board`,
  `ask_human`, `request_package`, `request_visual_feedback`, `report_progress`. Each
  sandbox's MCP config carries a bearer token bound to `(run_id, role)`. The server
  derives the caller's role **from the token, never from arguments**, and checks the
  role's existing RBAC scopes (default-deny, same `CredentialsBroker` rules as DESIGN §30).
- Budgets: gateway spend cap per agent and per run; opencode `agent.<role>.steps`
  caps tool steps per turn; `doom_loop: deny`; scheduler `max_iterations` and the run
  deadline; a per-turn wall clock that calls `POST /session/{id}/abort`.
- Parallelism cap: `max_parallel_sandboxes` (default 3), enforced by the scheduler,
  because each sandbox costs about 1 GB.

---

## 6. What changes in the codebase

### 6.1 New

| Path | Purpose |
|---|---|
| `core/harness/opencode_client.py` | Typed async client for the ~12 endpoints used (session, prompt_async, abort, message, permission reply, diff, event SSE) |
| `core/harness/opencode_runner.py` | `OpencodeAgentRunner`, the scheduler runner callable (§4.1) |
| `core/harness/event_bridge.py` | SSE → board `Signal`s + quip triggers + Studio websocket |
| `core/harness/permissions.py` | Role scopes → opencode ruleset (§5.1); unit tested per role |
| `core/harness/results.py` | `AgentResult` / `ReviewResult` + validation + retry prompt |
| `core/harness/config_gen.py` | Generates `OPENCODE_CONFIG_CONTENT` (provider → gateway, agent defs from personas) |
| `worker/sandbox_manager.py` | Start/stop hardened opencode containers, networks, clones (generalises `docker_manager.py`) |
| `gateway/` | LLM gateway: virtual keys, model allow-list, spend caps, cost log |
| `api/mcp_server.py` | GuudFlow MCP server (board tools, token → role, RBAC) |
| `sandbox/agent-opencode.Dockerfile` | Pinned agent image |
| `tests/harness/fake_llm.py` | Scripted OpenAI-compatible LLM, the same technique used for this plan's research |

### 6.2 Kept as-is (or nearly)

`core/scheduler.py` (plus the review edge and blocked re-arm), `core/workspace.py`,
`core/signals.py`, `agents/persona.py` + memory, `agents/builtin/memory_writer.py`,
`core/quip_engine.py`, `core/meta_proposal.py`, `core/credentials_broker.py`,
`core/secret_scan.py`, all of `studio/`, `data/`, `api/` routes.

### 6.3 Retired once the new harness is default

`_generic_tool_use_loop` and the peer/skip/escalation side tasks in `core/runner.py`;
most of `core/protocol.py` (ASK/ANSWER/SKIP parsing); `core/loop_guard.py`;
`core/edit_blocks.py`; `core/context_manager.py` + `core/repo_map.py` (opencode has its
own context handling and LSP); most of `agents/tools/` (fs, code, git, board are replaced
by opencode built-ins + MCP); `core/llm/` (replaced by the gateway). Expect `core/runner.py`
to drop from ~5.5k lines to roughly 1k.

Everything stays behind a project setting `harness: "legacy" | "opencode"` until P6, so
old runs can still be replayed.

### 6.4 Hard constraints that change (needs your sign-off)

CLAUDE.md says not to make architectural guesses, so these are called out explicitly:

| # | Current rule | Proposed |
|---|---|---|
| 13 | All file I/O goes through `FileWorkspace` | Agent file I/O happens inside the sandbox via opencode; GuudFlow-side I/O still uses `FileWorkspace`. The sandbox mount *is* the path-safety boundary |
| 18 | One tool entrypoint: `BaseAgent.call_tool` | One tool *policy*: the generated opencode ruleset + MCP RBAC. Agents never see credentials (rule 17 is unchanged) |
| 19 | Built-in tools namespaced `fs:*`, `code:*` … | Scopes keep their names; `permissions.py` maps them onto opencode permission keys |
| 20 | Advisory file claims | Dropped: each role has its own clone and branch, so no shared working tree to claim |
| — | Protocol rules (`emit_commit`, `read_protocol`, ASK) | Replaced by structured results (§4.2) and async notes (§4.4) |

Rules 1–12 and 14–17 and 21–24 stay as they are.

---

## 7. Milestones

Each phase ends in a demo-able state. Follow CLAUDE.md: commit per change, tests per module.

**P0 — Spike and test harness (1–2 days)**
- `tests/harness/fake_llm.py` (scripted tool calls, SSE streaming), as used in §2.
- A pytest fixture that boots a pinned `opencode serve` with the locked config against the fake LLM.
- Contract tests: session create, structured output (valid, invalid, and plain-text
  cases), permission deny, `permission.asked` round-trip, abort, `/doc` snapshot.
- Confirm gotcha #9 (compaction works under deny-all).
- *Exit:* `pytest tests/harness/` green offline with no API key.

**P1 — One real agent end to end (2–3 days)**
- `opencode_client`, `results`, `permissions`, `OpencodeAgentRunner`; host mode only
  (`GUUDFLOW_UNSANDBOXED=1`).
- One builder role on `data/example_projects/cli-tool-from-spec` with a real model.
- *Exit:* the board shows STARTED → tool events → GIT_COMMIT → COMPLETE, and
  `agent/builder` holds working code.

**P2 — Live in Studio (1–2 days)**
- `event_bridge`: tool calls, text deltas, permission asks, and errors become board
  signals; quips fire on agent complete and review verdict; approvals answer
  `permission.asked`.
- *Exit:* watch a run live in the existing Run view; approve a bash `ask` from the UI.

**P3 — Sandboxes, gateway, MCP (3–4 days)**
- Agent image, `sandbox_manager`, internal network, per-role clones + runner-side fetch
  and checks (§5.5), LLM gateway with virtual keys, MCP server with token → role.
- **Security regression suite** (fake LLM, runs in CI): replays every escape from §2.2 #4
  (redirects to `../`, absolute paths, `$(…)`), writes `opencode.json` into the worktree,
  reads `/etc/passwd` and the host home, `curl`s the internet, calls another sandbox's
  port, posts to MCP as another role, edits `inputs/`, commits to `main`. Every case
  must be contained (the fake LLM drives the attempts; assertions check the host).
- *Exit:* the suite passes; a run's sandboxes have no route to anything but gateway + MCP.

**P4 — Multi-agent flow (3–4 days)**
- Scheduler: review edges (§4.5), blocked re-arm and cycle detection (§4.4),
  `max_parallel_sandboxes`.
- New scheduler tests alongside the existing six.
- *Exit:* planner → (backend ‖ frontend) → reviewer, with one forced review failure that
  loops once and passes, on `fullstack-web-app`.

**P5 — Meta-agent and personas on the new harness (2 days)**
- The meta proposal emits roles with scopes; `config_gen` compiles personas
  (7-layer prompt, memory recall, project rules) into `agent.<role>.prompt`.
  memory_writer reads opencode transcripts (`GET /session/{id}/message`).
- *Exit:* all six example projects run from the Studio gallery; quality gate from
  `.claude/future_buff/functional_verification.md` passes on two of them.

**P6 — Make it the default (1–2 days)**
- Flip `harness` default to `opencode`; retire §6.3 code in small commits; update
  DESIGN.md, CLAUDE.md, and the docs/diagrams.

Rough total: 2.5–3.5 weeks of focused work; P0–P2 alone already gives a watchable demo.

---

## 8. Testing strategy

- **Unit:** `permissions.py` (each role profile; last-match ordering; StructuredOutput
  always allowed), `results.py` (every validator), `config_gen.py`.
- **Contract (P0):** against a real pinned opencode binary + fake LLM; no network, no key.
- **Scheduler:** the existing 6 must stay green, plus review edge, blocked re-arm, cycle
  → HUMAN_NEEDED, and parallelism cap.
- **Security (P3):** the escape suite above, run in CI on every change to `core/harness/`,
  `worker/`, `sandbox/`, or `gateway/`.
- **Quality gate (P5):** real model, 1–2 reference projects, run before releases only
  (costs money).

---

## 9. Risks and open questions

| Risk | Mitigation |
|---|---|
| opencode moves fast; APIs churn (many endpoints are `/experimental`) | Pin the version; use only stable endpoints; `/doc` snapshot contract test; upgrade deliberately |
| ~1 GB per sandbox | `max_parallel_sandboxes`; reviewers could share one read-only sandbox later |
| Weak models still fail structured output | Model floor per role tier; one validated retry; failures go through the reviewer/escalation loop, not more runner code |
| gVisor not available on dev machines (macOS) | Docker Desktop's VM is the boundary there; runsc is used in production Linux |
| Personas' 7-layer prompt vs opencode's own system prompt | Put persona layers in `agent.<role>.prompt`; P5 measures whether opencode's base prompt conflicts |
| Compaction loses detail | The board and git are the record; the brief re-injects what matters on each spawn |

**Open questions for you:**
1. Is one container per agent acceptable for the demo (memory), or should P1–P2 start in
   host mode and add containers in P3, as planned?
2. Which models should be the floor for the demo roles (e.g. Sonnet-class for builders,
   Haiku-class for quips)?
3. OK to retire the ASK/ANSWER handshake entirely (§4.4)? It is the biggest behaviour change.

---

## Appendix: how the research was done

1. `npm i opencode-ai@1.18.34`; `opencode serve`; dumped `GET /doc` (OpenAPI, ~479 KB).
2. Compared `GET /agent` on a default server vs a locked one, with an attacker
   `opencode.json` placed in the working directory.
3. Wrote a ~50-line scripted OpenAI-compatible server (one canned tool call per request,
   SSE streaming) and pointed opencode at it via `provider.fake.options.baseURL`.
4. Drove sessions through the HTTP API and checked the tool states and the host
   filesystem after each scripted call (the bash, redirect, and external-path cases in §2.2).
5. Sent `format: json_schema` with valid, invalid, and plain-text model replies; inspected
   `AssistantMessage.structured` / `.error` and the request's tool list and `tool_choice`.
6. Measured RSS of running servers.
