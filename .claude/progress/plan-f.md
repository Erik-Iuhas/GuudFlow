# Active plan — Phase F — Long-term moat features

Status: planned. F.3 (real memory_writer LLM synthesis) leads, then
F.1/F.2 (Training Camp + archetype graduation), F.4 (persona chat) as
side quest. Order chosen by Erik 2026-04-26: "F.3 first because it
sits inside infrastructure that already works and pays off
immediately — every completed run starts producing real patterns."

The persona auto-load gap (`AgentRunner._build_agents` doesn't
auto-attach personas from `cfg.persona_id`) is closed by a scheduled
remote agent in 14 days. Until then, F.3 is tested via the same
`runner_factory` workaround `tests/api/test_full_journey.py` already
uses. After auto-load lands, the journey test simplifies and F.3 keeps
working unchanged.

---

## F.3 — Real memory_writer LLM synthesis (~half day)

Today's `MemoryWriter` (`agents/builtin/memory_writer.py`) ships
deterministic Steps 1, 2, 5, 6 (record episode, recompute confidence,
refresh reputation, reconciliation pass). Steps 3 (new pattern
detection from episode clusters) and 4 (style tendency updates from
evidence) are scaffolded as no-op placeholders. F.3 implements both
with LLM-driven synthesis, plus tightens Step 6 flip-detection.

### F.3.0 — Wire LLM into MemoryWriter (~30 min)

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

### F.3.1 — LLM-driven pattern distillation from episode clusters (~2 hrs)

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

### F.3.2 — Pattern→anti-pattern flip detection (~1 hr)

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

### F.3.3 — Style tendency updates from episode evidence (~1 hr)

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

### F.3.4 — Integration smoke test (~30 min)

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

## F.1 — Training Camp (~2-3 days, deferred until F.3 ships)

DESIGN §35. Toy scenario library, tournament mode, ranking board for
hiring decisions. Headline moat feature; biggest scope. Plan-f-1.md
when we get there.

## F.2 — Archetype graduation (~1 day, depends on F.1)

Promote YAML archetypes to shipped concrete classes through tournament
runs. Depends on F.1 producing validated variations.

## F.4 — Persona chat (~2 hrs, side quest)

DESIGN §37. `POST /api/personas/{id}/chat` for one-off conversations
outside any run. Independent of F.1-3. Could land any time.

---

## Verification at end of F.3

```bash
python -m pytest                                  # full suite stays green
cd studio && npm run typecheck && npm run build  # frontend clean
```

Suite expected: ~782 passed (774 + ~8 new tests).
