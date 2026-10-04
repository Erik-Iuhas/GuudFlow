# opencode harness — work breakdown (Opus vs Sonnet)

Companion to [OPENCODE_HARNESS_PLAN.md](OPENCODE_HARNESS_PLAN.md). Section references
(§) point there.

**Split rule:**
- **Opus** handles the foundation everything else depends on, security boundaries,
  the scheduler (critical path), anything that needs a real-model judgment call, and
  every review.
- **Sonnet 5.5** subagents handle well-specified modules with clear interfaces and
  tests: mapping tables, clients, adapters, UI, Dockerfiles from a spec, cleanup, docs.

Every Sonnet task works in its own git worktree from a brief that Opus writes (template
at the end). Opus reviews each one before it merges.

---

## Before anything starts (you)

- Sign off on the hard-constraint changes in plan §6.4.
- Answer the open questions in plan §9:
  1. Should phases 1–2 run on the host and add containers in phase 3?
  2. Which model is the minimum for each role?
  3. Should the ask/answer handshake be retired?
- Provide one real API key for phase 1. Phases 0, 2, 3 and 4 run offline against the
  fake LLM.

---

## Phase 0 — Test harness and building blocks

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| O1 | **Opus** | `tests/harness/fake_llm.py` (scripted OpenAI-compatible server with SSE and tool calls) + `tests/harness/conftest.py` fixture that boots a pinned `opencode serve` with the locked config (§5.2) + `scripts/install_opencode.sh` (pinned version) + `/doc` snapshot contract test | — | `pytest tests/harness/` green offline, no API key |
| S1 | Sonnet | `core/harness/results.py`: `AgentResult`, `ReviewResult`, `BlockedOn`, `Issue` (§4.2), validators, retry-prompt builder (§4.3) + `tests/harness/test_results.py` | — | Every validator has a pass case and a fail case |
| O16 | **Opus** | `core/harness/capabilities.py`: `Capabilities` dataclass, role-type defaults, and the compile table (§5.7) to permission rules, container/network profile and MCP grants. Security-relevant, so Opus writes it | — | Table-driven tests for every toggle value and every role default |
| S2 | Sonnet | `core/harness/permissions.py`: compiled capabilities → opencode ruleset for every profile in §5.1, the common base, last-match ordering, `StructuredOutput` always allowed + tests | O16 | One test per role default; ordering test; deny-all base test |
| S3 | Sonnet | `core/harness/opencode_client.py`: typed async httpx client for session create, `prompt_async`, message get, abort, permission reply, diff, `/event` SSE iterator, Basic auth + unit tests with mocked HTTP | — | Unit tests green; every method typed and documented |
| O2 | **Opus** | Contract tests that run S3 against O1's real opencode: structured output (valid, invalid, plain text), permission deny, `permission.asked` round-trip, "always" reply, abort, `task` restricted to `explore` with `subagent_depth: 1`. Confirms gotcha #9 and the MCP permission key | O1, S3 | Contract suite green; plan §2.2 updated with results |

S1 and S3 run in parallel while Opus builds O1 and O16; S2 starts as soon as O16 lands.

## Phase 1 — One real agent end to end

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| S4 | Sonnet | `core/harness/config_gen.py`: builds `OPENCODE_CONFIG_CONTENT` (provider → base URL, agent definitions, base permission, compaction, share/autoupdate off) + tests | S2 | Generated config passes opencode's own schema in the O1 fixture |
| O3 | **Opus** | `core/harness/opencode_runner.py`: `OpencodeAgentRunner` (§4.1): session lifecycle, validation with one retry, abort on wall-clock limit, board signals incl. a `CAPABILITIES` signal at every spawn (§5.7), host-mode warning, `harness` project flag in `ProjectConfig` | S1–S4, O2 | Fake-LLM integration test: brief → tool calls → valid result → COMPLETE on the board |
| O4 | **Opus** | First real-model run: one builder on `cli-tool-from-spec`; triage what breaks and adjust the briefs and prompts | O3 | `agent/builder` holds code that runs; findings written into the plan |

## Phase 2 — Live in Studio

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| O5 | **Opus** | Event-to-signal mapping table (which opencode events become which board signals, quip triggers, and what gets dropped), handed to S5 as its spec | O2 | Table merged into the plan |
| S5 | Sonnet | `core/harness/event_bridge.py`: table-driven SSE → `Signal`s and quip triggers; tests replay recorded event streams captured by the O1 fixture | S3, O5 | Recorded streams produce the expected board sequence |
| O6 | **Opus** | Approval wiring: `permission.asked` → `APPROVAL_NEEDED` → human decision → `POST /permission/{id}/reply`; per-project auto-approve list; sticky "always" approvals scoped to the run and recorded as `APPROVAL_GRANTED` (§5.9) | S5 | Integration test: a bash `ask` is approved from the API, and a rejection reaches the agent |
| S6 | Sonnet | Studio: render the new signal kinds (tool call, permission ask, structured result, blocked, `CAPABILITIES`, `WEB_REQUEST`) in BoardFeed and AgentStateCard; reuse ApprovalCard for permission asks; vitest coverage | S5 | `npm test` green; manual check on a fake-LLM run |
| S17 | Sonnet | Studio team approval screen: a switch group per role for the six capabilities (§5.7), role defaults pre-filled, `host_dev` shown with a warning and disabled in production; API accepts and stores the block | O16 | vitest + API tests green; approved capabilities appear in the run's first `CAPABILITIES` signals |

## Phase 3 — Sandboxes, gateway, MCP

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| S7 | Sonnet | `sandbox/agent-opencode.Dockerfile` + `worker/sandbox_manager.py` (start/stop, hardening flags, mounts, per-run internal network, gVisor detection) built strictly to §5.3/§5.4 | O3 | Image builds; unit tests check the container spec it generates |
| O7 | **Opus** | Line-by-line hardening review of S7, plus the network isolation (sandboxes reach only gateway and MCP, never each other) | S7 | Isolation verified by hand and in O10 |
| S8 | Sonnet | `gateway/`: FastAPI OpenAI-compatible passthrough; per-agent virtual keys bound to (run, role); model allow-list; spend caps; cost log; key revocation at run end + tests | — | Tests cover allow, deny, over-budget and revoked key |
| S9 | Sonnet | `api/mcp_server.py`: `post_note`, `read_board`, `ask_human`, `request_package`, `request_visual_feedback`, `report_progress`; role taken from the bearer token only; RBAC via `CredentialsBroker` + tests | — | Tests prove a token for role A cannot act as role B |
| S18 | Sonnet | `api/mcp_web.py`: `web_search` / `web_fetch` on the host (§5.8): domain allow/deny, private-IP blocking, per-run and per-role budgets, per-run cache, `WEB_REQUEST` signals, untrusted-content delimiters + size cap, search key via `CredentialsBroker` | S9 | Tests cover deny-listed domain, private IP, budget exhausted, cache hit, delimiter wrapping |
| S19 | Sonnet | `api/mcp_browser.py`: host-side Playwright `screenshot` tool reaching the role's dev server over the run network; saves to the run folder and serves via the static endpoint (§5.9) | S7, S9 | Screenshot of a sample app served from a sandbox; no browser binary in the agent image |
| O8 | **Opus** | Security review of S8, S9, S18 and S19 (key handling, token binding, default-deny, SSRF, injection delimiters, host → sandbox route) | S8, S9, S18, S19 | Review notes fixed |
| O9 | **Opus** | Git ownership (§5.5): bare run repo, per-role clones, runner-side fetch and ref checks, protected paths, secret scan, auto-commit of leftovers | O3 | Tests: touching another branch, `main`, or `inputs/` fails the turn |
| O10 | **Opus** | Security escape suite: replays every escape from §2.2 plus host reads, internet access, cross-sandbox calls, MCP impersonation, protected-path edits, plus: a role without `web` calling web tools, a deny-listed or private-IP fetch, the sandbox reaching out over the screenshot route, an agent trying to widen its own capabilities; runs in CI | S7–S9, S18, S19, O9 | Every case contained, with assertions made on the host |

## Phase 4 — Multi-agent flow

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| O11 | **Opus** | Scheduler: review edges with `max_rounds` (§4.5), blocked re-arm, A↔B cycle → `HUMAN_NEEDED` (§4.4), `max_parallel_sandboxes`; new tests alongside the existing six; no concurrency outside `core/scheduler.py` | O3 | All scheduler tests green, old and new |
| S10 | Sonnet | Studio PipelineGraph: draw review edges, round counters and blocked-on arrows | O11 | vitest green |
| O12 | **Opus** | Real multi-agent demo: planner → (backend ‖ frontend) → reviewer on `fullstack-web-app`, including one forced review loop | O11, P3 | Recorded run that completes; findings fed back into the briefs |

## Phase 5 — Meta-agent, personas, quality

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| O13 | **Opus** | Spec for how personas compile into opencode agents (the 7-layer prompt, memory recall, project rules, model tier) | O3 | Spec merged |
| S11 | Sonnet | `core/meta_proposal.py`: proposals emit role type + scopes + review edges; schema and test updates | O11 | Existing meta tests updated and green |
| S12 | Sonnet | Implement O13 in `config_gen.py` (reusing `effective_system_prompt()`) + tests | O13, S4 | Snapshot tests of the generated agent prompts |
| S13 | Sonnet | `memory_writer` adapter that reads opencode transcripts (`GET /session/{id}/message`) instead of the old turn logs | O3 | Memory-writer tests green on recorded transcripts |
| S14 | Sonnet | Quality gate: `tests/quality/`, `scripts/run_quality_gate.sh`, per-project assertions (`.claude/future_buff/functional_verification.md`) | O12 | Runs against a recorded workspace offline |
| S20 | Sonnet | One-shot (§10): `cli/oneshot.py` (`guudflow oneshot GOAL --web --budget --demo`), `POST /api/oneshot` reusing the invoke 202 job path, `data/teams/default_oneshot.json`, demo auto-approve, deliverables incl. per-agent cost from the gateway | O11, S8, S11 | API returns 202; fake-LLM end-to-end test of the default team; CLI prints the summary and cost table |
| O14 | **Opus** | Run all six example projects for real; triage; tune role profiles and briefs | S11–S14 | All six complete; quality gate passes on two |
| O17 | **Opus** | The demo: tune the default team and briefs on `api-client-library` with a real model; check the §10 exit criteria; record it | S20, O14 | `guudflow oneshot … --demo` completes under budget, the client's tests pass offline, Studio plays it live |

## Phase 6 — Make it the default

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| S15 | Sonnet | Retire the legacy code listed in plan §6.3, one module per commit, deleting or updating its tests | O14 | Full suite green after every commit |
| S16 | Sonnet | Draft updates to DESIGN.md, CLAUDE.md, ARCHITECTURE.md and the diagrams | O14 | Drafts ready |
| O15 | **Opus** | Review S15 and S16 (especially the hard-constraint text); flip the `harness` default to `opencode`; final full run | S15, S16, O17 | Default flipped; demo recorded |

---

## Totals

| | Opus | Sonnet |
|---|---|---|
| Tasks | 17 | 20 |
| Share of new code (rough) | ~35% | ~65% |
| Nature | Foundation, scheduler, security, real-model runs, every review | Modules, adapters, UI, infra from spec, cleanup, docs |

Running Sonnet tasks in parallel: phase 0 runs S1 and S3 together (S2 right after O16); phase 2 runs S6 and S17 together; phase 3 runs S7–S9 together, then S18 and S19.

## How each Sonnet task runs

**Brief (written by Opus):**
- Goal (1–2 sentences) and the plan sections it implements.
- Files to create or edit, with exact public signatures.
- The tests that must exist and pass, including the command to run.
- Out of scope: what *not* to touch (always: `core/scheduler.py`, anything security-
  related outside the task).
- Repo rules that apply: type hints, docstrings, a test file per module, commit per change.

**Review (Opus):**
1. Run the task's tests and the full `pytest` suite.
2. Read the whole diff against the brief and the CLAUDE.md hard rules.
3. Accept → merge into `claude/clever-pascal-vpz71c` and push.
   Small issues → Opus fixes them directly.
   Larger issues → **one** send-back with specific notes; if it still misses, Opus finishes it.
4. Keep each brief small enough that the diff can be reviewed in one sitting
   (rough target: under ~600 changed lines).

**Escalate to Opus:** any task that turns out to need a design decision the brief didn't
cover. The Sonnet worker stops and reports the decision instead of guessing (CLAUDE.md:
"add a `# QUESTION:` comment and move on").
