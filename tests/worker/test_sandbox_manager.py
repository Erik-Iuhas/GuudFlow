"""Tests for worker/sandbox_manager.py.

Unit tests use a fake docker client (no daemon). Integration tests need a Docker
daemon: the container-hardening ones run a plain `node` image with the exact kwargs
the manager produces; the opencode ones build/use the real agent image.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from core.harness.capabilities import ContainerProfile
from core.harness.server import LOCKED_FLAGS, OPENCODE_VERSION
from worker.sandbox_manager import (
    DEFAULT_IMAGE,
    LABEL_ROLE,
    LABEL_RUN,
    OPENCODE_PORT,
    RunNetwork,
    Sandbox,
    SandboxError,
    SandboxManager,
    SandboxSpec,
)

PROFILE = ContainerProfile(
    use_container=True, read_only_root=True, allow_test_runners=False,
    host_may_reach_dev_server=False,
)
CONFIG = {"share": "disabled", "autoupdate": False, "permission": {"*": "deny"}}
NODE_IMAGE = "node:20-bookworm-slim"


class FakeClient:
    """Stands in for docker.DockerClient; only `info()` is used by the pure code paths."""

    def __init__(self, runtimes: dict[str, Any] | None = None) -> None:
        self._runtimes = runtimes or {"runc": {}}

    def info(self) -> dict[str, Any]:
        return {"Runtimes": self._runtimes}


def make_spec(tmp_path: Path, **overrides: Any) -> SandboxSpec:
    fields: dict[str, Any] = dict(
        run_id="run1", role="dev", workdir=tmp_path / "clone", inputs_dir=None,
        profile=PROFILE, config=CONFIG, password="pw-secret",
        network=RunNetwork(name="guudflow-run1", gateway_ip="172.30.0.1", bridge="br-abc123def456"),
    )
    fields.update(overrides)
    return SandboxSpec(**fields)


@pytest.fixture
def manager() -> SandboxManager:
    return SandboxManager(client=FakeClient())  # type: ignore[arg-type]


# --------------------------------------------------------------------- unit


def test_default_image_is_pinned_version() -> None:
    assert DEFAULT_IMAGE == f"guudflow/agent-opencode:{OPENCODE_VERSION}"


def test_container_kwargs_has_every_hardening_flag(manager: SandboxManager, tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    kw = manager.container_kwargs(spec)
    assert kw["image"] == DEFAULT_IMAGE
    assert kw["name"] == "gf-run1-dev"
    assert kw["detach"] is True
    assert kw["network"] == "guudflow-run1"
    assert kw["read_only"] is True
    assert kw["cap_drop"] == ["ALL"]
    assert kw["security_opt"] == ["no-new-privileges:true"]
    assert kw["mem_limit"] == PROFILE.memory
    assert kw["pids_limit"] == PROFILE.pids_limit
    assert kw["nano_cpus"] == 1_000_000_000
    assert kw["tmpfs"] == {"/tmp": "rw,size=256m,mode=1777",
                           "/home/agent": "rw,size=512m,mode=1777"}
    assert kw["volumes"] == {str(tmp_path / "clone"): {"bind": "/work", "mode": "rw"}}
    assert kw["user"] == f"{os.getuid()}:{os.getgid()}"
    assert kw["working_dir"] == "/work"
    assert kw["labels"] == {LABEL_RUN: "run1", LABEL_ROLE: "dev"}
    assert "runtime" not in kw


def test_container_kwargs_never_contain_escape_hatches(manager: SandboxManager,
                                                       tmp_path: Path) -> None:
    kw = manager.container_kwargs(make_spec(tmp_path, inputs_dir=tmp_path / "in"))
    assert "privileged" not in kw and "ports" not in kw and "network_mode" not in kw
    assert "pid_mode" not in kw and "cap_add" not in kw
    for host_path in kw["volumes"]:
        assert "docker.sock" not in host_path
    assert not any("docker.sock" in str(v) for v in kw["volumes"].values())


def test_inputs_mount_is_read_only(manager: SandboxManager, tmp_path: Path) -> None:
    kw = manager.container_kwargs(make_spec(tmp_path, inputs_dir=tmp_path / "in"))
    assert kw["volumes"][str(tmp_path / "in")] == {"bind": "/inputs", "mode": "ro"}
    assert kw["volumes"][str(tmp_path / "clone")]["mode"] == "rw"
    assert len(kw["volumes"]) == 2


def test_runsc_used_only_when_listed(tmp_path: Path) -> None:
    mgr = SandboxManager(client=FakeClient({"runc": {}, "runsc": {}}))  # type: ignore[arg-type]
    assert mgr.runtime() == "runsc"
    assert mgr.container_kwargs(make_spec(tmp_path))["runtime"] == "runsc"


def test_name_is_sanitised(manager: SandboxManager, tmp_path: Path) -> None:
    kw = manager.container_kwargs(make_spec(tmp_path, run_id="r 1/x", role="tech lead!"))
    assert kw["name"] == "gf-r-1-x-tech-lead-"


def test_explicit_user_and_image_respected(manager: SandboxManager, tmp_path: Path) -> None:
    kw = manager.container_kwargs(make_spec(tmp_path, user="10001:10001", image="x/y:1"))
    assert kw["user"] == "10001:10001" and kw["image"] == "x/y:1"


def test_use_container_false_raises(manager: SandboxManager, tmp_path: Path) -> None:
    profile = ContainerProfile(use_container=False, read_only_root=False,
                               allow_test_runners=True, host_may_reach_dev_server=True)
    with pytest.raises(SandboxError):
        manager.container_kwargs(make_spec(tmp_path, profile=profile))


def test_env_is_built_from_scratch(manager: SandboxManager, tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_TOKEN", "do-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-do-not-leak")
    spec = make_spec(tmp_path)
    env = manager.container_env(spec)
    assert "do-not-leak" not in " ".join(env.values())
    assert "SECRET_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env
    assert "do-not-leak" not in str(manager.container_kwargs(spec))
    assert env["HOME"] == "/home/agent"
    assert env["XDG_CONFIG_HOME"] == "/home/agent/.config"
    assert env["XDG_DATA_HOME"] == "/home/agent/.data"
    assert env["XDG_STATE_HOME"] == "/home/agent/.state"
    assert env["XDG_CACHE_HOME"] == "/home/agent/.cache"
    assert env["OPENCODE_SERVER_PASSWORD"] == "pw-secret"
    assert '"share": "disabled"' in env["OPENCODE_CONFIG_CONTENT"]
    assert env["PATH"] == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    assert (env["GIT_CONFIG_COUNT"], env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]) == (
        "1", "safe.directory", "*")
    for key, value in LOCKED_FLAGS.items():
        assert env[key] == value
    expected = {"HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
                "OPENCODE_CONFIG_CONTENT", "OPENCODE_SERVER_PASSWORD", "PATH",
                "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", *LOCKED_FLAGS}
    assert set(env) == expected


def test_sandbox_base_url() -> None:
    box = Sandbox(container_id="c", name="n", ip="172.30.0.2", password="p")
    assert box.base_url == f"http://172.30.0.2:{OPENCODE_PORT}"


def test_restrict_host_ports_returns_false_without_iptables(
        manager: SandboxManager, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise FileNotFoundError("iptables")
    monkeypatch.setattr(subprocess, "run", boom)
    net = RunNetwork("guudflow-x", "172.30.0.1", "br-abc")
    assert manager.restrict_host_ports(net, [8000]) is False
    manager.remove_host_port_rules(net)  # must not raise


# ------------------------------------------------------------- integration

DOCKER = SandboxManager.available()
needs_docker = pytest.mark.skipif(not DOCKER, reason="docker daemon not available")


def _inspect(container_id: str) -> dict[str, Any]:
    import docker
    return docker.from_env().api.inspect_container(container_id)


@pytest.fixture
async def live() -> AsyncIterator[tuple[SandboxManager, str]]:
    """A manager plus a unique run id; everything for the run is removed afterwards."""
    mgr = SandboxManager()
    run_id = f"t{uuid.uuid4().hex[:8]}"
    yield mgr, run_id
    await mgr.stop_run(run_id)


def _git_workdir(tmp_path: Path) -> Path:
    workdir = tmp_path / "clone"
    workdir.mkdir()
    subprocess.run(["git", "init", "-q", str(workdir)], check=True)
    return workdir


@needs_docker
async def test_network_lifecycle(live: tuple[SandboxManager, str]) -> None:
    mgr, run_id = live
    net = await mgr.create_run_network(run_id)
    assert net.name == f"guudflow-{run_id}"
    assert net.gateway_ip and net.bridge.startswith("br-") and len(net.bridge) == 15
    assert await mgr.create_run_network(run_id) == net  # idempotent
    raw = mgr.client.networks.get(net.name)
    assert raw.attrs["Internal"] is True
    assert raw.attrs["Options"]["com.docker.network.bridge.enable_icc"] == "false"
    assert raw.attrs["Labels"][LABEL_RUN] == run_id
    await mgr.stop_run(run_id)
    assert not [n for n in mgr.client.networks.list(names=[net.name]) if n.name == net.name]


@needs_docker
async def test_hardened_container_via_manager_kwargs(
        live: tuple[SandboxManager, str], tmp_path: Path) -> None:
    """Run the manager's exact kwargs on a plain node image (no opencode image needed)."""
    mgr, run_id = live
    net = await mgr.create_run_network(run_id)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    spec = make_spec(tmp_path, run_id=run_id, workdir=_git_workdir(tmp_path), inputs_dir=inputs,
                     network=net, image=NODE_IMAGE)
    kwargs = mgr.container_kwargs(spec)
    kwargs["command"] = ["sleep", "120"]
    container = mgr.client.containers.run(**kwargs)
    info = _inspect(container.id)
    host = info["HostConfig"]
    assert host["ReadonlyRootfs"] is True
    assert "ALL" in host["CapDrop"]
    assert host["NetworkMode"] == net.name
    assert host["Privileged"] is False
    assert not host["PortBindings"]
    assert {m["Destination"] for m in info["Mounts"] if m["Type"] == "bind"} == {"/work", "/inputs"}
    assert {m["Destination"]: m["RW"] for m in info["Mounts"]}["/inputs"] is False
    assert info["Config"]["Labels"][LABEL_ROLE] == "dev"

    # No egress at all (internal network): even a TCP connect to the internet fails.
    code, _ = container.exec_run(["node", "-e", (
        "require('https').get('https://registry.npmjs.org',{timeout:5000},"
        "()=>process.exit(0)).on('error',()=>process.exit(3)).on('timeout',()=>process.exit(3))"
    )])
    assert code == 3

    # Files written by the agent keep the host uid.
    code, out = container.exec_run(["sh", "-c", "echo x > /work/f.txt"])
    assert code == 0, out
    assert (spec.workdir / "f.txt").stat().st_uid == os.getuid()
    # /inputs is read-only, root fs is read-only.
    assert container.exec_run(["sh", "-c", "echo x > /inputs/f.txt"])[0] != 0
    assert container.exec_run(["sh", "-c", "echo x > /etc/f.txt"])[0] != 0

    await mgr.stop_run(run_id)
    assert not mgr.client.containers.list(all=True, filters={"label": f"{LABEL_RUN}={run_id}"})


@needs_docker
async def test_restrict_and_remove_host_port_rules(live: tuple[SandboxManager, str]) -> None:
    mgr, run_id = live
    net = await mgr.create_run_network(run_id)
    if not mgr.restrict_host_ports(net, [8000, 4000]):
        pytest.skip("iptables not usable in this environment")
    try:
        listing = subprocess.run(["iptables", "-S", "INPUT"], capture_output=True, text=True,
                                 check=True).stdout
        mine = [ln for ln in listing.splitlines() if f"guudflow:{net.name}" in ln]
        assert len(mine) == 2 and all(net.bridge in ln for ln in mine)
        assert any("4000,8000" in ln for ln in mine)
    finally:
        mgr.remove_host_port_rules(net)
    listing = subprocess.run(["iptables", "-S", "INPUT"], capture_output=True, text=True,
                             check=True).stdout
    assert f"guudflow:{net.name}" not in listing


@pytest.fixture
async def agent_image() -> SandboxManager:
    """Ensure the real agent image exists (builds it on first use); skip if it cannot be built."""
    mgr = SandboxManager()
    try:
        await mgr.ensure_image()
    except SandboxError as exc:
        pytest.skip(f"agent image unavailable (build failed): {str(exc)[:200]}")
    return mgr


@needs_docker
async def test_opencode_sandbox_end_to_end(agent_image: SandboxManager, tmp_path: Path) -> None:
    mgr = agent_image
    run_id = f"t{uuid.uuid4().hex[:8]}"
    try:
        net = await mgr.create_run_network(run_id)
        inputs = tmp_path / "inputs"
        inputs.mkdir()
        spec = make_spec(tmp_path, run_id=run_id, workdir=_git_workdir(tmp_path),
                         inputs_dir=inputs, network=net, password="pw-e2e")
        box = await mgr.start(spec, timeout=90.0)

        async with httpx.AsyncClient(auth=httpx.BasicAuth("opencode", "pw-e2e")) as client:
            assert (await client.get(f"{box.base_url}/global/health")).status_code == 200
        async with httpx.AsyncClient() as client:  # password is enforced
            assert (await client.get(f"{box.base_url}/global/health")).status_code == 401

        host = _inspect(box.container_id)["HostConfig"]
        assert host["ReadonlyRootfs"] is True and "ALL" in host["CapDrop"]
        assert host["NetworkMode"] == net.name
        mounts = {m["Destination"] for m in _inspect(box.container_id)["Mounts"]
                  if m["Type"] == "bind"}
        assert mounts == {"/work", "/inputs"}

        import docker
        container = docker.from_env().containers.get(box.container_id)
        # The agent image has python3 but no node.
        code, _ = container.exec_run(["python3", "-c", (
            "import socket,sys\n"
            "try:\n socket.create_connection(('registry.npmjs.org', 443), timeout=5)\n"
            "except OSError:\n sys.exit(3)"
        )])
        assert code == 3
        code, out = container.exec_run(["sh", "-c", "echo x > /work/f.txt"])
        assert code == 0, out
        assert (spec.workdir / "f.txt").stat().st_uid == os.getuid()

        await mgr.stop_run(run_id)
        assert not mgr.client.containers.list(all=True, filters={"label": f"{LABEL_RUN}={run_id}"})
        assert not [n for n in mgr.client.networks.list(names=[net.name]) if n.name == net.name]
    finally:
        await mgr.stop_run(run_id)


@needs_docker
async def test_start_failure_removes_container_and_reports_logs(
        live: tuple[SandboxManager, str], tmp_path: Path) -> None:
    """A container that never serves health fails fast with logs and is cleaned up."""
    mgr, run_id = live
    net = await mgr.create_run_network(run_id)
    spec = make_spec(tmp_path, run_id=run_id, workdir=_git_workdir(tmp_path), network=net,
                     image=NODE_IMAGE)
    # node's default entrypoint/cmd exits immediately (no tty) -> "exited" is reported.
    with pytest.raises(SandboxError, match="exited|not healthy|no IP"):
        await mgr.start(spec, timeout=10.0)
    assert not mgr.client.containers.list(all=True, filters={"label": f"{LABEL_RUN}={run_id}"})
