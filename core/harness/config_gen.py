"""Generate the opencode config document for one GuudFlow role (plan §5.2).

One opencode server runs exactly one role, so the config is built per role and
passed to `OpencodeServer(config=...)` (it becomes `OPENCODE_CONFIG_CONTENT`).
Everything here is pure data: no I/O, no process control.

Deliberately absent: any `format` / structured-output setting. Forced tool use
is rejected by the newest Claude models, so roles finish via the
`submit_result` MCP tool instead (plan §2.2 gotcha #12).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.harness.capabilities import READONLY_BASH, CompiledCapabilities
from core.harness.permissions import build_permission
from core.llm.registry import ModelInfo

_NPM_BY_CLIENT = {
    "anthropic": "@ai-sdk/anthropic",
    "openai_compatible": "@ai-sdk/openai-compatible",
}


@dataclass(frozen=True)
class ImageCaps:
    """Limits opencode applies when resizing image attachments."""

    max_width: int = 1280
    max_height: int = 1280
    max_base64_bytes: int = 5_000_000


@dataclass(frozen=True)
class ProviderBinding:
    """How opencode reaches one model."""

    provider_key: str              # opencode provider id, e.g. "anthropic" or "mistral"
    model: ModelInfo               # from core.llm.registry
    client: str                    # ProviderInfo.client: "anthropic" | "openai_compatible"
    base_url: str | None = None    # override (gateway in P3; provider default otherwise)
    api_key: str | None = None     # host_dev only; None means opencode gets no key

    @property
    def model_ref(self) -> str:
        """opencode `provider/model` reference."""
        return f"{self.provider_key}/{self.model.id}"


def provider_entry(binding: ProviderBinding) -> dict[str, Any]:
    """One `provider.<key>` config entry for `binding`."""
    if binding.client not in _NPM_BY_CLIENT:
        raise ValueError(f"unknown provider client {binding.client!r}")
    model = binding.model
    options = {"baseURL": binding.base_url, "apiKey": binding.api_key}
    return {
        "npm": _NPM_BY_CLIENT[binding.client],
        "options": {k: v for k, v in options.items() if v is not None},
        "models": {
            model.id: {
                "name": model.id,
                "tool_call": model.supports_tools,
                "attachment": model.supports_images,
                "modalities": {
                    "input": ["text", "image", "pdf"] if model.supports_images else ["text"],
                    "output": ["text"],
                },
                "limit": {"context": model.max_context_tokens,
                          "output": model.max_output_tokens},
            }
        },
    }


def agent_entry(compiled: CompiledCapabilities, *, model_ref: str, prompt: str,
                description: str = "", steps: int = 60,
                temperature: float | None = None) -> dict[str, Any]:
    """`agent.<name>` entry: a primary agent locked to the role's compiled permissions."""
    entry: dict[str, Any] = {
        "mode": "primary",
        "model": model_ref,
        "prompt": prompt,
        "description": description,
        "steps": steps,
        "permission": build_permission(compiled),
    }
    if temperature is not None:
        entry["temperature"] = temperature
    return entry


# Gotcha #11: `subagent_depth: 1` does not stop nesting and subagents inherit the
# role's rules, so the built-in explore agent is pinned read-only and cannot spawn.
EXPLORE_OVERRIDE: dict[str, Any] = {
    "permission": {
        "task": "deny",
        "edit": "deny",
        "bash": {"*": "deny", **{pattern: "allow" for pattern in READONLY_BASH}},
    }
}


def _providers(bindings: list[ProviderBinding]) -> dict[str, dict[str, Any]]:
    """Provider entries for `bindings`, merging `models` when a provider_key repeats."""
    providers: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        entry = provider_entry(binding)
        existing = providers.get(binding.provider_key)
        if existing is None:
            providers[binding.provider_key] = entry
        else:
            existing["models"].update(entry["models"])
    return providers


def build_config(*, role: str, agent: dict[str, Any], binding: ProviderBinding,
                 small_binding: ProviderBinding | None = None,
                 mcp_url: str | None = None, mcp_token: str | None = None,
                 image_caps: ImageCaps = ImageCaps(),
                 extra_providers: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Full opencode config for the server that runs `role`.

    Raises ValueError when `mcp_url` is given without `mcp_token`.
    """
    if mcp_url is not None and not mcp_token:
        raise ValueError("mcp_url requires mcp_token")
    small = small_binding or binding
    providers = _providers([binding, small])
    providers.update(extra_providers or {})
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "share": "disabled",
        "autoupdate": False,
        "model": binding.model_ref,
        "small_model": small.model_ref,
        "default_agent": role,
        "provider": providers,
        # Top-level deny-all so opencode's built-in agents (build, plan, general) are locked too.
        "permission": {"*": "deny", "read": "allow", "glob": "allow",
                       "grep": "allow", "list": "allow"},
        "agent": {role: agent, "explore": EXPLORE_OVERRIDE},
        "subagent_depth": 1,
        "compaction": {"auto": True},
        "attachment": {"image": {
            "auto_resize": True,
            "max_width": image_caps.max_width,
            "max_height": image_caps.max_height,
            "max_base64_bytes": image_caps.max_base64_bytes,
        }},
    }
    if mcp_url is not None:
        config["mcp"] = {"guudflow": {
            "type": "remote", "url": mcp_url, "enabled": True, "oauth": False,
            "headers": {"Authorization": f"Bearer {mcp_token}"},
        }}
    return config
