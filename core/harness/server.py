"""Launch and stop a locked-down `opencode serve` process.

Plan §5.2. Every opencode server GuudFlow starts goes through `OpencodeServer`, in
host mode (`host_dev`) and in tests. The container path (P3) builds the same
environment via `locked_env()` and runs it inside the sandbox instead.

Security properties enforced here:
  - The child environment is built from an allow-list. The host environment is
    NOT inherited, so host API keys and tokens never reach an agent unless the
    caller passes them explicitly in `extra_env`.
  - HOME / XDG dirs point at a private per-server directory, so host skills and
    config (e.g. ~/.claude/skills) are not picked up (gotcha #3).
  - Project config in the working directory is ignored
    (OPENCODE_DISABLE_PROJECT_CONFIG), so an agent cannot widen its own
    permissions by writing an opencode.json (gotcha #2).
  - The server is password protected and bound to 127.0.0.1 by default.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# Pinned opencode version. Upgrades are deliberate: bump this, re-run
# scripts/install_opencode.sh, and update tests/harness/opencode_api_snapshot.json.
OPENCODE_VERSION = "1.18.34"

# Where scripts/install_opencode.sh puts the binary (relative to the repo root).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_BIN = _REPO_ROOT / ".tools" / "opencode" / "node_modules" / ".bin" / "opencode"

# Env vars copied from the host. Nothing else is inherited.
_HOST_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE",
                       "NODE_EXTRA_CA_CERTS", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
                       "https_proxy", "http_proxy", "no_proxy")
# opencode reaches GuudFlow's gateway and MCP server on this machine; a proxy must never carry
# those calls (a company proxy cannot reach them and the turn hangs).
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def _no_proxy(env: dict[str, str]) -> str:
    """NO_PROXY with the local hosts always in it."""
    have = [h.strip() for h in (env.get("NO_PROXY") or env.get("no_proxy") or "").split(",") if h.strip()]
    return ",".join(have + [h for h in _LOCAL_HOSTS if h not in have])

# Flags from plan §5.2. All are verified to exist in the pinned binary.
LOCKED_FLAGS: dict[str, str] = {
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_SHARE": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    # No telemetry: opencode's OpenTelemetry export is opt-in (config
    # `experimental.openTelemetry`, set false in config_gen); this also stops the OTel SDK
    # from exporting even if some OTEL_* variable were present.
    "OTEL_SDK_DISABLED": "true",
}


class OpencodeNotInstalled(RuntimeError):
    """Raised when no opencode binary can be found."""


def find_opencode_binary() -> Path:
    """Locate the pinned opencode binary.

    Order: $OPENCODE_BIN, the repo-local install from scripts/install_opencode.sh.
    A binary on $PATH is deliberately not used: it may be a different version.
    """
    env_bin = os.environ.get("OPENCODE_BIN")
    if env_bin:
        path = Path(env_bin)
        if path.is_file():
            return path
        raise OpencodeNotInstalled(f"OPENCODE_BIN={env_bin} does not exist")
    modules = _DEFAULT_BIN.parents[1]
    if os.name != "nt":
        try:
            return native_opencode_binary()
        except OpencodeNotInstalled:
            pass
        # A checkout shared with Windows (WSL on /mnt/c) can hold an install made by Windows
        # npm: its shim runs opencode.exe through WSL interop, as a Windows process that gets
        # none of the locked environment (no config, no password, the user's own home) and
        # listens where WSL cannot reach it.
        if (modules / "opencode-ai" / "bin" / "opencode.exe").exists():
            raise OpencodeNotInstalled(
                f"{modules.parent} holds a Windows opencode (installed by Windows npm); "
                "run scripts/install_opencode.sh on this machine to add the Linux binary")
        if _DEFAULT_BIN.exists():
            return _DEFAULT_BIN
    else:
        # npm on Windows leaves a .cmd shim, not a runnable `opencode`: use the native exe.
        for pkg in ("opencode-windows-x64", "opencode-windows-x64-baseline", "opencode-windows-arm64"):
            exe = modules / pkg / "bin" / "opencode.exe"
            if exe.is_file():
                return exe
    raise OpencodeNotInstalled(
        f"opencode {OPENCODE_VERSION} not found; run scripts/install_opencode.sh"
    )


def native_opencode_binary() -> Path:
    """The platform-native opencode executable behind the npm wrapper (for the sandbox image)."""
    root = _DEFAULT_BIN.parents[1]  # .tools/opencode/node_modules
    for pkg in ("opencode-linux-x64", "opencode-linux-arm64", "opencode-linux-x64-baseline"):
        candidate = root / pkg / "bin" / "opencode"
        if candidate.is_file():
            return candidate
    raise OpencodeNotInstalled(
        f"native opencode binary not found under {root}; run scripts/install_opencode.sh")


def free_port() -> int:
    """Return a free TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def locked_env(
    home: Path,
    config: dict[str, Any],
    password: str,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the full environment for an opencode server (plan §5.2).

    `config` is the opencode config document (see core.harness.config_gen); it is
    passed via OPENCODE_CONFIG_CONTENT. `extra_env` is the only way to hand a
    secret (e.g. a provider key in host_dev mode) to the child process.
    """
    env = {k: os.environ[k] for k in _HOST_ENV_ALLOWLIST if k in os.environ}
    env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".data"),
        "XDG_STATE_HOME": str(home / ".state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "OPENCODE_CONFIG_CONTENT": json.dumps(config),
        "OPENCODE_SERVER_PASSWORD": password,
    })
    env["NO_PROXY"] = env["no_proxy"] = _no_proxy(env)
    env.update(LOCKED_FLAGS)
    if extra_env:
        env.update(extra_env)
    return env


@dataclass
class OpencodeServer:
    """One running `opencode serve` process.

    Use as an async context manager, or call `start()` / `stop()`.
    `workdir` is the directory the server is started in; sessions pick their
    project directory per request (`?directory=`).
    """

    config: dict[str, Any]
    home: Path
    workdir: Path
    extra_env: dict[str, str] = field(default_factory=dict)
    hostname: str = "127.0.0.1"
    port: int = 0
    password: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    # The first start in a fresh home unpacks and indexes; on a slow disk (WSL on /mnt/c) that
    # can take well over a minute. GUUDFLOW_OPENCODE_STARTUP_S overrides it.
    startup_timeout: float = field(default_factory=lambda: float(
        os.environ.get("GUUDFLOW_OPENCODE_STARTUP_S") or 180.0))
    _proc: asyncio.subprocess.Process | None = field(default=None, init=False, repr=False)
    _log_path: Path | None = field(default=None, init=False, repr=False)
    _tmp: Path | None = field(default=None, init=False, repr=False)

    @property
    def base_url(self) -> str:
        """HTTP base URL of the running server."""
        return f"http://{self.hostname}:{self.port}"

    @property
    def log_path(self) -> Path | None:
        """File that receives the server's stdout/stderr."""
        return self._log_path

    async def start(self) -> "OpencodeServer":
        """Start the server and wait until /global/health answers."""
        binary = find_opencode_binary()
        self.home.mkdir(parents=True, exist_ok=True)
        if not self.port:
            self.port = free_port()
        env = locked_env(self.home, self.config, self.password, self.extra_env)
        # opencode (a bun binary) unpacks a ~5 MB native library into TMPDIR on every start
        # and never removes it; a private TMPDIR is deleted on stop instead of filling /tmp.
        self._tmp = self.home / ".tmp"
        self._tmp.mkdir(parents=True, exist_ok=True)
        env.setdefault("TMPDIR", str(self._tmp))
        self._log_path = self.home / "opencode-serve.log"
        log = self._log_path.open("wb")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                str(binary), "serve", "--hostname", self.hostname, "--port", str(self.port),
                cwd=str(self.workdir), env=env,
                stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True,
            )
        finally:
            log.close()
        await self._wait_healthy()
        await self._check_providers()
        return self

    async def _check_providers(self) -> None:
        """Refuse a server that offers a provider outside the config's `enabled_providers`.

        opencode's own hosted models (provider `opencode`) must never be callable. If the
        config did not reach the process, it falls back to its defaults; such a server is
        stopped before it gets a prompt.
        """
        allowed = self.config.get("enabled_providers")
        if allowed is None:
            return
        auth = httpx.BasicAuth("opencode", self.password)
        async with httpx.AsyncClient(auth=auth, timeout=self.startup_timeout,
                                     trust_env=False) as client:
            resp = await client.get(f"{self.base_url}/config/providers")
        resp.raise_for_status()
        extra = sorted({p["id"] for p in resp.json()["providers"]} - set(allowed))
        if extra:
            await self.stop()
            raise RuntimeError(f"opencode serve offers providers outside GuudFlow's {allowed}: "
                               f"{extra}; refusing it (its config did not apply)")

    async def _wait_healthy(self) -> None:
        deadline = asyncio.get_running_loop().time() + self.startup_timeout
        auth = httpx.BasicAuth("opencode", self.password)
        # trust_env=False: the server is on localhost, and an HTTP(S)_PROXY from the environment
        # would send the health check to the proxy, so it never sees the server.
        async with httpx.AsyncClient(auth=auth, timeout=2.0, trust_env=False) as client:
            while True:
                if self._proc is not None and self._proc.returncode is not None:
                    raise RuntimeError(
                        f"opencode serve exited with {self._proc.returncode}; see {self._log_path}"
                        + self._log_tail()
                    )
                try:
                    resp = await client.get(f"{self.base_url}/global/health")
                    if resp.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                if asyncio.get_running_loop().time() > deadline:
                    tail = self._log_tail()
                    await self.stop()
                    raise TimeoutError(f"opencode serve not healthy after "
                                       f"{self.startup_timeout:g}s; see {self._log_path}" + tail)
                await asyncio.sleep(0.25)

    def _log_tail(self, lines: int = 15) -> str:
        """The end of the server log, for an error message."""
        try:
            text = (self._log_path.read_text(errors="replace") if self._log_path else "")
        except OSError:
            return ""
        tail = "\n".join(text.strip().splitlines()[-lines:])
        return f"\n--- last lines of the opencode log ---\n{tail}" if tail else \
            "\n(the opencode log is empty: the binary may not have started at all)"

    async def stop(self) -> None:
        """Terminate the server (SIGTERM, then SIGKILL after 5s)."""
        proc, self._proc = self._proc, None
        try:
            if proc is None or proc.returncode is not None:
                return
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        finally:
            if self._tmp is not None:
                shutil.rmtree(self._tmp, ignore_errors=True)

    async def __aenter__(self) -> "OpencodeServer":
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()


def opencode_available() -> bool:
    """True when the pinned opencode binary is installed (used to skip tests)."""
    try:
        find_opencode_binary()
    except OpencodeNotInstalled:
        return False
    return shutil.which("git") is not None
