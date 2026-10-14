#!/usr/bin/env bash
# Install Studio's npm packages, tolerating proxies and mirrors that lack an exact version.
#
#   bash scripts/studio_deps.sh
#
# Tries, in order, stopping at the first that works:
#   1. npm ci                      the exact versions in package-lock.json
#   2. npm install                 any version package.json allows (a mirror may lack the exact one)
#   3. npm install --legacy-peer-deps
# Each with retries. GUUDFLOW_NPM_REGISTRY points npm at a company mirror (otherwise npm's own
# config applies, e.g. ~/.npmrc). If all fail, use a prebuilt Studio instead: no npm needed
# (put studio-dist.tar.gz in prebuilt/, see README "Quirks").
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/studio"
command -v npm >/dev/null 2>&1 || { echo "npm not found (Node 18+), or use a prebuilt Studio" >&2; exit 1; }

common=(--no-audit --no-fund --fetch-retries=5 --fetch-retry-mintimeout=20000
        --fetch-retry-maxtimeout=120000)
[ -n "${GUUDFLOW_NPM_REGISTRY:-}" ] && common+=(--registry "$GUUDFLOW_NPM_REGISTRY")

try() { echo "→ npm $*"; npm "$@" "${common[@]}"; }

try ci && exit 0
echo "WARN: exact versions from package-lock.json could not be installed; trying versions package.json allows" >&2
git -C "$ROOT" diff --quiet -- studio/package-lock.json 2>/dev/null && keep_lock=1 || keep_lock=0
try install && { [ "$keep_lock" = 1 ] && git -C "$ROOT" checkout -q -- studio/package-lock.json 2>/dev/null; exit 0; }
try install --legacy-peer-deps && { [ "$keep_lock" = 1 ] && git -C "$ROOT" checkout -q -- studio/package-lock.json 2>/dev/null; exit 0; }
cat >&2 <<'MSG'
ERROR: Studio's npm packages could not be installed.
  - Behind a company proxy or mirror: set GUUDFLOW_NPM_REGISTRY=<mirror url> (or registry= in ~/.npmrc)
    and, for a private certificate, GUUDFLOW_CA_BUNDLE.
  - Or skip npm: get a prebuilt Studio (studio-dist.tar.gz) from someone who can build it
    (bash scripts/run.sh studio-pack) or from the repo's CI artifacts, then:
      put it in prebuilt/ and run Prepare (or: bash scripts/run.sh studio-ensure)
MSG
exit 1
