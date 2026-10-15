"""Which opencode binary GuudFlow starts, and refusing a server whose config did not apply."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.git_workspace import GitWorkspace
from core.harness import server
from core.harness.git_roles import RoleClones
from core.harness.opencode_client import OpencodeClient, output_format, text_part
from core.harness.results import AgentResult, result_json_schema
from core.harness.server import OpencodeNotInstalled, OpencodeServer, find_opencode_binary
from tests.harness.conftest import init_repo, locked_config, requires_opencode
from tests.harness.fake_llm import FakeLLM, ToolCall

posix_only = pytest.mark.skipif(os.name == "nt", reason="the Linux lookup")


def _install(tmp_path: Path, *files: str) -> Path:
    """A fake .tools/opencode/node_modules holding `files`; returns `.bin/opencode`."""
    modules = tmp_path / "node_modules"
    for rel in (".bin/opencode", *files):
        (modules / rel).parent.mkdir(parents=True, exist_ok=True)
        (modules / rel).write_text("")
    return modules / ".bin" / "opencode"


WINDOWS_NPM = ("opencode-ai/bin/opencode.exe", "opencode-windows-x64/bin/opencode.exe")


@posix_only
def test_linux_uses_the_platform_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCODE_BIN", raising=False)
    monkeypatch.setattr(server, "_DEFAULT_BIN", _install(tmp_path, *WINDOWS_NPM,
                                                         "opencode-linux-x64/bin/opencode"))
    assert find_opencode_binary() == tmp_path / "node_modules/opencode-linux-x64/bin/opencode"


@posix_only
def test_windows_npm_install_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """WSL on a checkout set up by Windows npm: the shim would start opencode.exe through
    interop, a Windows process without GuudFlow's config that WSL cannot reach."""
    monkeypatch.delenv("OPENCODE_BIN", raising=False)
    monkeypatch.setattr(server, "_DEFAULT_BIN", _install(tmp_path, *WINDOWS_NPM))
    with pytest.raises(OpencodeNotInstalled, match="Windows opencode"):
        find_opencode_binary()


@posix_only
def test_other_posix_installs_use_the_npm_bin(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCODE_BIN", raising=False)
    shim = _install(tmp_path, "opencode-darwin-arm64/bin/opencode")
    monkeypatch.setattr(server, "_DEFAULT_BIN", shim)
    assert find_opencode_binary() == shim


@requires_opencode
async def test_server_without_its_config_is_refused(tmp_path: Path) -> None:
    """If the config never reaches the process, opencode offers its own hosted models;
    such a server is stopped before it can get a prompt."""
    srv = OpencodeServer(config={"$schema": "https://opencode.ai/config.json",
                                 "autoupdate": False, "share": "disabled",
                                 "enabled_providers": ["guudflow-test"]},
                         home=tmp_path / "home", workdir=init_repo(tmp_path / "work"),
                         extra_env={"OPENCODE_CONFIG_CONTENT": "{}"})
    with pytest.raises(RuntimeError, match=r"providers outside GuudFlow's .*'opencode'"):
        await srv.start()
    assert srv._proc is None


@requires_opencode
async def test_a_role_nested_in_another_repo_cannot_read_outside_its_work(
        tmp_path: Path, fake_llm: FakeLLM) -> None:
    """Host mode keeps runs under GuudFlow's own checkout (data/runs/...). The role's work dir
    has no .git, so opencode took the enclosing repo as its project and allowed reads of
    everything in it (.env, data/secrets). Its boundary must be the work dir."""
    outer = init_repo(tmp_path / "guudflow")
    (outer / "secret.txt").write_text("TOPSECRET-42\n")
    clones = RoleClones(GitWorkspace(init_repo(tmp_path / "guudflow/data/runs/r/repo")),
                        tmp_path / "guudflow/data/runs/r/roles")
    work = clones.ensure("dev", "Dev")
    fake_llm.script(ToolCall("read", {"filePath": str(outer / "secret.txt")}),
                    ToolCall("StructuredOutput", {"status": "done", "summary": "Read it.",
                                                  "files_changed": []}))
    async with OpencodeServer(config=locked_config(fake_llm), home=tmp_path / "home",
                              workdir=work) as srv:
        client = OpencodeClient(srv.base_url, str(work), password=srv.password)
        try:
            session = await client.create_session()
            await client.prompt(session["id"], [text_part("go")],
                                format=output_format(result_json_schema(AgentResult)))
        finally:
            await client.aclose()
    seen = json.dumps([r["messages"] for r in fake_llm.tool_requests()])
    assert len(fake_llm.tool_requests()) >= 2  # the model did get the read's result back
    assert "TOPSECRET-42" not in seen


@requires_opencode
async def test_server_with_its_config_offers_only_guudflow_providers(tmp_path: Path) -> None:
    srv = OpencodeServer(config={"$schema": "https://opencode.ai/config.json",
                                 "autoupdate": False, "share": "disabled",
                                 "enabled_providers": ["guudflow-test"]},
                         home=tmp_path / "home", workdir=init_repo(tmp_path / "work"))
    async with srv:
        assert srv._proc is not None
