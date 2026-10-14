#!/usr/bin/env bash
# GuudFlow local setup — Linux / macOS / WSL.
#
# Creates a Python venv, installs deps, installs playwright chromium,
# copies .env.example → .env if not present, and prints next steps.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

echo "GuudFlow setup"
echo "==============="
echo "Repo: ${ROOT}"
echo

# 1. Python check
if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
    echo "ERROR: python (3.11+) not found in PATH"
    exit 1
fi
PY="$(command -v python3 || command -v python)"
PY_VERSION="$(${PY} --version | awk '{print $2}')"
echo "Python: ${PY} (${PY_VERSION})"

# 2. Virtualenv + 3. Python deps. uv is used when present (much faster); set GUUDFLOW_NO_UV=1
# to force python -m venv + pip.
# uv manages the Python packages. GUUDFLOW_NO_UV=1 falls back to python -m venv + pip.
USE_UV=1
if [ "${GUUDFLOW_NO_UV:-0}" = "1" ]; then USE_UV=0
elif ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "       (or set GUUDFLOW_NO_UV=1 to use pip)"
    exit 1
fi

if [ ! -d "venv" ]; then
    echo "Creating virtualenv at ./venv ..."
    if [ "${USE_UV}" = "1" ]; then uv venv --python 3.11 venv || uv venv venv
    else "${PY}" -m venv venv; fi
else
    echo "Virtualenv already exists at ./venv"
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
        (cd studio && npm install --silent --no-audit --no-fund)
    fi
else
    echo "INFO: npm not found — skipping studio. Install Node 18+ to run the frontend."
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
echo "  3. Install opencode:     scripts/install_opencode.sh   (then: scripts/doctor.sh)"
echo "  4. Start the backend:    uvicorn api.main:app --port 8000"
echo "  5. Start the frontend:   cd studio && npm run dev"
echo "  6. Open the studio:      http://localhost:3001"
echo
echo "Full guide: docs/SETUP.md"
