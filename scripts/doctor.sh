#!/usr/bin/env bash
# Check that this machine is ready to run GuudFlow, and say what to fix.
#
#   scripts/doctor.sh            # human-readable checklist; exit 1 if a required item fails
#
# [ok] ready   [!!] optional or advisory   [XX] required and missing
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"   # where the uv installer puts uv

FAIL=0
ok()   { printf '  \033[32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m[!!]\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m[XX]\033[0m %s\n' "$*"; FAIL=1; }
have() { command -v "$1" >/dev/null 2>&1; }
env_value() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'"; }

echo "Tools"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  bad "Python 3.11+ not found (https://www.python.org/downloads/ or: uv python install 3.11)"
elif "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  ok "Python $("$PY" --version 2>&1 | awk '{print $2}')"
else
  bad "Python is $("$PY" --version 2>&1 | awk '{print $2}'); 3.11+ is required"
fi
have git && ok "git $(git --version | awk '{print $3}')" || bad "git not found"
if have uv; then ok "uv $(uv --version | awk '{print $2}') (installs the Python packages)"
else bad "uv not found (curl -LsSf https://astral.sh/uv/install.sh | sh)"; fi
if have node && have npm; then
  major="$(node -p 'process.versions.node.split(".")[0]')"
  [ "$major" -ge 18 ] && ok "Node $(node --version) / npm $(npm --version)" \
    || bad "Node $(node --version) is too old; 18+ is required for Studio"
else
  warn "Node/npm not found; needed to build or develop Studio (not for Docker Compose)"
fi

echo "Agent sandboxes"
if have docker && docker info >/dev/null 2>&1; then
  osname="$(docker info --format '{{.OperatingSystem}}' 2>/dev/null)"
  case "$osname" in
    *"Docker Desktop"*) warn "Docker Desktop cannot host GuudFlow sandboxes; use Docker Engine (WSL2 on Windows) or GUUDFLOW_SANDBOX=host" ;;
    *) ok "Docker Engine reachable ($osname)" ;;
  esac
  docker image inspect guudflow:latest >/dev/null 2>&1 \
    && ok "image guudflow:latest built (Compose)" \
    || warn "image guudflow:latest not built yet (docker compose build)"
else
  warn "Docker not reachable; agents would run in host mode (GUUDFLOW_SANDBOX=host, trusted work only)"
fi
[ -x .tools/opencode ] && ok "opencode binary at .tools/opencode" \
  || warn "opencode not installed locally (scripts/install_opencode.sh); Compose bundles it"

echo "Project"
[ -d venv ] && ok "Python environment ./venv" || warn "no ./venv yet (scripts/setup.sh)"
[ -d studio/node_modules ] && ok "Studio packages installed" || warn "studio/node_modules missing (scripts/setup.sh)"
[ -f studio/dist/index.html ] && ok "Studio built (studio/dist)" \
  || warn "Studio not built (scripts/build.sh studio); the dev server on :3001 works without it"

echo "Configuration"
if [ -f .env ]; then
  ok ".env exists"
  [ -n "$(env_value GUUDFLOW_API_TOKEN)" ] && ok "GUUDFLOW_API_TOKEN set" \
    || bad "GUUDFLOW_API_TOKEN is empty in .env (any long random string: openssl rand -hex 24)"
  if [ -n "$(env_value ANTHROPIC_API_KEY)" ] || [ -n "${ANTHROPIC_API_KEY:-}" ]; then ok "ANTHROPIC_API_KEY set"
  else warn "no ANTHROPIC_API_KEY; paste a key in Studio (Settings → Models & providers) after starting"; fi
else
  bad ".env missing (cp .env.example .env)"
fi
if have ss && ss -ltn 2>/dev/null | grep -q ':8000 '; then
  warn "port 8000 is already in use (GUUDFLOW_PORT / --port to change)"
else
  ok "port 8000 free"
fi

echo
[ "$FAIL" -eq 0 ] && echo "Ready." || echo "Fix the [XX] items above, then re-run scripts/doctor.sh."
exit "$FAIL"
