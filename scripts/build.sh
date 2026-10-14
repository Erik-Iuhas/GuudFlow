#!/usr/bin/env bash
# Build the pieces of GuudFlow.
#
#   scripts/build.sh studio     # Studio -> studio/dist (served by the API when GUUDFLOW_STUDIO_DIST is set)
#   scripts/build.sh opencode   # the pinned opencode binary -> .tools/opencode
#   scripts/build.sh sandbox    # the agent sandbox image (needs .tools/opencode and Docker)
#   scripts/build.sh docker     # the all-in-one image for docker compose (builds its own Studio and
#                               # opencode inside; needs nothing from the two above)
#   scripts/build.sh native     # studio + opencode: everything the Python (uv) path needs
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"   # where the uv installer puts uv

studio()   { (cd studio && npm ci --no-audit --no-fund && npm run build); }
opencode() { scripts/install_opencode.sh; }
sandbox()  { scripts/build_sandbox.sh; }
docker_()  { docker compose build; }

case "${1:-native}" in
  studio) studio ;;
  opencode) opencode ;;
  sandbox) sandbox ;;
  docker) docker_ ;;
  native|all) studio; opencode ;;
  *) echo "usage: scripts/build.sh [studio|opencode|sandbox|docker|native]" >&2; exit 2 ;;
esac
