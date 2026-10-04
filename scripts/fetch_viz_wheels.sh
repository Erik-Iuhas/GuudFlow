#!/usr/bin/env bash
# Download the pinned chart-stack wheels for the optional agent-opencode-viz image
# (plan §5.10). `docker build` for that image has no network, so the wheels are fetched
# here on the host and COPYed in. Output goes to a gitignored directory:
#   .tools/viz-wheels/   (override with the first argument or GUUDFLOW_VIZ_WHEELS_DIR)
#
# The image's python is 3.11 on x86_64 (Debian bookworm), so we ask pip for exactly
# those wheels regardless of the host interpreter. Never commit the wheels.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-${GUUDFLOW_VIZ_WHEELS_DIR:-$ROOT/.tools/viz-wheels}}"
REQS="$ROOT/sandbox/viz-requirements.txt"

mkdir -p "$DEST"
rm -f "$DEST"/*.whl
python3 -m pip download \
    --only-binary=:all: \
    --python-version 3.11 --implementation cp \
    --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 \
    --platform manylinux_2_24_x86_64 --platform manylinux_2_27_x86_64 \
    --platform manylinux_2_28_x86_64 --platform manylinux_2_5_x86_64 \
    --platform manylinux1_x86_64 \
    -d "$DEST" -r "$REQS"
echo "viz wheels in $DEST ($(du -sh "$DEST" | cut -f1))"
