#!/usr/bin/env bash
# Offline quality gate for a finished run (plan S14).
#
# Usage: scripts/run_quality_gate.sh RUN_DIR [PROJECT] [extra args, e.g. --json]
#
#   RUN_DIR   a finished run directory (board.jsonl, tasks.jsonl, llm_costs.jsonl,
#             workspace/working/ = the merged repo)
#   PROJECT   optional example project id whose functional checks should run:
#             algorithm-with-benchmarks, api-client-library, bug-hunt-and-fix,
#             cli-tool-from-spec, codebase-wiki, fullstack-web-app
#
# Runs the generic run-health checks (plus the project's functional checks when
# PROJECT is given) with no network and no model, prints the report table, and
# exits 0 when the gate passed, 1 when it failed, 2 on a usage error.
#
# Environment: PYTHON (interpreter, default python3),
#              GUUDFLOW_GATE_TIMEOUT (seconds per subprocess in the merged tree, default 120).
set -euo pipefail

if [[ $# -lt 1 ]]; then
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
fi
if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$1"
shift
ARGS=()
if [[ $# -ge 1 && "$1" != -* ]]; then
  ARGS+=(--project "$1")
  shift
fi

cd "$REPO_ROOT"
exec "${PYTHON:-python3}" -m tests.quality "$RUN_DIR" ${ARGS[@]+"${ARGS[@]}"} "$@"
