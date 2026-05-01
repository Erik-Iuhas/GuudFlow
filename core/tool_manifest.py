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
    # Track 3d.4 — manifest-driven guidance copied from the tool class.
    priority: int = 100
    when_to_use: str = ""

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

        Track 3d.4 — tools are now sorted by `priority` (lower = reach first),
        and each line carries an inline `when_to_use` hint when populated.
        Replaces the previous category-grouped layout: priority order encodes
        the funnel (read → reason → act) directly so the LLM doesn't have to
        infer it from category names.
        """
        if not self.descriptors:
            return ""

        # Stable sort: priority asc, then name for determinism on ties.
        ordered = sorted(self.descriptors, key=lambda d: (d.priority, d.name))

        lines = [
            "AVAILABLE TOOLS (use in this order of preference — "
            "lower number = reach first; prefer batch variants when applicable):",
            "",
        ]
        for d in ordered:
            tag = f"[{d.priority:>3}]"
            if d.when_to_use:
                lines.append(f"{tag} `{d.name}` — {d.when_to_use}")
                lines.append(f"      {d.description}")
            else:
                lines.append(f"{tag} `{d.name}` — {d.description}")
            if d.input_schema:
                args = _summarise_schema(d.input_schema)
                if args:
                    lines.append(f"      arguments: {args}")
            lines.append("")
        return "\n".join(lines).rstrip()


    def render_aci(self) -> str:
        """SWE-agent-style ACI documentation for the tool catalog.

        Tools that implement `aci_doc()` on their BaseTool class get a full
        manpage-style block.  All others fall back to the compact one-liner.
        Injected into Layer 5 of effective_system_prompt() (M1 spec §5).
        """
        if not self.descriptors:
            return ""

        sections: list[str] = []
        for d in self.descriptors:
            # Try the tool instance's aci_doc() — only available for local tools
            # where the BaseTool subclass is stored alongside the descriptor.
            aci = getattr(d, "_aci_doc_cache", None)
            if aci is not None:
                sections.append(_render_aci_block(d.name, aci))
            else:
                # Compact fallback for broker/MCP tools and local tools without aci_doc().
                args = _summarise_schema(d.input_schema) if d.input_schema else ""
                line = f"TOOL: {d.name}\n  PURPOSE: {d.description}"
                if args:
                    line += f"\n  ARGS:    {args}"
                sections.append(line)

        return "AVAILABLE TOOLS (ACI format):\n\n" + "\n\n".join(sections)


def _render_aci_block(name: str, doc: dict[str, str]) -> str:
    lines = [f"TOOL: {name}"]
    for key in ("PURPOSE", "ARGS", "EMITS", "ERRORS", "EXAMPLE", "NOTES"):
        val = doc.get(key.lower()) or doc.get(key)
        if val:
            lines.append(f"  {key}:   {val}")
    return "\n".join(lines)


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
    # Track 3d.4 — manifest-driven guidance.
    priority: int = 100
    when_to_use: str = ""


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
            # Track 3e — batch tools piggyback on their parent's scope, so a
            # role with `fs-edit` declared+granted gets `fs-edit-batch` for
            # free without needing a separate RBAC entry.
            scope = BATCH_PARENT_SCOPES.get(tool_name, tool_name)
            if not _scope_is_baseline(scope) and not role_rbac.grants(scope):
                continue
            descriptors.append(ToolDescriptor(
                name=entry.name,
                description=entry.description,
                input_schema=entry.input_schema,
                invocation="local",
                scope=scope,
                priority=entry.priority,
                when_to_use=entry.when_to_use,
            ))

        # 1b. Auto-include baseline tools not already added via declared_local_tools
        already_names = {d.name for d in descriptors}
        for scope in BASELINE_LOCAL_SCOPES:
            entry = self.local_registry.get(scope)
            if entry is None:
                continue
            if entry.name in already_names:
                continue
            descriptors.append(ToolDescriptor(
                name=entry.name,
                description=entry.description,
                input_schema=entry.input_schema,
                invocation="local",
                scope=scope,
                priority=entry.priority,
                when_to_use=entry.when_to_use,
            ))
            already_names.add(entry.name)

        # 1c. Track 3e — auto-include batch variants when the parent is in the
        # manifest. Means agents that have fs-edit/fs-write get the batch
        # variants automatically without explicit declaration.
        parent_to_child = {parent: child for child, parent in BATCH_PARENT_SCOPES.items()}
        existing_scopes = {d.scope for d in descriptors}
        for d in list(descriptors):  # snapshot — we mutate below
            child_name = parent_to_child.get(d.scope)
            if child_name is None:
                continue
            if child_name in already_names:
                continue
            child_entry = self.local_registry.get(child_name)
            if child_entry is None:
                continue
            descriptors.append(ToolDescriptor(
                name=child_entry.name,
                description=child_entry.description,
                input_schema=child_entry.input_schema,
                invocation="local",
                scope=d.scope,  # share the parent's scope for RBAC accounting
                priority=child_entry.priority,
                when_to_use=child_entry.when_to_use,
            ))
            already_names.add(child_entry.name)

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
    "fs-read", "fs-list", "fs-exists", "fs-read_range", "fs-grep", "fs-diff",
    "board-message",
    "git-diff", "git-changed-files", "git-log", "git-read", "git-status",
    "code-outline",
    "code-outline-batch",   # Track 3e — read-only, same as code-outline
    "doc-markdown", "doc-html",
    "web-fetch", "web-search",
    "text-diff", "text-estimate_tokens",
    "data-json", "data-csv", "data-yaml", "data-xml",
    "time-now", "time-diff",
    "math-eval",
    # Story lifecycle — no RBAC gate, same as board-message.
    # Any agent that writes docs may open or close a story.
    "story-open", "story-complete",
})


# Track 3e — write-class batch tools piggyback on their singular counterpart's
# scope, so any role with `fs-edit` granted automatically gets `fs-edit-batch`
# without project-config changes.
BATCH_PARENT_SCOPES: dict[str, str] = {
    "fs-edit-batch": "fs-edit",
    "fs-write-batch": "fs-write",
}


def _scope_is_baseline(scope: str) -> bool:
    return scope in BASELINE_LOCAL_SCOPES
