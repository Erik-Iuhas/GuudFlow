#!/usr/bin/env bash
# Build the pieces of GuudFlow.
#
#   scripts/build.sh studio     # Studio -> studio/dist (served by the API when GUUDFLOW_STUDIO_DIST is set)
#   scripts/build.sh opencode   # the pinned opencode binary -> .tools/opencode
#   scripts/build.sh sandbox    # the agent sandbox image (needs .tools/opencode and Docker)
#   scripts/build.sh docker     # the all-in-one image used by docker compose
#   scripts/build.sh all        # studio + opencode + docker
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

studio()   { (cd studio && npm ci --no-audit --no-fund && npm run build); }
opencode() { scripts/install_opencode.sh; }
sandbox()  { scripts/build_sandbox.sh; }
docker_()  { docker compose build; }

case "${1:-all}" in
  studio) studio ;;
  opencode) opencode ;;
  sandbox) sandbox ;;
  docker) docker_ ;;
  all) studio; opencode; docker_ ;;
  *) echo "usage: scripts/build.sh [studio|opencode|sandbox|docker|all]" >&2; exit 2 ;;
esac
