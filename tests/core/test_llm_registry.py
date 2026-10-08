"""Tests for ModelRegistry — model resolution, client construction, deprecation."""
from __future__ import annotations

import json
import logging

import pytest

from core.llm.registry import ModelRegistry, NoModelAvailableError


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _write_registry(tmp_path, models: list[dict], providers: dict | None = None) -> str:
    path = tmp_path / "models.json"
    path.write_text(json.dumps({
        "models": models,
        "providers": providers or {
            "anthropic": {
                "client": "anthropic",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
            "ollama": {
                "client": "openai_compatible",
                "base_url": "http://localhost:11434/v1",
                "api_key_env": "OLLAMA_API_KEY",
            },
        },
    }), encoding="utf-8")
    return str(path)


BASE_MODELS = [
    {
        "id": "claude-opus-4-7",
        "provider": "anthropic",
        "tier": "heavy",
        "supports_tools": True,
        "max_context_tokens": 200000,
        "max_output_tokens": 8192,
        "cost_per_1k_input": 0.015,
        "cost_per_1k_output": 0.075,
        "deprecated": False,
    },
    {
        "id": "claude-sonnet-4-6",
        "provider": "anthropic",
        "tier": "solid",
        "supports_tools": True,
        "max_context_tokens": 200000,
        "max_output_tokens": 8192,
        "cost_per_1k_input": 0.003,
        "cost_per_1k_output": 0.015,
        "deprecated": False,
    },
    {
        "id": "qwen2.5:7b",
        "provider": "ollama",
        "tier": "fast",
        "supports_tools": True,
        "max_context_tokens": 32768,
        "max_output_tokens": 4096,
        "cost_per_1k_input": 0.0,
        "cost_per_1k_output": 0.0,
        "deprecated": False,
    },
]


# ── Resolution ────────────────────────────────────────────────────────────────


def test_resolve_by_tier_with_api_key(tmp_path, monkeypatch):
    path = _write_registry(tmp_path, BASE_MODELS)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    reg = ModelRegistry(path)
    m = reg.resolve("solid")
    assert m.id == "claude-sonnet-4-6"
    assert m.tier == "solid"


def test_resolve_with_preference(tmp_path, monkeypatch):
    path = _write_registry(tmp_path, BASE_MODELS)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    reg = ModelRegistry(path)
    m = reg.resolve("heavy", preference="claude-opus-4-7")
    assert m.id == "claude-opus-4-7"


def test_resolve_preference_wrong_tier_falls_back(tmp_path, monkeypatch):
    path = _write_registry(tmp_path, BASE_MODELS)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    reg = ModelRegistry(path)
    # Asking for "fast" but preference is "solid" model — should fall back
    m = reg.resolve("fast", preference="claude-sonnet-4-6")
    # No fast-tier Anthropic model in BASE_MODELS, but ollama is local → resolves
    assert m.tier == "fast"


def test_resolve_local_endpoint_needs_no_key(tmp_path, monkeypatch):
    path = _write_registry(tmp_path, BASE_MODELS)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    reg = ModelRegistry(path)
    m = reg.resolve("fast")
    assert m.id == "qwen2.5:7b"


def test_resolve_no_available_model_raises(tmp_path, monkeypatch):
    path = _write_registry(tmp_path, BASE_MODELS)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    reg = ModelRegistry(path)
    with pytest.raises(NoModelAvailableError):
        reg.resolve("heavy")


# ── Deprecation ───────────────────────────────────────────────────────────────


def test_deprecated_model_warns_at_load(tmp_path, caplog, monkeypatch):
    models = BASE_MODELS + [{
        "id": "old-model",
        "provider": "anthropic",
        "tier": "solid",
        "deprecated": True,
        "replacement": "claude-sonnet-4-6",
    }]
    path = _write_registry(tmp_path, models)
    with caplog.at_level(logging.WARNING):
        ModelRegistry(path)
    assert "old-model" in caplog.text
    assert "deprecated" in caplog.text


def test_deprecated_model_returned_with_warning_when_preferred(tmp_path, caplog, monkeypatch):
    models = BASE_MODELS + [{
        "id": "old-solid",
        "provider": "anthropic",
        "tier": "solid",
        "deprecated": True,
        "replacement": "claude-sonnet-4-6",
    }]
    path = _write_registry(tmp_path, models)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    reg = ModelRegistry(path)
    with caplog.at_level(logging.WARNING):
        m = reg.resolve("solid", preference="old-solid")
    assert m.id == "old-solid"
    assert "deprecated" in caplog.text.lower()


def test_deprecated_model_skipped_in_fallback(tmp_path, monkeypatch):
    models = [
        {
            "id": "deprecated-solid",
            "provider": "anthropic",
            "tier": "solid",
            "deprecated": True,
            "replacement": "good-solid",
        },
        {
            "id": "good-solid",
            "provider": "anthropic",
            "tier": "solid",
            "deprecated": False,
        },
    ]
    path = _write_registry(tmp_path, models)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    reg = ModelRegistry(path)
    m = reg.resolve("solid")
    assert m.id == "good-solid"


# ── list_models / get ─────────────────────────────────────────────────────────


def test_list_models_returns_all(tmp_path):
    path = _write_registry(tmp_path, BASE_MODELS)
    reg = ModelRegistry(path)
    ids = {m.id for m in reg.list_models()}
    assert ids == {"claude-opus-4-7", "claude-sonnet-4-6", "qwen2.5:7b"}


def test_get_returns_model_by_id(tmp_path):
    path = _write_registry(tmp_path, BASE_MODELS)
    reg = ModelRegistry(path)
    m = reg.get("claude-sonnet-4-6")
    assert m is not None
    assert m.cost_per_1k_input == 0.003


def test_get_returns_none_for_unknown(tmp_path):
    path = _write_registry(tmp_path, BASE_MODELS)
    reg = ModelRegistry(path)
    assert reg.get("nonexistent") is None


# ── Client construction ───────────────────────────────────────────────────────


def test_get_client_returns_anthropic_client(tmp_path, monkeypatch):
    path = _write_registry(tmp_path, BASE_MODELS)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    reg = ModelRegistry(path)
    from core.llm.anthropic import AnthropicClient
    m = reg.get("claude-sonnet-4-6")
    client = reg.get_client(m)
    assert isinstance(client, AnthropicClient)


def test_get_client_returns_openai_client_for_ollama(tmp_path, monkeypatch):
    path = _write_registry(tmp_path, BASE_MODELS)
    reg = ModelRegistry(path)
    from core.llm.openai import OpenAIClient
    m = reg.get("qwen2.5:7b")
    client = reg.get_client(m)
    assert isinstance(client, OpenAIClient)


def test_get_client_is_cached(tmp_path, monkeypatch):
    path = _write_registry(tmp_path, BASE_MODELS)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    reg = ModelRegistry(path)
    m = reg.get("claude-sonnet-4-6")
    c1 = reg.get_client(m)
    c2 = reg.get_client(m)
    assert c1 is c2


# ── Real models.json ──────────────────────────────────────────────────────────


# ── Track 12 — cheapest_tool_capable() ────────────────────────────────────────


def test_cheapest_tool_capable_picks_lowest_cost(tmp_path, monkeypatch):
    """Lowest sum of input + output cost wins."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    path = _write_registry(tmp_path, BASE_MODELS)
    reg = ModelRegistry(path)
    pick = reg.cheapest_tool_capable()
    # qwen2.5:7b is local (cost 0); should win on price.
    assert pick.id == "qwen2.5:7b"


def test_cheapest_tool_capable_skips_deprecated(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    models = [
        {**BASE_MODELS[1], "deprecated": False},  # sonnet, $0.018/k
        {
            "id": "ancient-cheap",
            "provider": "anthropic",
            "tier": "fast",
            "supports_tools": True,
            "cost_per_1k_input": 0.001,
            "cost_per_1k_output": 0.001,
            "deprecated": True,
        },
    ]
    path = _write_registry(tmp_path, models)
    reg = ModelRegistry(path)
    pick = reg.cheapest_tool_capable()
    # ancient-cheap would win on price but is deprecated.
    assert pick.id == "claude-sonnet-4-6"


def test_cheapest_tool_capable_skips_no_tool_support(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    models = [
        {**BASE_MODELS[1]},  # sonnet, supports tools
        {
            "id": "no-tools-cheap",
            "provider": "anthropic",
            "tier": "fast",
            "supports_tools": False,
            "cost_per_1k_input": 0.0001,
            "cost_per_1k_output": 0.0001,
            "deprecated": False,
        },
    ]
    path = _write_registry(tmp_path, models)
    reg = ModelRegistry(path)
    pick = reg.cheapest_tool_capable()
    assert pick.id == "claude-sonnet-4-6"


def test_cheapest_tool_capable_skips_unkeyed_provider(tmp_path, monkeypatch):
    """Models whose provider has no API key set are excluded."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    # Force only a remote anthropic model with no key
    models = [BASE_MODELS[1]]  # sonnet
    path = _write_registry(tmp_path, models)
    reg = ModelRegistry(path)
    with pytest.raises(NoModelAvailableError, match="tool-capable"):
        reg.cheapest_tool_capable()


def test_cheapest_tool_capable_alphabetical_tiebreak(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    models = [
        {
            "id": "z-zero",
            "provider": "anthropic",
            "tier": "fast",
            "supports_tools": True,
            "cost_per_1k_input": 0.0,
            "cost_per_1k_output": 0.0,
            "deprecated": False,
        },
        {
            "id": "a-zero",
            "provider": "anthropic",
            "tier": "fast",
            "supports_tools": True,
            "cost_per_1k_input": 0.0,
            "cost_per_1k_output": 0.0,
            "deprecated": False,
        },
    ]
    path = _write_registry(tmp_path, models)
    reg = ModelRegistry(path)
    pick = reg.cheapest_tool_capable()
    assert pick.id == "a-zero"


# ── Real models.json ──────────────────────────────────────────────────────────


def test_real_models_json_loads(monkeypatch):
    """Smoke-test that data/models.json parses without error."""
    from pathlib import Path
    path = Path("data/models.json")
    if not path.exists():
        pytest.skip("data/models.json not found")
    reg = ModelRegistry(path)
    models = reg.list_models()
    assert len(models) > 0
    tiers = {m.tier for m in models}
    assert {"heavy", "solid", "fast"}.issubset(tiers)



def test_shipped_haiku_5_5_is_the_fast_default_with_a_working_context() -> None:
    from core.llm.registry import DEFAULT_WORKING_CONTEXT, ModelRegistry
    from pathlib import Path
    reg = ModelRegistry(Path(__file__).resolve().parents[2] / "data" / "models.json")
    haiku = reg.get("claude-haiku-5-5")
    assert haiku is not None and haiku.long_context_threshold == 100_000
    assert reg.tier_model("fast").id == "claude-haiku-5-5"
    sonnet = reg.get("claude-sonnet-5-5")
    assert sonnet is not None and sonnet.max_context_tokens == 1_000_000
    assert sonnet.working_context == DEFAULT_WORKING_CONTEXT


def test_pick_effort_fits_the_level_to_what_the_model_takes() -> None:
    from core.llm.registry import ModelInfo
    m = ModelInfo(id="m", provider="anthropic", tier="solid",
                  effort_levels=("low", "medium", "high", "max"), default_effort="high")
    assert m.pick_effort(None) == "high"            # the model's default
    assert m.pick_effort("low") == "low"
    assert m.pick_effort("xhigh") == "high"         # not offered: the next level down
    assert m.pick_effort("bogus") is None
    assert ModelInfo(id="n", provider="openai", tier="fast").pick_effort("high") is None
    only_high = ModelInfo(id="h", provider="openai", tier="fast", effort_levels=("high",))
    assert only_high.pick_effort("low") == "high"   # nothing lower: the lowest it has
    assert only_high.pick_effort(None) is None      # no default: the provider decides


def test_shipped_models_carry_effort_levels() -> None:
    from pathlib import Path

    from core.llm.registry import ModelRegistry
    reg = ModelRegistry(Path(__file__).resolve().parents[2] / "data" / "models.json")
    opus = reg.get("claude-opus-5-5")
    assert opus is not None and opus.default_effort == "high" and "max" in opus.effort_levels
    haiku = reg.get("claude-haiku-5-5")
    assert haiku is not None and haiku.max_context_tokens == 1_000_000
    assert reg.get("claude-haiku-4-5") is not None and reg.get("claude-haiku-4-5").effort_levels == ()
