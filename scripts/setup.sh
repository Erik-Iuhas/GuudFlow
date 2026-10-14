#!/usr/bin/env bash
# GuudFlow local setup — Linux / macOS / WSL.
#
# Creates a Python venv, installs deps, installs playwright chromium,
# copies .env.example → .env if not present, and prints next steps.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"
source scripts/net_env.sh   # proxy, mirror and certificate settings from .env
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"   # where the uv installer puts uv

echo "GuudFlow setup"
echo "==============="
echo "Repo: ${ROOT}"
echo

# 1-3. Python 3.11, ./venv and the packages: the shared step (proxy settings, offline wheels in
# prebuilt/; see scripts/python_env.sh). Nothing below works without it, so stop if it fails.
bash scripts/python_env.sh || { echo "ERROR: no working Python environment; fix the line above and run setup again"; exit 1; }
# shellcheck disable=SC1091
source venv/bin/activate
if command -v uv >/dev/null 2>&1 && [ "${GUUDFLOW_NO_UV:-0}" != "1" ]; then
    pip_install() { uv pip install --quiet --python venv/bin/python "$@"; }
else
    pip_install() { python -m pip install --quiet "$@"; }
fi

# Optional embeddings (heavy — pulls PyTorch). Off by default; opt in with
# GUUDFLOW_WITH_EMBEDDINGS=1.
if [ "${GUUDFLOW_WITH_EMBEDDINGS:-0}" = "1" ]; then
    echo "Installing optional embedding dependencies (this is heavy)..."
    pip_install -r requirements-optional.txt
fi

# 4. Playwright browsers (skippable)
if [ "${GUUDFLOW_SKIP_PLAYWRIGHT:-0}" != "1" ]; then
    echo "Installing Playwright chromium (set GUUDFLOW_SKIP_PLAYWRIGHT=1 to skip)..."
    python -m playwright install chromium || {
        echo "WARN: playwright install failed — you can re-run later if you need visual_checker"
    }
fi

# 5. Studio (optional): the shared step. An archive in prebuilt/ wins and npm is never run.
if [ "${GUUDFLOW_SKIP_NPM:-0}" != "1" ]; then
    bash scripts/studio_ensure.sh || echo "WARN: no Studio yet. Put studio-dist.tar.gz (or the artifact zip) in prebuilt/ and run: bash scripts/run.sh studio-ensure"
fi

# The agent runtime: every role runs in opencode. Without it no agent can start.
if [ ! -x .tools/opencode ] && [ "${GUUDFLOW_SKIP_OPENCODE:-0}" != "1" ]; then
    echo "Installing the pinned opencode agent runtime (scripts/install_opencode.sh)..."
    bash scripts/install_opencode.sh || echo "WARN: opencode install failed: agents cannot start until scripts/install_opencode.sh succeeds"
fi

# 6. .env
FRESH_INSTALL=0
if [ ! -f ".env" ]; then
    FRESH_INSTALL=1
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "Created .env from .env.example"
    else
        cat > .env <<'EOF'
ANTHROPIC_API_KEY=
GUUDFLOW_API_TOKEN=
GUUDFLOW_MODE=dev
EOF
        echo "Created .env (no .env.example found)"
    fi
else
    echo ".env already exists — leaving alone"
fi

# 7. Seed default personas on first install (idempotent — won't
# overwrite existing personas; safe to skip on re-run).
if [ "${FRESH_INSTALL}" = "1" ] || [ "${GUUDFLOW_FORCE_SEED:-0}" = "1" ]; then
    echo "Seeding default personas..."
    python scripts/seed_data.py || echo "WARN: seed_data.py failed — you can re-run manually"
fi

echo
echo "✓ Setup complete."
echo
echo "Next steps:"
echo "  1. Edit .env and add your ANTHROPIC_API_KEY"
echo "  2. Activate the venv:    source venv/bin/activate"
echo "  3. Check everything:     scripts/doctor.sh"
echo "  4. Start the backend:    uvicorn api.main:app --port 8000"
echo "  5. Start the frontend:   cd studio && npm run dev"
echo "  6. Open the studio:      http://localhost:3001"
echo
echo "Full guide: docs/SETUP.md"
