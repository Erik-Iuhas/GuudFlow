# GuudFlow sandbox — DESIGN §29.
#
# This is the per-run ephemeral container that worker/docker_manager.py
# spins up at run start and tears down at run end. The runner mounts the
# run's workspace at /workspace (rw) and the persona roster at /personas
# (ro), then routes `code:exec` calls into this container via `docker exec`.
#
# Hard requirements (§29 / CONTAINER_LIMITS in worker/docker_manager.py):
#   - Runs with network_mode: none — no outbound network access
#   - Runs with read_only: true — root filesystem is immutable
#   - /tmp is tmpfs (size=64m) — only writable spot besides /workspace
#   - mem 512m, 1 CPU, pids 50, no-new-privileges
#
# Image philosophy: minimal. Python + bash + coreutils. Anything else an
# agent needs goes through the IT agent (DESIGN §21) at runtime, into
# /workspace/.venv or /tmp.

FROM python:3.11-slim

# Defence in depth: even with no-new-privileges, run as a non-root user.
RUN groupadd --system runner \
 && useradd --system --gid runner --create-home --home-dir /home/runner runner

# Minimal tooling. bash for shell-mode `code:exec`. coreutils for
# the standard unix surface (cat, ls, head, tail, etc.). git so
# agents can read the repo state mounted at /workspace.
# Anything else is intentionally absent.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        bash \
        coreutils \
        git \
 && rm -rf /var/lib/apt/lists/*

# Standard Python flags: don't write .pyc files, unbuffered stdout/stderr.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Working directory — /workspace is mount-time so this is just a default cwd
# for any direct `docker exec` calls before a workspace cd.
WORKDIR /workspace

USER runner

# The worker doesn't ship a real CMD — it uses `docker exec` to drive code
# execution per call. The container stays alive on a sleeper so exec_run
# has something to attach to.
CMD ["tail", "-f", "/dev/null"]
