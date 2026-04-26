#!/usr/bin/env bash
# AgentFlow local setup — Linux / macOS / WSL.
#
# Creates a Python venv, installs deps, installs playwright chromium,
# copies .env.example → .env if not present, and prints next steps.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

echo "AgentFlow setup"
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

# 2. Virtualenv
if [ ! -d "venv" ]; then
    echo "Creating virtualenv at ./venv ..."
    "${PY}" -m venv venv
else
    echo "Virtualenv already exists at ./venv"
fi

# shellcheck disable=SC1091
source venv/bin/activate
PY_VENV="$(command -v python)"
echo "Activated: ${PY_VENV}"

# 3. Python deps
echo "Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# Optional embeddings (heavy — pulls PyTorch). Off by default; opt in with
# AGENTFLOW_WITH_EMBEDDINGS=1.
if [ "${AGENTFLOW_WITH_EMBEDDINGS:-0}" = "1" ]; then
    echo "Installing optional embedding dependencies (this is heavy)..."
    pip install --quiet -r requirements-optional.txt
fi

# 4. Playwright browsers (skippable)
if [ "${AGENTFLOW_SKIP_PLAYWRIGHT:-0}" != "1" ]; then
    echo "Installing Playwright chromium (set AGENTFLOW_SKIP_PLAYWRIGHT=1 to skip)..."
    python -m playwright install chromium || {
        echo "WARN: playwright install failed — you can re-run later if you need visual_checker"
    }
fi

# 5. Studio (optional)
if command -v npm >/dev/null 2>&1; then
    if [ "${AGENTFLOW_SKIP_NPM:-0}" != "1" ]; then
        echo "Installing studio dependencies..."
        (cd studio && npm install --silent --no-audit --no-fund)
    fi
else
    echo "INFO: npm not found — skipping studio. Install Node 18+ to run the frontend."
fi

# 6. .env
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "Created .env from .env.example"
    else
        cat > .env <<'EOF'
ANTHROPIC_API_KEY=
AGENTFLOW_API_TOKEN=
AGENTFLOW_MODE=dev
EOF
        echo "Created .env (no .env.example found)"
    fi
else
    echo ".env already exists — leaving alone"
fi

echo
echo "✓ Setup complete."
echo
echo "Next steps:"
echo "  1. Edit .env and add your ANTHROPIC_API_KEY"
echo "  2. Activate the venv:    source venv/bin/activate"
echo "  3. Start the backend:    uvicorn api.main:app --reload --port 8000"
echo "  4. Start the frontend:   cd studio && npm run dev"
echo "  5. Open the studio:      http://localhost:3000"
echo
echo "Or run the full stack via Docker:"
echo "  docker compose up --build"
