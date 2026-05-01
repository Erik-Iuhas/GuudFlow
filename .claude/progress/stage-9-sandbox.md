# Stage 9 — Sandbox Dockerfile

**Budget:** 50-100k tokens
**Status:** done (2026-04-25) — Dockerfile + README + build script shipped; build verified locally requires running Docker daemon
**Exit criteria (CLAUDE.md):** Image builds; container runs with resource limits; production-mode `code:exec` routes through container.

DESIGN sections: §29.

## Files
- [x] `sandbox/Dockerfile` ✅ — `python:3.11-slim` + bash + coreutils + git + non-root `runner` user; `tail -f /dev/null` sleeper so `docker exec` has a target
- [x] `sandbox/.dockerignore` ✅ — empty build context except the Dockerfile (workspace mounts at runtime, never baked into the image)
- [x] `sandbox/README.md` ✅ — what's in / what's intentionally out / runtime constraints reference / build + smoke-test commands / production deploy notes
- [x] `scripts/build_sandbox.sh` ✅ — one-command build + smoke test

## Verification
- Dockerfile reviewed against §29's CONTAINER_LIMITS contract (worker/docker_manager.py): network_mode none, mem 512m, pids 50, read_only root, tmpfs /tmp, no-new-privileges, non-root user
- `docker build -t guudflow-sandbox:latest sandbox/` — built successfully on Docker 27.4.0 (Desktop on Windows). Final image sha256:556c53fe…
- Smoke tests pass:
  - `python -c "print('python ok')"` → ok
  - `bash -lc "echo bash ok"` → ok
  - `git --version` → 2.47.3
  - `whoami` → `runner` (non-root)
- §29 runtime constraints verified end-to-end with `--network=none --read-only --tmpfs /tmp:size=64m --memory=512m --pids-limit=50 --security-opt=no-new-privileges`:
  - cwd: /workspace ✓
  - uid: 999 (non-root) ✓
  - outbound network blocked ✓
  - root filesystem rejects writes ✓
  - /tmp writable (only mutable spot besides /workspace) ✓
- Backend suite still **530 passed, 2 skipped** — Stage 9 is image-only, no Python changes

## Image philosophy
- Minimal: Python + bash + coreutils + git
- No language-specific tooling baked in
- No project deps — IT agent (DESIGN §21) installs at runtime into `/workspace/.venv` or `/tmp`
- No editors, no curl/wget (network_mode none makes them useless and they're attack surface)
- Per-project sandboxes: build a derived image and override `worker.docker_manager.DEFAULT_IMAGE`

## What's STILL missing for full production code:exec routing

The Dockerfile lands the IMAGE; the wire-up of `code:exec` to route through `DockerManager.exec_in()` in production mode is recorded in [followups.md](followups.md) under "Stage 7 follow-ons". Roughly:
- Detect `GUUDFLOW_MODE=production` in the runner
- Replace the dev host `code:exec` runner with `lambda lang, args, opts: docker.exec_in(run_id, lang, opts['code'])`
- Test against a real local Docker daemon

That's a small (~30 line) follow-on; the Dockerfile is a prerequisite, now satisfied.

## Build
```bash
./scripts/build_sandbox.sh
# or
docker build -t guudflow-sandbox:latest sandbox/
```

## Smoke test
```bash
docker run --rm guudflow-sandbox:latest python -c "print('hi')"
docker run --rm guudflow-sandbox:latest bash -lc "echo hi"
```
