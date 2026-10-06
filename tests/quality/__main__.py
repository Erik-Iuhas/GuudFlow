"""CLI: `python -m tests.quality RUN_DIR [--project ID] [--json]`.

Prints the gate report for a finished run directory and exits 0 when every check
passed (skipped checks do not count against it), 1 otherwise, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tests.quality.gate import run_gate


def main(argv: list[str] | None = None) -> int:
    """Run the gate for the arguments in `argv` and return the process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m tests.quality",
        description="Offline quality gate for a finished run directory.")
    parser.add_argument("run_dir", type=Path, help="the run directory (holds board.jsonl and workspace/)")
    parser.add_argument("--project", help="example project id (e.g. api-client-library) to also run its functional checks")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    report = run_gate(args.run_dir, args.project)
    print(json.dumps(report.to_dict(), indent=2) if args.json else report.render())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
