#!/usr/bin/env bash
# The one Studio step every script uses (prepare, setup, dev): make sure Studio is available.
#
#   bash scripts/studio_ensure.sh           # a usable Studio: prebuilt, existing build, or npm packages
#   bash scripts/studio_ensure.sh --build   # same, but an npm route also builds studio/dist
#
# Order, stopping at the first that works:
#   1. an archive in prebuilt/ (studio-dist*.tar.gz or the artifact .zip) -> unpacked to studio/dist
#      (only when it is newer than the current build), npm is never run
#   2. an existing studio/dist                                          -> nothing to do
#   3. npm packages (scripts/studio_deps.sh), and with --build `npm run build`
# Exits 1 with instructions when none works.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
BUILD=0; [ "${1:-}" = "--build" ] && BUILD=1

ARCH="$(ls -t prebuilt/*.tar.gz prebuilt/*.tgz prebuilt/*.zip 2>/dev/null | grep -v "/python-wheels" | head -1)"  # any name: browsers rename downloads
if [ -n "$ARCH" ]; then
  if [ ! -f studio/dist/.unpacked ] || [ "$ARCH" -nt studio/dist/.unpacked ]; then
    bash scripts/studio_bundle.sh unpack "$ARCH" || exit 1
  fi
  echo "Studio: prebuilt from $ARCH (npm skipped)"; exit 0
fi
# npm packages count only when the dev server can run here (scripts/studio_dev_ok.sh)
source scripts/studio_dev_ok.sh
if [ -f studio/dist/index.html ] && ! studio_dev_ok; then
  echo "Studio: using the existing build in studio/dist (npm skipped)"; exit 0
fi
if studio_dev_ok && [ "$BUILD" = 0 ]; then
  echo "Studio: npm packages already installed"; exit 0
fi
if [ "${GUUDFLOW_SKIP_NPM:-0}" != "1" ] && command -v npm >/dev/null 2>&1 && bash scripts/studio_deps.sh; then
  if [ "$BUILD" = 1 ]; then (cd studio && npm run build) || exit 1; fi
  echo "Studio: npm packages installed$([ "$BUILD" = 1 ] && echo ', built to studio/dist')"; exit 0
fi
[ -f studio/dist/index.html ] && { echo "Studio: npm failed; keeping the existing studio/dist"; exit 0; }
echo "Studio: not available. Put studio-dist.tar.gz (or the GitHub artifact zip) in $ROOT/prebuilt/ and run this again." >&2
exit 1
