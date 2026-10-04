"""What host-side MCP tools can see about the run they serve (plan §5.6, §5.8–§5.10).

Every GuudFlow MCP tool module exposes `tools(ctx: RunToolContext) -> list[McpTool]`.
The run builds one context and registers the returned tools on its MCP server;
which role may call which tool is decided by the role's compiled capabilities
(`CompiledCapabilities.mcp_tools`), never by the tool itself.

Tools must stay stateless apart from per-run state they keep inside the closure
created by `tools(ctx)` (budgets, caches), per the project rule that tools are
input in, output out.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.signals import Priority, Signal, SignalType
from core.workspace import Workspace


@dataclass
class RunToolContext:
    """Run-scoped facts and callbacks shared by host-side MCP tools."""

    run_id: str
    workspace: Workspace
    run_root: Path
    # Role → the role's git clone on the host (its /work in a sandbox).
    clone_for: Callable[[str], Path]
    # Role → host/IP where the role's processes (e.g. a dev server) are reachable
    # from the host: "127.0.0.1" in host mode, the container IP in a sandbox.
    sandbox_host_for: Callable[[str], str]
    # Free-form project settings for tools (allow-lists, budgets), e.g.
    # project.approval_gates plus a `web` block. Read-only for tools.
    settings: dict[str, Any] = field(default_factory=dict)
    # Secrets (search API keys) come from here, never from tool arguments.
    secret: Callable[[str], str | None] = lambda name: None

    def post(self, kind: SignalType, role: str, summary: str,
             detail: dict[str, Any] | str | None = None, priority: Priority = "normal") -> None:
        """Post a board signal attributed to `role`."""
        text = json.dumps(detail) if isinstance(detail, dict) else detail
        self.workspace.post_signal(Signal(type=kind, summary=summary, from_agent=role,
                                          detail=text, priority=priority,
                                          iteration=self.workspace.iteration))
