# sandbox/

The minimal image used as the per-run execution sandbox in production.

`worker/docker_manager.py` calls `containers.run(image="agentflow-sandbox:latest", ...)`
at run start. Each run gets its own ephemeral container with the workspace
mounted, the persona roster read-only, and §29's resource caps applied.

## What's in the image

- `python:3.11-slim` base
- `bash` (for shell-mode `code:exec`)
- `coreutils` (cat / ls / head / tail / etc.)
- `git` (so agents can inspect the repo mounted at `/workspace`)
- A non-root `runner` user

That's it. No language-specific tooling, no project deps. Anything an agent
needs at runtime goes through the IT agent (DESIGN §21) into the writable
mounts (`/workspace/.venv` or `/tmp`).

## What's NOT in the image (intentionally)

- Curl / wget — `network_mode: none` makes them useless and they're
  attack-surface
- Editors — agents don't run interactive tools
- Node / Java / Ruby / Go — out of scope for the initial build; if a
  project needs them, build a project-specific sandbox image and point
  `worker/docker_manager.DEFAULT_IMAGE` at it
- The AgentFlow application code — the runner runs on the host

## Runtime constraints (set by docker_manager, NOT by this Dockerfile)

These are imposed at `containers.run()` time and live in
`worker/docker_manager.CONTAINER_LIMITS`:

```
network_mode: none
mem_limit: 512m
memswap_limit: 512m
cpu_period: 100000
cpu_quota: 100000      # 1 cpu
pids_limit: 50
read_only: true
tmpfs: /tmp size=64m
security_opt: no-new-privileges
```

Mounts:
- `/workspace` — rw, scoped to one run (`data/runs/{run_id}/workspace/`)
- `/personas` — ro, the project's persona roster

## Build

```bash
docker build -t agentflow-sandbox:latest sandbox/
```

The build context is just this directory (see `.dockerignore`). The build
should take < 60s on a warm cache.

## Smoke test

```bash
docker run --rm agentflow-sandbox:latest python -c "print('hi')"
docker run --rm agentflow-sandbox:latest bash -lc "echo hi"
```

Both should print `hi`.

## When to rebuild

The image is small and rebuilds are cheap. Rebuild when:

- Bumping the Python version
- Adding a baseline tool (e.g. a different shell)
- Pinning a CVE-fixed `python-slim` tag

Per-project tooling does NOT belong in this image — the IT agent + a
runtime venv is the right pattern.

## Production deploy notes

- The image must exist on the host where the worker runs
- For k8s / multi-node deploys, push to a registry and update
  `DEFAULT_IMAGE` in `worker/docker_manager.py` (or set it via env)
- Pinning to a SHA digest (e.g. `agentflow-sandbox@sha256:...`) is
  recommended in production so a tag drift doesn't silently change
  the runtime
