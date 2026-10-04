"""Fixtures that run the real pinned opencode binary against the fake LLM.

Tests that need opencode use `opencode_env`. They are skipped (not failed)
when the binary is not installed; run scripts/install_opencode.sh to enable.
"""

from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from core.harness.opencode_client import OpencodeClient
from core.harness.server import OpencodeServer, opencode_available
from tests.harness.fake_llm import FakeLLM, fake_provider_config

requires_opencode = pytest.mark.skipif(
    not opencode_available(), reason="opencode not installed (scripts/install_opencode.sh)"
)

# Deny-all base from plan §5.1, plus what the contract tests need.
LOCKED_PERMISSION: dict[str, Any] = {
    "*": "deny",
    "StructuredOutput": "allow",
    "read": "allow", "glob": "allow", "grep": "allow", "list": "allow",
    "edit": "allow",
    "bash": {"*": "deny", "git status*": "allow", "echo *": "allow"},
    "external_directory": "deny",
    "webfetch": "deny", "websearch": "deny",
    "task": "deny", "question": "deny", "doom_loop": "deny",
}


def locked_config(fake: FakeLLM, permission: dict[str, Any] | None = None,
                  **extra: Any) -> dict[str, Any]:
    """An opencode config in the shape config_gen produces, pointed at `fake`."""
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "share": "disabled",
        "autoupdate": False,
        "permission": permission if permission is not None else LOCKED_PERMISSION,
        **fake_provider_config(fake.base_url),
    }
    config.update(extra)
    return config


def init_repo(path: Path) -> Path:
    """Create a git repo with one commit at `path`."""
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(a, cwd=path, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "test@guudflow.local")
    run("git", "config", "user.name", "test")
    (path / "README.md").write_text("fixture\n")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "init")
    return path


@dataclass
class OpencodeEnv:
    """A running opencode server, its client, the fake LLM and the work dir."""

    server: OpencodeServer
    client: OpencodeClient
    fake: FakeLLM
    workdir: Path


@pytest.fixture
def fake_llm() -> Iterator[FakeLLM]:
    """A started FakeLLM, stopped after the test."""
    with FakeLLM() as fake:
        yield fake


@pytest.fixture
async def opencode_env(tmp_path: Path, fake_llm: FakeLLM,
                       request: pytest.FixtureRequest) -> AsyncIterator[OpencodeEnv]:
    """Boot a locked opencode server against the fake LLM.

    Override the config with `@pytest.mark.opencode_config(permission=..., **extra)`.
    """
    if not opencode_available():
        pytest.skip("opencode not installed (scripts/install_opencode.sh)")
    marker = request.node.get_closest_marker("opencode_config")
    kwargs = dict(marker.kwargs) if marker else {}
    config = locked_config(fake_llm, **kwargs)
    workdir = init_repo(tmp_path / "work")
    server = OpencodeServer(config=config, home=tmp_path / "home", workdir=workdir)
    await server.start()
    client = OpencodeClient(server.base_url, str(workdir), password=server.password)
    try:
        yield OpencodeEnv(server=server, client=client, fake=fake_llm, workdir=workdir)
    finally:
        await client.aclose()
        await server.stop()


def pytest_configure(config: pytest.Config) -> None:
    """Register the markers used by harness tests."""
    config.addinivalue_line("markers", "opencode_config(**kwargs): override the locked config")
