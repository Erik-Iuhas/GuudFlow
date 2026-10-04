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
| O2 | **Opus** | Contract tests that run S3 against O1's real opencode: structured output (valid, invalid, plain text), permission deny, `permission.asked` round-trip, "always" reply, abort, `task` restricted to `explore` with `subagent_depth: 1`, `read` on a PNG returning an attachment, and how MCP image results reach the model. Confirms gotcha #9 and the MCP permission key | O1, S3 | Contract suite green; plan §2.2 updated with results |

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
| S30 | Sonnet | `core/harness/artifacts.py`: artifact registry, `ARTIFACT` signal, card-thread entry; `.guudflow/screens/` (git-ignored) as the screenshot target; EXIF stripping + tests (§5.10) | O9, S19 | Registry picks up new SVG/PNG/PDF/HTML after a turn; screenshots land where `read` can reach them |
| S31 | Sonnet | `api/mcp_visual.py`: host-side `visual_diff` (pixel diff + Playwright element-box comparison), per-task baselines, diff image output + tests (§5.10) | S19 | Tests on fixture images: identical, small shift, large regression |
| O8 | **Opus** | Security review of S8, S9, S18, S19, S30 and S31 (key handling, token binding, default-deny, SSRF, injection delimiters, host → sandbox route) | S8, S9, S18, S19, S30, S31 | Review notes fixed |
| O9 | **Opus** | Git ownership (§5.5): bare run repo, per-role clones, runner-side fetch and ref checks, protected paths, secret scan, auto-commit of leftovers | O3 | Tests: touching another branch, `main`, or `inputs/` fails the turn |
| O10 | **Opus** | Security escape suite: replays every escape from §2.2 plus host reads, internet access, cross-sandbox calls, MCP impersonation, protected-path edits, plus: a role without `web` calling web tools, a deny-listed or private-IP fetch, the sandbox reaching out over the screenshot route, an agent trying to widen its own capabilities; runs in CI | S7–S9, S18, S19, O9 | Every case contained, with assertions made on the host |

## Phase 4 — Multi-agent flow

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| O11 | **Opus** | Scheduler: review edges with `max_rounds` (§4.5), blocked re-arm, A↔B cycle → `HUMAN_NEEDED` (§4.4), `max_parallel_sandboxes`; new tests alongside the existing six; no concurrency outside `core/scheduler.py` | O3 | All scheduler tests green, old and new |
| S10 | Sonnet | Studio PipelineGraph: draw review edges, round counters and blocked-on arrows | O11 | vitest green |
| S28 | Sonnet | `core/harness/tasks.py` schemas: `PlannedTask`, `PlanResult` and their validators (owners exist, no cycles, acceptance required, no `L`), `AgentResult.task_id` + per-item `acceptance`, per-item `ReviewResult`, typed card-thread entries; extend the existing `Task` dataclass and persister + tests (§4.6) | S1 | One pass and one fail test per rule |
| O23 | **Opus** | Task-board scheduling (§4.6): tasks as the unit of work, task dependencies, column transitions from scheduler events only, file-overlap guard, WIP limits, definition of done per `kind`; cards created by the runner from the validated plan | O11, S28 | Scheduler tests: card flow end to end, overlap serialised, WIP respected, review fail returns the card with its issues |
| S21 | Sonnet | `core/harness/overseer.py`: `OverseerResult` / `Intervention` schemas and validators for the menu in plan §11.3, `OVERSEER_INTERVENTION` signal + tests | — | One pass and one fail test per intervention type; caps enforced |
| O18 | **Opus** | Stuck detectors (including card aging) + situation report (§11.2) and scheduler-side application of each intervention (§11.3), with dedupe, cooldown and per-run caps; the overseer replaces the tech lead as escalation handler | O11, O23, S21 | Fake-LLM scenarios for every detector: the stall is detected, the intervention is applied by the scheduler, the run recovers or reaches `HUMAN_NEEDED` |
| O19 | **Opus** | Overseer prompt and real-model tuning on forced stalls (blocked cycle, repeated review issue, burn without output) | O18 | Overseer picks a sensible intervention in each scripted stall; results noted in the plan |
| O12 | **Opus** | Real multi-agent demo: planner → (backend ‖ frontend) → reviewer on `fullstack-web-app`, including one forced review loop | O11, P3 | Recorded run that completes; findings fed back into the briefs |

## Phase 5 — Meta-agent, personas, quality

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| O13 | **Opus** | Spec for how personas compile into opencode agents (the 7-layer prompt, memory recall, project rules, model tier) | O3 | Spec merged |
| S11 | Sonnet | `core/meta_proposal.py`: proposals emit role type + scopes + review edges; schema and test updates | O11 | Existing meta tests updated and green |
| S12 | Sonnet | Implement O13 in `config_gen.py` (reusing `effective_system_prompt()`) + tests | O13, S4 | Snapshot tests of the generated agent prompts |
| S13 | Sonnet | `memory_writer` adapter that reads opencode transcripts (`GET /session/{id}/message`) instead of the old turn logs | O3 | Memory-writer tests green on recorded transcripts |
| S14 | Sonnet | Quality gate: `tests/quality/`, `scripts/run_quality_gate.sh`, per-project assertions (`.claude/future_buff/functional_verification.md`) | O12 | Runs against a recorded workspace offline |
| S22 | Sonnet | `core/playbook.py`: playbook storage, evidence runs, recency-weighted confidence, lifecycle states (reuse the persona-memory pattern code) + tests | — | Tests for add, reinforce, contradict/decay, archive |
| O20 | **Opus** | Retrospective + carry-forward brief (§11.4); the meta-agent starts from the playbook and shows changes as a diff; regression test that replays the `run_632863526b` continuation scenario; open cards carried forward with their threads | O18, S22, S11, O23 | Continuation test no longer spirals; the second run of a project shows playbook changes on the approval screen |
| O24 | **Opus** | Visual review loop (§5.10): builder self-check, `visual_diff` pre-check, vision-gated reviewer, baselines, human annotation → card; real-model run on a frontend example | O23, S30, S31 | A visual card goes through one fail-and-fix round driven by the screenshot; image tokens reported per agent |
| S32 | Sonnet | Optional `agent-opencode-viz` image variant (matplotlib, plotly, kaleido) + chart-rendering smoke test | S7 | Headless chart renders in the sandbox with no network |
| S20 | Sonnet | One-shot (§10): `cli/oneshot.py` (`guudflow oneshot GOAL --web --budget --demo`), `POST /api/oneshot` reusing the invoke 202 job path, `data/teams/default_oneshot.json`, demo auto-approve, deliverables incl. per-agent cost from the gateway | O11, S8, S11 | API returns 202; fake-LLM end-to-end test of the default team; CLI prints the summary and cost table |
| O14 | **Opus** | Run all six example projects for real; triage; tune role profiles and briefs | S11–S14 | All six complete; quality gate passes on two |
| O17 | **Opus** | The demo: tune the default team and briefs on `api-client-library` with a real model; check the §10 exit criteria; record it | S20, O14 | `guudflow oneshot … --demo` completes under budget, the client's tests pass offline, Studio plays it live |

## Phase 6 — Make it the default

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| S15 | Sonnet | Retire the legacy code listed in plan §6.3, one module per commit, deleting or updating its tests | O14 | Full suite green after every commit |
| S16 | Sonnet | Draft updates to DESIGN.md, CLAUDE.md, ARCHITECTURE.md and the diagrams | O14 | Drafts ready |
| O15 | **Opus** | Review S15 and S16 (especially the hard-constraint text); flip the `harness` default to `opencode`; final full run | S15, S16, O17 | Default flipped; demo recorded |

## UI track (runs in parallel with the harness phases)

Plan §12. Mostly Sonnet: it's well-specified front-end work once the contracts are set.

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| O21 | **Opus** | UI spec: design tokens, the typed run-event store contract (generated from the event bridge's signal types), and the Run cockpit layout | O5 | Spec merged; event types shared between backend and Studio |
| S23 | Sonnet | U0: Tailwind + tokens (light/dark), shadcn/ui, TanStack Query, Zustand run-event store; migrate shared components | O21 | Shared components on tokens; vitest green |
| S24 | Sonnet | U1: Run cockpit: React Flow pipeline graph, per-agent lanes, approvals bar (once/always/reject), budget meter, virtualized board and quip feeds | S23, S5 | Plays a recorded 2,000-event run smoothly; vitest + Playwright flow |
| S25 | Sonnet | U2: one-shot launcher; team approval as a graph with the playbook diff (S17's capability switches move onto the new components) | S23, S17, S20 | Playwright: launch a one-shot, approve a team with a capability change |
| S26 | Sonnet | U3: overseer panel, run report (cost per agent, branch diffs, screenshots, web log, capability record, retrospective), project page with trends and playbook | S24, O18, O20 | Playwright: read a run report; overseer interventions render with rationale |
| S27 | Sonnet | U4: migrate the remaining views; delete `styles.css` and all inline styles; Playwright visual snapshot suite | S26 | `grep "style={{"` finds nothing; visual suite green |
| S29 | Sonnet | Task board UI: rebuilt `KanbanBoard` with role swimlanes, scheduler-driven columns, card drawer (checklist, thread, diffs, artifacts), drag to reprioritise, add a human card, comment | S23, O23 | Playwright: add a card, drag it, comment, see it in the next brief |
| S33 | Sonnet | Artifact gallery: registry view filtered by task/role, before/after slider across rounds, diff images, annotate-to-comment; DOMPurify for SVG, sandboxed iframe + CSP for HTML | S23, S30 | Playwright flow; a malicious SVG/HTML fixture can't run script or reach the network |
| O22 | **Opus** | UX review passes at U1 and U4: information hierarchy, accessibility, empty and error states | S24, S27 | Review notes fixed |

---

## Totals

| | Opus | Sonnet |
|---|---|---|
| Tasks | 24 | 33 |
| Share of new code (rough) | ~30% | ~70% |
| Nature | Foundation, scheduler, security, overseer logic, real-model runs, specs, every review | Modules, adapters, schemas, the UI track, infra from spec, cleanup, docs |

Running Sonnet tasks in parallel: phase 0 runs S1 and S3 together (S2 right after O16); phase 2 runs S6 and S17 together; phase 3 runs S7–S9 together, then S18 and S19; the UI track (S23–S27) runs alongside the harness phases once O21 lands.

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
