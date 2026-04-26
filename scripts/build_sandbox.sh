#!/usr/bin/env bash
# Build the AgentFlow sandbox image.
#
# Usage:
#   ./scripts/build_sandbox.sh                  # tags as agentflow-sandbox:latest
#   ./scripts/build_sandbox.sh my-org/my-name   # custom tag
set -euo pipefail

TAG="${1:-agentflow-sandbox:latest}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "Building sandbox image as ${TAG}"
docker build -t "${TAG}" "${HERE}/sandbox"

echo
echo "Smoke test:"
docker run --rm "${TAG}" python -c "print('python ok')"
docker run --rm "${TAG}" bash -lc "echo bash ok"

echo
echo "Done. To use a custom tag in the worker, set:"
echo "  export AGENTFLOW_SANDBOX_IMAGE=${TAG}"
echo "(then update worker/docker_manager.DEFAULT_IMAGE or read this env)"
