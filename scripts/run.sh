#!/usr/bin/env bash
# One entry point for every task, run through bash so it works even when the scripts lost
# their execute bit (copied from a Windows disk, unpacked from a zip).
#
#   bash scripts/run.sh prepare    # detect this machine and set everything up (start here)
#   bash scripts/run.sh setup | dev [--no-reload] | stop | doctor | install-uv | install-opencode
#   bash scripts/run.sh build <studio|opencode|sandbox|docker|native>
#   bash scripts/run.sh check [all|backend|studio|escape]
#   bash scripts/run.sh api        # the API with the built Studio on :8000
#   bash scripts/run.sh studio-deps | studio-pack [out] | studio-unpack <file>   # npm trouble
#
# On Windows, VS Code runs scripts/run.ps1 instead, which forwards here through WSL (default)
# or uses the PowerShell scripts natively (GUUDFLOW_WINDOWS_MODE=native).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
chmod +x scripts/*.sh 2>/dev/null || true   # best effort: restore lost execute bits

cmd="${1:-help}"; [ $# -gt 0 ] && shift
case "$cmd" in
  prepare)          exec bash scripts/prepare.sh "$@" ;;
  setup)            exec bash scripts/setup.sh "$@" ;;
  dev)              exec bash scripts/dev.sh "$@" ;;
  stop)             exec bash scripts/stop.sh "$@" ;;
  doctor)           exec bash scripts/doctor.sh "$@" ;;
  install-uv)       exec bash scripts/install_uv.sh "$@" ;;
  install-opencode) exec bash scripts/install_opencode.sh "$@" ;;
  build)            exec bash scripts/build.sh "$@" ;;
  studio-deps)      exec bash scripts/studio_deps.sh "$@" ;;
  studio-pack)      exec bash scripts/studio_bundle.sh pack "$@" ;;
  studio-unpack)    exec bash scripts/studio_bundle.sh unpack "$@" ;;
  check)            exec bash scripts/check.sh "$@" ;;
  api)
    [ -f venv/bin/activate ] && source venv/bin/activate
    GUUDFLOW_STUDIO_DIST=studio/dist exec uvicorn api.main:app --port "${GUUDFLOW_PORT:-8000}" ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; [ "$cmd" = help ] || exit 2 ;;
esac
