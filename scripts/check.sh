#!/usr/bin/env bash
# Run the project's checks.
#
#   scripts/check.sh            # fast: backend tests (no Docker tests) + Studio typecheck and tests
#   scripts/check.sh backend    # only pytest
#   scripts/check.sh studio     # only Studio typecheck + vitest
#   scripts/check.sh escape     # sandbox escape suite (needs Docker and root for iptables)
#
# Extra pytest arguments go after `--`:  scripts/check.sh backend -- -k plan -x
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"   # where the uv installer puts uv
[ -f venv/bin/activate ] && source venv/bin/activate
what="${1:-all}"; [ $# -gt 0 ] && shift
[ "${1:-}" = "--" ] && shift

PYV="python"; [ -x venv/bin/python ] && PYV="venv/bin/python"
backend() { GUUDFLOW_SKIP_DOCKER_TESTS=1 "$PYV" -m pytest -q "$@"; }
studio()  { (cd studio && npm run typecheck && npm test); }
escape()  { "$PYV" -m pytest -q tests/harness/test_escape_suite.py tests/harness/test_run_docker.py "$@"; }

case "$what" in
  all)     backend "$@"; studio ;;
  backend) backend "$@" ;;
  studio)  studio ;;
  escape)  escape "$@" ;;
  *) echo "usage: scripts/check.sh [all|backend|studio|escape] [-- pytest args]" >&2; exit 2 ;;
esac
