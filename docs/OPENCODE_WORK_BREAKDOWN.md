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
| S2 | Sonnet | `core/harness/permissions.py`: role scopes → opencode ruleset for every profile in §5.1, the common base, last-match ordering, `StructuredOutput` always allowed + tests | — | One test per profile; ordering test; deny-all base test |
| S3 | Sonnet | `core/harness/opencode_client.py`: typed async httpx client for session create, `prompt_async`, message get, abort, permission reply, diff, `/event` SSE iterator, Basic auth + unit tests with mocked HTTP | — | Unit tests green; every method typed and documented |
| O2 | **Opus** | Contract tests that run S3 against O1's real opencode: structured output (valid, invalid, plain text), permission deny, `permission.asked` round-trip, abort. Confirms gotcha #9 and the MCP permission key | O1, S3 | Contract suite green; plan §2.2 updated with results |

S1, S2 and S3 run in parallel while Opus builds O1.

## Phase 1 — One real agent end to end

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| S4 | Sonnet | `core/harness/config_gen.py`: builds `OPENCODE_CONFIG_CONTENT` (provider → base URL, agent definitions, base permission, compaction, share/autoupdate off) + tests | S2 | Generated config passes opencode's own schema in the O1 fixture |
| O3 | **Opus** | `core/harness/opencode_runner.py`: `OpencodeAgentRunner` (§4.1): session lifecycle, validation with one retry, abort on wall-clock limit, board signals, host-mode warning, `harness` project flag in `ProjectConfig` | S1–S4, O2 | Fake-LLM integration test: brief → tool calls → valid result → COMPLETE on the board |
| O4 | **Opus** | First real-model run: one builder on `cli-tool-from-spec`; triage what breaks and adjust the briefs and prompts | O3 | `agent/builder` holds code that runs; findings written into the plan |

## Phase 2 — Live in Studio

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| O5 | **Opus** | Event-to-signal mapping table (which opencode events become which board signals, quip triggers, and what gets dropped), handed to S5 as its spec | O2 | Table merged into the plan |
| S5 | Sonnet | `core/harness/event_bridge.py`: table-driven SSE → `Signal`s and quip triggers; tests replay recorded event streams captured by the O1 fixture | S3, O5 | Recorded streams produce the expected board sequence |
| O6 | **Opus** | Approval wiring: `permission.asked` → `APPROVAL_NEEDED` → human decision → `POST /permission/{id}/reply`; per-project auto-approve list | S5 | Integration test: a bash `ask` is approved from the API, and a rejection reaches the agent |
| S6 | Sonnet | Studio: render the new signal kinds (tool call, permission ask, structured result, blocked) in BoardFeed and AgentStateCard; reuse ApprovalCard for permission asks; vitest coverage | S5 | `npm test` green; manual check on a fake-LLM run |

## Phase 3 — Sandboxes, gateway, MCP

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| S7 | Sonnet | `sandbox/agent-opencode.Dockerfile` + `worker/sandbox_manager.py` (start/stop, hardening flags, mounts, per-run internal network, gVisor detection) built strictly to §5.3/§5.4 | O3 | Image builds; unit tests check the container spec it generates |
| O7 | **Opus** | Line-by-line hardening review of S7, plus the network isolation (sandboxes reach only gateway and MCP, never each other) | S7 | Isolation verified by hand and in O10 |
| S8 | Sonnet | `gateway/`: FastAPI OpenAI-compatible passthrough; per-agent virtual keys bound to (run, role); model allow-list; spend caps; cost log; key revocation at run end + tests | — | Tests cover allow, deny, over-budget and revoked key |
| S9 | Sonnet | `api/mcp_server.py`: `post_note`, `read_board`, `ask_human`, `request_package`, `request_visual_feedback`, `report_progress`; role taken from the bearer token only; RBAC via `CredentialsBroker` + tests | — | Tests prove a token for role A cannot act as role B |
| O8 | **Opus** | Security review of S8 and S9 (key handling, token binding, default-deny) | S8, S9 | Review notes fixed |
| O9 | **Opus** | Git ownership (§5.5): bare run repo, per-role clones, runner-side fetch and ref checks, protected paths, secret scan, auto-commit of leftovers | O3 | Tests: touching another branch, `main`, or `inputs/` fails the turn |
| O10 | **Opus** | Security escape suite: replays every escape from §2.2 plus host reads, internet access, cross-sandbox calls, MCP impersonation, protected-path edits; runs in CI | S7–S9, O9 | Every case contained, with assertions made on the host |

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
| O14 | **Opus** | Run all six example projects for real; triage; tune role profiles and briefs | S11–S14 | All six complete; quality gate passes on two |

## Phase 6 — Make it the default

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| S15 | Sonnet | Retire the legacy code listed in plan §6.3, one module per commit, deleting or updating its tests | O14 | Full suite green after every commit |
| S16 | Sonnet | Draft updates to DESIGN.md, CLAUDE.md, ARCHITECTURE.md and the diagrams | O14 | Drafts ready |
| O15 | **Opus** | Review S15 and S16 (especially the hard-constraint text); flip the `harness` default to `opencode`; final full run | S15, S16 | Default flipped; demo recorded |

---

## Totals

| | Opus | Sonnet |
|---|---|---|
| Tasks | 15 | 16 |
| Share of new code (rough) | ~35% | ~65% |
| Nature | Foundation, scheduler, security, real-model runs, every review | Modules, adapters, UI, infra from spec, cleanup, docs |

Running Sonnet tasks in parallel: phase 0 runs S1–S3 together; phase 3 runs S7–S9 together.

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
