"""Shared project dependencies (plan §16).

The repo's manifest (`package.json`, `requirements.txt`) is the source of truth. The host
installs exactly what it pins into one environment per manifest,
`<deps root>/<manager>-<hash>/`, and points `<deps root>/<manager>` at the current one.
Every role sees the deps root read-only (`/deps` in a sandbox), so an environment
synced by one worker is there for all of them.

Installs run on the host with the same rules as `request_package`: exact pins only, no
install scripts (`npm --ignore-scripts`), binary wheels only (`pip --only-binary=:all:`), a
fixed registry, no shell, and never in the agent's worktree (the host writes its own
package.json / requirements file from the parsed pins, so nothing the agent wrote steers it).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.harness.artifacts import safe_resolve

Installer = Callable[[list[str]], Awaitable[tuple[int, str]]]

MANAGERS = ("npm", "pip")
MANIFEST = {"npm": "package.json", "pip": "requirements.txt"}
MAX_MANIFEST_BYTES = 200_000
MAX_PACKAGES = 300
NPM_REGISTRY = "https://registry.npmjs.org/"
STATE_FILE = "state.json"
SANDBOX_MOUNT = "/deps"

_NPM_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$", re.I)
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")
_PIP_LINE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?P<extras>\[[A-Za-z0-9,._ -]+\])?\s*==\s*"
    r"(?P<version>[0-9][0-9A-Za-z.+!-]*)$")


class DepsError(ValueError):
    """A manifest that cannot be synced, with every problem in the message."""


@dataclass
class Package:
    """One pinned dependency and where it came from."""

    name: str
    version: str
    added_by: str = ""
    approved_by: str = ""
    run_id: str = ""
    at: float = 0.0


@dataclass
class DepsEnv:
    """The current environment of one manager."""

    manager: str
    key: str
    packages: list[Package] = field(default_factory=list)

    def pins(self) -> dict[str, str]:
        """Name → version (the name as written in the manifest)."""
        return {p.name: p.version for p in self.packages}


def normalise(manager: str, name: str) -> str:
    """Canonical package name: PEP 503 for pip, lower case for npm."""
    return re.sub(r"[-_.]+", "-", name).lower() if manager == "pip" else name.lower()


# ── reading the manifest ────────────────────────────────────────────────────
def read_pins(worktree: Path, manager: str) -> dict[str, str]:
    """The exact pins in the manifest at the root of `worktree`.

    Raises DepsError listing every entry that is not an exact pin (ranges, URLs, git or
    file sources, options), so the worker can fix them all at once.
    """
    if manager not in MANAGERS:
        raise DepsError(f"manager must be one of {', '.join(MANAGERS)}")
    path = safe_resolve(worktree, MANIFEST[manager])
    if path is None or not path.is_file():
        raise DepsError(f"no {MANIFEST[manager]} at the root of your working directory")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise DepsError(f"{MANIFEST[manager]} is larger than {MAX_MANIFEST_BYTES} bytes")
    text = path.read_text(encoding="utf-8", errors="replace")
    pins, problems = (_npm_pins(text) if manager == "npm" else _pip_pins(text))
    if len(pins) > MAX_PACKAGES:
        problems.append(f"more than {MAX_PACKAGES} packages")
    if problems:
        raise DepsError(f"{MANIFEST[manager]} needs exact pins: " + "; ".join(problems))
    return pins


def _npm_pins(text: str) -> tuple[dict[str, str], list[str]]:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, [f"not valid JSON ({exc.msg} at line {exc.lineno})"]
    if not isinstance(doc, dict):
        return {}, ["not a JSON object"]
    pins: dict[str, str] = {}
    problems: list[str] = []
    for section in ("dependencies", "devDependencies"):
        block = doc.get(section) or {}
        if not isinstance(block, dict):
            problems.append(f"{section} is not an object")
            continue
        for name, spec in block.items():
            version = spec.strip().lstrip("=").lstrip("v") if isinstance(spec, str) else ""
            if not _NPM_NAME.match(name):
                problems.append(f"{name!r} is not a package name")
            elif not _SEMVER.match(version):
                problems.append(f"{name}: {spec!r} is not an exact version (write e.g. \"1.2.3\", "
                                "not \"^1.2.3\", a range, a URL or a path)")
            else:
                pins[name] = version
    return pins, problems


def _pip_pins(text: str) -> tuple[dict[str, str], list[str]]:
    pins: dict[str, str] = {}
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        m = _PIP_LINE.match(line)
        if m is None:
            problems.append(f"line {number}: {line[:80]!r} is not `name==version`")
            continue
        pins[m.group("name") + (m.group("extras") or "").replace(" ", "")] = m.group("version")
    return pins, problems


def env_key(manager: str, pins: dict[str, str]) -> str:
    """`<manager>-<hash>`: the same pins always name the same environment."""
    body = "\n".join(f"{normalise(manager, n)}=={v}" for n, v in sorted(
        pins.items(), key=lambda kv: normalise(manager, kv[0])))
    return f"{manager}-{hashlib.sha256(body.encode()).hexdigest()[:16]}"


# ── the store ───────────────────────────────────────────────────────────────
class ProjectDeps:
    """The environments of one project under `root` (`data/projects/{id}/deps`)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _state(self) -> dict[str, dict[str, object]]:
        try:
            data = json.loads((self.root / STATE_FILE).read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def current(self, manager: str) -> DepsEnv | None:
        """The active environment of `manager`, or None before the first sync."""
        raw = self._state().get(manager)
        if not isinstance(raw, dict) or not isinstance(raw.get("key"), str):
            return None
        packages = [Package(**{k: v for k, v in p.items() if k in Package.__dataclass_fields__})
                    for p in raw.get("packages") or [] if isinstance(p, dict)]
        return DepsEnv(manager, str(raw["key"]), packages)

    def all(self) -> list[DepsEnv]:
        """Every manager's active environment."""
        return [env for m in MANAGERS if (env := self.current(m)) is not None]

    def env_dir(self, key: str) -> Path:
        return self.root / key

    def built(self, key: str) -> bool:
        """Has the environment for `key` been installed (and not left half-built)?"""
        return (self.env_dir(key) / ".complete").is_file()

    async def build(self, manager: str, pins: dict[str, str], install: Installer) -> tuple[int, str]:
        """Install `pins` into the environment for them (reused when already built)."""
        key = env_key(manager, pins)
        if self.built(key):
            return 0, "reused"
        self.root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{key}-", dir=self.root))
        try:
            code, output = await install(_install_argv(manager, pins, staging))
            if code != 0:
                return code, output
            (staging / ".complete").write_text(json.dumps(pins, sort_keys=True))
            target = self.env_dir(key)
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
            return 0, output
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def activate(self, manager: str, packages: list[Package]) -> DepsEnv:
        """Make the (built) environment for `packages` current: record it and swap the link."""
        env = DepsEnv(manager, env_key(manager, {p.name: p.version for p in packages}), packages)
        link = self.root / manager
        tmp = self.root / f".{manager}.link"
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(env.key, tmp)       # relative, so it resolves under /deps too
        os.replace(tmp, link)
        state = self._state()
        state[manager] = {"key": env.key, "packages": [asdict(p) for p in packages],
                          "updated_at": time.time()}
        tmp_state = self.root / f".{STATE_FILE}.tmp"
        tmp_state.write_text(json.dumps(state, indent=1))
        os.replace(tmp_state, self.root / STATE_FILE)
        return env


def merge_provenance(manager: str, pins: dict[str, str], old: DepsEnv | None, *, role: str,
                     approved_by: str, run_id: str) -> list[Package]:
    """The new package list: unchanged pins keep who added them, new or changed ones are `role`'s."""
    before = {normalise(manager, p.name): p for p in (old.packages if old else [])}
    out: list[Package] = []
    now = time.time()
    for name, version in sorted(pins.items(), key=lambda kv: normalise(manager, kv[0])):
        prev = before.get(normalise(manager, name))
        if prev is not None and prev.version == version:
            out.append(prev)
        else:
            out.append(Package(name, version, role, approved_by, run_id, now))
    return out


# ── how roles see it ────────────────────────────────────────────────────────
def role_env(mount: str, base_path: str) -> dict[str, str]:
    """NODE_PATH, PYTHONPATH and PATH entries for the deps root at `mount`.

    The `<mount>/<manager>` links move when an environment is synced, so a running role
    sees the new one from its next command."""
    npm, pip = f"{mount}/npm/node_modules", f"{mount}/pip/site-packages"
    return {
        "NODE_PATH": npm,
        "PYTHONPATH": pip,
        "PATH": f"{npm}/.bin:{pip}/bin:{base_path}" if base_path else f"{npm}/.bin:{pip}/bin",
    }


def link_node_modules(worktree: Path, mount: str) -> str | None:
    """Point `<worktree>/node_modules` at the shared npm environment (ESM imports and bundlers
    ignore NODE_PATH). Never replaces a real directory; returns why not, or None."""
    link = worktree / "node_modules"
    target = f"{mount}/npm/node_modules"
    if link.is_symlink():
        if os.readlink(link) == target:
            return None
        link.unlink()
    elif link.exists():
        return "node_modules already exists as a real folder; remove it to use the shared one"
    os.symlink(target, link)
    return None


def _install_argv(manager: str, pins: dict[str, str], prefix: Path) -> list[str]:
    """The installer command for a host-written manifest in `prefix` (no shell, no scripts)."""
    if manager == "pip":
        req = prefix / "requirements.txt"
        req.write_text("".join(f"{n}=={v}\n" for n, v in sorted(pins.items())))
        return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                "--isolated", "--only-binary=:all:", "--target", str(prefix / "site-packages"), "-r", str(req)]
    (prefix / "package.json").write_text(json.dumps(
        {"name": "guudflow-deps", "private": True, "dependencies": dict(sorted(pins.items()))},
        indent=1))
    (prefix / ".npmrc").write_text("")
    return ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund",
            f"--registry={NPM_REGISTRY}", "--prefix", str(prefix),
            "--userconfig", str(prefix / ".npmrc")]
