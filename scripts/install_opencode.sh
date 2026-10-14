#!/usr/bin/env bash
# Install the pinned opencode binary into .tools/opencode (git-ignored).
# The version must match OPENCODE_VERSION in core/harness/server.py.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/net_env.sh   # proxy, mirror and certificate settings from .env
VERSION="${OPENCODE_VERSION:-$(python3 -c 'import re,sys; print(re.search(r"OPENCODE_VERSION = \"([^\"]+)\"", open("core/harness/server.py").read()).group(1))')}"
DEST=".tools/opencode"
mkdir -p "$DEST"
cd "$DEST"
[ -f package.json ] || npm init -y >/dev/null
npm install --no-audit --no-fund --save-exact "opencode-ai@${VERSION}"
./node_modules/.bin/opencode --version
