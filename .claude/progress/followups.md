# Deferred follow-ons

These are concrete pieces of work explicitly punted from earlier stages.
Pick them up when their value lines up with a near-term need (deployment,
new contributors, scaling).

## Onboarding & deployment

### Tier 1 — Onboarding docs ✅ DONE (2026-04-25, Phase A)
- [x] `README.md` at repo root — quickstart, configuration table, link to DESIGN.md
- [x] `scripts/setup.sh` + `scripts/setup.ps1` — one-command env + deps + playwright + studio install + .env scaffold; `GUUDFLOW_WITH_EMBEDDINGS=1` opts into the heavy embeddings deps
- [x] `requirements-optional.txt` — embeddings (PyTorch + sentence-transformers) split out so default install stays light (~60s)

### Tier 2 — Local Docker ✅ DONE (2026-04-25, Phase A)
- [x] `Dockerfile` — multi-stage (build studio bundle → Python runtime); `--build-arg WITH_EMBEDDINGS=0/1`; `docker-cli` (NOT `docker.io`) for talking to the host's mounted socket
- [x] `.dockerignore`
- [x] `docker-compose.yml` — host stack with `/var/run/docker.sock` mount for sibling-container sandbox spawning; persistent `./data` volume
- [x] `api/main.py` serves studio bundle at `/` when `GUUDFLOW_STUDIO_DIST` is set

### Tier 3 — Production k8s (substantial; do when there's a real deploy target)
Now lives in **ROADMAP Phase E.**
- [ ] `worker/redis_queue.py` — Redis-backed `JobQueue` impl
- [ ] `GUUDFLOW_QUEUE_BACKEND=redis` env flag flips it on
- [ ] `KubeJobManager` — k8s-native equivalent of `DockerManager` (spawns ephemeral Pods with NetworkPolicy denyAll, securityContext, resource limits) since the docker-socket sibling-container pattern doesn't translate cleanly to multi-node clusters
- [ ] Separate API + worker deployments (set `GUUDFLOW_DISABLE_INPROC_WORKER=1` on API pods)
- [ ] `deploy/helm/` Helm chart with Service, Deployments, optional Redis dep, persistent volume
- [ ] Update README with k8s deploy walkthrough

## Stage 7 follow-ons

- [ ] **`code:exec` → container routing** (now ROADMAP Phase B) — when `GUUDFLOW_MODE=production`, the runner should swap the dev host runner for a container-routed runner that calls `DockerManager.exec_in()`. ~30 lines in `agents/tools/code.py` + `core/runner.py`.
- [ ] **Standalone worker shared-queue** — `python -m worker.job_runner` currently has its own InMemoryJobQueue, doesn't share with the API process. Tier 3 Redis queue resolves this.

## Stage 5 follow-ons (from earlier sessions)

- [ ] **Tech-lead escalation detection** (now ROADMAP Phase C) — runner needs to track per-role rejection counts and dispatch the tech_lead agent at the configured threshold (DESIGN §23). Currently tech_lead has the system prompt + class but isn't auto-activated.
- [ ] **Meta agent's actual proposal synthesis** (now ROADMAP Phase D) — currently `/api/meta/propose` returns a deterministic skeleton. Real LLM-driven proposal that reads `inputs/README.md` per §33 lands here. Depends on Phase D-prime so there's actually something for the meta-agent to read.
- [ ] **Real Playwright wiring for visual_checker** — `request_visual_feedback` posts the signal but no actual screenshot is taken yet. Stage 6 has `screenshot_callback` parameter on the runner; Stage 8 frontend may also need this surface.

## Rich input ingestion (raised post-Phase A — now ROADMAP Phase D-prime)

The current proposal flow assumes "type a goal in a textbox." DESIGN §33
specifies five initialization patterns (greenfield, git_url, uploads,
prior_run, mixed) but only `FileWorkspace.read_input()` and the `inputs/`
layout are built. Users showing up with rich asks (PDF specs, existing
repos, prior-run continuations) currently have nowhere to put them.

See **ROADMAP Phase D-prime** for the full punch list. Note that
"between-run continuation" in §33 is *not* the in-run review/iteration
loop — that's already handled by the scheduler + reviewer (DESIGN §19,
§23) and bounded by `max_iterations`. Continuation here means the
between-run case: prior run completed, user reviewed, time passed, fresh
run builds on the prior output.

## OpenAI-compat adapter follow-on

- [ ] **Prompt-based tool calling adapter** for OpenAI-compatible models that don't support native function calling. Modern Ollama models (qwen2.5, llama3.1 8B+) support OpenAI tool spec; smaller / older ones don't. A separate adapter that emulates tool calling via prompt templates would extend the smaller-model story further.

## Sandbox ✅ DONE

- [x] **Stage 9** — `sandbox/Dockerfile`. Built and verified live (sha256:556c53fe…) with all §29 runtime constraints (network_mode none, read-only root, tmpfs, mem cap, pids cap, no-new-privileges, non-root user).
