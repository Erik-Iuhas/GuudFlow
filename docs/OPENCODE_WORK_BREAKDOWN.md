# opencode harness — work breakdown (Opus vs Sonnet)

Companion to [OPENCODE_HARNESS_PLAN.md](OPENCODE_HARNESS_PLAN.md). Section references
(§) point there. Task IDs are stable: they don't get renumbered when tasks are added,
merged or removed.

**Split rule:**
- **Opus** handles the foundation everything else depends on, security boundaries,
  the scheduler (critical path), anything that needs a real-model judgment call, specs,
  and every review.
- **Sonnet 5.5** subagents handle well-specified modules with clear interfaces and
  tests: schemas, mapping tables, clients, adapters, UI, infrastructure from a spec,
  cleanup, docs.

Every Sonnet task works in its own git worktree from a brief that Opus writes (template
at the end). Opus reviews each one before it merges.

---

## Before anything starts (you)

- Sign off on the hard-constraint changes in plan §6.4.
- Answer the open questions in plan §9:
  1. Should phases 1–2 run on the host (`host_dev`) and add containers in phase 3?
  2. Which model is the minimum for each role? (This also decides which roles can get
     `vision: on`, §5.7.)
  3. Should the ask/answer handshake be retired (§4.4)?
- Provide one real API key for the real-model tasks (O4, O12, O14, O17, O19, O24).
  Everything else runs offline against the fake LLM.
- Optional: a web-search provider key for the researcher (S18). Without it, `web: search`
  is unavailable and the one-shot runs with `--web off`.

---

## Status

As of commit `e80822f` plus the documentation update (S16). Legend: **Done** = merged, offline tests
green. **Partly** = the offline part is merged, the rest is listed. **Deferred (you)** = needs a real
model and API key, or a decision only you can make; nothing in the repo blocks it. **Not started**.

Everything that runs offline against the fake LLM is done. What is left falls into three groups:
the real-model runs (O4, O12, O14, O17 and the real-model halves of O19 and O24), the legacy-code
retirement (S15) and final review (O15), and the rest of the Studio track (S24 to S29, S33, O22).

| Phase | Task | Status | Notes |
|---|---|---|---|
| P0 | O1 | Done | Fake LLM, locked-launch fixture, `scripts/install_opencode.sh`, `/doc` snapshot test |
| P0 | O16 | Done | `core/harness/capabilities.py` |
| P0 | S1 | Done | `core/harness/results.py` |
| P0 | S2 | Done | `core/harness/permissions.py` |
| P0 | S3 | Done | `core/harness/opencode_client.py` |
| P0 | O2 | Done | Contract tests; findings are in plan §2.2 |
| P0 | S35 | Done | `.github/workflows/ci.yml` (backend, harness contract, Docker escape-suite job, Studio) |
| P1 | S4 | Done | `core/harness/config_gen.py` |
| P1 | O3 | Done | `core/harness/run.py` (`OpencodeRun`) and `opencode_runner.py`; `harness` flag in `ProjectConfig` |
| P1 | O4 | Deferred (you) | First real-model run of one builder on `cli-tool-from-spec`; needs an API key |
| P2 | O5 | Done | Event-to-signal table in plan §2.1a; signal types in `core/signals.py` |
| P2 | S5 | Done | `core/harness/event_bridge.py` |
| P2 | O6 | Done | `core/harness/approvals.py` |
| P2 | S6 | Done | Legacy Run view renders the harness signals |
| P2 | S17 | Done | Capability switches on team approval |
| P3 | S7 | Done | `sandbox/agent-opencode.Dockerfile`, `worker/sandbox_manager.py` |
| P3 | O7 | Done | Hardening review and network isolation wired in `OpencodeRun` |
| P3 | S8 | Done | `gateway/app.py` |
| P3 | S9 | Done | `core/harness/mcp_server.py`, `mcp_tools.py` |
| P3 | S18 | Done | `core/harness/mcp_web.py` (a search-provider key is optional, see "Before anything starts") |
| P3 | S19 | Done | `core/harness/mcp_browser.py` |
| P3 | S30 | Done | `core/harness/artifacts.py` |
| P3 | S31 | Done | `core/harness/mcp_visual.py` |
| P3 | O8 | Done | Security review; findings fixed (host-only git dir and tool dir) |
| P3 | O9 | Done | `core/harness/git_roles.py` |
| P3 | O10 | Done | `tests/harness/test_escape_suite.py`; skips without Docker, runs in CI |
| P4 | O11 | Done | Review edges, blocking, cycle to human, parking, `max_parallel`, `has_work`, watchdog |
| P4 | S28 | Done | `core/harness/tasks.py` |
| P4 | O23 | Done | `core/harness/task_board.py`, `task_flow.py` |
| P4 | S34 | Done | `api/routes/cards.py`, `core/harness/card_ops.py`, live re-arm |
| P4 | S21 | Done | `core/harness/overseer.py` |
| P4 | O18 | Done | `core/harness/detectors.py`, `overseer_flow.py`; overseer replaces the tech lead |
| P4 | O19 | Partly | Offline stall scenarios with a scripted model are done; tuning the overseer prompt on a real model is deferred (you) |
| P4 | O12 | Deferred (you) | Real multi-agent demo on `fullstack-web-app`; needs an API key |
| P5 | O13 | Done | Plan §5.11 |
| P5 | S11 | Done | `core/meta_proposal.py` |
| P5 | S12 | Done | `core/harness/persona_prompt.py` |
| P5 | S13 | Done | `memory_writer` reads opencode runs |
| P5 | S14 | Done | `tests/quality/`, `scripts/run_quality_gate.sh` (runs offline on a recorded workspace) |
| P5 | S22 | Done | `core/playbook.py` |
| P5 | O20 | Done | `core/harness/retrospective.py`; continuation regression test |
| P5 | O24 | Partly | Visual review loop is done and tested offline; the real-model run on a frontend example is deferred (you) |
| P5 | S32 | Done | `sandbox/agent-opencode-viz.Dockerfile` |
| P5 | S20 | Done | `core/oneshot.py`, `cli/oneshot.py`, `api/routes/oneshot.py`, `data/teams/default_oneshot.json` |
| P5 | O14 | Deferred (you) | All six example projects for real, quality gate on two; needs an API key |
| P5 | O17 | Deferred (you) | The recorded demo on `api-client-library` with a real model; needs an API key |
| P6 | S15 | Done | Legacy tool loop, tech lead, loop guard and edit blocks removed (~13.5k lines; `core/runner.py` 5.5k → ~0.7k lines). `harness: "legacy"` is refused. `context_manager`, `repo_map`, `protocol` and `agents/tools` stay: host-side agents (meta, memory writer, persona chat) still import them. `core/llm/` stays for the same reason |
| P6 | S16 | Done | This documentation update: CLAUDE.md, DESIGN.md, `docs/ARCHITECTURE.md`, README |
| P6 | O15 | Partly | Default flipped (API, worker, `ProjectConfig`, approval screen); S16 reviewed and corrected; S15 reviewed and merged. Open: the final full real-model run (you) |
| UI | O21 | Done | `docs/STUDIO_UI_SPEC.md`, generated `runEvents.gen.ts` |
| UI | S23 | Done | U0: Tailwind and tokens, ui primitives, run-event store, shared components migrated |
| UI | S24 | Done | `features/cockpit/`; `/runs/:id` (legacy view at `?legacy=1`) |
| UI | S25 | Done | `/oneshot`, `features/team/` (graph + playbook panel) |
| UI | S29 | Done | `features/board/`, `/runs/:id/board` |
| UI | S33 | Done | `features/artifacts/`, `/runs/:id/artifacts`, safe artifact-file endpoint |
| UI | S26 | Done | `features/overseer/`, `/runs/:id/report`, project Insights tab |
| UI | S27 | Done | U4: every view on tokens and ui primitives, `styles.css` deleted, Tailwind preflight on; visual suite `studio/e2e/visual.e2e.ts` (light and dark, 0.2% tolerance) |
| UI | O22 | Partly | Offline passes on every visual baseline (light and dark): graph zoom, edge routing and handles, lane overflow, dark-mode rows, budget bar colour, one spend formatter. A pass on a live real-model run is deferred (you) |

**What you need to do to finish the deferred work:** provide an API key (see "Before anything starts")
and run O4, O12, O14 and O17, plus the real-model parts of O19 and O24, using the commands in the
README's "Run the demo" section.

---

## Phase overview

| Phase | Goal | Tasks | Estimate | Exit (plan §7) |
|---|---|---|---|---|
| P0 | Test harness and building blocks | O1, O16, O2, S1, S2, S3, S35 | 1–2 days | `pytest tests/harness/` green offline; CI runs it |
| P1 | One real agent end to end | S4, O3, O4 | 2–3 days | One builder produces working code on `cli-tool-from-spec` |
| P2 | Live in Studio | O5, S5, O6, S6, S17 | 1–2 days | Watch a run live; approve a bash `ask` from the UI |
| P3 | Sandboxes, gateway, MCP, host tools | S7, O7, S8, S9, S18, S19, S30, S31, O8, O9, O10 | 4–5 days | Escape suite passes; sandboxes reach only gateway + MCP |
| P4 | Multi-agent flow: task board, scheduler, overseer | O11, S28, O23, S34, S21, O18, O19, O12 | 4–5 days | planner → (backend ‖ frontend) → reviewer with a review loop, driven by cards |
| P5 | Meta-agent, personas, playbook, visuals, one-shot | O13, S11, S12, S13, S14, S22, O20, O24, S32, S20, O14, O17 | 3–4 days | All six examples run; the one-shot demo passes §10's exit criteria |
| P6 | Make it the default | S15, S16, O15 | 1–2 days | `harness` default is `opencode`; legacy code retired |
| UI | Studio modernization (parallel) | O21, S23, S24, S25, S26, S27, S29, S33, O22 | runs alongside P2–P6 | §12.4 done criteria |

Rough total: 4–5 weeks of focused work. P0–P2 alone already gives a watchable demo.

---

## Phase 0 — Test harness and building blocks

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| O1 | **Opus** | `tests/harness/fake_llm.py` (scripted OpenAI-compatible server with SSE and tool calls) + `tests/harness/conftest.py` fixture that boots a pinned `opencode serve` with the locked config and env flags (§5.2) + `scripts/install_opencode.sh` (pinned version) + `/doc` snapshot contract test. Tests skip cleanly when the binary isn't installed | — | `pytest tests/harness/` green offline, no API key |
| O16 | **Opus** | `core/harness/capabilities.py`: `Capabilities` dataclass with all seven toggles (`sandbox`, `web`, `shell`, `packages`, `subagents`, `browser`, `vision`), role-type defaults, `vision` gating on the model's declared image input, and the compile table (§5.7) to permission rules, container/network profile and MCP grants. Security-relevant, so Opus writes it | — | Table-driven tests for every toggle value, every role default, and vision gating |
| S1 | Sonnet | `core/harness/results.py`: `AgentResult`, `ReviewResult`, `BlockedOn`, `Issue` (§4.2), validators, retry-prompt builder (§4.3) + `tests/harness/test_results.py`. Leaves room for S28's task fields | — | Every validator has a pass case and a fail case |
| S2 | Sonnet | `core/harness/permissions.py`: compiled capabilities → opencode ruleset (§5.1): common base, role profiles, last-match ordering, `StructuredOutput` always allowed, `task` → `explore` only for `explore_only` + tests | O16 | One test per role default; ordering test; deny-all base test |
| S3 | Sonnet | `core/harness/opencode_client.py`: typed async httpx client for session create (with `permission`), `prompt_async`, message get, abort, permission reply (`once`/`always`/`reject`), diff, `/event` SSE iterator, Basic auth + unit tests with mocked HTTP | — | Unit tests green; every method typed and documented |
| O2 | **Opus** | Contract tests that run S3 against O1's real opencode: structured output (valid, invalid, plain text), permission deny, `permission.asked` round-trip, `always` reply, abort, `task` restricted to `explore` with `subagent_depth: 1`, `read` on a PNG returning an attachment, how MCP image results reach the model, compaction under deny-all (gotcha #9), and the MCP permission key | O1, S3 | Contract suite green; plan §2.2 and §5.10 updated with the results |
| S35 | Sonnet | CI (GitHub Actions; there is none today): backend `pytest`, `studio` tests, harness contract tests with the pinned opencode, and a Docker job for the escape suite (enabled once O10 lands) | O1 | Workflow green on the branch |

S1 and S3 run in parallel while Opus builds O1 and O16; S2 starts as soon as O16 lands;
S35 starts once O1 lands.

## Phase 1 — One real agent end to end

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| S4 | Sonnet | `core/harness/config_gen.py`: builds `OPENCODE_CONFIG_CONTENT`: provider → base URL (gateway in P3, the provider directly in host mode), model `attachment`/`modalities`, `attachment.image` resize caps (§5.10), agent definitions with `steps`, compiled permissions, `subagent_depth`, compaction, the GuudFlow MCP server entry with its per-agent token, `share: disabled`, `autoupdate: false` + tests | S2 | Generated config is accepted by opencode in the O1 fixture |
| O3 | **Opus** | `core/harness/opencode_runner.py`: `OpencodeAgentRunner` (§4.1): session lifecycle, brief builder (only what opencode can't know, §5.9; untrusted inputs such as `research/notes.md` wrapped in delimiters, §5.8), validation with one retry, abort on wall-clock limit, board signals incl. `CAPABILITIES` at every spawn (§5.7), host-mode warning, `harness: legacy \| opencode` flag in `ProjectConfig` | S1–S4, O2 | Fake-LLM integration test: brief → tool calls → valid result → COMPLETE on the board |
| O4 | **Opus** | First real-model run: one builder on `cli-tool-from-spec`; triage what breaks and adjust the briefs and prompts | O3 | `agent/builder` holds code that runs; findings written into the plan |

## Phase 2 — Live in Studio

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| O5 | **Opus** | Event-to-signal mapping table (which opencode events become which board signals, quip triggers, and what gets dropped) **and** the new `SignalType` values used across the plan: `CAPABILITIES`, `AGENT_RESULT`, `WEB_REQUEST`, `ARTIFACT`, `OVERSEER_INTERVENTION`, card-thread entries. Handed to S5 and O21 as their spec | O2 | Table merged into the plan; signal types added to `core/signals.py` with tests |
| S5 | Sonnet | `core/harness/event_bridge.py`: table-driven SSE → `Signal`s and quip triggers; tests replay recorded event streams captured by the O1 fixture | S3, O5 | Recorded streams produce the expected board sequence |
| O6 | **Opus** | Approval wiring: `permission.asked` → `APPROVAL_NEEDED` → human decision → `POST /permission/{id}/reply`; per-project auto-approve list; sticky `always` approvals scoped to the run and recorded as `APPROVAL_GRANTED` (§5.9); capability widening only through approval, applied from the next spawn (§5.7) | S5 | Integration test: a bash `ask` is approved from the API, a rejection reaches the agent, and an `always` stops repeat prompts |
| S6 | Sonnet | Legacy Run view, minimal: render the P2 signal kinds (tool call, permission ask, agent result, blocked, `CAPABILITIES`) in BoardFeed and AgentStateCard; reuse ApprovalCard for permission asks (once/always/reject). Later signal kinds render in the UI track | S5 | `npm test` green; manual check on a fake-LLM run |
| S17 | Sonnet | Team approval: a switch group per role for the seven capabilities (§5.7), role defaults pre-filled, `vision` disabled with a tooltip when the model lacks image input, `host_dev` warned and disabled in production; API accepts and stores the block. If U0 (S23) has landed, build on the new components | O16 | vitest + API tests green; approved capabilities appear in the run's first `CAPABILITIES` signals |

## Phase 3 — Sandboxes, gateway, MCP, host tools

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| S7 | Sonnet | `sandbox/agent-opencode.Dockerfile` + `worker/sandbox_manager.py` (start/stop, hardening flags, mounts incl. read-only `inputs/` and `inputs/mockups/`, per-run internal network, gVisor detection, 1 GB memory) built strictly to §5.3/§5.4 | O3 | Image builds; unit tests check the container spec it generates |
| O7 | **Opus** | Line-by-line hardening review of S7, plus the network isolation: sandboxes reach only gateway and MCP, never each other; host → sandbox allowed only for screenshots | S7 | Isolation verified by hand and in O10 |
| S8 | Sonnet | `gateway/`: FastAPI OpenAI-compatible passthrough; per-agent virtual keys bound to (run, role); model allow-list; spend caps (incl. the one-shot `--budget`); cost log with image tokens reported separately; key revocation at run end + tests | — | Tests cover allow, deny, over-budget, revoked key and image-token accounting |
| S9 | Sonnet | `api/mcp_server.py`: `post_note(task_id, …)`, `read_board`, `ask_human`, `request_package`, `request_visual_feedback`, `report_progress`; tools granted per role from the O16 compile output; role taken from the bearer token only; RBAC via `CredentialsBroker` + tests | O16 | Tests prove a token for role A cannot act as role B, or use a tool it wasn't granted |
| S18 | Sonnet | `api/mcp_web.py`: `web_search` / `web_fetch` on the host (§5.8): domain allow/deny, private-IP blocking, per-run and per-role budgets, per-run cache, `WEB_REQUEST` signals, untrusted-content delimiters + size cap, search key via `CredentialsBroker` | S9 | Tests cover deny-listed domain, private IP, budget exhausted, cache hit, delimiter wrapping |
| S19 | Sonnet | `api/mcp_browser.py`: host-side Playwright `screenshot` tool reaching the role's dev server over the run network; writes into the role's clone at `.guudflow/screens/` (so `read` can reach it, §5.10) and the run folder for Studio's static endpoint (§5.9) | S7, S9 | Screenshot of a sample app served from a sandbox; no browser binary in the agent image |
| S30 | Sonnet | `core/harness/artifacts.py`: artifact registry, `ARTIFACT` signal, card-thread entry; `.guudflow/screens/` git-ignored; EXIF stripping + tests (§5.10) | O9, S19 | Registry picks up new SVG/PNG/PDF/HTML after a turn; screenshots land where `read` can reach them |
| S31 | Sonnet | `api/mcp_visual.py`: host-side `visual_diff` (pixel diff + Playwright element-box comparison), per-task baselines, diff image output + tests (§5.10) | S19 | Tests on fixture images: identical, small shift, large regression |
| O8 | **Opus** | Security review of S8, S9, S18, S19, S30 and S31 (key handling, token binding, default-deny, SSRF, injection delimiters, host → sandbox route) | S8, S9, S18, S19, S30, S31 | Review notes fixed |
| O9 | **Opus** | Git ownership (§5.5): bare run repo, per-role clones, runner-side fetch and ref checks, protected paths, secret scan, auto-commit of leftovers | O3 | Tests: touching another branch, `main`, or `inputs/` fails the turn |
| O10 | **Opus** | Security escape suite: every escape from §2.2, host reads, internet access, cross-sandbox calls, MCP impersonation, protected-path edits, a role without `web` calling web tools, a deny-listed or private-IP fetch, the sandbox reaching out over the screenshot route, an agent trying to widen its own capabilities; wired into S35's Docker job | S7–S9, S18, S19, O9, S35 | Every case contained, with assertions made on the host; CI runs it |

## Phase 4 — Multi-agent flow: task board, scheduler, overseer

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| O11 | **Opus** | Scheduler: review edges with `max_rounds` (§4.5), blocked re-arm, A↔B cycle → `HUMAN_NEEDED` (§4.4), `max_parallel_sandboxes`; escalation goes to the overseer, not the tech lead; new tests alongside the existing six; no concurrency outside `core/scheduler.py` | O3 | All scheduler tests green, old and new |
| S28 | Sonnet | `core/harness/tasks.py` schemas: `PlannedTask`, `PlanResult` and their validators (owners exist, no cycles, acceptance required, no `L`), `AgentResult.task_id` + per-item `acceptance`, per-item `ReviewResult`, typed card-thread entries; extend the existing `Task` dataclass and persister + tests (§4.6) | S1 | One pass and one fail test per rule |
| O23 | **Opus** | Task-board scheduling (§4.6): tasks as the unit of work, task dependencies, column transitions from scheduler events only, file-overlap guard, WIP limits, definition of done per `kind`; cards created by the runner from the validated plan; each brief carries its card and thread | O11, S28 | Scheduler tests: card flow end to end, overlap serialised, WIP respected, review fail returns the card with its issues |
| S34 | Sonnet | Card API for humans: add a card, change priority, comment on a card (REST + websocket events); human entries land in the card thread and the next brief | O23 | API tests green; a human comment shows up in the next brief for that task |
| S21 | Sonnet | `core/harness/overseer.py`: `OverseerResult` / `Intervention` schemas and validators for the menu in §11.3 (card-level `split`, `reassign`, `rebrief`), `OVERSEER_INTERVENTION` signal + tests | O5 | One pass and one fail test per intervention type; caps enforced |
| O18 | **Opus** | `core/harness/detectors.py`: stuck detectors incl. card aging + situation report (§11.2); scheduler-side application of each intervention (§11.3) with dedupe, cooldown and per-run caps; `decide` writes `docs/decisions.md`; the overseer replaces the tech lead as escalation handler | O11, O23, S21 | Fake-LLM scenarios for every detector: the stall is detected, the intervention is applied by the scheduler, the run recovers or reaches `HUMAN_NEEDED` |
| O19 | **Opus** | Overseer prompt and real-model tuning on forced stalls (blocked cycle, repeated review issue, burn without output, aging card) | O18 | Overseer picks a sensible intervention in each scripted stall; results noted in the plan |
| O12 | **Opus** | Real multi-agent demo: planner → (backend ‖ frontend) → reviewer on `fullstack-web-app`, driven by cards, with one forced review loop | O11, O23, O18, P3 | Recorded run that completes; findings fed back into the briefs |

## Phase 5 — Meta-agent, personas, playbook, visuals, one-shot

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| O13 | **Opus** | Spec for how personas compile into opencode agents (the 7-layer prompt, memory recall, project rules, model tier) | O3 | Spec merged |
| S11 | Sonnet | `core/meta_proposal.py`: proposals emit role type, a `capabilities` block per role (role-type defaults; `vision` only where the model supports it), review edges, and a planner role; schema and test updates | O11, O16 | Existing meta tests updated and green |
| S12 | Sonnet | Implement O13 in `config_gen.py` (reusing `effective_system_prompt()`) + tests | O13, S4 | Snapshot tests of the generated agent prompts |
| S13 | Sonnet | `memory_writer` adapter that reads opencode transcripts (`GET /session/{id}/message`) and card threads instead of the old turn logs | O3, O23 | Memory-writer tests green on recorded transcripts |
| S14 | Sonnet | Quality gate: `tests/quality/`, `scripts/run_quality_gate.sh`, per-project assertions (`.claude/future_buff/functional_verification.md`) | O12 | Runs against a recorded workspace offline |
| S22 | Sonnet | `core/playbook.py`: playbook storage (team adjustments, brief addenda, known pitfalls, review rounds, capability hints, task templates), evidence runs, recency-weighted confidence, lifecycle states (reuse the persona-memory pattern code) + tests | — | Tests for add, reinforce, contradict/decay, archive |
| O20 | **Opus** | Retrospective + carry-forward brief (§11.4); the meta-agent starts from the playbook and returns its changes as a diff; open cards carried forward with their threads; regression test that replays the `run_632863526b` continuation scenario | O18, S22, S11, O23 | Continuation test no longer spirals; a second run of a project proposes playbook changes |
| O24 | **Opus** | Visual review loop (§5.10): builder self-check, `visual_diff` pre-check, vision-gated reviewer, per-task baselines, per-turn/per-run image caps, human annotation → card; real-model run on a frontend example | O23, S30, S31 | A visual card goes through one fail-and-fix round driven by the screenshot; image tokens reported per agent |
| S32 | Sonnet | Optional `agent-opencode-viz` image variant (matplotlib, plotly, kaleido) + chart-rendering smoke test | S7 | Headless chart renders in the sandbox with no network |
| S20 | Sonnet | One-shot (§10): `cli/oneshot.py` (`guudflow oneshot GOAL --web --budget --demo`), `POST /api/oneshot` reusing the invoke 202 job path, `data/teams/default_oneshot.json` + role templates (researcher writing `research/notes.md`, planner emitting the task list, two builders, reviewer), demo auto-approve, deliverables incl. per-agent cost from the gateway | O11, O23, S8, S11, S18 | API returns 202; fake-LLM end-to-end test of the default team; CLI prints the summary and cost table |
| O14 | **Opus** | Run all six example projects for real; triage; tune role profiles, capabilities and briefs | S11–S14, S20 | All six complete; quality gate passes on two |
| O17 | **Opus** | The demo: tune the default team and briefs on `api-client-library` with a real model; check the §10 exit criteria; record it | S20, O14 | `guudflow oneshot … --demo` completes under budget, the client's tests pass offline, Studio plays it live |

## Phase 6 — Make it the default

| ID | Owner | Task | Depends on | Done when |
|---|---|---|---|---|
| S15 | Sonnet | Retire the legacy code listed in plan §6.3 (incl. `_generic_tool_use_loop`, protocol ASK/ANSWER/SKIP parsing, `tech_lead.py`, `assign-task`/`decompose-task`/`closes:`, `context_manager`/`repo_map`, `core/llm/`), one module per commit, deleting or updating its tests | O14 | Full suite green after every commit |
| S16 | Sonnet | Draft updates to DESIGN.md, CLAUDE.md (hard constraints per §6.4, stage list, protocol and IT rules), ARCHITECTURE.md and the diagrams | O14 | Drafts ready |
| O15 | **Opus** | Review S15 and S16 (especially the hard-constraint text); flip the `harness` default to `opencode`; final full run | S15, S16, O17 | Default flipped; demo recorded |

## UI track (parallel with P2–P6)

Plan §12. Mostly Sonnet: it's well-specified front-end work once the contracts are set.

| ID | Owner | Step | Task | Depends on | Done when |
|---|---|---|---|---|---|
| O21 | **Opus** | — | UI spec: design tokens, the typed run-event store contract (generated from O5's signal types, incl. cards, artifacts, overseer, capabilities, web requests), and the Run cockpit layout | O5 | Spec merged; event types shared between backend and Studio |
| S23 | Sonnet | U0 | Tailwind + tokens (light/dark), shadcn/ui, TanStack Query, Zustand run-event store; migrate shared components | O21 | Shared components on tokens; vitest green |
| S24 | Sonnet | U1 | Run cockpit: React Flow pipeline graph with review edges, round counters and blocked-on arrows (absorbs the old S10), per-agent lanes, approvals bar (once/always/reject), budget meter, virtualized board and quip feeds | S23, S5, O11 | Plays a recorded 2,000-event run smoothly; vitest + Playwright flow |
| S25 | Sonnet | U2 | One-shot launcher; team approval as a graph with the playbook diff (S17's capability switches move onto the new components) | S23, S17, S20, O20 | Playwright: launch a one-shot, approve a team with a capability change and a playbook change |
| S29 | Sonnet | U3 | Task board: rebuilt `KanbanBoard` with role swimlanes, scheduler-driven columns, card drawer (checklist, thread, diffs, artifacts), drag to reprioritise, add a human card, comment | S23, S34 | Playwright: add a card, drag it, comment, see it in the next brief |
| S33 | Sonnet | U3 | Artifact gallery: registry view filtered by task/role, before/after slider across rounds, diff images, annotate-to-comment; DOMPurify for SVG, sandboxed iframe + CSP for HTML | S23, S30 | Playwright flow; a malicious SVG/HTML fixture can't run script or reach the network |
| S26 | Sonnet | U3 | Overseer panel, run report (cost per agent, branch diffs, screenshots, web log, capability record, retrospective), project page with overseer metrics trending and the playbook | S24, O18, O20 | Playwright: read a run report; overseer interventions render with rationale |
| S27 | Sonnet | U4 | Migrate the remaining views; delete `styles.css` and all inline styles; Playwright visual snapshot suite | S26, S29, S33 | `grep "style={{"` finds nothing; visual suite green |
| O22 | **Opus** | U1, U4 | UX review passes: information hierarchy, accessibility, empty and error states | S24, S27 | Review notes fixed |

---

## Totals

| | Opus | Sonnet |
|---|---|---|
| Tasks | 24 (O1–O24) | 34 (S1–S35; S10 merged into S24) |
| Share of new code (rough) | ~30% | ~70% |
| Nature | Foundation, capabilities, scheduler and task board, security, overseer logic, real-model runs, specs, every review | Schemas, clients, adapters, host tools, infrastructure from spec, CI, the UI track, cleanup, docs |

**Running Sonnet tasks in parallel:**
- P0: S1 and S3 together; S2 right after O16; S35 after O1.
- P2: S6 and S17 together.
- P3: S7, S8 and S9 together; then S18, S19; then S30, S31.
- P4: S28 and S21 together; S34 after O23.
- P5: S11, S13, S22 and S32 together; then S12, S14, S20.
- UI: S23 once O21 lands; then S24; then S25, S29, S33 together; then S26; then S27.

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
