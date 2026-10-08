"""Model registry — loads data/models.json and resolves tier → model + client."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.llm.client import LLMClient
    from core.model_config import KeyStore

log = logging.getLogger(__name__)


# A 1M-token window is there when a task needs it; a session that fills it costs 5x a 200k one
# per turn. Agents work in this much unless the model or the project says otherwise.
DEFAULT_WORKING_CONTEXT = 200_000

# Effort levels in GuudFlow's own words, least to most thinking. A model lists the ones it takes;
# config_gen turns a level into the provider's option (`effort` for Anthropic, `reasoningEffort`
# for OpenAI-compatible APIs).
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")


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
    # Cache writes; None means the usual 1.25x of the input price.
    cache_write_per_1k: float | None = None
    deprecated: bool = False
    replacement: str | None = None
    # The context an agent works in, below the model's maximum: opencode compacts (and prunes
    # old tool output) as a turn's prompt nears it. None means DEFAULT_WORKING_CONTEXT.
    working_context_tokens: int | None = None
    # Prompts over this many tokens are billed at the long-context prices (all of the request);
    # None means one price at every size.
    long_context_threshold: int | None = None
    long_input_per_1k: float | None = None
    long_output_per_1k: float | None = None
    # Effort levels the model accepts (empty: no effort control, the provider decides) and the
    # one agents use unless the team or project picks another. None means the provider default.
    effort_levels: tuple[str, ...] = ()
    default_effort: str | None = None

    def pick_effort(self, want: str | None) -> str | None:
        """The level to send for `want` (falling back to the model's default): the highest
        supported level at or below it, else the lowest supported one. None when the model has
        no effort control or nothing was asked for."""
        want = want or self.default_effort
        if not self.effort_levels or want not in EFFORT_LEVELS:
            return None
        rank = EFFORT_LEVELS.index(want)
        below = [lv for lv in self.effort_levels if EFFORT_LEVELS.index(lv) <= rank]
        if below:
            return max(below, key=EFFORT_LEVELS.index)
        return min(self.effort_levels, key=EFFORT_LEVELS.index)

    @property
    def working_context(self) -> int:
        """The context opencode is allowed to fill before it compacts."""
        want = self.working_context_tokens or DEFAULT_WORKING_CONTEXT
        return max(1, min(want, self.max_context_tokens))

    @property
    def cache_write_price(self) -> float:
        """USD per 1k cache-write tokens."""
        return (self.cache_write_per_1k if self.cache_write_per_1k is not None
                else self.cost_per_1k_input * 1.25)


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

    def __init__(self, path: Path | str, *, keys: "KeyStore | None" = None):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        # Keys typed into Studio (host only); environment variables still win.
        self._keys = keys

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
                cache_write_per_1k=m.get("cache_write_per_1k"),
                deprecated=m.get("deprecated", False),
                replacement=m.get("replacement"),
                working_context_tokens=m.get("working_context_tokens"),
                long_context_threshold=m.get("long_context_threshold"),
                long_input_per_1k=m.get("long_input_per_1k"),
                long_output_per_1k=m.get("long_output_per_1k"),
                effort_levels=tuple(lv for lv in EFFORT_LEVELS
                                    if lv in (m.get("effort_levels") or ())),
                default_effort=m.get("default_effort") or None,
            )
            self._models[info.id] = info

        self._providers: dict[str, ProviderInfo] = {}
        for name, pdata in data.get("providers", {}).items():
            self._providers[name] = ProviderInfo(
                client=pdata["client"],
                api_key_env=pdata["api_key_env"],
                base_url=pdata.get("base_url", ""),
            )

        # The models each tier uses when a role names no model (Studio's tier pickers).
        # Several per tier is allowed: roles are spread across them (model diversity).
        from core.model_config import tier_list
        self._tiers: dict[str, list[str]] = {}
        for t, value in (data.get("tiers") or {}).items():
            ids = [mid for mid in tier_list(value) if mid in self._models]
            if ids:
                self._tiers[t] = ids

        for m in self._models.values():
            if m.deprecated:
                hint = f" Use {m.replacement!r} instead." if m.replacement else ""
                log.warning("Model %r is deprecated.%s", m.id, hint)

        self._client_cache: dict[str, LLMClient] = {}

    @classmethod
    def for_data_root(cls, data_root: Path | None) -> "ModelRegistry":
        """The install's registry: its saved configuration (or the defaults) and its keys."""
        from core.model_config import KeyStore, effective_path
        return cls(effective_path(data_root),
                   keys=KeyStore(Path(data_root)) if data_root is not None else None)

    def api_key(self, provider_name: str) -> str | None:
        """The host-side key for a provider (env var, else a key stored from Studio)."""
        pinfo = self._providers.get(provider_name)
        if pinfo is None:
            return None
        env = os.environ.get(pinfo.api_key_env, "")
        if env:
            return env
        return self._keys.get(provider_name) if self._keys is not None else None

    def tier_model(self, tier: str) -> ModelInfo | None:
        """The model a tier uses for estimates: the saved tier choice, else the first
        non-deprecated model of that tier (keys are not needed to price a model)."""
        if tier in self._tiers:
            return self._models[self._tiers[tier][0]]
        return next((m for m in self._models.values() if m.tier == tier and not m.deprecated), None)

    def tiers(self) -> dict[str, list[str]]:
        """The saved tier choices (tier -> model ids)."""
        return {t: list(ids) for t, ids in self._tiers.items()}

    def tier_pool(self, tier: str) -> list[ModelInfo]:
        """The tier's chosen models whose provider has a key, in the saved order."""
        return [self._models[mid] for mid in self._tiers.get(tier, [])
                if self._has_api_key(self._models[mid].provider)]

    def list_models(self) -> list[ModelInfo]:
        return list(self._models.values())

    def provider(self, name: str) -> ProviderInfo | None:
        """Provider config by name (data/models.json `providers`)."""
        return self._providers.get(name)

    def get(self, model_id: str) -> ModelInfo | None:
        return self._models.get(model_id)

    def resolve(self, tier: str, preference: str | None = None, *, seat: str | None = None,
                avoid: Iterable[str] = ()) -> ModelInfo:
        """Pick a model for the given tier.

        With no preference, the tier's saved models (`tiers`) win when their provider has a
        key. With several, `seat` (e.g. "project:role") picks one stably, so roles of the same
        tier spread across the models, and models in `avoid` (say, the model of the work a
        reviewer checks) are skipped while another one is left. If preference is a model_id with matching tier, return it (with a
        deprecation warning if needed). Otherwise fall back to the first
        non-deprecated model matching tier whose provider has an API key set.
        """
        pool = self.tier_pool(tier) if not preference else []
        if pool:
            avoided = set(avoid)
            options = [m for m in pool if m.id not in avoided] or pool
            if seat is None or len(options) == 1:
                return options[0]
            digest = int(hashlib.sha256(seat.encode()).hexdigest(), 16)
            return options[digest % len(options)]
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
        if self.api_key(provider_name):
            return True
        # Local endpoints (Ollama, LM Studio, vLLM) don't need a real key
        return "localhost" in pinfo.base_url or "127.0.0.1" in pinfo.base_url

    def _build_client(self, provider_name: str) -> LLMClient:
        pinfo = self._providers.get(provider_name)
        if pinfo is None:
            raise NoModelAvailableError(f"Unknown provider {provider_name!r}")
        api_key = self.api_key(provider_name)

        if pinfo.client == "anthropic":
            from core.llm.anthropic import AnthropicClient
            return AnthropicClient(api_key=api_key)

        if pinfo.client == "openai_compatible":
            from core.llm.openai import OpenAIClient
            return OpenAIClient(base_url=pinfo.base_url, api_key=api_key)

        raise NoModelAvailableError(
            f"Unknown client type {pinfo.client!r} for provider {provider_name!r}"
        )
