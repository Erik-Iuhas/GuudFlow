# sandbox/

The minimal image used as the per-run execution sandbox in production.

`worker/docker_manager.py` calls `containers.run(image="guudflow-sandbox:latest", ...)`
at run start. Each run gets its own ephemeral container with the workspace
mounted, the persona roster read-only, and §29's resource caps applied.

## What's in the image

- `python:3.11-slim` base
- `bash` (for shell-mode `code-exec`)
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
- The GuudFlow application code — the runner runs on the host

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
docker build -t guudflow-sandbox:latest sandbox/
```

The build context is just this directory (see `.dockerignore`). The build
should take < 60s on a warm cache.

## Smoke test

```bash
docker run --rm guudflow-sandbox:latest python -c "print('hi')"
docker run --rm guudflow-sandbox:latest bash -lc "echo hi"
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
- Pinning to a SHA digest (e.g. `guudflow-sandbox@sha256:...`) is
  recommended in production so a tag drift doesn't silently change
  the runtime

## Optional chart-rendering variant: `agent-opencode-viz`

The opencode agent sandbox (`agent-opencode.Dockerfile`, plan §5.3) has a chart-rendering
sibling, `guudflow/agent-opencode-viz:<opencode version>` (plan §5.10). It is
`FROM guudflow/agent-opencode:<version>` plus matplotlib (Agg backend), plotly, kaleido
(with its own headless Chromium), numpy and pandas, so chart-heavy roles do not need the IT
agent for packages. It adds roughly 0.15 GB compressed (about 0.65 GB on disk), so it is
opt-in and never the default.

Pinned versions live in `sandbox/viz-requirements.txt`. plotly is held below 6.1 because
kaleido 0.2.1 is the release that bundles Chromium; kaleido 1.x needs a separate Chrome
download, which the offline build cannot do.

### Build

`docker build` for these images has no network, so the wheels are downloaded on the host
and copied in:

```bash
scripts/fetch_viz_wheels.sh        # -> .tools/viz-wheels/ (gitignored, never commit)
python -c "import asyncio; from worker.sandbox_manager import SandboxManager; \
asyncio.run(SandboxManager().ensure_image(variant='viz'))"
```

`ensure_image(variant="viz")` builds the default image first if it is missing, then the viz
image from `agent-opencode-viz.Dockerfile` with a context holding only the Dockerfile,
`viz_install.py` and the wheels. The base image has no pip, so `viz_install.py` unpacks the
wheels in a build stage and only the unpacked packages are copied into the final image; the
wheels never reach it. Without wheels the build raises `SandboxError` telling you to run
`scripts/fetch_viz_wheels.sh`. Set `GUUDFLOW_VIZ_WHEELS_DIR` to keep the wheels elsewhere.

### Use

Hardening is identical to the default image: the manager's flags are unchanged and the image
keeps the unprivileged `agent` user. `MPLBACKEND=Agg` and `MPLCONFIGDIR=/tmp/matplotlib` are
preset because the root filesystem is read-only.

A role gets the viz image only when asked, through the existing per-spec image override:

```python
from worker.sandbox_manager import SandboxSpec, image_for_variant
spec = SandboxSpec(..., image=image_for_variant("viz"))   # or image=VIZ_IMAGE
```

Wiring this into run configuration (for example a role capability) is a later change.

### Test

`tests/worker/test_viz_image.py` builds the image and renders a matplotlib PNG and a plotly
PNG (kaleido) inside a container started with the manager's hardened kwargs. It skips when
Docker is unavailable, `GUUDFLOW_SKIP_DOCKER_TESTS` is set, or the wheels are missing.
