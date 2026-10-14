#!/usr/bin/env bash
# Download the prebuilt Studio that the "Studio bundle" GitHub Action publishes, and unpack it.
#
#   bash scripts/run.sh studio-fetch [tag]     # default tag: studio-latest
#
# For a private repo set GITHUB_TOKEN (a token that can read the repo). Uses the proxy settings
# and GUUDFLOW_CA_BUNDLE like every other download. If this machine cannot reach GitHub either,
# download studio-dist.tar.gz from the repo's Releases page elsewhere and put it in prebuilt/.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p prebuilt
OUT=prebuilt/studio-dist.tar.gz
TAG="${1:-studio-latest}"
SLUG="${GUUDFLOW_REPO:-$(git remote get-url origin 2>/dev/null | sed -E 's#(\.git)?$##; s#.*[:/]([^/]+/[^/]+)$#\1#')}"
[ -n "$SLUG" ] || { echo "cannot tell the GitHub repo; set GUUDFLOW_REPO=owner/repo" >&2; exit 1; }
URL="https://github.com/${SLUG}/releases/download/${TAG}/studio-dist.tar.gz"
CA=(); [ -n "${GUUDFLOW_CA_BUNDLE:-}" ] && CA=(--cacert "$GUUDFLOW_CA_BUNDLE")
AUTH=(); [ -n "${GITHUB_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
echo "Downloading $URL"
if curl -fL --retry 4 "${CA[@]}" -o "$OUT" "$URL" 2>/dev/null; then
  :
elif [ -n "${GITHUB_TOKEN:-}" ]; then
  # Private repo: release assets come through the API with the token.
  echo "Public download failed; trying the GitHub API with GITHUB_TOKEN"
  API="https://api.github.com/repos/${SLUG}/releases/tags/${TAG}"
  ASSET="$(curl -fsSL "${CA[@]}" "${AUTH[@]}" "$API" | python3 -c 'import json,sys
try:
    print(next(a["url"] for a in json.load(sys.stdin)["assets"] if a["name"] == "studio-dist.tar.gz"))
except Exception:
    pass')"
  [ -n "$ASSET" ] || { echo "no studio-dist.tar.gz in release ${TAG} of ${SLUG} (has the Studio bundle action run?)" >&2; exit 1; }
  curl -fL --retry 4 "${CA[@]}" "${AUTH[@]}" -H "Accept: application/octet-stream" -o "$OUT" "$ASSET"
else
  echo "could not download ${URL}: the Studio bundle action may not have run yet, the repo may be private (set GITHUB_TOKEN), or GitHub is blocked here (download it elsewhere and put it in prebuilt/)" >&2
  rm -f "$OUT"
  exit 1
fi
bash scripts/studio_bundle.sh unpack "$OUT"
