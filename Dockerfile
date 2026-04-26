# AgentFlow host image (API + in-process worker).
#
# Multi-stage build:
#   Stage 1: build the studio bundle with Node
#   Stage 2: install Python deps, copy app, copy bundle, run uvicorn
#
# The host container is a SIBLING of the per-run sandbox container, not its
# parent. docker-compose.yml mounts /var/run/docker.sock so this container
# can ask the machine's Docker daemon to spawn sandbox containers as
# siblings (DESIGN §29).

# ── Stage 1: studio bundle ─────────────────────────────────────────────────

FROM node:22-slim AS studio-build

WORKDIR /studio

# Install deps first for cache efficiency.
COPY studio/package.json studio/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

# Copy and build.
COPY studio/ ./
RUN npm run build

# ── Stage 2: Python runtime ────────────────────────────────────────────────

FROM python:3.11-slim

# git for core/git_workspace.py runtime ops; docker-cli (NOT docker.io,
# which pulls the full daemon) so we can talk to the host's mounted
# /var/run/docker.sock.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git docker-cli \
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

# Studio bundle from stage 1.
COPY --from=studio-build /studio/dist /app/studio/dist

# Default config — override in docker-compose.yml or `docker run -e ...`.
ENV AGENTFLOW_DATA_DIR=/app/data \
    AGENTFLOW_MODE=dev \
    AGENTFLOW_STUDIO_DIST=/app/studio/dist \
    AGENTFLOW_CORS_ORIGINS=*

EXPOSE 8000

# In-process worker is enabled by default. To run the worker as a separate
# process, set AGENTFLOW_DISABLE_INPROC_WORKER=1 on this container and run a
# second container with command: ["python", "-m", "worker.job_runner"].
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
