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
if command -v npm >/dev/null 2>&1; then
  [ -f package.json ] || npm init -y >/dev/null
  npm install --no-audit --no-fund --save-exact "opencode-ai@${VERSION}"
fi
[ "$(uname -s)" = Linux ] || { ./node_modules/.bin/opencode --version; exit 0; }

# Linux runs the platform package's own binary (core/harness/server.py). WSL often has no Linux
# npm, and a checkout shared with Windows may hold Windows npm's install (opencode.exe), so the
# package is fetched from the registry when it is missing, checked against its sha512.
case "$(uname -m)" in
  x86_64) PKG=opencode-linux-x64 ;;
  aarch64|arm64) PKG=opencode-linux-arm64 ;;
  *) echo "ERROR: no opencode build for $(uname -m)" >&2; exit 1 ;;
esac
if [ ! -x "node_modules/$PKG/bin/opencode" ]; then
  REG="${GUUDFLOW_NPM_REGISTRY:-https://registry.npmjs.org}"
  META="$(curl -fsSL "${REG%/}/$PKG/$VERSION")"
  TGZ="$(printf '%s' "$META" | python3 -c 'import json,sys; print(json.load(sys.stdin)["dist"]["tarball"])')"
  SRI="$(printf '%s' "$META" | python3 -c 'import json,sys; print(json.load(sys.stdin)["dist"]["integrity"])')"
  TMP="$(mktemp)"
  trap 'rm -f "$TMP"' EXIT
  echo "Fetching $PKG@$VERSION"
  curl -fsSL "$TGZ" -o "$TMP"
  python3 - "$TMP" "$SRI" <<'EOF'
import base64, hashlib, sys
algo, want = sys.argv[2].split("-", 1)
got = base64.b64encode(hashlib.new(algo, open(sys.argv[1], "rb").read()).digest()).decode()
sys.exit(0 if got == want else f"ERROR: {sys.argv[1]} does not match its {algo} integrity")
EOF
  rm -rf "node_modules/$PKG"
  mkdir -p "node_modules/$PKG"
  tar -xzf "$TMP" -C "node_modules/$PKG" --strip-components=1
  chmod +x "node_modules/$PKG/bin/opencode"
fi
"node_modules/$PKG/bin/opencode" --version
