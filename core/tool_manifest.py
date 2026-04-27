"""ToolManifest — what an agent is actually allowed to call at runtime.

DESIGN §31. Per-agent intersection of:
  - declared local tools
  - declared MCP servers (filtered by RBAC and the server's catalog)

The manifest is built once at run start by the runner. It is the source
of truth for the LLM's tool catalog (rendered into Layer 5 of
`effective_system_prompt()`) AND for `BaseAgent.call_tool` dispatch.

Two-point enforcement (DESIGN §31, rule 76):
  - Manifest gates what the LLM sees. The prompt only lists what's callable.
  - Broker re-validates at call time as defense in depth.

This module defines the data classes and the builder. The builder takes
the project config and an RBAC view, plus the broker's MCP catalogs, and
returns one manifest per agent role.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal


Invocation = Literal["local", "broker"]


@dataclass
class ToolDescriptor:
    """One entry in an agent's tool catalog.

    `invocation` decides where `BaseAgent.call_tool` routes:
      - "local"  → run the registered BaseTool subclass directly
      - "broker" → route through the credentials broker
    `scope` is set for broker-routed tools only (the RBAC scope to check).
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    invocation: Invocation
    scope: str | None = None
    server: str | None = None  # set for MCP tools — convenience for the broker

    def __post_init__(self) -> None:
        if self.invocation == "broker" and not self.scope:
            raise ValueError(
                f"broker-invoked descriptor {self.name!r} must declare a scope"
            )
        if self.invocation == "local" and self.scope is None:
            # Local tools may also have an RBAC scope (e.g. fs:write requires
            # a scope grant). Not required, but we don't enforce absence.
            pass


@dataclass
class ToolManifest:
    """Per-agent tool catalog. Iterable over descriptors."""

    descriptors: list[ToolDescriptor] = field(default_factory=list)

    def __iter__(self):
        return iter(self.descriptors)

    def __len__(self) -> int:
        return len(self.descriptors)

    def find(self, name: str) -> ToolDescriptor | None:
        for d in self.descriptors:
            if d.name == name:
                return d
        return None

    def names(self) -> list[str]:
        return [d.name for d in self.descriptors]

    def render_for_prompt(self) -> str:
        """Compact catalog for injection into Layer 5 of effective_system_prompt.

        Format is Markdown-ish: tool name, one-line description, arg shape.
        Tools that share a category are grouped to keep the catalog scannable.
        """
        if not self.descriptors:
            return ""

        # Group by category prefix ("fs-", "doc-", "mcp-github-" → "mcp-github").
        groups: dict[str, list[ToolDescriptor]] = {}
        for d in self.descriptors:
            head, _, _ = d.name.partition("-")
            if head == "mcp" and "-" in d.name[4:]:
                # mcp-server-tool → group by "mcp-server"
                _, server, _ = d.name.split("-", 2)
                key = f"mcp-{server}"
            else:
                key = head or "tools"
            groups.setdefault(key, []).append(d)

        lines = ["AVAILABLE TOOLS:", ""]
        for category in sorted(groups.keys()):
            lines.append(f"### {category}")
            for d in groups[category]:
                lines.append(f"- `{d.name}` — {d.description}")
                if d.input_schema:
                    args = _summarise_schema(d.input_schema)
                    if args:
                        lines.append(f"    arguments: {args}")
            lines.append("")
        return "\n".join(lines).rstrip()


def _summarise_schema(schema: dict[str, Any]) -> str:
    """One-line summary of a JSONSchema for prompt rendering. Best-effort —
    handles the common `{"type": "object", "properties": {...}}` case."""
    if not isinstance(schema, dict):
        return ""
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return ""
    required = set(schema.get("required") or [])
    parts = []
    for name, prop in props.items():
        ptype = prop.get("type", "any") if isinstance(prop, dict) else "any"
        marker = "" if name in required else "?"
        parts.append(f"{name}{marker}:{ptype}")
    return "{" + ", ".join(parts) + "}"


# ── Builder ────────────────────────────────────────────────────────────────


@dataclass
class RoleRBAC:
    """The slice of project RBAC config that pertains to one role."""

    role: str
    allowed_scopes: list[str] = field(default_factory=list)

    def grants(self, scope: str) -> bool:
        """True if this role's allowed_scopes covers `scope`.

        A scope grant covers itself and all sub-scopes:
          - "mcp-github" grants "mcp-github" and "mcp-github-read" and
            "mcp-github-get_pr".
          - "mcp-github-read" grants only "mcp-github-read".
        """
        for granted in self.allowed_scopes:
            if scope == granted:
                return True
            if scope.startswith(granted + "-"):
                return True
        return False

    def has_any_in(self, prefix: str) -> bool:
        """True if this role has ANY scope under `prefix` — used to skip
        whole MCP servers the role can't touch at all."""
        for granted in self.allowed_scopes:
            if granted == prefix or granted.startswith(prefix + "-") or prefix.startswith(granted + "-") or prefix.startswith(granted):
                return True
        return False


CatalogProvider = Callable[[str], Iterable["MCPToolEntry"]]


@dataclass
class MCPToolEntry:
    """One tool fetched from an MCP server's `list_tools` response.

    `verb_scope` is set by the broker if the project config defines
    verb-level scopes for this MCP server (DESIGN §31). It is the suffix
    (e.g. "read" / "write") — the full required scope is
    "mcp:{server}:{verb_scope}". If None, the required scope is
    "mcp:{server}:{tool_name}" or "mcp:{server}" depending on grant
    granularity.
    """

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    verb_scope: str | None = None


@dataclass
class LocalToolEntry:
    """Metadata about a registered local built-in tool."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


class ToolManifestBuilder:
    """Builds a ToolManifest for a specific agent role.

    The builder is project-scoped (carries RBAC + local registry + MCP
    catalogs) and produces a per-role manifest. Stage 5's runner constructs
    one builder per run.
    """

    def __init__(
        self,
        local_registry: dict[str, LocalToolEntry],
        mcp_catalogs: dict[str, list[MCPToolEntry]],
        rbac: dict[str, RoleRBAC],
    ):
        self.local_registry = local_registry
        self.mcp_catalogs = mcp_catalogs
        self.rbac = rbac

    def build_for_agent(
        self,
        role: str,
        declared_local_tools: Iterable[str],
        declared_mcp_servers: Iterable[str],
    ) -> ToolManifest:
        descriptors: list[ToolDescriptor] = []
        role_rbac = self.rbac.get(role) or RoleRBAC(role=role)

        # 1. Local tools
        for tool_name in declared_local_tools:
            entry = self.local_registry.get(tool_name)
            if entry is None:
                continue  # declared but not registered — skip silently
            scope = tool_name  # local scope name == tool name
            if not _scope_is_baseline(scope) and not role_rbac.grants(scope):
                continue
            descriptors.append(ToolDescriptor(
                name=entry.name,
                description=entry.description,
                input_schema=entry.input_schema,
                invocation="local",
                scope=scope,
            ))

        # 2. MCP tools
        for server in declared_mcp_servers:
            server_prefix = f"mcp-{server}"
            if not role_rbac.has_any_in(server_prefix):
                continue
            catalog = self.mcp_catalogs.get(server) or []
            for tool in catalog:
                if tool.verb_scope:
                    required = f"{server_prefix}-{tool.verb_scope}"
                else:
                    required = f"{server_prefix}-{tool.name}"
                if not role_rbac.grants(required):
                    continue
                descriptors.append(ToolDescriptor(
                    name=f"{server_prefix}-{tool.name}",
                    description=tool.description,
                    input_schema=tool.input_schema,
                    invocation="broker",
                    scope=required,
                    server=server,
                ))

        return ToolManifest(descriptors=descriptors)


# Read tools that every agent gets without explicit RBAC grant.
# DESIGN §31 rule 74 — read tools are baseline; write tools require scope.
BASELINE_LOCAL_SCOPES: frozenset[str] = frozenset({
    "fs-read", "fs-list", "fs-exists", "fs-read_range", "fs-grep",
    "git-diff", "git-changed-files", "git-log", "git-read", "git-status",
    "code-outline",
    "doc-markdown", "doc-html",
    "web-fetch", "web-search",
    "text-diff", "text-estimate_tokens",
    "data-json", "data-csv", "data-yaml", "data-xml",
    "time-now", "time-diff",
    "math-eval",
})


def _scope_is_baseline(scope: str) -> bool:
    return scope in BASELINE_LOCAL_SCOPES
