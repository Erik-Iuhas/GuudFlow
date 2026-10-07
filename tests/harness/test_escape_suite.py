"""O10: the security escape suite (plan §2.2, §5).

A scripted agent runs hostile code inside a real hardened sandbox; every
assertion is made on the host. Covers: writes outside /work (incl. shell
redirects, gotcha #4), read-only inputs and tool dir, the upstream key, internet,
DNS and metadata, host ports other than gateway/MCP, other sandboxes' opencode
servers, gateway misuse, MCP impersonation and ungranted tools, protected-path
edits (self-widening via opencode.json/.opencode), planted git config/hooks/filters,
and symlinks out of the repo.

Skipped when Docker or the opencode binary is unavailable (CI runs it in the
Docker job).
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from core.runner import AgentConfig
from core.scheduler import SchedulerStatus
from core.signals import SignalType
from tests.harness.fake_llm import FakeLLM, ToolCall
from tests.harness.test_run_docker import UPSTREAM_SECRET, _docker_ok, docker_runner, done

pytestmark = pytest.mark.skipif(not _docker_ok(), reason="docker or opencode unavailable")

PROBE = (Path(__file__).parent / "escape_probe.py").read_text()


class HostListener:
    """A TCP listener on every host interface; records any connection."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.5)
        self.port: int = self.sock.getsockname()[1]
        self.connections: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self.sock.accept()
                self.connections.append(addr[0])
                conn.close()
            except OSError:
                continue

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self.sock.close()


@pytest.fixture
def host_listener() -> Iterator[HostListener]:
    listener = HostListener()
    yield listener
    listener.close()


def _write(path: str, content: str) -> ToolCall:
    return ToolCall("write", {"filePath": path, "content": content})


def _bash(command: str) -> ToolCall:
    return ToolCall("bash", {"command": command, "description": "probe"})


def _failures(runner: Any) -> list[str]:
    return [f"{s.summary} {s.detail or ''}" for s in runner.workspace.board
            if s.type is SignalType.FAILED]


async def test_sandbox_contains_every_probe(tmp_path: Path, fake_llm: FakeLLM,
                                            host_listener: HostListener) -> None:
    peer_script = ("import json,socket,time\n"
                   "start=time.time()\n"
                   "time.sleep(45)\n"
                   "json.dump({'ip':socket.gethostbyname(socket.gethostname()),'start':start,'end':time.time()},"
                   "open('peer_out.json','w'))\n")
    fake_llm.route("Your turn: builder",
                   _write("escape_probe.py", PROBE),
                   _bash(f"python3 escape_probe.py {host_listener.port} probe_out.json"),
                   done("Ran the escape probe inside the sandbox and saved the results."))
    fake_llm.route("Your turn: peer",
                   _write("peer.py", peer_script),
                   _bash("python3 peer.py"),
                   done("Stayed up as the peer sandbox for the cross-sandbox probe."))
    run_root = tmp_path / "run"
    (run_root / "inputs").mkdir(parents=True)
    (run_root / "inputs" / "spec.md").write_text("# spec\n")
    runner = docker_runner(tmp_path, fake_llm, [AgentConfig(role="builder"),
                                                AgentConfig(role="peer")])
    result = await runner.run()
    assert result.status is SchedulerStatus.COMPLETE, _failures(runner)

    work = runner.git.working_dir
    r = json.loads((work / "probe_out.json").read_text())
    peer = json.loads((work / "peer_out.json").read_text())

    # Identity and kernel hardening.
    assert r["uid"] != 0
    assert int(r["cap_eff"], 16) == 0, r["cap_eff"]
    assert r["no_new_privs"] == "1"
    assert r["seccomp"] == "2"
    assert r["docker_sock"] is False

    # Filesystem: nothing outside /work is writable, redirects included (gotcha #4).
    for key in ("write_root", "write_etc", "write_usr_bin", "write_inputs", "write_tool_dir"):
        assert r[key] != "written", key
    assert r["redirect_root"] is True
    assert r["inputs_visible"] is True
    assert not any(p.startswith("/home") for p in r["mount_points"] if p != "/home/agent")
    assert (run_root / "inputs" / "spec.md").read_text() == "# spec\n"
    assert not (run_root / "inputs" / "pwned.md").exists()
    assert not list((run_root / "roles" / "builder" / "guudflow").glob("pwned*"))

    # The upstream provider key is nowhere the agent can read.
    assert r["secret_leaks"] == []
    assert UPSTREAM_SECRET not in json.dumps(r)

    # No internet, DNS or cloud metadata.
    for key in ("internet_ip", "internet_ip_80", "metadata"):
        assert r[key] != "open", key
    assert r["dns"] != "resolved"

    # Host: gateway and MCP are reachable, nothing else is.
    assert r["host_gateway_port"] == "open"
    assert r["host_mcp_port"] == "open"
    assert r["host_other_port"] != "open"
    assert r["host_ssh"] != "open"
    assert host_listener.connections == []

    # Other sandboxes: the peer was up during the scan, and unreachable.
    assert peer["ip"] != r["own_ip"]
    assert peer["ip"].rsplit(".", 1)[0] == r["own_ip"].rsplit(".", 1)[0]
    assert r["peers_4096"] == []

    # Gateway: only inference on the granted model with the virtual key.
    assert r["gw_other_model"] in (403,), r["gw_other_model"]
    assert r["gw_models_list"] in (403, 404, 405), r["gw_models_list"]
    assert r["gw_other_endpoint"] in (403, 404, 405), r["gw_other_endpoint"]
    assert r["gw_no_key"] == 401, r["gw_no_key"]
    assert r["gw_other_provider"] in (403, 404), r["gw_other_provider"]

    # MCP: identity comes from the token; ungranted tools are refused.
    assert r["mcp_no_token"]["status"] == 401
    assert r["mcp_forged_token"]["status"] == 401
    assert r["mcp_read_board"]["status"] == 200
    assert r["mcp_tools_list"] == sorted({"ask_human", "post_note", "read_board", "report_progress",
                                          "request_package", "submit_result", "changes",
                                          "history", "notebook"}), r["mcp_tools_list"]
    for key in ("mcp_web_search", "mcp_screenshot"):
        call = r[key]
        assert "is not available to role 'builder'" in call["body"], call
    web = [s for s in runner.workspace.board if s.type is SignalType.WEB_REQUEST]
    assert web == []


async def test_self_widening_and_planted_git_never_reach_main(tmp_path: Path,
                                                              fake_llm: FakeLLM) -> None:
    marker = tmp_path / "host-pwned"
    git_config = (f"[core]\n\tfsmonitor = touch {marker}-fsmonitor\n\thooksPath = .git/hooks\n"
                  f"[filter \"evil\"]\n\tclean = touch {marker}-filter\n")
    fake_llm.route("Your turn: builder",
                   _write("opencode.json", '{"permission": "allow"}'),
                   _write(".opencode/agent/builder.md", "---\npermission: allow\n---\n"),
                   _write(".git/config", git_config),
                   _write(".git/hooks/pre-commit", f"#!/bin/sh\ntouch {marker}-hook\n"),
                   _write(".gitattributes", "* filter=evil\n"),
                   _bash("python3 -c \"import os; os.chmod('.git/hooks/pre-commit', 0o755)\""),
                   _write("app.py", "x = 1\n"),
                   done("Widened my own permissions and planted git config."))
    runner = docker_runner(tmp_path, fake_llm, [AgentConfig(role="builder")])
    result = await runner.run()
    assert result.failed == ["builder"] and not result.completed
    assert any("protected paths" in f for f in _failures(runner)), _failures(runner)
    main = runner.git.working_dir
    for path in ("opencode.json", ".opencode", "app.py"):
        assert not (main / path).exists(), path
    assert not list(tmp_path.glob("host-pwned*")), "nothing planted in /work may run on the host"


async def test_symlink_out_of_the_repo_never_reaches_main(tmp_path: Path,
                                                          fake_llm: FakeLLM) -> None:
    fake_llm.route("Your turn: builder",
                   _bash("python3 -c \"import os; os.symlink('/etc/shadow', 'leak'); "
                         "os.symlink('../../../../', 'up')\""),
                   done("Linked files from outside the repo into the worktree."))
    runner = docker_runner(tmp_path, fake_llm, [AgentConfig(role="builder")])
    result = await runner.run()
    assert result.failed == ["builder"] and not result.completed
    assert any("symlinks pointing outside" in f for f in _failures(runner)), _failures(runner)
    main = runner.git.working_dir
    assert not (main / "leak").is_symlink() and not (main / "up").is_symlink()
