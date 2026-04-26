# AgentFlow Roadmap

Sequenced next steps after the initial build. Each phase has a clear
"what it unblocks" so you can prioritize based on what you want to do
first (deploy? demo a real run? hand to another developer?).

The longer inventory of deferred items lives in
[`.claude/progress/followups.md`](.claude/progress/followups.md). This
file groups them into shippable phases.

---

## Phase A — Make the repo runnable by another human ✅ DONE

- [x] `README.md` — quickstart, architecture, configuration, status
- [x] `scripts/setup.sh` + `scripts/setup.ps1` — venv + deps + playwright
- [x] Host `Dockerfile` (multi-stage: builds studio + runs API)
- [x] `docker-compose.yml` — full stack with Docker socket mount + data volume

**Unblocks:** anyone clones the repo and gets running in 5 minutes.

---

## Phase B — Close the dev→prod gap (~1 hour)

Today the sandbox image is built and verified, but the runner doesn't
yet route `code:exec` through it in production mode. This is a small
wire-up and the highest-value next step for anyone deploying.

- [ ] Detect `AGENTFLOW_MODE=production` in `core/runner.py`
- [ ] Replace the dev host `code:exec` runner with a container-routed
      runner that calls `DockerManager.exec_in(run_id, lang, code)`
- [ ] Add a test (mocked DockerManager) verifying the routing happens
- [ ] Document the switch in `README.md`

**Unblocks:** real production-grade `code:exec` execution. Until this
lands, "production mode" still runs LLM-generated code on the host.

**Files:** `agents/tools/code.py`, `core/runner.py`,
`tests/agents/test_code_exec_hardening.py`

---

## Phase C — Make a live run feel live (~half day)

The Run view in the studio streams the board over WebSocket and that
works. But three pieces are stubbed and the run experience is less
interactive than it should be.

- [ ] **Live `USER_COMMENT` POST endpoint** — `POST /api/runs/{run_id}/comment`
      that posts a `USER_COMMENT` signal to the active workspace.
      Frontend's `CommentBox` is already wired; backend is a no-op today.
- [ ] **Real quip stream** — quips are written to
      `data/runs/{id}/quip_log.json` but the studio's `QuipFeed`
      synthesises pseudo-quips from board events. Either:
      - extend the existing `/ws/runs/{id}` to also stream quips, OR
      - add a parallel `/ws/runs/{id}/quips`
- [ ] **Tech-lead escalation auto-detection** — runner counts
      `REJECTED` signals per role; at threshold (default 2 per CLAUDE.md
      Escalation rules), spawns the `tech_lead` agent with the
      relevant context. Today `tech_lead` exists as a class but is
      never auto-activated.

**Unblocks:** a real demo run feels interactive. Users can steer
mid-flight; the platform escalates automatically when stuck.

**Files:** `api/routes/runs.py` (new comment route), `api/websocket.py`
or new `api/routes/quips.py`, `core/runner.py` (rejection counting +
tech_lead dispatch).

---

## Phase D — Make the meta-agent actually propose (~half day)

`POST /api/meta/propose` currently returns a deterministic
developer+reviewer skeleton. The whole point of the meta-agent is that
it should READ the goal + `inputs/` and produce a tailored proposal.

- [ ] Implement `MetaAgent.run()` properly:
  - Read `inputs/README.md` if present (DESIGN §33)
  - Drive an LLM call with the META_SYSTEM_PROMPT + goal + input excerpts
  - Parse the LLM's structured output into a `TeamProposal`
  - Return rationale + agents + RBAC
- [ ] Wire `/api/meta/propose` to actually invoke `MetaAgent.run()`
      instead of returning the skeleton
- [ ] Update the proposal UI if needed (probably already handles
      rich proposals correctly)

**Unblocks:** "describe a goal, get a smart team" — the platform
finally feels intelligent rather than form-driven.

**Files:** `agents/builtin/meta.py`, `api/routes/meta.py`.

---

## Phase E — Production deploy story (~1 day, when there's a target)

For multi-pod / k8s deployments, today's `InMemoryJobQueue` and
`DockerManager`-via-socket-mount don't translate cleanly. Skip this
until you have somewhere to actually deploy.

- [ ] **Redis-backed `JobQueue`** — `worker/redis_queue.py` implementing
      the `JobQueue` Protocol. Toggle via `AGENTFLOW_QUEUE_BACKEND=redis`.
- [ ] **`KubeJobManager`** — k8s-native equivalent of `DockerManager`
      that creates ephemeral Pods (with NetworkPolicy denyAll, read-only
      rootfs, resource limits, runAsNonRoot) instead of Docker
      containers. Same interface; runner is agnostic to which one's
      active.
- [ ] **Helm chart** under `deploy/helm/` — Service for the API,
      Deployments for API and worker, optional Redis dependency,
      persistent volume for `data/`, NetworkPolicies that keep sandbox
      Pods isolated.
- [ ] Document the deploy walkthrough in `README.md` or
      `deploy/README.md`.

**Unblocks:** real horizontal scaling, multi-tenant deploys, cloud
production.

**Files:** new `worker/redis_queue.py`, new `worker/kube_job_manager.py`,
new `deploy/helm/`, `worker/docker_manager.py` becomes one of two
backends.

---

## Phase F — Long-term moat features (each is a project)

These are designed in DESIGN.md but aren't on the critical path. Pick
them up when real users tell you which one matters.

- [ ] **Training Camp** (DESIGN §35) — toy scenario library,
      tournament mode, ranking board for hiring decisions. Critical
      for smaller-model users who need to bootstrap persona quality
      without paying for production runs.
- [ ] **Real `memory_writer` LLM synthesis** (DESIGN §34) — the
      data-layer reconciliation works (lifecycle transitions,
      contradiction reconciliation, episode staleness). The LLM-driven
      *distillation* — turning clusters of episodes into new patterns —
      is scaffolded but not implemented.
- [ ] **Persona chat endpoint** (DESIGN §37) — `POST /api/personas/{id}/chat`
      for one-off conversations with a persona outside any run. Marked
      future/optional in the design.
- [ ] **Real Playwright wiring for `visual_checker`** (DESIGN §5) —
      Stage 5 has the agent shell. Production rendering wire-up
      (host-side Playwright, screenshot delivery to the user, spatial
      annotation tools).
- [ ] **UICanvas** (DESIGN §15) — structured spatial language for
      design-driven projects. Marked future-only.

---

## How to pick what's next

| If you want to... | Do |
|---|---|
| ...let someone else run the repo | Phase A (done) |
| ...deploy to production safely | Phase B + Phase E |
| ...demo a real interactive run | Phase C + Phase D |
| ...make agents genuinely smarter over time | Phase F (training camp + memory synthesis) |

Recommended order assuming nothing else: **B → C → D**, then E or F as
needs emerge. Each is a half-day-or-less commitment and unlocks
visible value.

---

## Tracking

When a phase ships, mark it ✅ DONE in this file and clean the
relevant items out of `.claude/progress/followups.md`. The
`.claude/progress/BOARD.md` is the always-on dashboard for current
state; this ROADMAP is the forward-looking plan.
