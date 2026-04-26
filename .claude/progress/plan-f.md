# Active plan — Phase F — Long-term moat features

Status: F.3 ✅ shipped 2026-04-26. F.1/F.2/F.4 still planned.
Order chosen by Erik 2026-04-26: "F.3 first because it sits inside
infrastructure that already works and pays off immediately — every
completed run starts producing real patterns."

The persona auto-load gap (`AgentRunner._build_agents` doesn't
auto-attach personas from `cfg.persona_id`) is closed by a scheduled
remote agent in 14 days. Until then, F.3 is tested via the same
`runner_factory` workaround `tests/api/test_full_journey.py` already
uses. After auto-load lands, the journey test simplifies and F.3 keeps
working unchanged.

---

## F.3 — Real memory_writer LLM synthesis ✅ SHIPPED 2026-04-26

Today's `MemoryWriter` (`agents/builtin/memory_writer.py`) ships
deterministic Steps 1, 2, 5, 6 (record episode, recompute confidence,
refresh reputation, reconciliation pass). Steps 3 (new pattern
detection from episode clusters) and 4 (style tendency updates from
evidence) are scaffolded as no-op placeholders. F.3 implements both
with LLM-driven synthesis, plus tightens Step 6 flip-detection.

### F.3.0 — Wire LLM into MemoryWriter ✅ SHIPPED [b0bfd23]

**Goal:** `MemoryWriter.__init__(llm=None)` + runner threads its LLM
client through. `llm=None` retains today's deterministic behaviour
(no regression). Tests using `MockLLMClient` get a real synthesis
path.

- `agents/builtin/memory_writer.py` — add `llm` constructor arg, store
  on instance, pass through to `_update_persona`.
- `core/runner.py:_run_memory_writer` — pass `self.llm` when
  instantiating writer, OR ensure the existing instantiation site
  (`MemoryWriter()` from worker.job_runner) gets the llm threaded.
- Audit construction sites: `worker/job_runner.py` constructs
  `MemoryWriter()` with no args. Update to take llm from the same
  source the runner uses.

**Tests** (extend `tests/agents/builtin/test_memory_writer.py`):
- Constructed with `llm=None` — same behaviour as today
- Constructed with `MockLLMClient` — llm reachable inside on_run_complete

Commit: `feat(memory): thread LLM into MemoryWriter (Phase F.3.0)`

### F.3.1 — LLM-driven pattern distillation from episode clusters ✅ SHIPPED [b0bfd23]

**Goal:** Step 3 emits new emerging patterns. The LLM is asked
ONLY when there are ≥3 episodes sharing tags (otherwise no synthesis
needed; saves tokens).

- `agents/builtin/memory_writer.py` — new `_distill_patterns(persona,
  llm)` method. Cluster recent episodes by shared tags (≥3 episodes,
  ≥2 shared tags). For each cluster, render an LLM prompt asking for
  a single-pattern synthesis with structured tool-use:
  ```
  emit_pattern(title, lesson, applies_when[], confidence_seed)
  emit_no_pattern(reason)
  ```
  The LLM returns ONE structured tool call per cluster. Defensive:
  malformed tool call → skip the cluster, no synthesis. Cluster size
  cap (e.g. last 50 episodes) keeps token cost bounded.

- New `Pattern` records seeded with:
  - `status = STATUS_EMERGING`
  - `confidence = 0.6` (per §34 "emerging starts at 0.6")
  - `source_episodes = [<cluster's episode_ids>]`
  - `lifetime_applications = 0` (synthesis ≠ application)
  - `first_observed = now`

**Tests:**
- Cluster of 3 episodes with shared tags → mocked LLM returns
  `emit_pattern(...)` → new pattern added, status=emerging, confidence=0.6
- Cluster of 3 → mocked LLM returns `emit_no_pattern(reason)` → no
  pattern added, no error
- Cluster of 3 → mocked LLM raises → no pattern added, no error
- < 3 episodes in any cluster → LLM not called at all
- Existing patterns: dedupe — if synthesis would emit a pattern with
  identical `lesson` to an existing pattern, skip it (LLM already saw
  the existing patterns in its prompt; this is belt-and-suspenders).

Commit: `feat(memory): LLM-driven pattern distillation (Phase F.3.1)`

### F.3.2 — Pattern→anti-pattern flip detection ✅ SHIPPED [761de9c]

**Goal:** Step 6d wires the §34 flip mechanic. Today the comment in
the code says "(Detection requires 'what worked' alternatives in
source episodes; implemented as a hook so the §34 mechanic is wired
without over-fitting heuristics. The LLM-driven version lands later.)"
F.3.2 lands the LLM-driven version.

- `agents/builtin/memory_writer.py` — extend `_reconcile` to detect
  flip candidates: patterns in `declining` status with ≥5 consecutive
  failed applications. For each, ask the LLM (structured tool):
  ```
  emit_flip(anti_lesson, alternative)
  emit_no_flip(reason)
  ```
  with the failing pattern + the source episodes that contributed.
  On `emit_flip`, build an `AntiPattern` per `_anti_to_json` shape +
  set the original pattern to `superseded` with `superseded_by`
  pointing at the new anti-pattern id.

**Tests:**
- Pattern with 5 consecutive failures + LLM returns flip → AntiPattern
  created, original pattern superseded with link
- Pattern with 5 consecutive failures + LLM returns no_flip → original
  pattern stays declining (existing transition logic still runs)
- Pattern with < 5 failures → LLM not called
- LLM raises → no flip, original pattern stays declining

Commit: `feat(memory): LLM-driven pattern flip detection (Phase F.3.2)`

### F.3.3 — Deterministic style drift from run evidence ✅ SHIPPED [dc5709a]

**Goal:** Step 4 is scaffolded. Today's persona has `style.json` with
working-style tendency scores (e.g. "incremental_commits": 0.7,
"prefers_tests_first": 0.4). Each run produces signals that drift the
scores. F.3.3 implements deterministic updates first (cheap, evidence
is already on the workspace board); the LLM-driven version is queued
to F.3.5 (deferred — deterministic gets us 80% there).

Decision (Erik): start with deterministic. F.3.3 is "deterministic
style drift," not LLM. Rename internally for clarity.

- `agents/builtin/memory_writer.py` — `_drift_style(persona, role,
  workspace)`:
  - `incremental_commits` ↑ if N GIT_COMMITs by role this run > 1
  - `prefers_tests_first` ↑ if first commit message contains "test"
  - `responds_to_feedback` ↑ if role had a REJECTED → APPROVED arc
  - `verbose_documentation` ↑ if doc:* tools were called

- Drift step: tendency = (1-α) * tendency + α * observation, α=0.1
  (gentle drift, ~10 runs to fully shift a stuck score).

**Tests:**
- Run with multiple commits → incremental_commits drifts up
- Run with single big commit → drifts down (small effect)
- Run with no rejection → responds_to_feedback unchanged
- Run with REJECTED→APPROVED → responds_to_feedback drifts up

Commit: `feat(memory): deterministic style drift from run evidence (Phase F.3.3)`

### F.3.4 — Integration smoke test ✅ SHIPPED 2026-04-26

**Goal:** One end-to-end test that proves the synthesis loop works:
two runs of the same persona, run 1 produces episodes, run 2's
memory_writer synthesizes a pattern from the cluster and the
persona's `patterns.json` reflects it.

- `tests/agents/builtin/test_memory_writer_synthesis.py` (new) —
  uses `MockLLMClient` scripted with the synthesis tool calls. NOT
  the full HTTP journey — purely the writer-level test.

Commit: `test(memory): synthesis integration over two runs (Phase F.3.4)`

### F.3.5 — DEFERRED: LLM-driven style synthesis

Future. The deterministic drift covers the common cases; LLM
synthesis for style would catch nuanced patterns ("reaches for
defensive null checks even when not needed") but isn't worth the
complexity until a real user request surfaces.

---

## F.1 — Training Camp ✅ SHIPPED 2026-04-26

DESIGN §35. Toy scenario library, tournament mode, ranking board for
hiring decisions. Headline moat feature.

### Scope decisions (Erik 2026-04-26)

- **Auto-check types for the first cut: `files_present` + `text_contains` only.**
  Full `pytest` / `ruff` checks need subprocess execution + output
  parsing inside the run sandbox — that's Phase F.1.5+ territory.
  `files_present` and `text_contains` cover ~70% of useful rubric
  checks at zero infra cost.
- **Manual checks: deferred.** `manual_checks` show up in the rubric
  schema for forward-compat but aren't surfaced in the UI yet — F.1
  ships the auto-graded path.
- **Tournaments run sequentially in F.1.** Parallel execution would
  require running N JobRunners or extending the queue to dispatch a
  fan-out; deferred until there's a real concurrency need. The API
  shape supports parallel later without changes — only the dispatch
  internals would shift.

### F.1.0 — Scenario library + signals ✅ SHIPPED [e27d888]

- `data/training_camp/scenarios/` directory layout
- One seed scenario: `hello_python_v1` (write a `greet()` function)
- `core/training/__init__.py` + `core/training/scenario.py` —
  `Scenario` + `Rubric` dataclasses + loader
- `core/signals.py` — `RUBRIC_SCORE`, `TOURNAMENT_RESULT` types
- Tests: `tests/core/test_training_scenario.py`

Commit: `feat(training): scenario library + rubric signals (Phase F.1.0)`

### F.1.1 — Rubric scorer + training-mode runner branch ✅ SHIPPED [71305bd]

- `agents/builtin/rubric_scorer.py` — runs `auto_checks` against the
  workspace, posts `RUBRIC_SCORE` signal with weighted score
- `core/runner.py` — when `project.mode == "training"`, tag episodes
  as `training=True` and trigger rubric scorer post-run
- `agents/builtin/memory_writer.py` — already honours `Episode.training`
  for reputation; verify the wire-up
- Tests: `tests/agents/builtin/test_rubric_scorer.py` + integration in
  `tests/core/test_runner.py`

Commit: `feat(training): rubric scorer + training-mode runner branch (Phase F.1.1)`

### F.1.2 — Training API + tournament launcher ✅ SHIPPED [537617e]

- `api/routes/training.py` — three endpoints:
  - `GET /api/training/scenarios` → list available scenarios
  - `POST /api/training/run` → launch one training run
    `{persona_id, scenario_id}` returns `run_id`
  - `POST /api/training/tournament` → launch N training runs
    `{scenario_id, persona_ids[]}` returns `tournament_id` + per-run
    `run_id`s; also posts a `TOURNAMENT_RESULT` aggregate when all
    runs complete (sequential dispatch in F.1)
- `core/training/tournament.py` — sequential orchestrator that builds
  N projects (one per persona) and enqueues them
- Studio: `studio/src/api.ts` — `trainingApi.{listScenarios,run,tournament}`
- Tests: `tests/api/test_training.py`

Commit: `feat(api): training run + tournament endpoints (Phase F.1.2)`

### F.1.3 — Graduation + ranking board + seed scenarios ✅ SHIPPED 2026-04-26

- `agents/builtin/memory_writer.py` — extend `_refresh_reputation` to
  set `graduation_status` per §35 criteria (≥3 distinct scenarios,
  ≥0.7 avg rubric on last 5, ≥1 tournament)
- `api/routes/personas.py` — `GET /api/personas/rankings?role_type=&scenario=`
  → leaderboard query over reputation + training episodes
- Seed scenarios: 3-4 canonical ones beyond `hello_python_v1`:
  - `python_function_v1` (slightly bigger function with edge cases)
  - `markdown_doc_v1` (doc writer scenario)
  - `bug_fix_v1` (fix a broken function with a failing test description)
- Tests + final integration test that drives a tournament end-to-end

Commit: `feat(training): graduation + ranking + seed scenarios (Phase F.1.3)`

### What this phase does NOT do

- **No subprocess-based auto-checks** (pytest/ruff) — deferred to a
  follow-up phase where the worker can execute checks in the run's
  Docker sandbox. Without sandbox isolation, running pytest on
  agent-generated code is a risk vector.
- **No parallel tournament execution** — sequential is fine for a
  3-4 persona tournament; parallel needs queue or runner changes.
- **No studio UI for the training tab** — that's Phase G material.
  F.1.2 ships the typed API client method so G can build on it.

## F.2 — Archetype graduation (~1 day, depends on F.1)

Promote YAML archetypes to shipped concrete classes through tournament
runs. Depends on F.1 producing validated variations.

## F.4 — Persona chat ✅ SHIPPED 2026-04-26

DESIGN §37. `POST /api/personas/{id}/chat` thin shell over
`Persona.recall()` + a single LLM call. Read-only (no memory
mutation), no tools, no scheduler.

Files:
- `agents/persona_chat.py` — `build_chat_system_prompt()` +
  `chat_with_persona()` (pure logic, testable without FastAPI)
- `api/routes/personas.py` — `POST /chat` route with auth, 404 for
  unknown persona, 400 for empty message, 502 on LLM failure
- `studio/src/api.ts` — `personasApi.chat()` typed client method

Tests (`tests/api/test_personas.py`): happy path returns reply +
recall summary; unknown persona → 404; empty message → 400; missing
auth → 401/403; LLM raises → 502; system prompt includes identity +
character + memory block when patterns recall.

Project/run-context layering (DESIGN §37 step 3+4) is forward-compat
in the request body but not yet wired into the prompt — F.4 ships the
core consultation surface; richer context is a follow-up when there's
a real user need.

---

## Verification at end of F.3

```bash
python -m pytest                                  # full suite stays green
cd studio && npm run typecheck && npm run build  # frontend clean
```

Suite expected: ~782 passed (774 + ~8 new tests).
