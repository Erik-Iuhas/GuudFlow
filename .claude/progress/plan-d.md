# Active plan — Phase D — Real meta-agent proposal ✅ SHIPPED 2026-04-26

Status: ✅ done. All 6 chunks landed (D.0 → D.5). Final state:
- 41 new tests passing (test_meta + test_archetype_doc + integration)
- Studio typecheck + prod build clean
- `/api/meta/propose` reads inputs + roster + 16-archetype catalog and
  returns LLM-driven proposals; deterministic skeleton fallback on any
  failure mode

Builds on the foundation already shipped:

- **D-prime** — `inputs/README.md` is auto-generated, uploads/source_repo
  /from_run_* are seeded into the run's workspace at start
- **C.0** — `GET /api/agents/catalog` exposes every archetype with
  metadata (`name`, `default_tools`, `default_rbac_scopes`,
  `default_model_tier`, `description`, `when_to_use`)
- **C.0.5** — pre-run cost preview gate already exists; the meta-agent's
  LLM call itself doesn't need a separate gate (it runs ONCE before
  the team is approved, and the user's "Approve" click on the proposal
  is its own confirmation). This phase doesn't add new gates.

Today's `agents/builtin/meta.py` has the `MetaAgent(BaseAgent)` shell +
`TeamProposal` dataclass + `META_SYSTEM_PROMPT` but no `run()`. The
route at `api/routes/meta.py` returns a hardcoded developer + reviewer
skeleton regardless of input.

## Goal

`/api/meta/propose` reads project inputs + the agent catalog + the
persona roster, calls an LLM, and returns a tailored proposal.

Two flavours per DESIGN §7 + §33:

- **Greenfield / fresh-with-inputs** — propose a team from scratch
  informed by uploaded specs + source repos.
- **Continuation** — when a `from_run_{id}/` is attached or
  `pending_continuation` is on the project, read the prior run's
  team + outputs + new ask, produce a *delta proposal*
  (keep / drop / add / swap-tier / replace-persona / re-hire) so the
  user approves what's *changing*.

---

## Chunk 0 — Worker archetype reference + naming rules ✅ SHIPPED [b3fee5a]

Erik's feedback: the meta-agent picking from C.0's 4 shipped archetypes
(code_reviewer, tech_lead, requirements_writer, architect) is too thin.
We need a richer pool of archetype *descriptions* the meta-agent can
draw on, even when the concrete classes don't yet exist. (Originally
queued for Phase F; moved here because Phase D actually depends on it.)

### `docs/worker-archetypes.md` (new)

Hand-written reference, grounded in patterns from production multi-
agent frameworks (AutoGen, LangGraph, CrewAI) plus DESIGN.md. ~20
archetypes covering the breadth of common workflows. Per archetype:

- **Name** (the registry key the meta-agent would use)
- **Abstract base** (Reviewer / EscalationHandler / Specialist)
- **Responsibility** (one paragraph; this becomes the
  `system_prompt` when the meta-agent proposes the archetype as a
  prompt-defined agent)
- **Default model tier** (heavy / solid / fast)
- **Default tools** (declared_local_tools list)
- **Default RBAC scopes**
- **When to use** (the meta-agent's selection criterion)
- **Shipped?** (✅ if a concrete class exists in `agents/builtin/`,
  📝 if only described in this markdown — meta-agent proposes
  prompt-defined when 📝)

Categories the markdown covers (final list emerges from the research
pass below):
- **Reviewers** — code, security, accessibility, performance, API design
- **Escalation handlers** — tech_lead (shipped), incident_responder,
  deadline_watcher
- **Specialists — engineering** — frontend, backend, full-stack,
  mobile, devops/SRE, database, data engineer, ML engineer, security
- **Specialists — process** — requirements_writer (shipped), tech
  writer, QA / test writer, migration coordinator, release manager
- **Synthesis** — memory_writer (shipped, intentionally NOT in the
  catalog because it's a runner side-task)

### Research

Small WebSearch pass to ground the archetype list — agent patterns
common in AutoGen / LangGraph / CrewAI / Microsoft AutoGen Studio /
real production multi-agent setups. Goal is breadth (covering the
common categories above), not depth (one archetype per role is
enough; the markdown can grow over time).

### Meta-agent inputs

The meta-agent at proposal time reads:
1. The runtime catalog (`agents/catalog.list_archetypes()`) — for
   shipped archetypes, gets the concrete class metadata
2. `docs/worker-archetypes.md` — for the broader pool. The meta-agent
   prompt includes both (deduped by name; runtime catalog wins on
   conflict because shipped > described).

A small parser at `core/archetype_doc.py` reads the markdown into
structured records. Same `ArchetypeMeta` shape as the catalog so the
LLM sees a uniform list.

### Naming rules (Phase D contract)

Three layers:
- **Prompt-level** — meta system prompt: "every role name must be
  unique within this team. Use descriptive names (`auth_dev`,
  `api_dev`) over numeric suffixes when possible."
- **Parse-time** — `_dedupe_role_names(roles)` appends `_2`, `_3`
  fallback if the LLM produces collisions
- **Scheduler-level** — existing `core/scheduler.py:95` raises on
  duplicates. Belt-and-suspenders.

For **persona ids** (when the meta-agent recommends "create new
persona"): check `PersonaStore.list()` for existing ids; generate a
new id with a structured scheme like `{role}-{adjective}-v1`
(e.g., `aria-frontend-v1`). Persona ids are globally unique (data
layout is `data/personas/{persona_id}/`).

### Files

- `docs/worker-archetypes.md` (new)
- `core/archetype_doc.py` (new — markdown parser, returns the same
  `ArchetypeMeta` shape as `agents/catalog.py`)
- `agents/catalog.py` (extend) — `list_all_archetypes()` returns the
  union of runtime catalog + markdown, deduped
- Tests:
  - `tests/core/test_archetype_doc.py` — parser parses the markdown
    into structured records; lookup by name; falls back gracefully
    on missing fields
  - `tests/agents/test_catalog.py` — `list_all_archetypes()` includes
    both shipped and described archetypes; runtime wins on conflict

Commit: `docs+feat: worker archetype reference + naming rules (Phase D.0)`

---

## Chunk 1 — MetaAgent.run() core ✅ SHIPPED [cbbfe68]

### What lands

`MetaAgent.run(project_dir, goal, prior_run_id=None) -> TeamProposal`.

Steps:

1. **Read project inputs** (D-prime layout):
   - `data/projects/{id}/inputs/README.md` if present
   - `inputs/source_repo.meta.json` for source_repo origin
   - First N bytes of any uploads (PDF/MD/DOCX → text via existing
     `doc:*` tools, capped at the meta-agent's token budget)
   - For continuation: the prior run's `meta.json` +
     `from_run_{id}/feedback.md` if present
2. **Read the agent catalog** via `agents.catalog.list_archetypes()`.
   Pass it to the LLM as a "you may pick from these archetypes" list.
3. **Read the persona roster** via `PersonaStore.list()`. Pass with
   each persona's specialty + reputation summary so the LLM can
   recommend `hire <existing>` vs `create new`.
4. **LLM call** with `META_SYSTEM_PROMPT` + structured tool-use output.
   The LLM emits a structured `propose_team` tool call rather than
   parsing free-form JSON — leverages the existing tool-use infra.
5. **Parse** the tool call into `TeamProposal` (rationale + roles
   + rbac + persona hires + delta annotations if continuation).
6. **Defensive fallbacks** — if the LLM call fails or the structured
   output is malformed, fall back to today's deterministic skeleton
   so `/api/meta/propose` never breaks.

### Files

- `agents/builtin/meta.py` — flesh out `MetaAgent.run()`
- New: `core/meta_proposal.py` (or inline) — input-reading helpers,
  prompt rendering, structured-output parsing
- Tests: `tests/agents/builtin/test_meta.py` (new)

### Tests

- Mocked LLM returning a valid structured proposal → parses correctly
- Mocked LLM returning malformed output → fallback skeleton
- Empty inputs (greenfield, no uploads, no source_repo) → still works
- With uploads → input excerpts appear in the rendered prompt
- Catalog passed to LLM includes archetype names + descriptions
- Roster passed to LLM (when personas exist) includes specialty +
  reputation; meta-agent recommendations reference real persona ids

Commit: `feat(agents): real meta-agent proposal (Phase D.1)`

---

## Chunk 2 — API wire-up ✅ SHIPPED [9c92388]

### What lands

`/api/meta/propose` accepts an optional `project_id`:
- Given → instantiate `MetaAgent`, point at the project's inputs,
  return the LLM-driven proposal
- Absent → return today's deterministic skeleton (back-compat for
  the studio's "Propose" view when no project context exists yet)

### Files

- `api/routes/meta.py` — wire MetaAgent
- `api/main.py` — pass the LLM client to the route (or via app.state)
- Tests: extend `tests/api/test_meta.py`

### Tests

- POST with `project_id` → returns LLM-driven proposal (mocked LLM)
- POST without `project_id` → still returns the skeleton (back-compat)
- POST with `project_id` for a missing project → 404
- POST when LLM fails → returns skeleton + a warning field
  (don't 500; the user can still proceed)

Commit: `feat(api): /api/meta/propose uses MetaAgent when project_id given (Phase D.2)`

---

## Chunk 3 — Continuation-aware delta proposals ✅ SHIPPED [f33e713]

DESIGN §33's "Meta-agent re-evaluation on continuation". When a
project has either `pending_continuation` on its config (set by
D-prime Chunk 8's "Continue from this run" button) OR a
`from_run_*/` directory in inputs, the meta-agent produces a *delta
proposal*, not a greenfield team.

### Behaviour

- Detect continuation context: prefer `pending_continuation.run_id`
  (explicit user intent), fall back to scanning `inputs/from_run_*/`
- Extend `MetaAgent.run()` to load:
  - Prior run's `meta.json` (status, completed_agents, iteration)
  - Prior team config (from the project's stored config — the
    project_id is in meta.json's `project_id_of_origin`)
  - Prior personas' reputation summaries (NOT full memory — that's
    per-persona at Layer 4 by the runner)
- Switch to `META_CONTINUATION_PROMPT`: emits `keep / drop / add /
  swap-tier / replace-persona / re-hire` per role with rationale
- Returned `TeamProposal` grows a `deltas` field:
  ```python
  @dataclass
  class RoleDelta:
      role: str
      action: str  # "keep" | "drop" | "add" | "swap_tier" | "replace_persona" | "re-hire"
      rationale: str
      from_state: dict | None = None  # prior config snapshot
      to_state: dict | None = None    # proposed config snapshot
  ```
- Keep `roles` + `rbac` populated with the EFFECTIVE proposed team
  (so callers that don't care about deltas still get a usable team)

### Files

- `agents/builtin/meta.py` — META_CONTINUATION_PROMPT + run() branch
- `agents/builtin/meta.py` — `RoleDelta` dataclass + `TeamProposal.deltas`
- Tests: `tests/agents/builtin/test_meta.py` (extend)

### Tests

- Continuation context detected from `pending_continuation` field
- Continuation context detected from `from_run_*/` dir alone
- Delta proposal: `keep` for unchanged roles, `add` for new ones, etc.
- Round-trip: serialise `TeamProposal` to dict for the API response

Commit: `feat(agents): continuation-aware delta proposals (Phase D.3)`

---

## Chunk 4 — Studio rationale rendering + delta UI ✅ SHIPPED [171dbe6]

### What lands

`Propose.tsx` surfaces the rationale prominently, highlights any input
file paths the LLM mentions (`src/auth.py`, `inputs/uploads/spec.pdf`)
as clickable links, and renders deltas as a two-column diff when
present.

### Files

- `studio/src/views/Propose.tsx` — rationale block, highlighted file
  links, delta diff renderer
- `studio/src/types.ts` — `RoleDelta` type to mirror backend
- `studio/src/api.ts` — extend `metaApi.propose()` return type

### Tests

- Manual smoke per Stage 8 convention (no automated frontend tests)
- Verify typecheck + production build pass

Commit: `feat(studio): meta-agent rationale + delta rendering (Phase D.4)`

---

## Chunk 5 — Runner / continuation integration ✅ SHIPPED 2026-04-26

### What landed

`decisions.md` records the option-B choice: when the user approves a
delta proposal, `Propose.tsx` PUTs the entire updated team config to
`/api/projects/{id}` (overwriting `agents`). The runner just uses the
updated config — no continuation-aware path needed beyond what
D-prime already shipped (inputs seeding from `from_run_*/`).

The continuation flow is already covered by:
- `test_meta.py::test_propose_continuation_*` — unit tests for delta
  computation across keep/drop/add/swap_tier/replace_persona
- `test_meta.py::test_compute_deltas_*` — `compute_deltas()` as a pure
  function, edge cases
- `test_api/test_meta.py` — `/api/meta/propose` with `prior_run_id`
  routes the continuation context through end-to-end

The journey test (`tests/api/test_full_journey.py`) is intentionally
NOT extended with a continuation leg this phase: it would require
running two full LLM-mocked journeys back-to-back and revisiting the
runner_factory wrapper that closes the persona auto-load gap. That
gap has its own scheduled remote agent in 14 days — once it lands,
extending the journey test with a continuation leg becomes a clean
follow-up rather than two layered workarounds. Captured in
`BOARD.md` "Pending follow-ups".

Commit: `docs(meta): record option-B decision for continuation deltas (Phase D.5)`

---

## Risks and notes

- **LLM-call cost during tests** — every test that exercises the
  meta-agent must use `MockLLMClient`. Real Anthropic calls in CI
  would burn money fast. The MockLLMClient with scripted tool-use
  responses is the pattern (test_runner_e2e + test_job_runner show
  the shape).
- **Structured output reliability** — relying on tool-use rather than
  JSON parsing is the right call (tool-use schema is enforced by the
  API; JSON-in-text parsing is not). The fallback skeleton on parse
  failure is the safety net.
- **Persona auto-load gap** — the scheduled agent (in 14 days) is
  going to fix `AgentRunner._build_agents` to honor `cfg.persona_id`.
  Phase D's MetaAgent generates AgentConfigs with `persona_id` set;
  the auto-load fix is what makes those personas actually attach at
  runtime. Phase D is correct to set the field; coverage of the full
  persona memory lifecycle e2e waits for the auto-load fix.
- **Catalog drift** — the meta-agent reads the catalog at proposal
  time. If a project has been customized with non-catalog roles,
  those roles still get proposed — the catalog is a hint, not a
  closed taxonomy.

## Verification at end of phase

```bash
python -m pytest                                  # full suite stays green
cd studio && npm run typecheck && npm run build  # frontend clean
```

Suite expected: ~750-760 passed (726 + ~25 new tests).
