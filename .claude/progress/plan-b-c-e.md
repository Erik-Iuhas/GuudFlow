# Active plan — Phase B → Phase C → Phase E

User-chosen sequence: **Phase B → Phase C → Phase E**, building on the
shipped foundation + D-prime (rich input ingestion, full-journey e2e test).

Total estimate: ~30–40 hours of focused work across multiple sessions.
Each chunk below is independently shippable and leaves the suite green.

Order rationale:
- **B** is small (~1-2 hrs) and unblocks safe production runs of agent-
  generated code. Lands first because everything downstream assumes it.
- **C** turns a live run from "watch logs scroll" into "interact with a
  running team" — the headline UX win. Independent of E.
- **E** packages the result for real deployment (Redis + k8s + Helm).
  Depends on B (because container-routed `code:exec` is what gets
  pod-routed under k8s) and benefits from C (the comment + quip
  surfaces should be wired before users hit them in prod).

---

## Phase B — Close the dev→prod gap (~1-2 hrs, 1 chunk)

Goal: in production mode, `code:exec` routes through the sandbox
container instead of the host. Today the production path exists in
[worker/docker_manager.py](worker/docker_manager.py) (`exec_in()`) and
[sandbox/Dockerfile](sandbox/Dockerfile) is built and verified — the
runner just doesn't wire them together.

### Chunk 1 — Container-routed `code:exec` runner (~1-2 hrs)

What's there today:
- [agents/tools/code.py](agents/tools/code.py) `CodeExec` accepts a
  `runner: CodeRunner` callable; `make_dev_host_runner()` builds the
  hardened host runner.
- [core/runner.py:225](core/runner.py:225) `_instantiate_local_tools`
  always builds the host runner.
- [worker/docker_manager.py](worker/docker_manager.py) `DockerManager.exec_in()`
  works end-to-end in production but is never called from the runner.
- [worker/job_runner.py](worker/job_runner.py) constructs a
  `DockerManager` per job but only uses it for `start`/`stop`, not for
  routing `code:exec`.

Tasks:
- [ ] Add `make_container_runner(docker_manager, run_id)` to
      `agents/tools/code.py` — returns a `CodeRunner` that calls
      `await docker_manager.exec_in(run_id, language, code, timeout_s)`
      and shapes the result to match `make_dev_host_runner`'s output
      (`{ok, language, exit_code, stdout, stderr, duration_s}`).
- [ ] Add `docker_manager: DockerManager | None = None` to
      `AgentRunner.__init__` ([core/runner.py:119](core/runner.py:119)).
      Store on self.
- [ ] In `_instantiate_local_tools`, branch on
      `AGENTFLOW_MODE == "production"`:
      - production + docker_manager present → `make_container_runner(...)`,
        pass `is_dev_host_runner=False` to `CodeExec`.
      - otherwise → existing `make_dev_host_runner(...)` (with mode
        enforcement still firing on production+host_runner combo).
- [ ] [worker/job_runner.py:default_runner_factory](worker/job_runner.py)
      threads `self.docker` (the existing `DockerManager`) into
      `AgentRunner(...)`. JobRunner already constructs one — just pass it.
- [ ] [tests/agents/test_code_exec_hardening.py](tests/agents/test_code_exec_hardening.py)
      gains: production mode + container runner sends `code:exec`
      through `docker_manager.exec_in` (mocked); production mode without
      docker_manager refuses to run (existing behaviour, just verify).
- [ ] [tests/worker/test_job_runner.py](tests/worker/test_job_runner.py)
      gains a small e2e: a project that calls `code:exec`, monkey-patch
      `DockerManager.exec_in` to a fake, verify it was hit.
- [ ] Update README "Mode" / "Production" section to note that
      `AGENTFLOW_MODE=production` now actually routes `code:exec` through
      the sandbox.

**Phase B exit:** `AGENTFLOW_MODE=production` actually sandboxes
`code:exec`. The dev→prod gap noted in followups.md and ROADMAP §B
closes.

Commit: `feat(core): route code:exec through DockerManager in production (Phase B)`

---

## Phase C — Make a live run feel live (~1.5 days, 5 chunks)

Goal: the Run view stops being a passive log. Users post comments to a
running team, see real quips streamed live, and the runner escalates to
the tech lead automatically when work keeps getting rejected. The team
itself gains a layered class hierarchy so adding new agent archetypes
(escalation handlers, reviewers, specialists) is a one-prompt change
rather than copy-pasting dispatch wiring.

### Chunk 0 — Agent archetype foundation + catalog (~4-5 hrs)

User feedback (post-C.2): general-purpose agents that share similar
controls should be abstract classes; specific workers (frontend dev,
requirements writer) should share a base with different permissions;
the meta-agent needs a catalog of available archetypes when proposing
a team.

This chunk lands **before** C.3 + C.4 so they can build on the shared
bases rather than each shipping a one-off class.

#### Abstract bases — `agents/abstract.py` (new)

- `Reviewer(BaseAgent)` — reads diffs, evaluates work, decides
  APPROVED / REJECTED. Concrete subclasses provide `system_prompt`;
  the base provides the standard review flow. CodeReviewer + Architect
  both extend.
- `EscalationHandler(BaseAgent)` — wakes on a workspace event (runner
  dispatches it; the handler doesn't go through the scheduler).
  Concrete subclasses provide `system_prompt`. TechLead extends.
  ITAgent stays as-is for now (its batch-processing `run()` is a
  different shape — could extend later if useful).
- `Specialist(BaseAgent)` — does the actual work: writes code, fills
  artifacts, commits. Concrete subclasses provide `system_prompt` +
  class-level defaults for `declared_local_tools` /
  `default_model_tier` / `default_rbac_scopes`. Frontend, backend,
  requirements writer all extend with different defaults but inherit
  the same loop.

#### Refactor existing builtins

- `CodeReviewer` → extends `Reviewer`
- `TechLead` → extends `EscalationHandler`
- `ITAgent` — unchanged in this chunk
- `MemoryWriter`, `VisualChecker`, `TestRunner` — unchanged (synthesis /
  side-task agents; their shape doesn't fit any of the three bases
  cleanly)

#### Ship one concrete `Specialist` to prove the pattern

- `RequirementsWriter(Specialist)` — system prompt for "turn user goal
  + uploads into a structured requirements artifact"; default tools
  `[fs:read, fs:list, fs:write, doc:pdf, doc:docx, doc:markdown]`;
  default RBAC scopes `[fs:write, doc:*]`. Other specialists
  (frontend, backend, tester) added on demand once the pattern is
  validated.

#### Catalog — `agents/catalog.py` (new)

```python
@dataclass
class ArchetypeMeta:
    name: str                          # role_type registered in agents/registry.py
    abstract_base: str                 # "Reviewer" | "EscalationHandler" | "Specialist"
    description: str                   # what the agent does
    when_to_use: str                   # meta-agent selection criterion
    default_model_tier: str            # "heavy" | "solid" | "fast"
    default_tools: list[str]
    default_rbac_scopes: list[str]

AGENT_ARCHETYPES: list[ArchetypeMeta] = [
    ArchetypeMeta(name="code_reviewer", abstract_base="Reviewer", ...),
    ArchetypeMeta(name="tech_lead", abstract_base="EscalationHandler", ...),
    ArchetypeMeta(name="architect", abstract_base="Reviewer", ...),
    ArchetypeMeta(name="requirements_writer", abstract_base="Specialist", ...),
    # ITAgent, MemoryWriter, etc. can be added with abstract_base="None"
    # once we decide whether they belong in the catalog.
]
```

#### API endpoint

`GET /api/agents/catalog` — returns the archetype list. Read-only,
no auth required (it's purely informational). Used by:
- Meta-agent (Phase D) when proposing a team — picks from this
- Studio (Phase G or later) — shows what's available when a user
  manually customises

#### Tests

- `tests/agents/test_abstract.py` (new):
  - `Reviewer` base: a minimal subclass produces APPROVED / REJECTED
    via the inherited flow.
  - `EscalationHandler` base: a minimal subclass can be registered and
    woken via the runner's dispatch path.
  - `Specialist` base: a minimal subclass inherits class-level
    defaults; instantiation respects `default_tools`.
- `tests/agents/test_catalog.py` (new): catalog has expected entries;
  lookup-by-name works; every entry's `name` is also in the registry.
- `tests/api/test_agents_catalog.py` (new): GET endpoint returns the
  catalog as JSON.
- All existing tests for `CodeReviewer`, `TechLead` keep passing —
  refactor must be behaviour-preserving.

Commit: `feat(agents): abstract bases (Reviewer/EscalationHandler/Specialist) + catalog (Phase C.0)`

### Chunk 1 — Live `USER_COMMENT` POST endpoint — **SHIPPED [`e4ae1e3`](https://github.com/Erik-Iuhas/AgentFlow/commit/e4ae1e3)**
### Chunk 2 — Real quip stream over WebSocket — **SHIPPED [`19de77d`](https://github.com/Erik-Iuhas/AgentFlow/commit/19de77d)**

(Original positions in the chunk numbering kept for the commit
references; C.0 lands as a prerequisite for C.3 + C.4.)

### Chunk 0.5 — Approval gates + cost control (~4-5 hrs)

User feedback (post-C.0): today the only approval gate is the meta-
agent proposal at run start. Once approved, the team takes off and can
iterate, dispatch HEAVY-tier handlers (tech_lead, architect), and burn
unbounded tokens with no further confirmation. Lands **before C.3**
because C.3 is the first big-spend mid-run dispatch we'd want to gate.

#### Mechanism

- New `SignalType` values: `APPROVAL_NEEDED`, `APPROVAL_GRANTED`,
  `APPROVAL_DENIED`. Parallel to `HUMAN_NEEDED` but for system-
  initiated cost gates, not in-character agent questions.
- `Workspace.pending_approvals: list[ApprovalRequest]` — dataclass with
  `id`, `kind` (e.g. `"heavy_tier_dispatch"`), `summary`,
  `estimated_cost_usd`, `context`, `triggered_by`.
- Runner emits `APPROVAL_NEEDED` before any gated event, awaits a
  matching `APPROVAL_GRANTED` / `APPROVAL_DENIED` on the user_board,
  then proceeds (grant) or aborts the gated operation (deny).
- `POST /api/runs/{run_id}/approval/{approval_id}` — body
  `{decision: "grant"|"deny", note?: str}`. Auth required.
- Studio: inline approval card on the Run view (like HUMAN_NEEDED
  today) + a "Approval gates" tab in project Settings to toggle which
  gates apply.

#### Baseline defaults (opinionated, configurable per project)

| Gate | Default | Why |
|---|---|---|
| `pre_run_cost_preview` | ON | Show estimated cost from `agent count × model tier × max_iterations × max_tool_iterations` BEFORE `POST /invoke` actually enqueues. User clicks "Run" with cost visible. |
| `heavy_tier_mid_run_dispatch` | ON | Before tech_lead (C.3) or architect (C.4) — any HEAVY-tier agent triggered after the initial proposal — actually fires. |
| `iteration_breach_at` | 2 (int) | After a rejection, before the scheduler respawns the rejected agent for iteration ≥ N. Set to `null` to disable. |
| `per_iteration` | OFF | Heavy-handed — opt-in for high-stakes runs. |
| `token_budget_usd` | null (off) | Pause when cumulative estimated cost crosses this threshold. |

#### Project config schema

```json
{
  "approval_gates": {
    "pre_run_cost_preview": true,
    "heavy_tier_mid_run_dispatch": true,
    "iteration_breach_at": 2,
    "per_iteration": false,
    "token_budget_usd": null
  }
}
```

`serialise_project` reads these with sane defaults. Existing projects
without the field get the baseline.

#### Files

- `core/signals.py` — three new signal types
- `core/workspace.py` — `pending_approvals` field + helpers
- `core/runner.py` — new `_request_approval(kind, summary, ...)` that
  posts `APPROVAL_NEEDED` and awaits resolution. Used internally
  before HEAVY-tier dispatches and (later) iteration breaches.
- `core/runner.py:ProjectConfig.approval_gates: dict` — config field
- `worker/job_runner.py:serialise_project` — round-trips
  `approval_gates`
- `api/routes/runs.py` — `POST /api/runs/{id}/approval/{approval_id}`
- `api/routes/invoke.py` — when `pre_run_cost_preview` is on, returns
  `{accepted: false, requires_approval: true, estimated_cost_usd: ...}`
  on first POST; second POST with `confirm=true` enqueues
- `studio/src/components/ApprovalCard.tsx` (new) — inline gate prompt
- `studio/src/views/Studio.tsx` — Settings tab with toggles
- `studio/src/api.ts` — `approvalsApi.grant/deny`

#### Cost estimation

Reuse the existing `studio/src/components/CostStrip.tsx` logic on the
backend: `estimated_cost_usd = sum_over_agents(tier_cost_per_call ×
max_tool_iterations × max_iterations)`. Cheap, deterministic; same
shape the studio already shows pre-run.

#### Tests

- Approval signal round-trip: post `APPROVAL_NEEDED` → POST `/approval`
  → workspace updates correctly
- Disabled gate (`heavy_tier_mid_run_dispatch: false`) lets dispatch
  through with no pause
- Pre-run cost preview: first invoke returns 402-style "needs
  confirmation" with estimate; second invoke with `confirm=true` enqueues
- Project config round-trips `approval_gates` through
  `serialise_project`
- Iteration-breach gate fires at the configured threshold and not before
- Studio settings tab persists toggles via `PUT /api/projects/{id}`
  (component-level smoke is enough; backend test owns the contract)

Commit: `feat(api): approval gates + cost preview baseline (Phase C.0.5)`

#### How C.3 + C.4 use this

- **C.3 (tech_lead dispatch)**: in `_dispatch_escalation`, before
  posting `ESCALATION` and creating the side task, call
  `await self._request_approval(kind="heavy_tier_dispatch", ...)`.
  Granted → proceed; denied → post a system signal explaining and
  skip dispatch.
- **C.4 (architect review)**: same hook before the auto-injected
  architect's `run()` is dispatched.

### Chunk 1 — Live `USER_COMMENT` POST endpoint (~1 hr)

What's there today:
- [studio/src/components/CommentBox.tsx](studio/src/components/CommentBox.tsx)
  is wired in `Run.tsx`; check what (if anything) it actually POSTs.
- The websocket bridge has the workspace registry but no inbound
  comment surface.
- DESIGN §6 specifies `USER_COMMENT` and `USER_COMMENT_PRIORITY` signal
  types; `agents.check_user_board()` already absorbs them at step
  boundaries.

Tasks:
- [ ] `POST /api/runs/{run_id}/comment` in
      [api/routes/runs.py](api/routes/runs.py).
      Body: `{ "text": str, "priority": "normal" | "high" }`.
      `Depends(require_bearer)`.
- [ ] Look up `app.state.run_registry.get(run_id)`.
  - Active workspace → post `USER_COMMENT` (or `USER_COMMENT_PRIORITY`
    based on `priority`) signal with `from_user=True`. Return 200.
  - Not active → 410 Gone with `"run is no longer active"`.
- [ ] Test: post to active run (using the registry pattern from
      [tests/api/test_websocket.py](tests/api/test_websocket.py));
      post to inactive run → 410; missing auth → 401.
- [ ] Audit `studio/src/components/CommentBox.tsx`: if it doesn't
      already POST to this route, wire it. Optimistic update per Erik's
      UX rule (prepend locally, fire POST, revert on error).

Commit: `feat(api): POST /api/runs/{id}/comment posts USER_COMMENT live (Phase C.1)`

### Chunk 2 — Real quip stream over WebSocket (~2 hrs)

What's there today:
- [core/quip_engine.py](core/quip_engine.py) writes quips to disk
  (`quip_log.json`) on each event.
- The runner triggers `quip_engine.on_run_complete(...)` and
  `on_iteration_complete(...)` already.
- [studio/src/components/QuipFeed.tsx](studio/src/components/QuipFeed.tsx)
  synthesises pseudo-quips from board events — confirm during Chunk 2.

Decision: parallel `/ws/runs/{id}/quips` endpoint over extending the
existing one. Reasoning: quips and signals have different cadences and
schemas; mixing them in one stream pushes the "discriminator type"
problem onto every consumer. A separate channel keeps both sides simple.

Tasks:
- [ ] Add `subscribe(callback)` / `unsubscribe()` to `QuipEngine`,
      mirroring `Workspace.subscribe`. Persistent quip log stays
      authoritative; subscribers fire after persist.
- [ ] `QuipRegistry` in [api/websocket.py](api/websocket.py) (or sibling
      module) maps `run_id → QuipEngine`. JobRunner registers/unregisters
      alongside the workspace, similar to the existing pattern at
      [worker/job_runner.py:222](worker/job_runner.py:222).
- [ ] `/ws/runs/{run_id}/quips` endpoint: snapshot from disk on connect,
      then live stream via the subscriber. Same connect/close lifecycle
      as the existing board WS.
- [ ] [studio/src/api.ts](studio/src/api.ts) gains
      `connectQuipStream(runId, onMessage)`. `QuipFeed.tsx` swaps from
      synthesised to real subscription. Falls back to the on-disk
      `quip_log.json` (already persisted) when WS disconnects.
- [ ] Tests:
      - `test_quip_engine.py` gains a subscribe/notify case.
      - `test_websocket.py` gains a quip-stream variant of its existing
        live test.

Commit: `feat(api): /ws/runs/{id}/quips streams quips live (Phase C.2)`

### Chunk 3 — Tech-lead escalation auto-detection (~3-4 hrs)

What's there today:
- [agents/builtin/tech_lead.py](agents/builtin/tech_lead.py) exists with
  the system prompt and class but is never auto-activated.
- DESIGN §23: tech_lead is event-driven, same pattern as `it_agent`
  (which wakes on `PACKAGE_REQUEST`); HEAVY tier; activates rarely.
- CLAUDE.md "Escalation thresholds": stored in project config, defaults
  2/3/4 rejections.
- `SignalType.REJECTED` already exists in [core/signals.py:34](core/signals.py:34).
- `it_agent`'s wake-on-event pattern is the existing reference — read
  [agents/builtin/it_agent.py](agents/builtin/it_agent.py) and
  [core/scheduler.py](core/scheduler.py)'s spawn logic before designing
  this chunk to avoid re-inventing.

Tasks:
- [ ] Add `ESCALATION` to `SignalType` if not already there.
- [ ] In [core/runner.py](core/runner.py), subscribe to workspace board
      and maintain `_rejection_counts: dict[role, int]`. On every
      `REJECTED` targeting role X, increment. At threshold (read from
      `project.escalation_thresholds.tech_lead`, default 2), post an
      `ESCALATION` signal with `target_role: "tech_lead"` and the
      relevant context (rejected role, last rejection signal id).
- [ ] Tech-lead spec: registered with the scheduler as event-driven
      (no `depends_on`, wakes on `ESCALATION`). Mirror IT agent's
      registration shape.
- [ ] `tech_lead.run()` reads the escalation context, optionally pulls
      web research via `web:fetch`/`web:search`, posts a `STARTED`
      signal, produces an analysis comment back to the board (existing
      tech_lead system prompt is the contract).
- [ ] Threshold defaults live in `ProjectConfig`; per-role overrides
      via project config. Default `{tech_lead: 2}`. Don't hardcode.
- [ ] Tests:
      - Simulate 2 `REJECTED` signals on the same role, verify
        `ESCALATION` posted exactly once.
      - Verify tech_lead is spawned and runs.
      - Threshold of 0 disables (no auto-escalation).
      - Two different roles each crossing the threshold → two
        escalations.

Commit: `feat(core): auto-spawn tech_lead on rejection threshold (Phase C.3)`

### Chunk 4 — Final-stage architect review (~3-4 hrs)

The "office loops back with the architect" pattern — distinct from C.3's
tech_lead, which fires on **failure** (rejections piling up). C.4 is the
**success-path** check: after every regular reviewer approves and all
agents are otherwise complete, a HEAVY-tier architect agent does a
final pass on the merged result before the run is declared done. They
can approve (run completes) or reject specific agents (which re-enters
the existing iteration loop, bounded by `max_iterations`).

Why this is its own pattern:
- Stacked reviewers (`dev → r1 → r2`) only review the upstream agent.
  An architect needs to look at the whole merged result against the
  original goal.
- `tech_lead` (C.3) wakes on rejections — by the time it fires,
  something's already gone wrong. The architect runs on the happy path
  to catch "looks fine but misses the point" failures that no per-step
  reviewer would catch.
- Production runs that need this layer (e.g. anything the user cares
  about shipping) shouldn't have to manually wire stacked reviewers
  with hand-crafted depends_on covering every role.

What's there today:
- [agents/builtin/](agents/builtin/) has `tech_lead.py` and
  `code_reviewer.py` but no architect. Their patterns are the
  reference for this chunk.
- `AgentConfig` has `is_reviewer: bool` already; we'll add
  `is_final_reviewer: bool` as a peer.

Tasks:
- [ ] Add `is_final_reviewer: bool = False` to `AgentConfig`
      ([core/runner.py:84](core/runner.py:84)) and serialise it through
      `worker/job_runner.py:serialise_project`.
- [ ] In `AgentRunner._build_agents`, after the loop, if any agent has
      `is_final_reviewer=True`, auto-fill its `depends_on` to be every
      other (non-final-reviewer) role's name. Don't overwrite an
      explicit `depends_on` if the user set one — only fill when empty.
      Multiple final reviewers each depend on every non-final agent.
- [ ] New `agents/builtin/architect.py` registered as
      `agent_class_role_type: "architect"`:
      - System prompt frames the role as senior architect doing the
        final design review against the original goal. Reads
        `git:log`, `git:diff main..*`, `fs:read` of key deliverables.
      - Posts either `APPROVED` (run completes via existing path) or
        `REJECTED` targeting the specific role(s) with concrete reasons.
      - HEAVY tier by default in the registry hint.
- [ ] When the architect posts a `REJECTED`, normal iteration handling
      kicks in (existing scheduler logic) — the targeted agent's
      iteration count increments, agent re-runs, reviewer chain runs
      again, architect runs again. Bounded by `max_iterations`. Verify
      that the existing iteration mechanism handles "rejection from
      something that wasn't the agent's direct reviewer" — if not, fix
      that gap as part of this chunk.
- [ ] [agents/registry.py](agents/registry.py) registers `architect`
      → `Architect` class.
- [ ] Meta-agent skeleton at [api/routes/meta.py](api/routes/meta.py)
      gains an optional `architect` slot (off by default; enabled when
      the goal mentions ship/production/release or the user opts in).
      Defer the LLM-driven version to Phase D.
- [ ] Tests:
      - 3-agent run: dev + reviewer + architect. Reviewer approves;
        architect approves → run complete with architect's APPROVED on
        the board.
      - Architect rejects → developer re-runs → reviewer re-approves
        → architect re-approves → run complete. iteration count
        increments correctly.
      - Architect bounce-back hits `max_iterations` → run ends with
        STUCK / MAX_ITERATIONS, architect's last rejection visible.
      - Two final reviewers: both auto-depend on all non-final agents,
        both must approve before run completes.

Commit: `feat(core): final-stage architect review pattern (Phase C.4)`

**Phase C exit:** A live run is interactive AND has structured review
layers. User comments land in the running team's board. Quips stream
live. The tech lead shows up automatically when work keeps failing.
The architect does a final design review before the run is declared
done. The team has both a *rescue* path (tech_lead) and a *gate* path
(architect) — distinct functions, distinct trigger conditions.

---

## Phase E — Production-grade deployment (~3 days, 5 chunks)

Goal: ship a Helm chart and the production primitives so AgentFlow can
run on a real k8s cluster — Redis-backed shared queue, separate API and
worker deployments, k8s pods instead of docker containers for the
sandbox.

### Chunk 1 — Redis-backed `JobQueue` (~3-4 hrs)

What's there today:
- [worker/queue.py](worker/queue.py) defines a clean `JobQueue` Protocol;
  `InMemoryJobQueue` is the only impl.
- [api/main.py:53](api/main.py:53) builds an `InMemoryJobQueue`
  unconditionally.

Tasks:
- [ ] Add `redis>=5.0.0` to `requirements-optional.txt` (lazy-loaded
      so default install stays light, same posture as embeddings).
- [ ] `worker/redis_queue.py:RedisJobQueue` implements the Protocol:
      - `LPUSH`/`BRPOP` on `agentflow:queue` for the FIFO queue.
      - Hash `agentflow:jobs:{job_id}` stores serialised `Job`.
      - `update_status` updates the hash and `PUBLISH`es to
        `agentflow:status:{job_id}`.
      - JSON serialisation for `Job` (dataclass → dict).
- [ ] `api/main.py` reads `AGENTFLOW_QUEUE_BACKEND` (default
      `in_memory`); when set to `redis`, builds `RedisJobQueue` from
      `AGENTFLOW_REDIS_URL`. Same instance is what worker drains.
- [ ] Tests using `fakeredis` (add to `requirements-optional`):
      - Round-trip enqueue → dequeue → status updates.
      - Multi-consumer: two workers, only one gets each job.
      - Reconnect / connection-loss handling.
- [ ] Tests run only when `fakeredis` is installed (pytest skip
      otherwise) — match the embeddings pattern.

Commit: `feat(worker): RedisJobQueue backend (Phase E.1)`

### Chunk 2 — Standalone worker process (~1-2 hrs)

What's there today:
- [worker/job_runner.py:285](worker/job_runner.py:285) `_amain` already
  exists as a CLI entry but constructs its own InMemoryJobQueue —
  doesn't share with the API.
- `AGENTFLOW_DISABLE_INPROC_WORKER=1` already wires through
  [api/main.py:57](api/main.py:57).

Tasks:
- [ ] `_amain` reads `AGENTFLOW_QUEUE_BACKEND`/`AGENTFLOW_REDIS_URL`
      and constructs the matching backend. With the redis backend, API
      and worker share the queue across processes.
- [ ] `python -m worker.job_runner` with redis env vars must drain a
      job enqueued by a separate API process. Add an integration test
      that spins both up against `fakeredis` and verifies a round-trip.
- [ ] Document the production layout (API process disables in-proc
      worker; standalone worker process runs `worker.job_runner`).

Commit: `feat(worker): standalone worker drains shared Redis queue (Phase E.2)`

### Chunk 3 — `KubeJobManager` for sandbox pods (~4-5 hrs, depends on Phase B)

What's there today:
- [worker/docker_manager.py](worker/docker_manager.py) is the reference
  surface (`start`, `exec_in`, `stop`, `active_runs`).
- DESIGN §29 + [sandbox/Dockerfile](sandbox/Dockerfile) define the
  resource limits and security posture that the k8s pod must replicate.
- Phase B's container-routed `code:exec` runner is already mode-agnostic
  about *which* manager backs `exec_in` — it just calls the manager.
  That's the seam Chunk 3 plugs into.

Tasks:
- [ ] Add `kubernetes>=29.0.0` to `requirements-optional.txt`.
- [ ] `worker/kube_job_manager.py:KubeJobManager` implements the same
      surface as `DockerManager`:
      - `start(run_id, workspace_dir, personas_dir)` creates a Pod with:
        - `securityContext`: `runAsNonRoot`, `readOnlyRootFilesystem`,
          `allowPrivilegeEscalation: false`, drop all capabilities.
        - Resource limits matching `CONTAINER_LIMITS`
          ([worker/docker_manager.py:38](worker/docker_manager.py:38)):
          `memory: 512Mi`, `cpu: 1000m`, `pidsLimit: 50`.
        - `tmpfs` emptyDir at `/tmp` sized 64Mi.
        - Workspace mount: PVC or hostPath (multi-node implications —
          PVC is correct, document the requirement).
        - `imagePullPolicy: IfNotPresent` for the prebuilt sandbox image.
      - `exec_in(...)`: `kubernetes.stream.stream` against the running
        pod, same shape as `docker exec`.
      - `stop(...)`: delete the pod with grace period 5s.
      - NetworkPolicy `denyAll` applied at the namespace level
        (chart-managed, not per-pod).
- [ ] Backend selection in [worker/job_runner.py](worker/job_runner.py)
      via `AGENTFLOW_SANDBOX_BACKEND=docker|kubernetes` (default
      `docker` to match today's behaviour).
- [ ] Tests using a mocked `kubernetes.client` API:
      - Pod created with the right spec.
      - Exec invocation goes through `kubernetes.stream.stream`.
      - Stop deletes the pod cleanly.
      - Mode enforcement: missing kubernetes SDK in production
        backend=kubernetes raises clearly.

Commit: `feat(worker): KubeJobManager for k8s sandbox pods (Phase E.3)`

### Chunk 4 — Helm chart (~3-4 hrs)

Tasks:
- [ ] `deploy/helm/agentflow/Chart.yaml` (apiVersion: v2; deps:
      bitnami/redis as optional subchart with `enabled: true|false`).
- [ ] `templates/api-deployment.yaml`:
      - `AGENTFLOW_DISABLE_INPROC_WORKER=1`,
        `AGENTFLOW_QUEUE_BACKEND=redis`,
        `AGENTFLOW_MODE=production`,
        `AGENTFLOW_SANDBOX_BACKEND=kubernetes`.
      - Mounts `/data` PVC.
      - Service for the API (ClusterIP + optional Ingress).
- [ ] `templates/worker-deployment.yaml`:
      - Same env as API.
      - Replica count configurable (default 2).
      - Mounts `/data` PVC (same one — must be RWX).
- [ ] `templates/networkpolicy.yaml`:
      - `denyAll` egress for sandbox pods (selector by label assigned
        by `KubeJobManager`).
      - API/worker pods retain egress to anthropic API + Redis.
- [ ] `templates/serviceaccount.yaml` + RBAC: worker SA has rights to
      create/delete Pods + NetworkPolicies in the configured namespace
      (KubeJobManager needs them).
- [ ] `templates/redis.yaml` if subchart disabled — placeholder
      ConfigMap pointing at an external Redis URL.
- [ ] `values.yaml` covers: image refs, replica counts, anthropic key
      secret name, redis URL, namespace, PVC size, ingress host.
- [ ] CI verification: `helm lint deploy/helm/agentflow` and
      `helm template ...` must succeed (add to CI or document the
      one-liner in README).

Commit: `feat(deploy): Helm chart for k8s deployment (Phase E.4)`

### Chunk 5 — Deploy walkthrough + README updates (~1-2 hrs)

Tasks:
- [ ] `README.md` gets a "Production deployment" section with:
      - Build + push the host image (existing Dockerfile).
      - Build + push the sandbox image (existing
        [sandbox/Dockerfile](sandbox/Dockerfile)).
      - Provision a cluster with RWX storage (note that hostPath/EBS
        won't work for multi-node; document NFS / EFS / Filestore).
      - `helm install agentflow deploy/helm/agentflow -f values.yaml`.
      - Smoke test: create project via Ingress → invoke run → check
        worker pod logs and sandbox pod created.
      - Common config values table.
- [ ] Update [.claude/progress/BOARD.md](.claude/progress/BOARD.md) and
      ROADMAP.md to mark E shipped + bump test count.
- [ ] Update [.claude/progress/followups.md](.claude/progress/followups.md):
      Tier 3 items now ✅ shipped.

Commit: `docs: production k8s deployment walkthrough (Phase E.5)`

**Phase E exit:** A reasonably-skilled k8s operator can `helm install`
AgentFlow against a real cluster, and runs are sandboxed in ephemeral
pods with locked-down NetworkPolicies. Multiple worker replicas drain
the same Redis queue.

---

## Risk and dependencies

- **Phase E.3 (KubeJobManager) depends on Phase B** — it plugs into the
  same `code:exec` routing seam. Build B first.
- **Phase E.1 + E.2 are independent of B and C** — could ship in
  parallel if multiple developers were on this; for solo work, run them
  in the listed order.
- **Phase C.3 (tech_lead escalation) is the trickiest part** — it's the
  first dynamic agent dispatch in the system. Read
  [agents/builtin/it_agent.py](agents/builtin/it_agent.py) and the
  scheduler's event-driven path before designing. If the existing IT
  agent already establishes the pattern, follow it directly. If not,
  expect a small scheduler change to support "wake on signal type"
  agents.
- **fakeredis vs real Redis** — fakeredis is sufficient for our queue
  Protocol. If pubsub semantics drift, fall back to a docker-compose'd
  Redis for tests (slower but real).
- **k8s testing locally** — `kind` or `k3d` work for a one-off smoke;
  don't add k8s to the default test suite (too heavy).

## Tracking

Update this file as chunks ship. When a phase is done, mark it ✅ in
ROADMAP.md and clean the corresponding entries from
[followups.md](.claude/progress/followups.md).

After B + C + E:
- [BOARD.md](.claude/progress/BOARD.md) → "Next action": Phase D (real
  meta-agent proposal that reads inputs/), or Phase F (training camp /
  memory synthesis / persona chat) per user goal.
- The full deploy story is shipped; the platform is production-grade
  on the runtime side. Remaining work shifts to agent intelligence
  (Phase D + F) and run-review UX (Phase G).
