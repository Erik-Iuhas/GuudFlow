"""Generate the opencode config document for one GuudFlow role (plan §5.2).

One opencode server runs exactly one role, so the config is built per role and
passed to `OpencodeServer(config=...)` (it becomes `OPENCODE_CONFIG_CONTENT`).
Everything here is pure data: no I/O, no process control.

Deliberately absent: any `format` / structured-output setting. Forced tool use
is rejected by the newest Claude models, so roles finish via the
`submit_result` MCP tool instead (plan §2.2 gotcha #12).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from core.harness.capabilities import DENIED_BASH, READONLY_BASH, CompiledCapabilities
from core.harness.permissions import build_permission
from core.llm.registry import ModelInfo

# opencode's default agents, switched off in every GuudFlow server.
BUILTIN_AGENTS = ("build", "plan", "general")

_NPM_BY_CLIENT = {
    "anthropic": "@ai-sdk/anthropic",
    "openai_compatible": "@ai-sdk/openai-compatible",
}

# The provider option that carries an effort level: @ai-sdk/anthropic sends `effort` as
# `output_config.effort`; @ai-sdk/openai-compatible sends `reasoningEffort` as `reasoning_effort`,
# the top-level field OpenAI, OpenRouter, Groq, DeepSeek, Mistral and Ollama's /v1 all read. The
# words they accept differ, which a model's `effort_values` covers.
_EFFORT_OPTION_BY_CLIENT = {
    "anthropic": "effort",
    "openai_compatible": "reasoningEffort",
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
    working_context: int | None = None  # project override of the model's working context
    effort: str | None = None      # effort level to send, already fitted to the model's levels

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
    model_entry: dict[str, Any] = {
        "name": model.id,
        "tool_call": model.supports_tools,
        "attachment": model.supports_images,
        "modalities": {
            "input": ["text", "image", "pdf"] if model.supports_images else ["text"],
            "output": ["text"],
        },
        # `input` is the working context: opencode compacts once a turn's prompt
        # nears it, so a 1M-token model still works (and is billed) at ~200k.
        "limit": {"context": model.max_context_tokens,
                  "input": binding.working_context or model.working_context,
                  "output": model.max_output_tokens},
    }
    option = _EFFORT_OPTION_BY_CLIENT[binding.client]
    if binding.effort:
        # Model options reach the provider as provider options on every request.
        model_entry["options"] = {option: model.effort_value(binding.effort)}
    if model.effort_levels:
        # One variant per level, so a single turn can ask for another effort (a card whose
        # item the project manager marked harder or easier) with the prompt's `variant`.
        model_entry["variants"] = {lv: {option: model.effort_value(lv)}
                                   for lv in model.effort_levels}
    return {
        "npm": _NPM_BY_CLIENT[binding.client],
        "options": {k: v for k, v in options.items() if v is not None},
        "models": {model.id: model_entry},
    }


def agent_entry(compiled: CompiledCapabilities, *, model_ref: str, prompt: str,
                description: str = "", steps: int = 60,
                temperature: float | None = None,
                skills: Sequence[str] = ()) -> dict[str, Any]:
    """`agent.<name>` entry: a primary agent locked to the role's compiled permissions.

    `skills` are the GuudFlow skills the role may load (worker context plan R3); every other
    skill is hidden and refused."""
    permission = build_permission(compiled)
    permission["skill"] = {"*": "deny", **{name: "allow" for name in skills}}
    entry: dict[str, Any] = {
        "mode": "primary",
        "model": model_ref,
        "prompt": prompt,
        "description": description,
        "steps": steps,
        "permission": permission,
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
        # The read-only looks, then the denies (last match wins: no find -exec / -delete).
        "bash": {"*": "deny", **{pattern: "allow" for pattern in READONLY_BASH},
                 **{pattern: "deny" for pattern in DENIED_BASH}},
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
            # The first binding (the role's own model) wins when the small model is the same one.
            for mid, model_entry in entry["models"].items():
                existing["models"].setdefault(mid, model_entry)
    return providers


def build_config(*, role: str, agent: dict[str, Any], binding: ProviderBinding,
                 small_binding: ProviderBinding | None = None,
                 mcp_url: str | None = None, mcp_token: str | None = None,
                 image_caps: ImageCaps = ImageCaps(),
                 extra_providers: dict[str, dict[str, Any]] | None = None,
                 instructions: Sequence[str] = (),
                 skill_paths: Sequence[str] = ()) -> dict[str, Any]:
    """Full opencode config for the server that runs `role`.

    `instructions` are rules files loaded into the system prompt on every call and
    `skill_paths` folders of `SKILL.md` skills (worker context plan R), both absolute paths in
    the role's read-only tool directory.

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
        # Telemetry off, explicitly (it is opt-in in opencode; this keeps it off).
        "experimental": {"openTelemetry": False},
        "model": binding.model_ref,
        "small_model": small.model_ref,
        "default_agent": role,
        "provider": providers,
        # Only GuudFlow's providers exist in this server: opencode's built-in catalogue (and its
        # own hosted models) can never be picked, listed or called.
        "enabled_providers": sorted(providers),
        # Top-level deny-all so opencode's built-in agents (build, plan, general) are locked too.
        "permission": {"*": "deny", "read": "allow", "glob": "allow",
                       "grep": "allow", "list": "allow"},
        # The role's agent and the read-only explore helper only: opencode's own agents (build,
        # plan, general) are removed, so the server is purely GuudFlow's harness. Its internal
        # helpers (compaction, summary, title) stay; they use the role's model.
        "agent": {**{name: {"disable": True} for name in BUILTIN_AGENTS if name != role},
                  role: agent, "explore": EXPLORE_OVERRIDE},
        "subagent_depth": 1,
        # Compact near the working context and drop old tool output first (prune), so a long
        # turn stays inside it instead of growing toward the model's full window.
        "compaction": {"auto": True, "prune": True},
        "attachment": {"image": {
            "auto_resize": True,
            "max_width": image_caps.max_width,
            "max_height": image_caps.max_height,
            "max_base64_bytes": image_caps.max_base64_bytes,
        }},
    }
    if instructions:
        config["instructions"] = list(instructions)
    if skill_paths:
        config["skills"] = {"paths": list(skill_paths)}
    if mcp_url is not None:
        config["mcp"] = {"guudflow": {
            "type": "remote", "url": mcp_url, "enabled": True, "oauth": False,
            "headers": {"Authorization": f"Bearer {mcp_token}"},
        }}
    return config
