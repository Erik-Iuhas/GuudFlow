"""Install-wide registry of third-party MCP servers (docs/MCP_PLAN.md).

A person sets a server up once in Studio (Settings → MCP servers); every project and role
that has a grant can use it. The registry lives in `{data_root}/mcp_servers.json`.

Secrets never live there. A server's `credential` names an environment variable
(`{"env": "GITHUB_TOKEN"}`); a secret typed into Studio is stored host-only in
`{data_root}/secrets/mcp_keys.json` (directory 0700, file 0600), and an environment variable
wins over a stored secret. Nothing here returns a secret: views carry a key status
(`env` / `stored` / `missing`, or `none` when the server needs no credential). The secret is
only ever handed to `core.harness.mcp_proxy`, which injects it on the host.

How the credential reaches the server:
  - stdio: as the environment variable named by `credential.env`. `env` lists further host
    environment variable names the subprocess may see (its environment is otherwise empty
    apart from PATH/HOME).
  - http: `headers` maps header names to values. `${NAME}` is replaced from the host
    environment and `${credential}` by the secret. With a credential and no header using it,
    `Authorization: Bearer <secret>` is sent.

Tool names are namespaced `{server}__{tool}` where an agent sees them, so a server name is a
slug without a double underscore.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.harness.capabilities import ROLE_TYPES
from core.json_store import read_json, write_json

REGISTRY_FILE = "mcp_servers.json"
KEYS_FILE = Path("secrets") / "mcp_keys.json"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SEP = "__"
MAX_SECRET_CHARS = 4096

KeyStatus = Literal["env", "stored", "missing", "none"]
Transport = Literal["stdio", "http"]
REDACTED = "***"


class McpRegistryError(ValueError):
    """Invalid server definition or registry operation."""


@dataclass
class McpToolInfo:
    """One tool a server offers (cached from `tools/list`)."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(  # Any: a JSON Schema document
        default_factory=lambda: {"type": "object"})

    def to_dict(self) -> dict[str, Any]:  # Any: JSON document
        """JSON form."""
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}


@dataclass
class McpServerSpec:
    """One registered server."""

    name: str
    transport: Transport
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: list[str] = field(default_factory=list)          # host env var names passed through
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    credential: dict[str, str] = field(default_factory=dict)  # {"env": "VAR"} or {}
    enabled: bool = True
    default_grants: dict[str, list[str]] = field(default_factory=dict)  # tool → role types
    tools: list[McpToolInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:  # Any: JSON document
        """The stored form (no secrets: headers hold templates, never the key)."""
        return {"name": self.name, "transport": self.transport, "command": self.command,
                "args": list(self.args), "env": list(self.env), "url": self.url,
                "headers": dict(self.headers), "credential": dict(self.credential),
                "enabled": self.enabled,
                "default_grants": {k: list(v) for k, v in self.default_grants.items()},
                "tools": [t.to_dict() for t in self.tools]}

    def tool_names(self) -> list[str]:
        """Namespaced names (`server__tool`) of the cached tools."""
        return [namespaced(self.name, t.name) for t in self.tools]


def namespaced(server: str, tool: str) -> str:
    """The name an agent sees for `tool` of `server`."""
    return f"{server}{SEP}{tool}"


def split_namespaced(name: str) -> tuple[str, str] | None:
    """`server__tool` → (server, tool); None when `name` is not namespaced."""
    server, sep, tool = name.partition(SEP)
    return (server, tool) if sep and server and tool else None


# ── validation / parsing ────────────────────────────────────────────────────
def _str_list(value: object, what: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise McpRegistryError(f"{what} must be a list of strings")
    return list(value)


def _clean_headers(value: object, previous: Mapping[str, str]) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise McpRegistryError("headers must map header names to strings")
    out: dict[str, str] = {}
    for k, v in value.items():
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", k):
            raise McpRegistryError(f"bad header name {k!r}")
        # The API shows literal values as "***"; sending that back keeps what is stored.
        out[k] = previous.get(k, "") if v == REDACTED and k in previous else v
    return out


def _clean_credential(value: object) -> dict[str, str]:
    if not value:
        return {}
    if not isinstance(value, dict) or set(value) - {"env"}:
        raise McpRegistryError('credential must be {"env": "VAR_NAME"} or empty')
    env = value.get("env", "")
    if not isinstance(env, str) or not ENV_RE.match(env):
        raise McpRegistryError("credential.env must look like MY_SERVER_TOKEN")
    return {"env": env}


def _clean_grants(value: object) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise McpRegistryError("default_grants must map a tool name to a list of role types")
    out: dict[str, list[str]] = {}
    for tool, roles in value.items():
        roles_l = _str_list(roles, f"default_grants[{tool}]")
        unknown = [r for r in roles_l if r not in ROLE_TYPES]
        if unknown:
            raise McpRegistryError(
                f"default_grants[{tool}]: unknown role type {', '.join(unknown)}")
        out[str(tool)] = roles_l
    return out


def _clean_tools(value: object) -> list[McpToolInfo]:
    tools: list[McpToolInfo] = []
    for raw in value if isinstance(value, list) else []:
        if isinstance(raw, dict) and isinstance(raw.get("name"), str) and raw["name"]:
            schema = raw.get("input_schema", raw.get("inputSchema"))
            tools.append(McpToolInfo(
                name=raw["name"], description=str(raw.get("description") or ""),
                input_schema=schema if isinstance(schema, dict) else {"type": "object"}))
    return tools


def spec_from_dict(data: Mapping[str, Any], previous: McpServerSpec | None = None  # Any: JSON
                   ) -> McpServerSpec:
    """Validate a server document. `previous` supplies cached tools and redacted headers."""
    name = data.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise McpRegistryError("name must be a slug: lowercase letters, digits and '-', "
                               "starting with a letter (max 32)")
    transport = data.get("transport")
    if transport not in ("stdio", "http"):
        raise McpRegistryError("transport must be 'stdio' or 'http'")
    command = str(data.get("command") or "").strip()
    url = str(data.get("url") or "").strip()
    if transport == "stdio" and not command:
        raise McpRegistryError("a stdio server needs a command")
    if transport == "http" and not re.match(r"^https?://\S+$", url):
        raise McpRegistryError("an http server needs an http(s) url")
    env = _str_list(data.get("env"), "env")
    for e in env:
        if not ENV_RE.match(e):
            raise McpRegistryError(f"env entries are variable names, got {e!r}")
    tools = _clean_tools(data["tools"]) if "tools" in data else (previous.tools if previous else [])
    return McpServerSpec(
        name=name, transport=transport, command=command,
        args=_str_list(data.get("args"), "args"), env=env, url=url,
        headers=_clean_headers(data.get("headers"), previous.headers if previous else {}),
        credential=_clean_credential(data.get("credential")),
        enabled=bool(data.get("enabled", True)),
        default_grants=_clean_grants(data.get("default_grants")), tools=tools)


# ── secrets ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class McpKeyStore:
    """Host-only secrets of MCP servers: `{data_root}/secrets/mcp_keys.json`, mode 0600."""

    data_root: Path

    @property
    def path(self) -> Path:
        """Where the secrets live."""
        return Path(self.data_root) / KEYS_FILE

    def _read(self) -> dict[str, str]:
        doc = read_json(self.path, {})
        return {k: v for k, v in doc.items() if isinstance(v, str)} if isinstance(doc, dict) else {}

    def get(self, server: str) -> str | None:
        """The stored secret for a server, if any."""
        return self._read().get(server) or None

    def set(self, server: str, secret: str) -> None:
        """Store (or replace) a server's secret."""
        secret = secret.strip()
        if not secret or len(secret) > MAX_SECRET_CHARS or "\n" in secret or "\r" in secret:
            raise McpRegistryError("the secret must be a single non-empty line")
        keys = self._read()
        keys[server] = secret
        self._write(keys)

    def clear(self, server: str) -> bool:
        """Forget a stored secret; True if one was stored."""
        keys = self._read()
        if keys.pop(server, None) is None:
            return False
        self._write(keys)
        return True

    def _write(self, keys: dict[str, str]) -> None:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(keys, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)


# ── registry ────────────────────────────────────────────────────────────────
class McpRegistry:
    """Reads and writes `{data_root}/mcp_servers.json` and resolves credentials."""

    def __init__(self, data_root: Path | str) -> None:
        self.data_root = Path(data_root)
        self.keys = McpKeyStore(self.data_root)

    @property
    def path(self) -> Path:
        """The registry file."""
        return self.data_root / REGISTRY_FILE

    # storage
    def _load(self) -> dict[str, McpServerSpec]:
        doc = read_json(self.path, {})
        servers = doc.get("servers") if isinstance(doc, dict) else None
        out: dict[str, McpServerSpec] = {}
        for name, raw in (servers or {}).items() if isinstance(servers, dict) else []:
            try:
                out[name] = spec_from_dict({**raw, "name": name})
            except McpRegistryError:
                continue  # a hand-edited bad entry must not take the others down
        return out

    def _save(self, servers: Mapping[str, McpServerSpec]) -> None:
        write_json(self.path, {"servers": {n: s.to_dict() for n, s in servers.items()}})

    def list(self) -> list[McpServerSpec]:
        """Every registered server, in name order."""
        return [s for _, s in sorted(self._load().items())]

    def get(self, name: str) -> McpServerSpec | None:
        """One server, or None."""
        return self._load().get(name)

    def put(self, data: Mapping[str, Any], *, create: bool | None = None  # Any: JSON
            ) -> McpServerSpec:
        """Add or replace a server. `create=True` refuses an existing name, `False` a missing one."""
        servers = self._load()
        name = data.get("name")
        previous = servers.get(name) if isinstance(name, str) else None
        if create is True and previous is not None:
            raise McpRegistryError(f"server {name!r} already exists")
        if create is False and previous is None:
            raise McpRegistryError(f"unknown server {name!r}")
        spec = spec_from_dict(data, previous)
        # A tool the server no longer offers cannot keep a grant.
        spec.default_grants = {t: r for t, r in spec.default_grants.items()
                               if not spec.tools or any(x.name == t for x in spec.tools)}
        servers[spec.name] = spec
        self._save(servers)
        return spec

    def delete(self, name: str) -> bool:
        """Remove a server and its stored secret; True if it existed."""
        servers = self._load()
        if servers.pop(name, None) is None:
            return False
        self._save(servers)
        self.keys.clear(name)
        return True

    def set_tools(self, name: str, tools: Iterable[Mapping[str, Any]]  # Any: JSON
                  ) -> McpServerSpec:
        """Replace a server's cached tool list (after a successful connection test)."""
        servers = self._load()
        spec = servers.get(name)
        if spec is None:
            raise McpRegistryError(f"unknown server {name!r}")
        spec.tools = _clean_tools(list(tools))
        spec.default_grants = {t: r for t, r in spec.default_grants.items()
                               if any(x.name == t for x in spec.tools)}
        self._save(servers)
        return spec

    # credentials
    def secret(self, spec: McpServerSpec) -> str | None:
        """The server's secret for host-side injection: its env var, else the stored one."""
        env = spec.credential.get("env", "")
        return (os.environ.get(env) if env else None) or self.keys.get(spec.name) or None

    def key_status(self, spec: McpServerSpec) -> KeyStatus:
        """Where the secret comes from (never the secret itself)."""
        env = spec.credential.get("env", "")
        if env and os.environ.get(env):
            return "env"
        if self.keys.get(spec.name):
            return "stored"
        return "missing" if env else "none"

    # views
    def view(self, spec: McpServerSpec) -> dict[str, Any]:  # Any: JSON document
        """The API form of a server: no secrets, literal header values masked."""
        d = spec.to_dict()
        d["headers"] = {k: (v if "${" in v else REDACTED) for k, v in spec.headers.items()}
        d["key"] = self.key_status(spec)
        return d

    def offers(self) -> dict[str, frozenset[str]]:
        """Namespaced tool → role types that get it by default, for enabled servers' cached tools."""
        out: dict[str, frozenset[str]] = {}
        for spec in self._load().values():
            if not spec.enabled:
                continue
            for tool in spec.tools:
                out[namespaced(spec.name, tool.name)] = frozenset(
                    spec.default_grants.get(tool.name, ()))
        return out

    # import
    def import_servers(self, doc: Mapping[str, Any], *, replace: bool = False  # Any: JSON
                       ) -> dict[str, Any]:  # Any: JSON
        """Import a standard `mcp.json` (`mcpServers`) or an opencode config (`mcp`) block.

        Existing names are skipped unless `replace`. Literal secrets found in `env` or an
        `Authorization` header move to the host-only secret store, never into the registry.
        Returns `{"imported": [...], "skipped": [{name, reason}], "warnings": [...]}`.
        """
        block = doc.get("mcpServers")
        if block is None:
            block = doc.get("mcp")
        if block is None and all(isinstance(v, dict) for v in doc.values()) and doc:
            block = doc  # a bare {name: {...}} map
        if not isinstance(block, dict):
            raise McpRegistryError('expected {"mcpServers": {...}} or an opencode "mcp" block')
        existing = self._load()
        imported: list[str] = []
        skipped: list[dict[str, str]] = []
        warnings: list[str] = []
        for raw_name, entry in block.items():
            name = _slug(str(raw_name))
            try:
                if not isinstance(entry, dict):
                    raise McpRegistryError("entry is not an object")
                data, secret = _entry_to_spec(name, entry, warnings)
                if name in existing and not replace:
                    skipped.append({"name": name, "reason": "already exists"})
                    continue
                self.put(data)
                if secret:
                    self.keys.set(name, secret)
                imported.append(name)
            except McpRegistryError as exc:
                skipped.append({"name": str(raw_name), "reason": str(exc)})
        return {"imported": imported, "skipped": skipped, "warnings": warnings}


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    if not slug or not slug[0].isalpha():
        slug = f"s-{slug}" if slug else "server"
    return slug[:32].rstrip("-") or "server"


_REF = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_BEARER_REF = re.compile(r"^Bearer\s+\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")


def _entry_to_spec(name: str, entry: Mapping[str, Any], warnings: list[str]  # Any: JSON
                   ) -> tuple[dict[str, Any], str | None]:  # Any: JSON
    """One mcp.json / opencode entry → (registry document, literal secret to store)."""
    secret: str | None = None
    credential: dict[str, str] = {}
    env: list[str] = []
    data: dict[str, Any] = {"name": name, "enabled": entry.get("enabled", True) is not False}

    def take_secret(var: str, value: str) -> bool:
        nonlocal secret
        if secret is None and not credential:
            secret = value
            credential["env"] = var
            return True
        warnings.append(f"{name}: a second literal secret ({var}) was not imported; "
                        "set it as an environment variable on the host")
        return False

    kind = str(entry.get("type") or "").lower()
    if entry.get("command") is not None or kind in ("local", "stdio"):
        cmd = entry.get("command")
        if isinstance(cmd, list):  # opencode: command is the whole argv
            parts = [str(c) for c in cmd]
            command, args = (parts[0] if parts else ""), parts[1:]
        else:
            command, args = str(cmd or ""), [str(a) for a in entry.get("args") or []]
        raw_env = entry.get("env") or entry.get("environment") or {}
        for var, value in (raw_env.items() if isinstance(raw_env, dict) else []):
            value = str(value)
            if not value or _REF.match(value):
                ref = _REF.match(value)
                var_name = ref.group(1) if ref else str(var)
                if var_name == var:
                    (credential.update(env=var) if _looks_secret(var) and not credential
                     else env.append(str(var)))
                else:
                    warnings.append(f"{name}: {var}=${var_name} renamed variables are not "
                                    f"supported; export {var} on the host")
                    env.append(str(var))
            elif _looks_secret(str(var)):
                take_secret(str(var), value)
            else:
                warnings.append(f"{name}: literal value of {var} was not imported "
                                "(only secrets and variable names are kept)")
        data.update(transport="stdio", command=command, args=args, env=env)
    elif entry.get("url"):
        headers: dict[str, str] = {}
        for k, v in (entry.get("headers") or {}).items():
            v = str(v)
            if (k.lower() == "authorization" or _looks_secret(str(k))) and "${" not in v \
                    and "{env:" not in v:
                m = re.match(r"^(Bearer\s+)(.*)$", v, flags=re.I)
                prefix, token = (m.group(1), m.group(2)) if m else ("", v)
                if take_secret("MCP_" + re.sub(r"[^A-Za-z0-9]", "_", str(k)).upper(), token):
                    headers[str(k)] = prefix + "${credential}"
            elif "${" in v or "{env:" in v:
                headers[str(k)] = re.sub(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}", r"${\1}", v)
            else:
                headers[str(k)] = v
        data.update(transport="http", url=str(entry["url"]), headers=headers)
    else:
        raise McpRegistryError("entry has neither a command nor a url")
    data["credential"] = credential
    return data, secret


def _looks_secret(var: str) -> bool:
    return bool(re.search(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PAT)", var.upper()))
