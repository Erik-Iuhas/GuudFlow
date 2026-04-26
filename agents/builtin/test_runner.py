"""TestRunner — executes test commands and parses results.

DESIGN §31 (`code:test` is a convenience over `code:exec`). Stage 5
ships a thin wrapper that: invokes the test command, captures output,
detects pytest/jest result patterns, posts a structured signal.

Real test-runner is straightforward — most of the value is in detecting
"this output means the suite passed/failed" without the LLM having to
parse it itself.
"""

from __future__ import annotations

import re

from agents.base import BaseAgent


_PYTEST_FAILED = re.compile(r"(\d+)\s+failed", re.IGNORECASE)
_PYTEST_PASSED = re.compile(r"(\d+)\s+passed", re.IGNORECASE)
_PYTEST_SKIPPED = re.compile(r"(\d+)\s+skipped", re.IGNORECASE)
_JEST_RESULT = re.compile(
    r"Tests:\s+(?:(\d+) failed,\s*)?(?:(\d+) passed,\s*)?(?:(\d+) total)",
    re.IGNORECASE,
)


def parse_test_output(stdout: str, stderr: str = "") -> dict:
    """Best-effort parse — recognises pytest and jest summary lines."""
    combined = f"{stdout}\n{stderr}"

    # Jest first — its summary line is more structured.
    m = _JEST_RESULT.search(combined)
    if m:
        return {
            "framework": "jest",
            "failed": int(m.group(1) or 0),
            "passed": int(m.group(2) or 0),
            "total": int(m.group(3) or 0),
        }

    # Pytest — accept any of its summary keyword counts on a session line.
    is_pytest = "===" in combined or "test session" in combined.lower()
    failed = _PYTEST_FAILED.search(combined)
    passed = _PYTEST_PASSED.search(combined)
    skipped = _PYTEST_SKIPPED.search(combined)
    if is_pytest and (failed or passed or skipped):
        return {
            "framework": "pytest",
            "failed": int(failed.group(1)) if failed else 0,
            "passed": int(passed.group(1)) if passed else 0,
            "skipped": int(skipped.group(1)) if skipped else 0,
        }

    return {"framework": "unknown"}


class TestRunner(BaseAgent):
    """Wrapper that runs tests and posts a structured summary."""

    role: str = "test_runner"

    def __init__(self, command: str = "pytest -q", **kwargs):
        super().__init__(
            role=kwargs.pop("role", "test_runner"),
            description="Runs the project's test suite and reports a structured result.",
            **kwargs,
        )
        self.command = command

    async def run(self, workspace) -> str:
        result = await self.call_tool(workspace, "code-exec", {
            "language": "shell",
            "code": self.command,
            "timeout_s": 120,
        })
        if not result.get("ok"):
            return f"test run errored: {result.get('error', 'unknown')}"

        summary = parse_test_output(result.get("stdout", ""), result.get("stderr", ""))
        return (
            f"[{summary.get('framework', 'unknown')}] "
            f"passed={summary.get('passed', '?')} "
            f"failed={summary.get('failed', '?')} "
            f"skipped={summary.get('skipped', '?')}"
        )
