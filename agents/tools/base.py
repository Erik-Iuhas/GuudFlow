"""BaseTool — stateless contract every built-in tool implements.

DESIGN §3.5 / §31. Tools are stateless: input in, output out, no instance
state between calls. Concurrency is owned by the scheduler; tools don't
manage tasks.

A tool exposes:
  - `name`            — namespaced like "fs:read", "doc:pdf", "code:exec"
  - `description`     — what the LLM sees when deciding to use it
  - `input_schema`    — JSONSchema-shaped dict for the call's arguments
  - `run(arguments)`  — async; returns a dict result

The ToolManifest (core/tool_manifest.py) wraps these descriptors with
the RBAC scope they require so the broker can validate at call time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """Stateless contract for every built-in tool.

    Subclasses set `name`, `description`, and `input_schema` as class-level
    attributes (or constructor-set instance attributes — whichever is more
    natural). The runner instantiates each tool once per agent and calls
    `run()` per LLM tool-use request.
    """

    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {}

    @abstractmethod
    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute the tool. Returns a result dict that the LLM will see.

        Tools must NOT raise for ordinary user-input errors (file not found,
        invalid path, etc.). Instead, return `{"error": "..."}`. Raising is
        reserved for unrecoverable framework bugs.
        """


class ToolError(Exception):
    """Raised by the framework (not tools themselves) when something
    structural is wrong — e.g. a tool that wasn't registered, a manifest
    inconsistency. Tools surface user-facing errors via the result dict."""
