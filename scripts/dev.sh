#!/usr/bin/env bash
# Launch (or relaunch) the GuudFlow dev stack.
# Kills anything on 8000/3000, rebuilds venv if it was made by Windows, then
# starts backend + frontend in the background with live-tailed logs.
#
# Usage:  ./scripts/dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

# ── helpers ────────────────────────────────────────────────────────────────
die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo -e "\033[36m$*\033[0m"; }
ok()   { echo -e "\033[32m$*\033[0m"; }
warn() { echo -e "\033[33m$*\033[0m"; }

kill_port() {
    local port=$1
    local pids
    pids=$(lsof -ti tcp:"${port}" 2>/dev/null || true)
    if [ -n "${pids}" ]; then
        echo "  killing pid(s) ${pids} on :${port}"
        kill -9 ${pids} 2>/dev/null || true
    fi
}

# ── stop existing servers ───────────────────────────────────────────────────
info "→ Stopping existing servers..."
kill_port 8000
kill_port 3000
sleep 0.3

# ── python / venv ──────────────────────────────────────────────────────────
PY="$(command -v python3 || command -v python || die 'python3 not found')"

# Detect a Windows-built venv (has Scripts/ but not bin/)
if [ -d venv ] && [ ! -f venv/bin/activate ]; then
    warn "  venv was built by Windows Python — rebuilding for Linux..."
    rm -rf venv
fi

if [ ! -d venv ]; then
    info "→ Creating venv with ${PY}..."
    "${PY}" -m venv venv
    source venv/bin/activate
    info "→ Installing Python deps..."
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
else
    source venv/bin/activate
fi

# ── .env sanity check ──────────────────────────────────────────────────────
if [ ! -f .env ]; then
    warn "  .env missing — copying from .env.example"
    cp .env.example .env 2>/dev/null || cat > .env <<'EOF'
ANTHROPIC_API_KEY=
GUUDFLOW_API_TOKEN=
GUUDFLOW_MODE=dev
EOF
fi

# ── frontend deps ──────────────────────────────────────────────────────────
if [ ! -d studio/node_modules ]; then
    info "→ Installing studio deps..."
    (cd studio && npm install --silent --no-audit --no-fund)
fi

# ── launch ─────────────────────────────────────────────────────────────────
LOG_BACK="${ROOT}/.dev-backend.log"
LOG_FRONT="${ROOT}/.dev-frontend.log"

info "→ Starting backend  → http://localhost:8000"
uvicorn api.main:app --reload --reload-dir api --reload-dir core --reload-dir agents --reload-dir worker --port 8000 > "${LOG_BACK}" 2>&1 &
BACK_PID=$!

info "→ Starting frontend → http://localhost:3000"
(cd studio && npm run dev) > "${LOG_FRONT}" 2>&1 &
FRONT_PID=$!

ok "
  Backend  pid ${BACK_PID}  →  http://localhost:8000
  Frontend pid ${FRONT_PID}  →  http://localhost:3000
  Logs: tail -f .dev-backend.log .dev-frontend.log

  Ctrl-C to stop both.
"

# ── tail logs + forward signals ────────────────────────────────────────────
cleanup() {
    echo ""
    warn "Shutting down..."
    kill "${BACK_PID}" "${FRONT_PID}" 2>/dev/null || true
    wait "${BACK_PID}" "${FRONT_PID}" 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM

tail -f "${LOG_BACK}" "${LOG_FRONT}" &
TAIL_PID=$!

wait "${BACK_PID}" "${FRONT_PID}"
kill "${TAIL_PID}" 2>/dev/null || true
