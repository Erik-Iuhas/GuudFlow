#!/usr/bin/env bash
# Move a built Studio between machines, so a machine that cannot reach npm needs no npm at all.
# Studio is static files: the same build works on Linux, macOS, WSL and Windows.
#
#   bash scripts/studio_bundle.sh pack [out.tar.gz]     # after a build: studio/dist -> archive
#   bash scripts/studio_bundle.sh unpack <file.tar.gz>  # archive -> studio/dist
#
# With studio/dist in place, the API serves Studio on :8000 (GUUDFLOW_STUDIO_DIST=studio/dist), and
# scripts/dev.sh uses it when the dev server's npm packages are missing.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
case "${1:-}" in
  pack)
    [ -f studio/dist/index.html ] || { echo "no studio/dist: run bash scripts/run.sh build studio first" >&2; exit 1; }
    out="${2:-studio-dist.tar.gz}"
    git rev-parse --short HEAD > studio/dist/BUILD_COMMIT 2>/dev/null || true
    tar -czf "$out" -C studio dist
    echo "wrote $out ($(du -h "$out" | cut -f1)); copy it over and run: bash scripts/run.sh studio-unpack $out" ;;
  unpack)
    file="${2:?usage: studio_bundle.sh unpack <file.tar.gz|artifact.zip>}"
    [ -f "$file" ] || { echo "$file not found" >&2; exit 1; }
    case "$file" in
      *.zip)  # the GitHub Actions artifact: a zip with studio-dist.tar.gz inside
        tmp="$(mktemp -d)"
        python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$file" "$tmp"
        inner="$(find "$tmp" -name '*.tar.gz' | head -1)"
        [ -n "$inner" ] || { rm -rf "$tmp"; echo "$file has no .tar.gz inside" >&2; exit 1; }
        mv "$inner" studio-dist.tar.gz; rm -rf "$tmp"; file=studio-dist.tar.gz
        echo "took studio-dist.tar.gz out of the artifact zip" ;;
    esac
    rm -rf studio/dist.new && mkdir -p studio/dist.new
    tar -xzf "$file" -C studio/dist.new
    src=studio/dist.new/dist; [ -d "$src" ] || src=studio/dist.new
    [ -f "$src/index.html" ] || { rm -rf studio/dist.new; echo "$file has no Studio build (index.html)" >&2; exit 1; }
    rm -rf studio/dist && mv "$src" studio/dist && rm -rf studio/dist.new
    built="$(cat studio/dist/BUILD_COMMIT 2>/dev/null || echo unknown)"
    here="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "Studio installed in studio/dist (built from $built; this checkout is $here)."
    [ "$built" = "$here" ] || echo "Note: built from a different commit; fine unless the API changed in between." ;;
  *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
