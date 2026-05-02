# Production Sandbox — Containerise `code-exec` and Friends

**Type:** Stage 9 from the original CLAUDE.md plan. Deferred at MVP.
**Token budget when picked up:** ~150–250k for the minimal viable sandbox; more if production hardening (network policy, secrets isolation, image signing) is in scope.
**When to elevate:** before the first non-toy use case. Specifically:
- Running AgentFlow against an unfamiliar/untrusted repository.
- Multi-tenant deployment.
- Anywhere the host filesystem contains anything more sensitive than the user's own development workspace.

## What

Today, `code-exec` runs **on the host** in dev mode with a warning logged on every call. Per CLAUDE.md security rule:

> **Production**: `code_exec` MUST run inside the sandbox container. The worker must refuse to start in production mode without Docker available.

A minimal sandbox:

- Docker container per `code-exec` invocation (or per agent — depending on isolation/perf tradeoff).
- `network_mode: none` — no outbound traffic.
- Resource limits: CPU, memory, wall-clock.
- Filesystem: only `workspace/working` mounted read-write. Everything else read-only or absent.
- No host environment variables passed in. No host secrets.
- Anthropic / OpenAI / etc. API keys are NEVER in the container; the runner makes LLM calls on the host side.

## Why this is a real concern

Today's threat model is "trusted user runs AgentFlow on their own laptop against their own code." That's fine. The day the threat model expands to:

- Untrusted code in the workspace,
- Multi-tenant runs sharing infrastructure,
- Agents being given tools that could exfiltrate data,

…the host-mode `code-exec` becomes a critical vulnerability. Once the runner accepts a job from a network endpoint (which it does, via the API layer), the attack surface includes anything `code-exec` can do.

`FileWorkspace._safe_path` is good but it only protects the filesystem. Network calls from inside `code-exec`, environment variable reading, process spawning — all unconstrained.

## Files (when picked up)

Per the original CLAUDE.md plan:

- `sandbox/Dockerfile` — minimal Python image with the worker-side machinery.
- `worker/docker_manager.py` — already a stub; populate it with real container lifecycle.
- `worker/job_runner.py` — refuse to start in production mode without Docker.
- `core/runner.py` (or extracted; see runner_refactor.md) — route `code-exec` to docker_manager when not in dev mode.
- Tests for: container starts, container is destroyed on completion, network is blocked, resource limits trigger correctly, host secrets are not visible.

## What good looks like

Per CLAUDE.md original spec:

> Image builds; container runs with resource limits; `code_exec` routes through container in production mode.

A successful Stage 9 ships as a single small Dockerfile + the manager wiring. Docker overhead (~1s per container start) is acceptable for the security gain. Faster alternatives (firecracker, gVisor) are interesting but out of scope for v1 — Docker is the lowest-friction path that any user already has.

## Out of scope

- Image signing / supply-chain verification (deploy-time concern).
- Multi-tenant secrets management (separate problem).
- Per-language sandbox tuning. Python and shell are the only two `code-exec` languages today.

## Ordering note

This becomes blocking the moment AgentFlow is used by anyone other than you on hardware you control. Until then, the dev-mode warning + your awareness suffices.

If you ever start considering "let me try AgentFlow on this random GitHub repo," **ship this first.**
