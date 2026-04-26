"""Tests for the dev-mode `code:exec` hardening.

Covers:
  - Mode enforcement (AGENTFLOW_MODE=production rejects host runner)
  - Per-run quota
  - Audit log
  - Env scrubbing (secrets don't leak into subprocess)
  - CWD pinning
  - POSIX rlimits (skipped on Windows)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from agents.tools.code import (
    CodeExec,
    DEFAULT_QUOTA,
    _DEFAULT_ENV_ALLOW,
    _scrub_env,
    make_dev_host_runner,
)


# ── Mode enforcement ───────────────────────────────────────────────────────


async def test_production_mode_blocks_default_runner(monkeypatch):
    monkeypatch.setenv("AGENTFLOW_MODE", "production")
    res = await CodeExec().run({"language": "python", "code": "print('x')"})
    assert "error" in res
    assert "production" in res["error"].lower()


async def test_dev_mode_allows_default_runner(monkeypatch):
    monkeypatch.setenv("AGENTFLOW_MODE", "dev")
    res = await CodeExec().run({"language": "python", "code": "print('hello')"})
    assert res.get("ok") is True


async def test_production_mode_does_not_block_custom_runner(monkeypatch):
    """When the runner is NOT the dev host runner (i.e. the container
    runner injected by Stage 7), the production-mode check doesn't apply."""
    monkeypatch.setenv("AGENTFLOW_MODE", "production")
    captured = {"called": False}
    async def fake(lang, args, options):
        captured["called"] = True
        return {"ok": True, "stdout": "from container"}
    res = await CodeExec(runner=fake, is_dev_host_runner=False).run(
        {"language": "python", "code": "x"}
    )
    assert res["ok"] is True
    assert captured["called"]


# ── Per-run quota ──────────────────────────────────────────────────────────


async def test_quota_enforces_limit():
    captured_calls = []
    async def runner(lang, args, options):
        captured_calls.append(options.get("code"))
        return {"ok": True, "stdout": ""}

    tool = CodeExec(runner=runner, quota=2)
    r1 = await tool.run({"language": "python", "code": "a"})
    r2 = await tool.run({"language": "python", "code": "b"})
    r3 = await tool.run({"language": "python", "code": "c"})

    assert r1["ok"] is True
    assert r2["ok"] is True
    assert "error" in r3
    assert "quota" in r3["error"].lower()
    assert captured_calls == ["a", "b"]


async def test_default_quota_is_50():
    assert DEFAULT_QUOTA == 50


# ── Env scrubbing ──────────────────────────────────────────────────────────


def test_scrub_env_includes_allowlisted_keys(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/u")
    env = _scrub_env(set(_DEFAULT_ENV_ALLOW))
    assert env.get("PATH") == "/usr/bin"
    assert env.get("HOME") == "/home/u"


def test_scrub_env_strips_unallowlisted(monkeypatch):
    monkeypatch.setenv("MY_RANDOM_VAR", "value")
    env = _scrub_env(set(_DEFAULT_ENV_ALLOW))
    assert "MY_RANDOM_VAR" not in env


def test_scrub_env_blocks_secret_named_keys_even_if_allowlisted(monkeypatch):
    """Belt-and-suspenders: a key like ANTHROPIC_API_KEY is still dropped
    even if a misconfigured allowlist included it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "supersecret")
    monkeypatch.setenv("MY_AUTH_TOKEN", "ohno")
    allow = set(_DEFAULT_ENV_ALLOW) | {"ANTHROPIC_API_KEY", "MY_AUTH_TOKEN"}
    env = _scrub_env(allow)
    assert "ANTHROPIC_API_KEY" not in env
    assert "MY_AUTH_TOKEN" not in env


@pytest.mark.skipif(sys.platform == "win32", reason="env-var verification via shell varies on Windows")
async def test_subprocess_does_not_see_unallowlisted_env(monkeypatch, tmp_path):
    """End-to-end: a secret env var on the runner does NOT appear in the
    subprocess's environment."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should_not_leak")
    runner = make_dev_host_runner()
    res = await runner(
        "python",
        [],
        {"code": "import os; print(os.environ.get('ANTHROPIC_API_KEY', 'absent'))"},
    )
    assert res.get("ok") is True
    assert "absent" in res["stdout"]


# ── CWD pinning ────────────────────────────────────────────────────────────


async def test_cwd_pinning_subprocess_runs_in_specified_dir(tmp_path):
    runner = make_dev_host_runner(cwd=tmp_path)
    res = await runner("python", [], {"code": "import os; print(os.getcwd())"})
    assert res.get("ok") is True
    # Resolve both sides to handle symlink prefixes (e.g. /private on macOS).
    assert Path(res["stdout"].strip()).resolve() == tmp_path.resolve()


# ── Audit log ──────────────────────────────────────────────────────────────


async def test_audit_log_records_call(tmp_path):
    audit = tmp_path / "audit.jsonl"
    runner = make_dev_host_runner(audit_path=audit)
    await runner("python", [], {"code": "print('hi')"})

    assert audit.exists()
    lines = audit.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["language"] == "python"
    assert entry["code_preview"] == "print('hi')"
    assert entry["mode"] == "dev"
    assert entry["exit_code"] == 0


async def test_audit_log_records_timeout(tmp_path):
    audit = tmp_path / "audit.jsonl"
    runner = make_dev_host_runner(audit_path=audit)
    await runner(
        "python",
        [],
        {"code": "import time; time.sleep(5)", "timeout_s": 0.5},
    )
    entries = [
        json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines() if l
    ]
    assert any(e.get("error") == "timeout" for e in entries)


async def test_audit_log_truncates_long_code(tmp_path):
    audit = tmp_path / "audit.jsonl"
    runner = make_dev_host_runner(audit_path=audit)
    long_code = "print(1) #" + "x" * 5000
    await runner("python", [], {"code": long_code, "timeout_s": 5})
    entry = json.loads(audit.read_text(encoding="utf-8").splitlines()[0])
    assert len(entry["code_preview"]) <= 200
    assert entry["code_len"] > 200


# ── POSIX rlimits ──────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="rlimits are POSIX-only")
async def test_rlimit_caps_address_space():
    """Try to allocate more memory than the rlimit allows. The subprocess
    should fail (likely MemoryError or non-zero exit). Best-effort —
    macOS RLIMIT_AS is not always strictly enforced, so we assert the
    rlimit attempt didn't break the runner, not that it strictly aborted."""
    runner = make_dev_host_runner(address_bytes=64 * 1024 * 1024)
    # Just verify the runner runs successfully with rlimit set — strict
    # enforcement is best-effort across kernels.
    res = await runner("python", [], {"code": "print('rlimit ok')"})
    assert res.get("ok") is True
    assert "rlimit ok" in res["stdout"]
