# GuudFlow on opencode — the thin-orchestrator rebuild

**Status:** written 2026-10-04; largely implemented. Per-task status is in `OPENCODE_WORK_BREAKDOWN.md` ("Status").
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
| Cheap codebase search | Built-in read-only `explore` subagent (listed by `GET /agent`), `subagent_depth` config key, `task` permission | Used by builders under `subagents: explore_only` (§5.9) |
| Sticky approvals | Permission reply `once \| always \| reject`; `GET /api/permission/saved` | "always" answers stop repeat prompts (§5.9) |
| Built-in web search | Env flags `OPENCODE_ENABLE_EXA`, `OPENCODE_WEBSEARCH_PROVIDER` exist in the binary | **Not tested.** Not used: web access goes through GuudFlow's host-side MCP tools instead (§5.8) |

### 2.1a Event → board signal mapping (O5)

The event bridge (`core/harness/event_bridge.py`) turns one role's opencode event stream
into board signals. A real recorded stream lives in
`tests/harness/fixtures/events_builder_turn.jsonl`.

| opencode event | Condition | Board signal | Summary / detail |
|---|---|---|---|
| `message.part.updated`, part `tool` | state `completed` or `error`; tool not `guudflow_submit_result`, `StructuredOutput` or `todowrite` | `TOOL_CALL` | `"{role}: {tool} {target} → ok/error"`; detail JSON `{tool, status, input (≤2 KB), output preview (≤1.5 KB) or error, duration_ms}` |
| `message.part.delta` (field `text`) and `message.part.updated` part `text` | parts of **assistant** messages only | buffered per message | — |
| `message.updated`, assistant, `time.completed` set | buffered text for that message is non-empty | `THINKING` | first 160 chars; detail = full text (≤4 KB) |
| `todo.updated` | list differs from the last one posted | `AGENT_TODO` | `"{role}: {n} todos ({k} done)"`; detail JSON todo list |
| `session.compacted` | — | `THINKING` | `"{role}: context compacted"` |
| `session.error` | — | `THINKING`, priority high | `"{role}: error — {message}"` (the runner decides whether the turn failed) |
| `permission.asked` / `.replied` | — | handled by the approval gate (O6), not the bridge | — |
| everything else | `plugin.added`, `catalog.updated`, `session.status`, `session.diff`, `session.updated`, `file.*`, step and patch parts, user-message parts, … | dropped | — |

Quips are triggered by the run (role complete, review verdict), not by the bridge.

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

10. **Listing messages breaks once `format` is used** (found in O2). After any prompt that
    sent `format` (even `{"type": "text"}`), `GET /session/{id}/message` returns
    `400 Expected OutputFormatJsonSchema` because the server fails to re-encode the stored
    user message. The `/api/session/...` endpoints return empty lists (separate store).
    **Design consequence:** GuudFlow builds each session's transcript from the `/event`
    stream (the event bridge records every part), never from the message list. A contract
    test pins the bug so an upgrade that fixes it is noticed.
11. **`subagent_depth: 1` does not stop nesting, and subagents inherit the role's rules**
    (found in O2). An `explore` subagent could spawn another explore and was offered `edit`.
    **Mitigation (verified):** `agent.explore.permission = {task: deny, edit: deny, bash:
    read-only}` in the generated config. config_gen always emits it.

12. **Forced tool use is rejected by the newest Claude models** (found while choosing models
    for P1). opencode's structured output forces `tool_choice: required` (seen in the fake-LLM
    request log), which the Anthropic provider sends as forced tool use. Claude Opus 5.5,
    Sonnet 5.5 and Fable 5.1 return HTTP 400 for forced `any`/`tool` choice.
    **Design consequence:** GuudFlow does **not** use opencode's `format`. Agents finish by
    calling the MCP tool `guudflow_submit_result` (normal `auto` tool choice). The MCP server
    validates the payload in the tool call and returns validation errors straight to the model
    in the same turn. This also sidesteps gotcha #10. §4.1 and §4.3 are updated accordingly.

13. **An agent with a shell can reach its own opencode server** (found in O10). The
    server password is in the container's environment, so `python3 -c` can call
    `127.0.0.1:4096` (read `/config`, answer its own permission asks). Layer 1 is therefore
    advisory for roles with `shell: project`; it is not a boundary.
    **Design consequence:** nothing security-relevant may depend on opencode's permission
    rules alone. The boundaries are host-side: the container (read-only root, no caps,
    non-root uid, internal network), the gateway (virtual key, model and endpoint allow-list),
    the MCP server (token identity, per-role grants), and the runner's branch checks
    (protected paths, symlinks, secrets). The escape suite asserts each of these with the
    agent using its tokens directly.

**Found and fixed by the O10 escape suite (`tests/harness/test_escape_suite.py`):**
role-type hints matched substrings, so `builder` (b-**ui**-lder) got frontend grants
(`screenshot`, vision); hints now match whole words. A root runner started agents as
uid 0; agents now run as the image's `agent` user (10001) and get `/work` handed over.
The suite covers: writes outside `/work` incl. redirects, read-only `/inputs` and tool dir,
the upstream key (env, files, opencode's DB), internet/DNS/metadata, host ports other than
gateway and MCP, other sandboxes' port 4096, gateway misuse (other model, `/models`,
other endpoints, no key, other provider), MCP without or with a forged token and
ungranted tools, `opencode.json`/`.opencode` self-widening, planted `.git` config, hooks
and filters (no host marker), and symlinks out of the repo.

**Verified in O2 (contract tests in `tests/harness/test_opencode_contract_o2.py`):**
`permission.asked` → reply `once`/`reject` round-trip; `always` covers repeat prompts for
the session; abort stops a running bash call; edit patterns are project-relative and a later
deny wins; `task: {"*": deny, explore: allow}` denies other subagent types; `read` on a PNG
reaches the model as an image; MCP tools are named `<server>_<tool>`, the `guudflow_*`
permission key works, deny-all hides them, and **MCP image results reach the model as
images**; manual compaction works under deny-all (gotcha #9 resolved).

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
3. `POST /session/{id}/prompt_async` with the task brief. The brief ends by telling the agent
   to finish with `guudflow_submit_result` (gotcha #12: no opencode `format`).
4. Bridge SSE events to board signals until `session.idle` or `session.error`.
5. Read the result the MCP server accepted for this session (already Pydantic-validated, §4.3).
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

- `guudflow_submit_result` validates the payload when it is called and returns any
  validation error to the model as the tool result, so the model can correct it in the same
  turn (no extra round trip).
- The turn ends with no accepted result → send **one** follow-up message in the same session
  telling the agent to call `guudflow_submit_result`.
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
reviewer. When rounds run out → escalation (the overseer, §11) → human. All of it is visible to,
and counted by, the scheduler. Scheduler tests are extended for it (§8).

### 4.6 The task board is the backbone

**Why the kanban never worked:** cards were created and closed only when agents wrote
free-text commands correctly (`assign-task`, `decompose-task`, `closes: task:<id>` inside
a `[COMMIT]` block). Whenever a model skipped or garbled one, the board drifted from
reality. The scheduler also worked per role, not per task, so cards were a side view,
not the work queue.

**The new rule: the board is the queue, and only the scheduler moves cards.**

**1. The planner's structured result *is* the task list.** It reuses the existing
`Task` dataclass (`core/workspace.py:239`) and `TASK_*` signals, with a few fields added:

```python
class PlannedTask(BaseModel):
    id: str                                  # "T3"
    title: str
    owner_role: str                          # must exist in the approved team
    acceptance: list[str] = Field(min_length=1)   # checklist, each item testable
    depends_on: list[str] = []               # other task ids
    files_hint: list[str] = []               # where the work will likely land
    size: Literal["S", "M", "L"]
    kind: Literal["code", "test", "docs", "research", "visual"]
    visual_refs: list[str] = []              # mockups or screenshots to match (§5.10)

class PlanResult(BaseModel):
    tasks: list[PlannedTask]
    notes: str
```

Validation rejects unknown owners, dependency cycles, missing acceptance criteria, and
tasks sized `L` (the planner must split those). The runner, not the agent, creates the
cards from the validated list.

**2. Tasks are the unit of work.**
- Each agent turn handles **one task**. The brief contains the card: title, acceptance
  checklist, dependency results, the card's thread, and `visual_refs`.
- A role is re-armed while it still has open, unblocked tasks. Task dependencies are
  resolved by the scheduler the same way role dependencies are today.
- `AgentResult` gains a required `task_id` and `acceptance: list[{item, met, evidence}]`.
- `ReviewResult` reviews one task and returns a verdict **per acceptance item**. A fail
  names the unmet items.

**3. Columns move only on scheduler events, never on agent text.**

| Event | Card moves to |
|---|---|
| Card created from a validated plan | To do |
| Scheduler starts a turn for the task | In progress |
| Result `done` and branch checks passed (§5.5) | Review |
| Reviewer passes every acceptance item | Done |
| Reviewer fails any item | In progress (issues added to the thread; round counter +1) |
| Result `blocked` | Blocked (with `blocked_on` shown on the card) |
| Result `failed`, or rounds exhausted | Needs attention (overseer wakes) |

**4. Communication lives on the cards.** Every message between agents is a typed entry in
a card's thread: `note`, `blocked_on`, `review_issue`, `decision`, `intervention`,
`human_comment`, `artifact` (§5.10). Agents post through `post_note(task_id, …)`. Each
brief includes the thread for its card, plus decisions from the cards it depends on.
Nobody waits on anybody: the board is the conversation.

**Suggested improvements built in:**
- **File-overlap guard:** two tasks whose `files_hint` overlap aren't run in parallel by
  different roles; the scheduler runs them in sequence. This replaces the old advisory
  file claims and removes most merge conflicts before they happen.
- **WIP limits:** at most one in-progress task per role, and a column cap on Review so
  reviewers don't fall behind.
- **Definition of done per `kind`:**
  - `code`: tests pass in the sandbox plus reviewer pass;
  - `test`: the new tests fail on the base branch and pass on the task branch;
  - `docs`, `research`: reviewer pass;
  - `visual`: visual check passes (§5.10) plus reviewer pass.
- **Card aging:** a card stuck in one column past its size-based limit
  (S: 10 min, M: 25 min) is a new stuck detector for the overseer (§11.2).
- **Overseer moves are card operations:** `split`, `reassign` and `rebrief` act on cards,
  so every intervention is visible where the work is.
- **Humans work the same board:** in Studio you can add a card, drag one to change
  priority, or comment on it. A human card or comment shows up in the next brief for that
  task, the same way the old `USER_COMMENT` absorption worked.
- **Continuations carry open cards forward,** with their threads, so a re-run picks up
  where the last one stopped instead of re-planning from scratch.
- **The playbook learns task templates:** for example, "API client goals split well into
  models / transport / endpoints / tests" (§11.4).

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

Profiles are compiled from the role's **capabilities** (§5.7), with the role type
supplying defaults and the existing RBAC scopes (`AgentConfig` + `core/tool_manifest.py`
`allowed_scopes`) still naming the MCP grants. Every profile starts deny-all and adds:

```jsonc
// common base, all roles
{ "*": "deny",
  "StructuredOutput": "allow",     // gotcha #5
  "read": "allow", "glob": "allow", "grep": "allow", "list": "allow", "lsp": "allow",
  "todowrite": "allow",
  "external_directory": "deny",
  "webfetch": "deny", "websearch": "deny",   // always: web goes through host MCP (§5.8)
  "task": "deny",                  // overridden by the `subagents` capability (§5.7)
  "question": "deny",              // humans are reached via the ask_human MCP tool instead
  "doom_loop": "deny",             // a looping agent fails fast; the scheduler handles it
  "guudflow_*": "allow" }          // MCP tools; real RBAC is enforced server-side (layer 6).
                                   // Verified in O2: MCP tools are named guudflow_<tool>.
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
- **Packages (revised in P3):** no sandbox gets internet egress, not even the IT agent's.
  `request_package(manager, name, version)` is a host-side MCP tool: packages on the
  project allow-list (pinned versions) are installed **on the host** into the role's
  clone (`pip install --target .guudflow/site-packages --only-binary=:all:`,
  `npm install --ignore-scripts`), so no install script ever runs; anything else becomes
  an approval card. The clone's `.git/info/exclude` keeps installed files out of commits.
- **Network model (verified in P3):** one Docker network per run, `--internal` and with
  inter-container traffic disabled. Host → container works; container → host services
  bound to the network's gateway IP works; container → container and container →
  internet (even DNS) fail. The gateway and MCP server bind to that gateway IP only.
- opencode's `webfetch`/`websearch` stay denied for every role. Web access is a
  host-side MCP tool (§5.8), so no sandbox ever needs internet egress for it.

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
- **No dead ends** (added after O22). A rejected branch is *set aside*: reset to
  `main`, with its patch kept read-only at `.guudflow/set_aside/NN.patch` and the reason
  in the role's next brief, so one bad commit cannot fail every later turn of the role.
  Unresolved conflict markers count as a failed check.
- **Conflicts go back to the agent.** If `main` moved during the turn and the merge
  conflicts, the runner merges `main` into the role's branch, leaves the markers in the
  worktree and sends one follow-up in the same session; the agent resolves them and
  resubmits, then the runner collects again. Still unresolved → set aside. A conflict
  found when syncing with `main` at turn start becomes the first item of the brief
  instead of failing the turn.
- **Timeouts wrap up.** A turn that hits its wall clock gets one short follow-up asking
  for a result now (`failed` with progress unless the work is complete), so the report and
  the partial work are kept.

### 5.6 MCP RBAC and budgets (layers 6 and 8)

- The **GuudFlow MCP server** (remote MCP over HTTP) exposes `post_note`, `read_board`,
  `ask_human`, `request_package`, `request_visual_feedback`, `report_progress`, and,
  only for roles whose capabilities grant them, `web_search`, `web_fetch` (§5.8) and
  `screenshot` (§5.9). Each
  sandbox's MCP config carries a bearer token bound to `(run_id, role)`. The server
  derives the caller's role **from the token, never from arguments**, and checks the
  role's existing RBAC scopes (default-deny, same `CredentialsBroker` rules as DESIGN §30).
- Budgets: gateway spend cap per agent and per run; opencode `agent.<role>.steps`
  caps tool steps per turn; `doom_loop: deny`; scheduler `max_iterations` and the run
  deadline; a per-turn wall clock that calls `POST /session/{id}/abort`.
- Parallelism cap: `max_parallel_sandboxes` (default 3), enforced by the scheduler,
  because each sandbox costs about 1 GB.

### 5.7 Capabilities: per-role toggles

Each role carries a small `capabilities` block. It is the one place a human decides
what an agent may do. GuudFlow compiles it into three outputs:

1. the role's **opencode permission ruleset** (§5.1),
2. the sandbox's **container and network profile** (§5.3, §5.4),
3. the role's **MCP tool grants** (§5.6).

```jsonc
"capabilities": {
  "sandbox":   "strict" | "standard" | "host_dev",
  "web":       "off" | "search" | "search+fetch",
  "shell":     "readonly" | "project" | "ask",
  "packages":  "off" | "via_it_agent",
  "subagents": "off" | "explore_only" | "full",
  "browser":   "off" | "screenshot"
}
```

**Compile table** (`core/harness/capabilities.py`):

| Toggle | Value | opencode rules | Container / network | MCP grants |
|---|---|---|---|---|
| `sandbox` | `strict` | deny-all base | read-only root, no network except gateway + MCP, no test runners | — |
| | `standard` | `strict` + test-runner bash allows (`pytest*`, `npm test*`, `npm run *`) | same as strict, plus `/work/.venv` and `node_modules` writable | — |
| | `host_dev` | same rules as `standard` | **no container**; warning on every spawn and in Studio; refused when `GUUDFLOW_ENV=production` | — |
| `web` | `off` | `webfetch`/`websearch` deny (always) | no change (never opens egress) | none |
| | `search` | same | same | `web_search` |
| | `search+fetch` | same | same | `web_search`, `web_fetch` |
| `shell` | `readonly` | bash: `git status/diff/log/show*`, `ls*`, `cat *` allow; rest deny | — | — |
| | `project` | `readonly` + the sandbox level's test runners + `git add/commit*`; installers (`pip`, `npm install`, `curl`, `wget`) deny | — | — |
| | `ask` | `project` + `"*": "ask"` for anything else (routed to the approval gate) | — | — |
| `packages` | `off` | installers deny | — | none |
| | `via_it_agent` | installers deny | — | `request_package` |
| `subagents` | `off` | `task` deny | — | — |
| | `explore_only` | `task: {"*": "deny", "explore": "allow"}`, `subagent_depth: 1` | — | — |
| | `full` | `task` allow, `subagent_depth: 1` | — | — |
| `browser` | `off` | — | — | none |
| | `screenshot` | — | host may reach the sandbox's dev-server port (§5.9) | `screenshot` |
| `vision` *(added)* | `off` | images are not attached | — | — |
| | `on` | `read` returns images/PDFs as attachments (§5.10) | — | `visual_diff` |

`vision: on` is only offered when the role's model declares image input
(`modalities.input` contains `image`, or `attachment: true`, in opencode's model config).
Otherwise the switch is disabled with a tooltip explaining why.

Defaults per role type (each can be overridden on the approval screen):

| Role | sandbox | web | shell | packages | subagents | browser | vision |
|---|---|---|---|---|---|---|---|
| researcher | strict | search+fetch | readonly | off | off | off | off |
| planner / architect | strict | off | readonly | off | explore_only | off | on (reads mockups) |
| builder | standard | off | project | via_it_agent | explore_only | off | off |
| frontend builder | standard | off | project | via_it_agent | explore_only | screenshot | on |
| tester | standard | off | project | via_it_agent | explore_only | off | off |
| reviewer | standard | off | readonly + test runners | off | explore_only | screenshot | on |
| IT agent | standard | off | installers only (§5.1) | — | off | off | off |

(`vision: on` defaults apply only where the model supports images.)

Reviewers use the `standard` sandbox because they run the test suite; their shell stays
read-only and gains only the test runners (no commits, no edits).

Rules:
- **Set before the run.** Capabilities show as switches per role on Studio's team
  approval screen. The human approves the capabilities together with the team.
- **Recorded on the board.** At each spawn the runner posts a `CAPABILITIES` signal with
  the effective block and a hash of the compiled ruleset. Any change mid-run (only via the
  approval gate) posts another one. Every run therefore shows exactly what each agent was
  allowed to do and when.
- **Narrowing only, at runtime.** A running agent can never widen its own capabilities.
  Widening needs a human through the approval gate and applies from the next spawn.

> Verified in O2: a `task` pattern of `"explore"` restricts subagents by name. `subagent_depth: 1`
> alone does **not** stop an explore subagent spawning another (gotcha #11), so the generated
> config also sets `agent.explore.permission` to read-only with `task: deny`.

### 5.8 Web access via the host

Sandboxes never get internet access for web content. opencode's built-in `webfetch` and
`websearch` stay denied for every role. Instead, GuudFlow's MCP server (on the host)
exposes:

- `web_search(query, max_results)` → titles, URLs, snippets.
- `web_fetch(url)` → readable text of one page.

The host side enforces:
- **Domain policy:** per-project allow/deny lists (deny wins); private and link-local
  IP ranges are always blocked (no SSRF into the host network).
- **Budget:** per-run and per-role request caps; responses cached per run (same URL or
  query → cached result, which does not count against the budget).
- **Audit:** every query and URL becomes a `WEB_REQUEST` board signal (role, query/URL,
  cache hit, bytes).
- **Untrusted content:** fetched text is returned wrapped in the existing untrusted-content
  delimiters (DESIGN prompt-injection rule), truncated to a size cap, scripts and markup
  stripped.
- **Keys stay on the host:** the search-provider API key is held by the
  `CredentialsBroker`, never sent to a sandbox (hard constraint #17).

**Researcher pattern.** Only a `researcher` role gets `web: search+fetch` by default.
It writes findings to `research/notes.md`, each with its source link. Builders read that
file; they never call web tools and never see raw web pages. This confines prompt
injection to one read-only, no-shell role.

This contains the risk but does not remove it: a page could still steer what the
researcher writes into its notes. So the builder brief wraps `research/notes.md` in the
same untrusted-content delimiters, and the reviewer checks code against the spec, not
against the notes.

### 5.9 Let the harness do what it is good at

- **Context management moves to opencode.** Compaction, LSP diagnostics, provider retries
  and todo tracking are opencode's job. GuudFlow stops building context itself:
  `core/context_manager.py` and `core/repo_map.py` retire (§6.3). The brief carries only
  what opencode can't know (goal, upstream results, notes, review issues).
- **Explore subagent for cheap search.** With `subagents: explore_only` (depth 1), a
  builder can send opencode's read-only `explore` agent to search its own clone. It runs
  in the same sandbox under the same rules, so GuudFlow doesn't need to schedule it.
  Its token cost still flows through the gateway and shows up per role.
- **Sticky approvals.** When a human answers an `ask` with "always", GuudFlow replies
  `always` to opencode and also records it on the board as an `APPROVAL_GRANTED` signal
  with `scope: run`. The same pattern is then auto-approved for that role for the rest of
  the run, so Studio isn't flooded. "Always" never outlives the run: sandboxes (and their
  opencode state) are destroyed at run end, and `host_dev` uses a fresh per-run `HOME`.
- **Screenshots run on the host.** `screenshot(url_path, viewport)` is an MCP tool that
  runs Playwright **on the host**, matching the existing visual-review rule; no browser
  runs inside a sandbox. The role's dev server listens inside its sandbox; the host
  reaches it over the run's internal network (host → sandbox is allowed, sandbox →
  outside is not). Screenshots are saved under the run and served by the existing static
  endpoint, never sent as base64 over the websocket.

### 5.10 Visual artifacts: making and reading images

**What opencode already does** (checked in the 1.18.34 binary and API spec):
- Its `read` tool returns images and PDFs to the model as attachments ("Image read
  successfully" / "PDF read successfully"), resized according to
  `attachment.image {auto_resize, max_width, max_height, max_base64_bytes}`.
- Prompts accept file parts (`FilePartInput {mime, url, filename}`), so an image can go
  straight into a brief.
- Tool results can carry file attachments (`ToolStateCompleted.attachments`).
- Model configs declare `attachment` and `modalities.input` (`image`, `pdf`, …), which is
  what the `vision` capability checks.

**Making visuals (inside the sandbox, no network needed):**

| Kind | How |
|---|---|
| Diagrams | Mermaid or SVG written as files |
| Charts | matplotlib (Agg backend) or plotly static export, run with Python |
| UI | HTML/CSS/JS served by the role's dev server, captured with the host `screenshot` tool (§5.9) |
| Documents | Markdown → HTML/PDF |

*Suggestion:* ship an optional `agent-opencode-viz` image variant with matplotlib, plotly
and kaleido preinstalled, so chart-heavy runs don't go through the IT agent every time.
Photo-style image generation isn't included. It could later be another host-side MCP
tool, like web search.

**Where files live:**
- Agents write visuals under `artifacts/` in their clone (committed when they are
  deliverables, such as diagrams in docs).
- Host screenshots are written into the role's clone at `.guudflow/screens/`
  (git-ignored). That keeps them inside the folder the agent may read
  (`external_directory` stays denied).
- Human mockups go in `inputs/mockups/`, which is mounted read-only. The planner links
  them to cards through `visual_refs` (§4.6).

**Artifact registry:** after each turn the runner indexes new image, SVG, PDF and HTML
files (path, type, producing role, `task_id`, size, hash), posts an `ARTIFACT` signal,
and adds an `artifact` entry to the card's thread. Studio's gallery and the run report
read from this registry.

**Visual review loop** (for cards of `kind: visual`):
1. The builder implements the card, takes a screenshot through the host tool, `read`s
   it, and fixes what it sees, up to N self-checks per turn (default 2).
2. **Cheap pre-check on the host:** `visual_diff(screenshot, ref)` runs a deterministic
   pixel and layout diff (for example pixelmatch, plus an element-box comparison from
   Playwright) against the card's `visual_refs` or the previous round's baseline. It
   returns a diff image and a score with no LLM cost. A large regression goes straight
   back to the builder without spending reviewer tokens.
3. The reviewer (with `vision: on`) gets the mockup, the screenshot and the diff image,
   and judges each acceptance item.
4. Each round's screenshot is kept as that task's **baseline**, so later rounds and
   continuations can catch visual regressions.
5. A human can annotate any screenshot in Studio (the existing visual-feedback mode).
   The annotated image becomes a `human_comment` on the card and is attached to the
   next brief.

**Budgets and gating:**
- `vision: on` only for models that declare image input (§5.7); visual cards are
  assigned only to such roles.
- Images are resized by opencode's `attachment.image` caps (default target ≤ 1280 px on
  the long side), with a per-turn image cap (default 6) and a per-run cap. Roughly
  1–1.5k tokens per screenshot; the gateway reports image tokens per agent.

**Safety:**
- Screenshots of external pages and fetched images are untrusted: text inside an image
  can carry prompt injection. Only the researcher and visual reviewers see such images,
  and their briefs say so.
- Agent-made SVG is sanitized (DOMPurify, with scripts and external references removed)
  before Studio renders it. Agent-made HTML is rendered only in a sandboxed `iframe`
  (`sandbox` attribute, CSP with no network), never inline.
- Image metadata (EXIF) is stripped before files are served.

> Verified in O2: `read` on a PNG reaches an `attachment: true` model as an image, and an
> MCP tool's image result reaches the model as an image too, so `screenshot` can return
> the image inline as well as writing the file.

---

### 5.11 Personas compile into opencode agents (O13)

A persona-backed role becomes one opencode agent whose `prompt` (the system prompt) is built
by `core/harness/persona_prompt.py: build_agent_prompt()`. It reuses the persona layers and
helpers the legacy `BaseAgent.effective_system_prompt()` uses, and drops the layers opencode
or the harness now owns. The prompt is built when the role's server starts; servers are
restarted per spawn by default, so memory recall is per spawn.

| Layer | Source | In the opencode prompt? |
|---|---|---|
| 1 identity | `persona.identity_layer()`; no persona → "You are the {role} on a small software team." + description | yes |
| 2 character | `persona.character_layer()` | yes |
| 3 stake framing | `persona.stake_layer()` | yes |
| 3.5 teammate brief | `AgentRunner._build_teammate_brief(role)` (reputation stats only) | yes |
| 3.6 manifesto | `BaseAgent.load_manifesto(project_id)` | yes |
| 3.7 project rules | `BaseAgent.load_project_rules(project_id, persona_id)`; above memory, rules win | yes |
| 3.8 project context | runner's `_project_context` (roadmap.md) | yes |
| continuation brief | carry-forward (§11.4) | **no**: goes in the turn brief (`BriefInputs.carry_forward`) |
| 4 memory | `await persona.recall(goal + role description [+ card title])` → `render_for_prompt()`; only when the persona has memory | yes |
| 4.5/4.7 repo map, relevance, subtask | legacy context manager | **no**: opencode reads files itself (§5.9) |
| 5 base prompt | `cfg.system_prompt` with legacy protocol instructions stripped (lines mentioning `[COMMIT]`, `[VERIFY]`, `[ASK]`, `[ANSWER]`, `fs-read`, `fs-list`, `ask-peer`, `board-message`) | yes |
| 5 tool catalog | legacy `ToolManifest` | **no**: opencode lists its tools; MCP tools describe themselves |
| 5.5 protocol voice, autonomy rules, context-before-output | legacy loop nudges | **no**: replaced by the brief's "How to work" and `submit_result` |
| 6 CoT primer | the legacy primer sentence | yes |
| 7 positive framing | "Make this something you are proud of." | yes, always the last line |

Model tier: the role's `model_id`, else `model_tier` resolved by the registry (already in
`_binding`). Escalation upgrades a tier on the next spawn when the overseer rebriefs a role
whose card failed twice (recorded in the `OVERSEER_INTERVENTION` signal); agents never change
their own model. Roles without a persona get layers 1, 5, 6 and 7 only. The prompt is
recorded on the board once per spawn as a hash plus the layer names it included, never the
memory text itself.

## 6. What changes in the codebase

### 6.1 New

| Path | Purpose |
|---|---|
| `core/harness/opencode_client.py` | Typed async client for the ~12 endpoints used (session, prompt_async, abort, message, permission reply, diff, event SSE) |
| `core/harness/opencode_runner.py` | `OpencodeAgentRunner`, the scheduler runner callable (§4.1) |
| `core/harness/event_bridge.py` | SSE → board `Signal`s + quip triggers + Studio websocket |
| `core/harness/capabilities.py` | `Capabilities` dataclass, role-type defaults, compile table (§5.7) |
| `core/harness/permissions.py` | Compiled capabilities → opencode ruleset (§5.1); unit tested per role |
| `api/mcp_web.py` | `web_search` / `web_fetch` host tools: domain policy, budget, cache, audit, delimiters (§5.8) |
| `api/mcp_browser.py` | Host-side Playwright `screenshot` tool (§5.9) |
| `api/routes/oneshot.py` + `cli/oneshot.py` | One-shot entry point (§10) |
| `data/teams/default_oneshot.json` | Default one-shot team (§10) |
| `core/harness/detectors.py` | Stuck detectors + situation report, called from the scheduler (§11.2) |
| `core/harness/overseer.py` | `OverseerResult`/`Intervention` schemas, validation, and how the scheduler applies each intervention (§11.3) |
| `core/harness/tasks.py` | `PlannedTask`/`PlanResult` validation, card creation, column transitions, file-overlap guard, WIP limits (§4.6) |
| `core/harness/artifacts.py` | Artifact registry + `ARTIFACT` signal (§5.10) |
| `api/mcp_visual.py` | Host-side `visual_diff` (pixel + layout diff, baselines) (§5.10) |
| `core/playbook.py` | Project playbook storage, confidence and lifecycle; carry-forward brief (§11.4) |
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
`agents/builtin/tech_lead.py` (folded into the overseer, §11); the `assign-task` / `decompose-task` tools and `closes:` parsing (cards now come from the planner's validated result, §4.6); `core/edit_blocks.py`; `core/context_manager.py` + `core/repo_map.py` (opencode has its
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

The later additions (capabilities §5.7, web §5.8, harness features §5.9, visuals §5.10,
task board §4.6, one-shot §10, overseer §11, UI §12) are scheduled task by task in
`OPENCODE_WORK_BREAKDOWN.md`, which is the source of truth for ordering and ownership.

Rough total: 4–5 weeks of focused work with the overseer and the UI track included;
P0–P2 alone already gives a watchable demo.

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
| Prompt injection through `research/notes.md` | Only the researcher touches raw web content; notes are delimited as untrusted in builder briefs; reviewer checks against the spec (§5.8) |
| Host Playwright needs to reach a network-closed sandbox | Host → sandbox only, on the run's internal network; P3 escape suite confirms the sandbox still can't reach out |
| opencode's own web search flags untested | Not relied on; host-side MCP search is the default |
| `host_dev` has no isolation | Refused in production; warning on every spawn and in Studio; demo mode only |
| The overseer becomes a new source of churn | Fixed intervention menu, caps per wake and per run, deterministic detectors, structural changes need a human (§11) |
| Playbook learns the wrong lesson | Evidence runs required, recency-weighted confidence with decay, never auto-applied outside demo mode (§11.4) |
| Planner produces poor task lists | Schema rules (owners exist, no cycles, acceptance required, no `L` tasks); overseer `split`/`reassign`; playbook task templates (§4.6, §11.4) |
| Image tokens add up | Resize caps, per-turn and per-run image caps, `visual_diff` pre-check before any LLM review (§5.10) |
| Injection or XSS through visuals | Untrusted-image handling, SVG sanitizing, sandboxed iframes, EXIF stripping (§5.10) |
| UI rewrite stalls feature work | Parallel track, view by view, behind existing routes (§12.4) |

**Open questions** (defaults taken on 2026-10-04 so implementation can proceed; change any of them later):
1. Is one container per agent acceptable for the demo (memory), or should P1–P2 start in
   host mode and add containers in P3, as planned? **Default: as planned** (host mode in
   P1–P2, containers from P3; `host_dev` stays available for trusted local demos).
2. Which models should be the floor for the demo roles (e.g. Sonnet-class for builders,
   Haiku-class for quips)? **Default:** heavy = Opus-class (overseer), solid = Sonnet-class
   (planner, builders, reviewer, researcher), fast = Haiku-class (quips). Configurable in
   `data/models.json`.
3. OK to retire the ASK/ANSWER handshake entirely (§4.4)? It is the biggest behaviour change.
   **Default: yes** for the opencode harness; the legacy harness keeps it until P6.

---

## 10. The generic one-shot (the demo)

One entry point that takes a goal and runs a sensible team end to end.

```bash
guudflow oneshot "Build a typed Python client for the Open-Meteo API with tests" \
  --web researcher --budget 5 --demo
```

or `POST /api/oneshot {goal, repo?, web, budget}`, which enqueues a job and returns
`202` at once (hard constraint #12). It reuses the existing invoke job path
(`api/routes/invoke.py` already returns 202 with a pollable `/{job_id}/status`).

**Default team** (`data/teams/default_oneshot.json`), which fits most goals:

```
researcher (optional, web on) → planner → (builder A ‖ builder B) → reviewer
                                              ↑______ review loop, 2 rounds ______|
```

- `--web researcher` includes the researcher (§5.8); `--web off` drops it. No other role
  gets web access.
- The planner splits the work into two independent halves for builder A and builder B
  (separate clones and branches, §5.5); the runner merges both before review.
- The reviewer loop is a scheduler review edge (§4.5) with `max_rounds: 2`, then tech
  lead, then human.
- Capabilities use the role defaults from §5.7.
- `--budget` (USD) becomes the run's spend cap at the gateway (§5.4); the run stops
  cleanly when it is reached.

**Approval:** with `--demo`, the team and capabilities are auto-approved and a
`CAPABILITIES` signal records what was granted. Without `--demo`, Studio's team approval
screen (with the capability switches) is shown first.

**Deliverables:**
- a merged branch in the run repo;
- a downloadable workspace (the existing deliverables endpoint);
- a run summary posted to the board;
- a cost breakdown per agent, taken from the gateway's cost log.

**Demo project:** `data/example_projects/api-client-library`. It already has a
researcher → design → implement → test → docs flow and shows everything at once: the
researcher reads real public API docs through the host web tools, two builders work in
parallel, the reviewer catches a real issue and sends one round back, and the quip feed
narrates it live.

**Exit criteria:** `guudflow oneshot … --demo` on `api-client-library` completes under
the budget; the merged client's tests pass offline; the board shows `CAPABILITIES`,
`WEB_REQUEST`, review-loop and cost signals; the Studio Run view plays it live.

---

## 11. The overseer: keeping runs on track, within and across runs

The old system had escalation (`agents/builtin/tech_lead.py`) but nothing that looked at
the *whole* run, noticed it was stuck, and changed course. Nothing carried coordination
lessons into the next run either: persona memory records per-agent lessons, not "this
team shape stalls on this project". The overseer fills both gaps. It **replaces the tech
lead** as the single escalation handler.

The design rule is the same as everywhere else in this plan: the overseer **decides**,
the **scheduler acts**. The overseer has no side channels, edits no code, and cannot
widen anyone's capabilities, its own included.

### 11.1 The overseer role

- HEAVY tier (one strong model), event-driven like the tech lead today: no `depends_on`;
  it is woken by the scheduler.
- Capabilities: `sandbox: strict`, `web: off`, `shell: readonly`,
  `subagents: explore_only`. It can read every branch's results and the board, and
  write nothing but its structured output.
- Result schema: `OverseerResult {assessment, interventions: list[Intervention]}`,
  validated like every other result (§4.3).

### 11.2 When it wakes (cheap, deterministic stuck detectors)

The detectors run in the scheduler and cost no LLM calls. Each one builds a
**situation report**: the graph with role states, the last result from each role, open
review issues, blocked chains, time and cost burn, and recent `WEB_REQUEST` and
permission activity. The overseer is woken with that report.

| Detector | Fires when |
|---|---|
| No progress | No role completed and no commit landed for N minutes (default 10) while roles are running |
| Same review issue | The reviewer fails the same file/issue in two consecutive rounds |
| Blocked chain | A `blocked_on` chain is longer than 2, or a cycle is found (§4.4) |
| Burn without output | Spend crosses 50% of budget with fewer than half the roles complete |
| Permission thrash | The same `ask` pattern is rejected 3+ times for one role |
| Repeated validation failure | A role fails structured-output validation on 2 consecutive spawns |
| Escalation | Review rounds exhausted (§4.5), or what used to go to the tech lead |

Wakes are deduplicated per detector and role, with a cooldown, and capped per run
(default 5). If the overseer itself fails or the cap is hit → `HUMAN_NEEDED`.

### 11.3 What it can do (a fixed menu of interventions)

| Intervention | Effect (applied by the scheduler) | Needs a human? |
|---|---|---|
| `rebrief(role, addendum)` | Next spawn of `role` gets the addendum prepended | No |
| `decide(question, decision, rationale)` | Writes a decision note to the board and `docs/decisions.md`; unblocks roles waiting on it | No |
| `reassign(work, from_role, to_role)` | Moves a slice of work between existing roles of the same type | No |
| `split(role, parts)` | Splits a role's brief into sequential parts within the same role | No |
| `extend_review(edge, +1)` | One extra review round, never beyond a hard cap | No |
| `restart_role(role, from_ref)` | Resets the role's clone to a known-good ref, then re-arms it | No |
| `pause(question)` | `HUMAN_NEEDED` with a plain-language question | — (is the human) |
| `propose_team_change(diff)` | Add or remove a role, change an edge, change capabilities, raise budget | **Yes**, via the approval gate |

Every intervention is posted as an `OVERSEER_INTERVENTION` board signal with its
rationale, and shows in Studio's overseer panel (§12) and the quip feed. Interventions
are bounded too: at most 3 per wake, at most one `restart_role` per role per run.

### 11.4 Across runs and re-runs: the project playbook

At the end of every run (after `memory_writer`), the overseer writes a **retrospective**:
where time and money went, what stalled and why, which interventions worked (did the
role complete after the intervention?), and which review issues recurred.

The retrospective updates a per-project **playbook**
(`data/projects/{id}/playbook.json`), separate from persona memory:

```jsonc
{
  "team_adjustments": [    // e.g. "split backend into api + persistence"
    {"change": {...}, "evidence_runs": ["run_…"], "confidence": 0.7, "state": "active"}
  ],
  "brief_addenda":   {"builder": ["Read research/notes.md before designing the client"]},
  "known_pitfalls":  [{"issue": "Open-Meteo returns hourly arrays, not objects", "evidence_runs": [...]}],
  "review_rounds":   {"builder->reviewer": 3},
  "capability_hints":{"tester": {"browser": "screenshot"}}
}
```

It follows the same rules as persona memory (hard constraints #16 and #22): every entry
needs evidence runs, confidence is recency-weighted and decays when contradicted, and
entries move through emerging → active → declining → archived.

How the playbook is used:
- **Next run:** the meta-agent starts its team proposal from the playbook. Playbook-driven
  changes appear as a highlighted diff on the team approval screen. They never apply
  silently (as with project rules today, hard constraint #24). In `--demo`, they are
  auto-approved like the rest of the team and recorded on the board.
- **Re-runs and continuations:** the overseer writes a **carry-forward brief**: what's
  done and verified (with commit refs), open issues, files of note, and decisions made.
  Seeded files are attributed to "previous run, commit X", which fixes the old
  continuation bug where seeded files with no author made agents distrust them and
  spiral into questions (`.claude/future_buff/bugs/run_632863526b_circular_ask_cascade.md`).
- **Measuring it:** every run records stall minutes, interventions and their outcomes,
  review rounds used, and cost per completed role. Studio's dashboard shows these trending
  across runs of the same project, so you can see whether the overseer is helping.

---

## 12. Studio UI modernization

### 12.1 Where it is today

React 18 + react-router, Vite, vitest. No component library, data-fetching layer, or
graph library. Styling is 658 lines of `styles.css` plus about **500 inline `style={{…}}`
objects**. `Run.tsx` (621 lines), `Projects.tsx` (668) and `Studio.tsx` (608) each hold
most of their view's logic. It works, but it is hard to extend, and the live run view
doesn't yet show what the new harness can show.

### 12.2 Target stack

| Concern | Choice | Why |
|---|---|---|
| Styling | Tailwind CSS + design tokens as CSS variables (light/dark) | Replaces inline styles; one token source |
| Components | shadcn/ui (Radix primitives, copied into the repo) | Accessible dialogs, switches, tabs, tooltips; owned code, no runtime dependency lock-in |
| Server state | TanStack Query | Caching, refetching, loading and error states for REST calls |
| Live state | One typed run-event store (Zustand) fed by the websocket | Every panel reads the same typed event stream; no per-view socket parsing |
| Graphs | React Flow (`@xyflow/react`) | Live pipeline graph: role states, review edges with round counters, blocked arrows |
| Long feeds | TanStack Virtual | Board, quip and tool-call feeds stay smooth over long runs |
| Diffs | A side-by-side diff viewer (e.g. `react-diff-view`) | Reviewing agent branches in place |
| Tests | vitest + Testing Library (kept); Playwright for key flows and visual snapshots | |

Pin the major versions at the start of U0 and upgrade deliberately (React 19 and the
current Vite major are candidates, checked for compatibility first).

### 12.3 Screens

- **Run cockpit** (the centrepiece; replaces `Run.tsx`):
  - left: live pipeline graph (React Flow);
  - centre: one lane per agent, streaming tool calls, file edits and its structured
    result as they happen (from the event bridge, §2.1);
  - right: tabs for board, quip feed, and overseer panel (interventions with rationale,
    detector that fired);
  - bottom bar: pending approvals (permission asks with once/always/reject), budget meter
    with per-agent cost, and the always-visible comment box.
- **Task board** (rebuilt `KanbanBoard`): swimlanes per role, columns driven by scheduler
  events, card drawer with the acceptance checklist, thread, diffs and artifacts; drag to
  reprioritise, add a human card, comment on a card.
- **Artifact gallery:** every visual from the registry, filterable by task and role; a
  before/after slider across review rounds; diff images; annotate a screenshot to
  comment on its card. SVG sanitized, HTML in sandboxed iframes (§5.10).
- **Team approval:** the proposed team as a graph; per-role capability switches (§5.7)
  with plain-language tooltips; playbook changes highlighted as a diff (§11.4); a cost
  estimate that updates as models and capabilities change.
- **One-shot launcher:** a goal box, web on/off, budget, demo toggle; the example
  gallery as starting points.
- **Run report:** summary, cost per agent, diff viewer per branch, screenshot gallery,
  `WEB_REQUEST` log, capability record, retrospective.
- **Project page:** run history with the overseer metrics trending (§11.4), the
  playbook (with its evidence links), persona roster.
- Global: dark mode, keyboard command palette, responsive layout down to tablet width,
  accessible focus states, real empty and error states.

### 12.4 How it rolls out

The UI work runs as a parallel track alongside the harness phases. It never blocks the
harness; each step ships behind the existing routes.

| Step | Scope | Aligns with |
|---|---|---|
| U0 | Tokens, Tailwind, shadcn/ui, TanStack Query, typed run-event store; migrate the shared components | Harness P0–P1 |
| U1 | Run cockpit on the new event store (graph, lanes, approvals bar, virtualized feeds) | Harness P2 |
| U2 | Team approval with capability switches; one-shot launcher | Harness P3–P5 |
| U3 | Overseer panel, run report, project page with trends and playbook | Harness P4–P5 + §11 |
| U4 | Migrate the remaining views (Dashboard, Roster, Training, Tournament, Settings, Inputs); delete `styles.css` and the inline styles; Playwright visual suite | Harness P6 |

**Done when:** no inline style objects remain; every view uses tokens and shared
components; the Run cockpit holds 60 fps scrolling on a 2,000-event run; Playwright flows
cover launching a one-shot, approving a team with capability changes, answering a
permission ask, and reading the run report.

---

## 13. Ongoing projects: runs are sessions of continuing work

A GuudFlow project is a piece of software under continuous development, not a one-off
job. A run is one working session on it. Three decisions (taken with the user after the
offline plan was finished) make that the default:

1. **One long-lived repo per project** (`core/project_repo.py`). `data/projects/{id}/repo.git`
   is seeded once (from the project's source repo, or empty). Every run starts as a clone of
   its `main` with full history, or of a pending run's branch when it continues that run.
   This replaces DESIGN §33 rule 85 ("fresh git history per run"). Projects whose source is
   `local_path_in_place` keep working in the user's own directory and skip the project repo.
2. **A human accepts every run** (`core/project_runs.py`). At run end the run's `main` is
   stored as `runs/{run_id}` and a `RUN_SUBMITTED` signal is posted. The project's `main`
   changes only when a human accepts the run (`--no-ff` merge). A conflicting accept is
   refused with the files; the human continues the run to reconcile it, or rejects it.
   Rejecting leaves `main` untouched and keeps the branch for reference.
3. **A persistent backlog** (`core/backlog.py`, `data/projects/{id}/backlog.json`). Humans
   add, edit, reprioritise, drop and reopen items at any time. A run takes the items chosen
   when it starts (`backlog_items`: ids or `"top"`). The planner sees them (agent-written
   text wrapped as untrusted), and its plan must give every item at least one card
   (`backlog_id`, enforced by `check_plan`). Teams without a planner get the items in every
   brief. At run end each item gets a pending outcome (`done` / `partial` / `not_started`),
   and unfinished extra cards become new items (`source: run:<id>`). An item becomes `done`
   only when the run that finished it is accepted; otherwise it reopens, with the run's
   summary in its history for the next attempt.

Continuity between runs: the last accepted run's carry-forward
(`data/projects/{id}/context/last_accepted.json`) goes into every brief of the next run.
Its open work lives on the backlog, not on the next run's board. Persona memory, the
playbook and the roadmap carry over as before.

API (bearer token on every route): `GET/POST /api/projects/{id}/backlog`,
`PATCH /api/projects/{id}/backlog/{item}` (fields, `status: dropped|open`),
`GET /api/projects/{id}/runs-review`, `GET …/runs-review/{run}/changes`,
`POST …/runs-review/{run}/accept|reject` (409 + `files` on conflict), and
`POST /api/invoke/{id}` with `backlog_items` and `continue_run`.

Studio: the project page opens on **Work**, with runs waiting for review on top (diff,
note, accept, reject, continue) and the backlog below (select items for the next run,
reorder, add with one acceptance check per line). The run report shows the run's review
state.

Not built yet: the meta-agent does not read the backlog when proposing a team; runs are
started by a human or CI, never on a schedule by themselves.

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
