#!/usr/bin/env bash
# GuudFlow local setup — Linux / macOS / WSL.
#
# Creates a Python venv, installs deps, installs playwright chromium,
# copies .env.example → .env if not present, and prints next steps.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"   # where the uv installer puts uv

echo "GuudFlow setup"
echo "==============="
echo "Repo: ${ROOT}"
echo

# 1. Python 3.11+ (a system python3 can be older: Ubuntu 22.04 / WSL ships 3.10)
source scripts/pick_python.sh
USE_UV=1
if [ "${GUUDFLOW_NO_UV:-0}" = "1" ]; then USE_UV=0
elif ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not found. Install it: bash scripts/run.sh install-uv"
    echo "       (or set GUUDFLOW_NO_UV=1 to use pip with a Python 3.11+ you installed)"
    exit 1
fi
PY="$(pick_python || true)"
if [ "$PY" = "3.11" ]; then
    echo "No Python 3.11 or 3.12 installed: uv will download 3.11."
elif [ -n "$PY" ]; then
    echo "Python: $PY ($("$PY" --version 2>&1))"
else
    echo "ERROR: Python 3.11 not found (set GUUDFLOW_PYTHON=/path/to/python3.11, or use uv)"; exit 1
fi
if [ "${USE_UV}" = "0" ] && [ "$PY" = "3.11" ]; then
    echo "ERROR: without uv, install Python 3.11 (or set GUUDFLOW_PYTHON)"; exit 1
fi

# 2. Virtualenv. An existing one that is broken or older than 3.11 is rebuilt.
if [ -d venv ] && ! venv_ok; then
    echo "./venv is missing Python or is not 3.11/3.12 ($(venv/bin/python --version 2>&1 || echo broken)): rebuilding"
    rm -rf venv
fi
if [ ! -d "venv" ]; then
    echo "Creating virtualenv at ./venv ..."
    if [ "${USE_UV}" = "1" ]; then
        uv venv --python "${PY:-3.11}" venv || { echo "ERROR: uv could not create a Python 3.11 venv (downloads blocked? set GUUDFLOW_PYTHON to an installed python3.11)"; exit 1; }
    else
        "$PY" -m venv venv
    fi
else
    echo "Virtualenv already exists at ./venv ($(venv/bin/python --version 2>&1))"
fi

# shellcheck disable=SC1091
source venv/bin/activate
PY_VENV="$(command -v python)"
echo "Activated: ${PY_VENV}"

echo "Installing Python dependencies$([ "${USE_UV}" = "1" ] && echo ' (uv)')..."
if [ "${USE_UV}" = "1" ]; then
    uv pip install --quiet -r requirements.txt
    pip_install() { uv pip install --quiet "$@"; }
else
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
    pip_install() { pip install --quiet "$@"; }
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

# 5. Studio (optional)
if command -v npm >/dev/null 2>&1; then
    if [ "${GUUDFLOW_SKIP_NPM:-0}" != "1" ]; then
        echo "Installing studio dependencies..."
        bash scripts/studio_deps.sh || echo "WARN: Studio packages missing. Studio still works from a prebuilt studio/dist (see README: Behind a proxy)."
    fi
else
    echo "INFO: npm not found — skipping studio. Install Node 18+ to run the frontend."
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
