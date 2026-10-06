# GuudFlow host image (API + in-process worker).
#
# Multi-stage build:
#   Stage 1: build the studio bundle with Node
#   Stage 2: install the pinned opencode binary with npm
#   Stage 3: install Python deps, copy app, bundle and opencode, run uvicorn
#
# The agent sandboxes are SIBLINGS of this container, started on the host's
# Docker daemon through the mounted /var/run/docker.sock. Run it with
# docker-compose.yml, which sets up what that needs (host networking, the data
# directory at the same path inside and outside, NET_ADMIN); see docs/SETUP.md.

# ── Stage 1: studio bundle ─────────────────────────────────────────────────

FROM node:22-slim AS studio-build

WORKDIR /studio

# Install deps first for cache efficiency.
COPY studio/package.json studio/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

# Copy and build.
COPY studio/ ./
RUN npm run build

# ── Stage 2: the pinned opencode binary ───────────────────────────────────
# The agent harness (one `opencode serve` per role, inside a sandbox) needs the
# exact version in core/harness/server.py. The sandbox image is built from this
# native binary on the first run.

FROM node:22-slim AS opencode

COPY core/harness/server.py /tmp/server.py
RUN version="$(sed -n 's/^OPENCODE_VERSION = "\(.*\)"/\1/p' /tmp/server.py)" \
 && test -n "$version" \
 && mkdir -p /opt/opencode && cd /opt/opencode \
 && npm init -y >/dev/null \
 && npm install --no-audit --no-fund --save-exact "opencode-ai@${version}" \
 && ./node_modules/.bin/opencode --version

# ── Stage 3: Python runtime ────────────────────────────────────────────────

FROM python:3.11-slim

# git for project repos and run branches; iptables so each run can lock its
# sandboxes out of other host ports. The Docker SDK talks to the mounted
# /var/run/docker.sock directly, so no docker CLI is needed.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git iptables \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Optional embeddings deps (PyTorch, sentence-transformers) add ~600MB
# and are lazy-loaded — off by default per DESIGN §28. Set this build
# arg to 1 to bake them into the image.
ARG WITH_EMBEDDINGS=0

# Python deps first for cache.
COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$WITH_EMBEDDINGS" = "1" ]; then \
        pip install --no-cache-dir -r requirements-optional.txt; \
    fi

# App code.
COPY . .

# Studio bundle from stage 1, opencode from stage 2 (where the harness looks for it).
COPY --from=studio-build /studio/dist /app/studio/dist
COPY --from=opencode /opt/opencode /app/.tools/opencode

# Default config — override in docker-compose.yml or `docker run -e ...`.
ENV GUUDFLOW_DATA_DIR=/app/data \
    GUUDFLOW_MODE=dev \
    GUUDFLOW_STUDIO_DIST=/app/studio/dist \
    GUUDFLOW_CORS_ORIGINS=*

EXPOSE 8000

# In-process worker is enabled by default. To run the worker as a separate
# process, set GUUDFLOW_DISABLE_INPROC_WORKER=1 on this container and run a
# second container with command: ["python", "-m", "worker.job_runner"].
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
