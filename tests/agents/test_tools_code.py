"""Tests for agents/tools/code.py.

Stage 4 ships only the dev-mode host runner. Container routing is Stage 7.
The CodeExec runner is injectable, so production wiring overrides without
modifying the tool.
"""

from __future__ import annotations

import sys

import pytest

from agents.tools.code import CodeExec


# ── Validation ─────────────────────────────────────────────────────────────


async def test_code_exec_invalid_language():
    res = await CodeExec().run({"language": "ruby", "code": "puts 1"})
    assert "error" in res


async def test_code_exec_missing_code():
    res = await CodeExec().run({"language": "python"})
    assert "error" in res


async def test_code_exec_empty_code():
    res = await CodeExec().run({"language": "python", "code": "   "})
    assert "error" in res


# ── Injected runner ─────────────────────────────────────────────────────────


async def test_code_exec_uses_injected_runner():
    captured = {}
    async def fake_runner(language: str, args: list[str], options: dict) -> dict:
        captured["lang"] = language
        captured["code"] = options.get("code")
        return {"ok": True, "exit_code": 0, "stdout": "fake", "stderr": ""}

    res = await CodeExec(runner=fake_runner).run({"language": "python", "code": "print('x')"})
    assert res["ok"] is True
    assert res["stdout"] == "fake"
    assert captured == {"lang": "python", "code": "print('x')"}


# ── Dev-mode host runner ────────────────────────────────────────────────────


async def test_code_exec_dev_python_runs():
    """Default dev runner executes Python on the host. Skipped if env unstable."""
    res = await CodeExec().run({"language": "python", "code": "print('hello')", "timeout_s": 10})
    assert res.get("ok") is True
    assert res["exit_code"] == 0
    assert "hello" in res["stdout"]
    assert res["dev_mode"] is True


async def test_code_exec_timeout(caplog):
    """Long-running code is killed. Use Python `time.sleep` for portability."""
    res = await CodeExec().run({
        "language": "python",
        "code": "import time; time.sleep(5)",
        "timeout_s": 0.5,
    })
    assert "error" in res
    assert "timeout" in res["error"].lower()
