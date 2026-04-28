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
