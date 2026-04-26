# Deferred follow-ons

These are concrete pieces of work explicitly punted from earlier stages.
Pick them up when their value lines up with a near-term need (deployment,
new contributors, scaling).

## Onboarding & deployment (deferred from Stage 7)

The repo currently has no README, no host Dockerfile, no docker-compose,
and no k8s manifests. Someone cloning the repo today must read CLAUDE.md
to figure out how to run it. Three tiers were proposed; user chose to
defer all of them.

### Tier 1 — Onboarding docs (cheap)
- [ ] `README.md` at repo root — project description, status (build in progress), quickstart, link to DESIGN.md
- [ ] `scripts/setup.sh` + `scripts/setup.ps1` — one-command env + deps + playwright

### Tier 2 — Local Docker (medium)
- [ ] `Dockerfile` for host app — single image, configurable entrypoint (API or standalone worker)
- [ ] `docker-compose.yml` — API + worker + optional Redis with volume mounts for `data/`
- [ ] Update README with `docker-compose up` path

### Tier 3 — Production k8s (substantial; do when there's a real deploy target)
- [ ] `worker/redis_queue.py` — Redis-backed `JobQueue` impl
- [ ] `AGENTFLOW_QUEUE_BACKEND=redis` env flag flips it on
- [ ] Separate API + worker deployments (set `AGENTFLOW_DISABLE_INPROC_WORKER=1` on API pods)
- [ ] `deploy/helm/` Helm chart with Service, Deployments, optional Redis dep, persistent volume
- [ ] Update README with k8s deploy walkthrough

## Stage 7 follow-ons

- [ ] **`code:exec` → container routing** — when `AGENTFLOW_MODE=production`, the runner should swap the dev host runner for a container-routed runner that calls `DockerManager.exec_in()`. ~30 lines in `agents/tools/code.py` + `core/runner.py`.
- [ ] **Standalone worker shared-queue** — `python -m worker.job_runner` currently has its own InMemoryJobQueue, doesn't share with the API process. Tier 3 Redis queue resolves this.

## Stage 5 follow-ons (from earlier sessions)

- [ ] **Tech-lead escalation detection** — runner needs to track per-role rejection counts and dispatch the tech_lead agent at the configured threshold (DESIGN §23). Currently tech_lead has the system prompt + class but isn't auto-activated.
- [ ] **Meta agent's actual proposal synthesis** — currently `/api/meta/propose` returns a deterministic skeleton. Real LLM-driven proposal that reads `inputs/README.md` per §33 lands here.
- [ ] **Real Playwright wiring for visual_checker** — `request_visual_feedback` posts the signal but no actual screenshot is taken yet. Stage 6 has `screenshot_callback` parameter on the runner; Stage 8 frontend may also need this surface.

## OpenAI-compat adapter follow-on

- [ ] **Prompt-based tool calling adapter** for OpenAI-compatible models that don't support native function calling. Modern Ollama models (qwen2.5, llama3.1 8B+) support OpenAI tool spec; smaller / older ones don't. A separate adapter that emulates tool calling via prompt templates would extend the smaller-model story further.

## Sandbox

- [ ] **Stage 9** — `sandbox/Dockerfile`. The image DockerManager spins up. Production deploy is blocked on this.
