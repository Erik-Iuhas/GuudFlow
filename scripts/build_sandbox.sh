#!/usr/bin/env bash
# Build the GuudFlow sandbox image.
#
# Usage:
#   ./scripts/build_sandbox.sh                  # tags as guudflow-sandbox:latest
#   ./scripts/build_sandbox.sh my-org/my-name   # custom tag
set -euo pipefail

TAG="${1:-guudflow-sandbox:latest}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "Building sandbox image as ${TAG}"
docker build -t "${TAG}" "${HERE}/sandbox"

echo
echo "Smoke test:"
docker run --rm "${TAG}" python -c "print('python ok')"
docker run --rm "${TAG}" bash -lc "echo bash ok"

echo
echo "Done. To use a custom tag in the worker, set:"
echo "  export GUUDFLOW_SANDBOX_IMAGE=${TAG}"
echo "(then update worker/docker_manager.DEFAULT_IMAGE or read this env)"
