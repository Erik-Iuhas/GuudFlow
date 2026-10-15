"""SandboxManager: hardened per-role `opencode serve` containers.

Plan sections 5.2 (locked launch env), 5.3 (container hardening) and 5.4 (network).
Each run gets one Docker network (`internal`, inter-container traffic off). Each role's
sandbox is a container on that network running `opencode serve` on port 4096. The host
reaches it directly at the container IP; the container reaches only the network's
gateway IP (where the LLM gateway and the MCP server bind), and, with
`restrict_host_ports()`, only the allowed ports there.

Security properties:
  - The container environment is built from scratch; nothing is read from the host
    environment (`container_env`).
  - Read-only root, all capabilities dropped, no-new-privileges, memory/pids/cpu limits,
    sized tmpfs for /tmp and the home directory.
  - Only the role's clone (rw) and, optionally, the inputs directory (ro) are mounted.
    Never the Docker socket, `privileged`, published ports or host networking.
  - gVisor (`runsc`) is used automatically when the daemon has it.

All blocking Docker SDK calls run via `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from core.harness.capabilities import ContainerProfile
from core.harness.server import LOCKED_FLAGS, OPENCODE_VERSION

if TYPE_CHECKING:
    import docker

_logger = logging.getLogger(__name__)

DEFAULT_IMAGE = f"guudflow/agent-opencode:{OPENCODE_VERSION}"
# Optional chart-rendering variant (plan §5.10): matplotlib, plotly, kaleido, numpy, pandas.
# Never the default. A run opts in per role with `SandboxSpec(image=VIZ_IMAGE)` or
# `image_for_variant("viz")`; build it with `SandboxManager.ensure_image(variant="viz")`.
VIZ_IMAGE = f"guudflow/agent-opencode-viz:{OPENCODE_VERSION}"
VIZ_WHEELS_ENV = "GUUDFLOW_VIZ_WHEELS_DIR"
LABEL_RUN = "guudflow.run"
LABEL_ROLE = "guudflow.role"
OPENCODE_PORT = 4096

_DOCKERFILE = Path(__file__).resolve().parents[1] / "sandbox" / "agent-opencode.Dockerfile"
_VIZ_DOCKERFILE = (Path(__file__).resolve().parents[1] / "sandbox"
                   / "agent-opencode-viz.Dockerfile")
_VIZ_INSTALLER = Path(__file__).resolve().parents[1] / "sandbox" / "viz_install.py"
_DEFAULT_VIZ_WHEELS = Path(__file__).resolve().parents[1] / ".tools" / "viz-wheels"
AGENT_UID = 10001                      # the image's unprivileged `agent` user
_AGENT_HOME = "/home/agent"
_CONTAINER_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_HEALTH_POLL_SECONDS = 0.25
_LOG_TAIL_LINES = 40
_IPTABLES_TIMEOUT = 10


class SandboxError(RuntimeError):
    """Raised when a sandbox cannot be created, started or become healthy."""


def image_for_variant(variant: str = "default") -> str:
    """Image tag for a sandbox variant: `"default"` or `"viz"` (chart rendering).

    Use the result as `SandboxSpec.image` to give one role the viz image; every other
    role keeps `DEFAULT_IMAGE`. Raises `SandboxError` for an unknown variant.
    """
    if variant == "default":
        return DEFAULT_IMAGE
    if variant == "viz":
        return VIZ_IMAGE
    raise SandboxError(f"unknown sandbox image variant {variant!r} (use 'default' or 'viz')")


def viz_wheels_dir() -> Path:
    """Directory holding the viz wheels: `$GUUDFLOW_VIZ_WHEELS_DIR` or `.tools/viz-wheels`."""
    override = os.environ.get(VIZ_WHEELS_ENV)
    return Path(override) if override else _DEFAULT_VIZ_WHEELS


@dataclass(frozen=True)
class RunNetwork:
    """A per-run Docker network. `bridge` is the Linux interface name (`br-<12 hex>`)."""

    name: str
    gateway_ip: str
    bridge: str


@dataclass(frozen=True)
class SandboxSpec:
    """Everything needed to start one role's sandbox."""

    run_id: str
    role: str
    workdir: Path                       # host path of the role's clone, mounted at /work (rw)
    inputs_dir: Path | None             # host path mounted at /inputs (ro), optional
    profile: ContainerProfile
    config: dict[str, Any]              # opencode config document (OPENCODE_CONFIG_CONTENT)
    password: str                       # OPENCODE_SERVER_PASSWORD
    network: RunNetwork
    image: str = DEFAULT_IMAGE
    # Defaults to the host uid:gid so files written in /work keep host ownership.
    user: str | None = None
    # Host-only tool output (screenshots, installed packages), mounted READ-ONLY at
    # /work/.guudflow so the agent can read it but never redirect host writes (O8).
    tool_dir: Path | None = None


@dataclass
class Sandbox:
    """A running sandbox container."""

    container_id: str
    name: str
    ip: str
    password: str

    @property
    def base_url(self) -> str:
        """HTTP base URL of the sandbox's opencode server (reachable from the host)."""
        return f"http://{self.ip}:{OPENCODE_PORT}"


def _container_name(run_id: str, role: str) -> str:
    """Docker container name `gf-{run_id}-{role}` limited to docker's allowed characters."""
    name = re.sub(r"[^a-zA-Z0-9_.-]", "-", f"gf-{run_id}-{role}")
    return name


RIPGREP_ENV = "GUUDFLOW_RIPGREP"


def ripgrep_binary() -> Path | None:
    """The ripgrep binary copied into the sandbox image: $GUUDFLOW_RIPGREP, else `rg` on PATH.

    A static (musl) build is safest; a dynamic one needs only libc, libgcc and libpcre2-8,
    which the Debian base image has."""
    wanted = os.environ.get(RIPGREP_ENV)
    if wanted:
        path = Path(wanted)
        return path if path.is_file() else None
    found = shutil.which("rg")
    return Path(found) if found else None


class SandboxManager:
    """Creates and destroys hardened opencode sandboxes and their per-run networks."""

    def __init__(self, client: "docker.DockerClient | None" = None, *,
                 image: str = DEFAULT_IMAGE) -> None:
        self._client = client
        self.image = image

    # ------------------------------------------------------------------ daemon

    @staticmethod
    def available() -> bool:
        """True when the Docker SDK is installed and the daemon answers a ping."""
        try:
            import docker
            client = docker.from_env()
            try:
                return bool(client.ping())
            finally:
                client.close()
        except Exception:  # noqa: BLE001 - any failure means "not available"
            return False

    @property
    def client(self) -> "docker.DockerClient":
        """The Docker client (created lazily from the environment)."""
        if self._client is None:
            import docker
            self._client = docker.from_env()
        return self._client

    def runtime(self) -> str | None:
        """Return "runsc" (gVisor) when the daemon lists it, else None (plain runc)."""
        try:
            runtimes = self.client.info().get("Runtimes") or {}
        except Exception as exc:  # noqa: BLE001
            _logger.warning("could not read docker runtimes: %s", exc)
            return None
        return "runsc" if "runsc" in runtimes else None

    # ------------------------------------------------------------------ image

    async def ensure_image(self, *, build: bool = True, variant: str = "default") -> None:
        """Make sure a sandbox image exists locally, building it if allowed.

        `variant="default"` (the default) is the manager's own image (`self.image`).
        `variant="viz"` is the optional chart-rendering image (`VIZ_IMAGE`), built on top of
        the default image (which is built first when missing). The viz build needs the wheels
        fetched by `scripts/fetch_viz_wheels.sh`; without them it raises `SandboxError`.

        The build context is a temp directory holding only the Dockerfile (plus the pinned
        binary, or the viz installer and wheels), so nothing from the repository is sent to
        the daemon.
        """
        if variant == "viz":
            await self._ensure_viz_image(build=build)
            return
        image_for_variant(variant)  # validates the name
        if await asyncio.to_thread(self._image_exists):
            return
        if not build:
            raise SandboxError(f"sandbox image {self.image} is missing and build=False")
        await asyncio.to_thread(self._build_image)

    async def _ensure_viz_image(self, *, build: bool) -> None:
        if await asyncio.to_thread(self._image_exists, VIZ_IMAGE):
            return
        if not build:
            raise SandboxError(f"sandbox image {VIZ_IMAGE} is missing and build=False")
        wheels = viz_wheels_dir()
        if not any(wheels.glob("*.whl")):
            raise SandboxError(
                f"cannot build {VIZ_IMAGE}: no wheels in {wheels}. Run "
                "scripts/fetch_viz_wheels.sh on the host first (docker build has no network)"
            )
        await self.ensure_image(build=True)  # the viz image builds FROM the default image
        await asyncio.to_thread(self._build_viz_image, wheels)

    def _image_exists(self, image: str | None = None) -> bool:
        from docker.errors import ImageNotFound
        try:
            self.client.images.get(image or self.image)
            return True
        except ImageNotFound:
            return False

    def _build_viz_image(self, wheels: Path) -> None:
        from docker.errors import BuildError
        if not _VIZ_DOCKERFILE.is_file() or not _VIZ_INSTALLER.is_file():
            raise SandboxError(f"missing {_VIZ_DOCKERFILE} or {_VIZ_INSTALLER}")
        with tempfile.TemporaryDirectory(prefix="gf-sandbox-viz-ctx-") as ctx:
            # Context = Dockerfile + installer + the wheels only; the build needs no network.
            shutil.copyfile(_VIZ_DOCKERFILE, Path(ctx) / "Dockerfile")
            shutil.copyfile(_VIZ_INSTALLER, Path(ctx) / "viz_install.py")
            (Path(ctx) / "wheels").mkdir()
            for wheel in wheels.glob("*.whl"):
                shutil.copyfile(wheel, Path(ctx) / "wheels" / wheel.name)
            _logger.info("building sandbox image %s", VIZ_IMAGE)
            try:
                self.client.images.build(path=ctx, tag=VIZ_IMAGE, rm=True, forcerm=True,
                                         buildargs={"BASE_IMAGE": self.image})
            except BuildError as exc:
                raise SandboxError(f"viz sandbox image build failed: {exc}") from exc

    def _build_image(self) -> None:
        from docker.errors import BuildError
        if not _DOCKERFILE.is_file():
            raise SandboxError(f"missing {_DOCKERFILE}")
        from core.harness.server import OpencodeNotInstalled, native_opencode_binary
        try:
            binary = native_opencode_binary()
        except OpencodeNotInstalled as exc:
            raise SandboxError(str(exc)) from exc
        with tempfile.TemporaryDirectory(prefix="gf-sandbox-ctx-") as ctx:
            # Context = Dockerfile + the pinned native binary (+ ripgrep); the build needs no
            # network.
            shutil.copyfile(_DOCKERFILE, Path(ctx) / "Dockerfile")
            shutil.copyfile(binary, Path(ctx) / "opencode")
            extras = Path(ctx) / "extras"
            extras.mkdir()
            rg = ripgrep_binary()
            if rg is not None:
                # opencode's grep and glob tools run ripgrep and would download it at first use,
                # which a sandbox cannot (no egress), so every grep failed.
                shutil.copyfile(rg, extras / "rg")
                (extras / "rg").chmod(0o755)
            else:
                _logger.warning("no ripgrep on the host (set %s): agents' grep tool will fail "
                                "in sandboxes", RIPGREP_ENV)
            _logger.info("building sandbox image %s", self.image)
            try:
                self.client.images.build(path=ctx, tag=self.image, rm=True, forcerm=True)
            except BuildError as exc:
                raise SandboxError(f"sandbox image build failed: {exc}") from exc

    # ---------------------------------------------------------------- network

    async def create_run_network(self, run_id: str) -> RunNetwork:
        """Create (or return) the run's internal network with inter-container traffic off."""
        return await asyncio.to_thread(self._create_run_network, run_id)

    def _create_run_network(self, run_id: str) -> RunNetwork:
        name = f"guudflow-{run_id}"
        net = self._find_network(name)
        if net is None:
            net = self.client.networks.create(
                name,
                driver="bridge",
                internal=True,
                options={"com.docker.network.bridge.enable_icc": "false"},
                labels={LABEL_RUN: run_id},
            )
            net.reload()
        return self._network_info(net)

    def _find_network(self, name: str) -> Any | None:
        for net in self.client.networks.list(names=[name]):
            if net.name == name:  # the names filter is a substring match
                return net
        return None

    @staticmethod
    def _network_info(net: Any) -> RunNetwork:
        configs = (net.attrs.get("IPAM") or {}).get("Config") or []
        gateway = next((c["Gateway"] for c in configs if c.get("Gateway")), "")
        if not gateway:
            # Docker omits Gateway when it is the first usable address of the subnet.
            subnet = next((c["Subnet"] for c in configs if c.get("Subnet")), "")
            if subnet:
                base = subnet.split("/")[0].rsplit(".", 1)[0]
                gateway = f"{base}.1"
        if not gateway:
            raise SandboxError(f"network {net.name} has no gateway address")
        return RunNetwork(name=net.name, gateway_ip=gateway, bridge=f"br-{net.id[:12]}")

    async def remove_run_network(self, run_id: str) -> None:
        """Remove the run's network (no error if it is already gone)."""
        await asyncio.to_thread(self._remove_network, f"guudflow-{run_id}")

    def _remove_network(self, name: str) -> None:
        from docker.errors import APIError, NotFound
        net = self._find_network(name)
        if net is None:
            return
        try:
            net.remove()
        except NotFound:
            pass
        except APIError as exc:
            raise SandboxError(f"could not remove network {name}: {exc}") from exc

    # ------------------------------------------------------- container config

    def container_env(self, spec: SandboxSpec) -> dict[str, str]:
        """Environment for the container, built from scratch (no host environment)."""
        env: dict[str, str] = {
            "HOME": _AGENT_HOME,
            "XDG_CONFIG_HOME": f"{_AGENT_HOME}/.config",
            "XDG_DATA_HOME": f"{_AGENT_HOME}/.data",
            "XDG_STATE_HOME": f"{_AGENT_HOME}/.state",
            "XDG_CACHE_HOME": f"{_AGENT_HOME}/.cache",
            "OPENCODE_CONFIG_CONTENT": json.dumps(spec.config),
            "OPENCODE_SERVER_PASSWORD": spec.password,
            "PATH": _CONTAINER_PATH,
            "PYTHONPATH": "/work/.guudflow/site-packages",
            "NODE_PATH": "/work/.guudflow/node_modules",
            # git's dubious-ownership check: /work is owned by the host uid.
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": "*",
        }
        env.update(LOCKED_FLAGS)
        return env

    def container_kwargs(self, spec: SandboxSpec) -> dict[str, Any]:
        """Everything passed to `containers.run(...)`. Pure: no daemon calls except runtime()."""
        profile = spec.profile
        if not profile.use_container:
            raise SandboxError(
                f"role {spec.role!r} has use_container=False; it must not be sandboxed"
            )
        volumes: dict[str, dict[str, str]] = {
            str(spec.workdir): {"bind": "/work", "mode": "rw"},
        }
        if spec.inputs_dir is not None:
            volumes[str(spec.inputs_dir)] = {"bind": "/inputs", "mode": "ro"}
        if spec.tool_dir is not None:
            volumes[str(spec.tool_dir)] = {"bind": "/work/.guudflow", "mode": "ro"}
        kwargs: dict[str, Any] = {
            "image": spec.image,
            "name": _container_name(spec.run_id, spec.role),
            "detach": True,
            "network": spec.network.name,
            "read_only": profile.read_only_root,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "mem_limit": profile.memory,
            "pids_limit": profile.pids_limit,
            "nano_cpus": 1_000_000_000,
            "tmpfs": {
                "/tmp": "rw,size=256m,mode=1777",
                _AGENT_HOME: "rw,size=512m,mode=1777",
            },
            "volumes": volumes,
            "user": self.container_user(spec),
            "environment": self.container_env(spec),
            "working_dir": "/work",
            "labels": {LABEL_RUN: spec.run_id, LABEL_ROLE: spec.role},
        }
        if self.runtime() == "runsc":
            kwargs["runtime"] = "runsc"
        return kwargs

    # -------------------------------------------------------------- lifecycle

    @staticmethod
    def container_user(spec: SandboxSpec) -> str:
        """uid:gid the agent runs as: the host user, never root.

        When the runner itself is root (CI, dev containers) the agent runs as the
        image's unprivileged `agent` user instead, and `start()` hands it /work.
        """
        if spec.user:
            return spec.user
        if os.getuid() == 0:
            return f"{AGENT_UID}:{AGENT_UID}"
        return f"{os.getuid()}:{os.getgid()}"

    @staticmethod
    def _hand_over_workdir(spec: SandboxSpec, user: str) -> None:
        """Make /work writable for a non-host agent uid (root runner only)."""
        uid, _, gid = user.partition(":")
        if os.getuid() != 0 or int(uid) == 0:
            return
        root = Path(spec.workdir)
        for path in [root, *root.rglob("*")]:
            if path.is_relative_to(root / ".guudflow"):
                continue
            try:
                os.lchown(path, int(uid), int(gid or uid))
            except FileNotFoundError:
                continue

    async def start(self, spec: SandboxSpec, *, timeout: float = 60.0) -> Sandbox:
        """Start the sandbox and wait until opencode's /global/health answers 200."""
        kwargs = await asyncio.to_thread(self.container_kwargs, spec)
        await asyncio.to_thread(self._hand_over_workdir, spec, kwargs["user"])
        try:
            container = await asyncio.to_thread(self._run_container, kwargs)
        except Exception as exc:  # noqa: BLE001 - docker raises many types; rewrap once
            raise SandboxError(f"could not start sandbox {kwargs['name']}: {exc}") from exc
        sandbox = Sandbox(container_id=container.id, name=kwargs["name"], ip="",
                          password=spec.password)
        try:
            sandbox.ip = await asyncio.to_thread(self._container_ip, container, spec.network.name)
            await self._wait_healthy(sandbox, container, timeout)
        except BaseException:
            await self.stop(sandbox)
            raise
        runtime = kwargs.get("runtime", "runc")
        _logger.info("sandbox %s up at %s (runtime=%s)", sandbox.name, sandbox.base_url, runtime)
        return sandbox

    def _run_container(self, kwargs: dict[str, Any]) -> Any:
        return self.client.containers.run(**kwargs)

    @staticmethod
    def _container_ip(container: Any, network: str) -> str:
        container.reload()
        nets = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        ip = (nets.get(network) or {}).get("IPAddress", "")
        if not ip:
            raise SandboxError(f"container {container.name} has no IP on network {network}")
        return str(ip)

    async def _wait_healthy(self, sandbox: Sandbox, container: Any, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        auth = httpx.BasicAuth("opencode", sandbox.password)
        url = f"{sandbox.base_url}/global/health"
        async with httpx.AsyncClient(auth=auth, timeout=2.0, trust_env=False) as client:
            while True:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                status = await asyncio.to_thread(self._container_status, container)
                if status not in ("created", "running"):
                    raise SandboxError(
                        f"sandbox {sandbox.name} is {status}; last logs:\n"
                        f"{await asyncio.to_thread(self._tail_logs, container)}"
                    )
                if loop.time() > deadline:
                    raise SandboxError(
                        f"sandbox {sandbox.name} not healthy after {timeout:.0f}s; last logs:\n"
                        f"{await asyncio.to_thread(self._tail_logs, container)}"
                    )
                await asyncio.sleep(_HEALTH_POLL_SECONDS)

    @staticmethod
    def _container_status(container: Any) -> str:
        container.reload()
        return str(container.status)

    @staticmethod
    def _tail_logs(container: Any) -> str:
        try:
            raw = container.logs(tail=_LOG_TAIL_LINES)
        except Exception as exc:  # noqa: BLE001 - logs are diagnostics only
            return f"<could not read logs: {exc}>"
        return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)

    async def stop(self, sandbox: Sandbox) -> None:
        """Force-remove the sandbox container (no error if it is already gone)."""
        await asyncio.to_thread(self._remove_container, sandbox.container_id)

    def _remove_container(self, container_id: str) -> None:
        from docker.errors import APIError, NotFound
        try:
            self.client.containers.get(container_id).remove(force=True)
        except NotFound:
            pass
        except APIError as exc:
            # 409 "removal ... is already in progress": Docker is removing it already.
            if exc.status_code != 409:
                raise

    async def run_check(self, tree: Path, command: str, *, timeout: float = 600.0,
                        image: str = DEFAULT_IMAGE) -> tuple[int | None, str]:
        """Run `command` once on `tree` (mounted rw at /work) in a throwaway container.

        Plan §14.3: no network at all, read-only root, every capability dropped,
        memory/pid/CPU limits, unprivileged user. Returns (exit code or None on
        timeout, combined output).
        """
        return await asyncio.to_thread(self._run_check, tree, command, timeout, image)

    def _run_check(self, tree: Path, command: str, timeout: float,
                   image: str) -> tuple[int | None, str]:
        import requests

        if os.getuid() == 0:
            # The image's agent user must be able to write the export (test caches).
            for path in [tree, *tree.rglob("*")]:
                try:
                    os.chown(path, AGENT_UID, AGENT_UID, follow_symlinks=False)
                except OSError:
                    pass
        user = f"{AGENT_UID}:{AGENT_UID}" if os.getuid() == 0 else f"{os.getuid()}:{os.getgid()}"
        container = self.client.containers.run(
            # The image's entrypoint is `opencode serve`; a check runs a plain shell.
            image, ["-c", command], entrypoint=["sh"], detach=True, network_mode="none",
            read_only=True, cap_drop=["ALL"], security_opt=["no-new-privileges:true"],
            mem_limit="1g", pids_limit=256, nano_cpus=1_000_000_000, user=user,
            tmpfs={"/tmp": "rw,size=256m,mode=1777", _AGENT_HOME: "rw,size=256m,mode=1777"},
            volumes={str(tree): {"bind": "/work", "mode": "rw"}}, working_dir="/work",
            environment={"HOME": _AGENT_HOME, "PATH": _CONTAINER_PATH, "CI": "1"},
            labels={LABEL_ROLE: "check"},
            **({"runtime": "runsc"} if self.runtime() == "runsc" else {}))
        try:
            try:
                code: int | None = int(container.wait(timeout=timeout).get("StatusCode", 1))
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                code = None
            output = container.logs(stdout=True, stderr=True).decode("utf-8", "replace")
            return code, output
        finally:
            container.remove(force=True)

    def missing_programs(self, programs: list[str]) -> list[str] | None:
        """Which of `programs` the sandbox image does not have (None: the image is not built
        yet, so nothing can be said). Runs one throwaway container with no network."""
        if not self._image_exists():
            return None
        script = 'for p in "$@"; do command -v "$p" >/dev/null 2>&1 || echo "$p"; done'
        output = self.client.containers.run(
            self.image, ["-c", script, "sh", *programs], entrypoint=["sh"], remove=True,
            network_mode="none", environment={"PATH": _CONTAINER_PATH})
        text = output.decode("utf-8", "replace") if isinstance(output, bytes) else str(output)
        return [line.strip() for line in text.splitlines() if line.strip()]

    async def stop_run(self, run_id: str) -> None:
        """Remove every container labelled with the run, then the run's network."""
        await asyncio.to_thread(self._remove_run_containers, run_id)
        await self.remove_run_network(run_id)

    def _remove_run_containers(self, run_id: str) -> None:
        from docker.errors import NotFound
        containers = self.client.containers.list(
            all=True, filters={"label": f"{LABEL_RUN}={run_id}"}
        )
        for container in containers:
            try:
                container.remove(force=True)
            except NotFound:
                pass

    # --------------------------------------------------- host port restriction

    @staticmethod
    def _rule_comment(network: RunNetwork) -> str:
        return f"guudflow:{network.name}"

    def restrict_host_ports(self, network: RunNetwork, allowed_ports: list[int]) -> bool:
        """Best effort: let containers on this bridge open connections to ONLY `allowed_ports`
        on the host (INPUT chain). Only NEW connections are matched, so replies to
        connections the host opens to the sandbox are unaffected.

        Returns False (and logs a warning) if iptables is unavailable or fails; never raises.
        """
        base = ["-i", network.bridge, "-m", "conntrack", "--ctstate", "NEW",
                "-m", "comment", "--comment", self._rule_comment(network)]
        tcp = ["-p", "tcp", *base]
        if allowed_ports:
            ports = ",".join(str(p) for p in sorted(set(allowed_ports)))
            tcp += ["-m", "multiport", "!", "--dports", ports]
        rules = [tcp + ["-j", "DROP"], ["-p", "udp", *base, "-j", "DROP"]]
        applied: list[list[str]] = []
        for rule in rules:
            if not self._iptables(["-I", "INPUT", *rule]):
                for done in applied:
                    self._iptables(["-D", "INPUT", *done])
                _logger.warning("could not restrict host ports for %s; relying on network "
                                "isolation only", network.name)
                return False
            applied.append(rule)
        return True

    def remove_host_port_rules(self, network: RunNetwork) -> None:
        """Delete the INPUT rules added by `restrict_host_ports` (best effort, never raises)."""
        comment = self._rule_comment(network)
        try:
            listing = subprocess.run(
                ["iptables", "-S", "INPUT"], capture_output=True, text=True,
                timeout=_IPTABLES_TIMEOUT, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _logger.warning("iptables unavailable, cannot remove rules: %s", exc)
            return
        if listing.returncode != 0:
            return
        for line in listing.stdout.splitlines():
            if line.startswith("-A INPUT ") and comment in line:
                self._iptables(["-D", "INPUT", *shlex.split(line)[2:]])

    @staticmethod
    def _iptables(args: list[str]) -> bool:
        try:
            result = subprocess.run(
                ["iptables", *args], capture_output=True, text=True,
                timeout=_IPTABLES_TIMEOUT, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _logger.warning("iptables failed: %s", exc)
            return False
        if result.returncode != 0:
            _logger.warning("iptables %s failed: %s", " ".join(args), result.stderr.strip())
            return False
        return True
