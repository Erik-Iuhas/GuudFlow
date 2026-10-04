"""Model registry — loads data/models.json and resolves tier → model + client."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.llm.client import LLMClient

log = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    id: str
    provider: str
    tier: str  # "heavy" | "solid" | "fast"
    supports_tools: bool = True
    supports_images: bool = False
    max_context_tokens: int = 200000
    max_output_tokens: int = 8192
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    cache_read_per_1k: float = 0.0
    deprecated: bool = False
    replacement: str | None = None


@dataclass
class ProviderInfo:
    client: str  # "anthropic" | "openai_compatible"
    api_key_env: str
    base_url: str = ""


class NoModelAvailableError(Exception):
    pass


class ModelRegistry:
    """Loads models.json and resolves (tier, preference) → ModelInfo + LLMClient.

    Client instances are cached per provider — one httpx session, one
    Anthropic SDK client — so callers don't pay construction cost per call.
    """

    def __init__(self, path: Path | str):
        data = json.loads(Path(path).read_text(encoding="utf-8"))

        self._models: dict[str, ModelInfo] = {}
        for m in data.get("models", []):
            info = ModelInfo(
                id=m["id"],
                provider=m["provider"],
                tier=m["tier"],
                supports_tools=m.get("supports_tools", True),
                supports_images=m.get("supports_images", False),
                max_context_tokens=m.get("max_context_tokens", 200000),
                max_output_tokens=m.get("max_output_tokens", 8192),
                cost_per_1k_input=m.get("cost_per_1k_input", 0.0),
                cost_per_1k_output=m.get("cost_per_1k_output", 0.0),
                cache_read_per_1k=m.get("cache_read_per_1k", 0.0),
                deprecated=m.get("deprecated", False),
                replacement=m.get("replacement"),
            )
            self._models[info.id] = info

        self._providers: dict[str, ProviderInfo] = {}
        for name, pdata in data.get("providers", {}).items():
            self._providers[name] = ProviderInfo(
                client=pdata["client"],
                api_key_env=pdata["api_key_env"],
                base_url=pdata.get("base_url", ""),
            )

        for m in self._models.values():
            if m.deprecated:
                hint = f" Use {m.replacement!r} instead." if m.replacement else ""
                log.warning("Model %r is deprecated.%s", m.id, hint)

        self._client_cache: dict[str, LLMClient] = {}

    def list_models(self) -> list[ModelInfo]:
        return list(self._models.values())

    def provider(self, name: str) -> ProviderInfo | None:
        """Provider config by name (data/models.json `providers`)."""
        return self._providers.get(name)

    def get(self, model_id: str) -> ModelInfo | None:
        return self._models.get(model_id)

    def resolve(self, tier: str, preference: str | None = None) -> ModelInfo:
        """Pick a model for the given tier.

        If preference is a model_id with matching tier, return it (with a
        deprecation warning if needed). Otherwise fall back to the first
        non-deprecated model matching tier whose provider has an API key set.
        """
        if preference and preference in self._models:
            m = self._models[preference]
            if m.tier == tier:
                if m.deprecated:
                    log.warning(
                        "Model %r is deprecated; consider switching to %r",
                        preference, m.replacement,
                    )
                return m

        for m in self._models.values():
            if m.tier != tier or m.deprecated:
                continue
            if self._has_api_key(m.provider):
                return m

        raise NoModelAvailableError(
            f"No available model for tier={tier!r}. "
            "Ensure the relevant API key env var is set."
        )

    def cheapest_tool_capable(self) -> ModelInfo:
        """Track 12 — fallback model picker for sub-LLM dispatch.

        Returns the cheapest non-deprecated, tool-capable model whose
        provider has an API key configured. Cost is the sum of input +
        output per-1k prices (equally weighted — we don't know the
        in/out ratio at config time). Stable tiebreaker: alphabetical
        id, so the choice doesn't drift across restarts when two
        models cost the same.

        Raises NoModelAvailableError if no candidate exists. Used when
        a project's `sub_llm_models[use_case]` is unset.
        """
        candidates: list[ModelInfo] = []
        for m in self._models.values():
            if m.deprecated:
                continue
            if not m.supports_tools:
                continue
            if not self._has_api_key(m.provider):
                continue
            candidates.append(m)

        if not candidates:
            raise NoModelAvailableError(
                "No tool-capable, non-deprecated model is available "
                "(checked data/models.json + provider API keys). "
                "Track 12 sub-LLM dispatch needs at least one usable model."
            )

        candidates.sort(
            key=lambda m: (
                m.cost_per_1k_input + m.cost_per_1k_output,
                m.id,
            ),
        )
        return candidates[0]

    def get_client(self, model: ModelInfo) -> LLMClient:
        """Return a cached LLMClient for the model's provider."""
        provider_name = model.provider
        if provider_name not in self._client_cache:
            self._client_cache[provider_name] = self._build_client(provider_name)
        return self._client_cache[provider_name]

    def _has_api_key(self, provider_name: str) -> bool:
        pinfo = self._providers.get(provider_name)
        if pinfo is None:
            return False
        key = os.environ.get(pinfo.api_key_env, "")
        if key:
            return True
        # Local endpoints (Ollama, LM Studio, vLLM) don't need a real key
        return "localhost" in pinfo.base_url or "127.0.0.1" in pinfo.base_url

    def _build_client(self, provider_name: str) -> LLMClient:
        pinfo = self._providers.get(provider_name)
        if pinfo is None:
            raise NoModelAvailableError(f"Unknown provider {provider_name!r}")
        api_key = os.environ.get(pinfo.api_key_env) or None

        if pinfo.client == "anthropic":
            from core.llm.anthropic import AnthropicClient
            return AnthropicClient(api_key=api_key)

        if pinfo.client == "openai_compatible":
            from core.llm.openai import OpenAIClient
            return OpenAIClient(base_url=pinfo.base_url, api_key=api_key)

        raise NoModelAvailableError(
            f"Unknown client type {pinfo.client!r} for provider {provider_name!r}"
        )
