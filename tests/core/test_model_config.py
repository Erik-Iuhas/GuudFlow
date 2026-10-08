"""Tests for core.model_config (Settings → Models & providers)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from core import model_config
from core.llm.registry import ModelRegistry
from core.model_config import KeyStore, ModelConfigError

ENV = "GF_TEST_PROVIDER_KEY"


def _doc() -> dict:
    return {
        "providers": {
            "acme": {"client": "openai_compatible", "base_url": "https://llm.acme.test/v1",
                     "api_key_env": ENV},
            "anthropic": {"client": "anthropic", "api_key_env": "GF_TEST_ANTHROPIC_KEY"},
        },
        "models": [
            {"id": "acme-big", "provider": "acme", "tier": "heavy",
             "cost_per_1k_input": 0.003, "cost_per_1k_output": 0.015},
            {"id": "acme-small", "provider": "acme", "tier": "heavy",
             "cost_per_1k_input": 0.001, "cost_per_1k_output": 0.002, "cache_write_per_1k": 0.0011},
            {"id": "claude-x", "provider": "anthropic", "tier": "fast"},
        ],
        "tiers": {"heavy": "acme-small"},
    }


def test_defaults_apply_until_an_install_saves_its_own(tmp_path: Path) -> None:
    assert model_config.effective_path(tmp_path) == model_config.DEFAULTS_PATH
    assert not model_config.is_custom(tmp_path)
    model_config.save(tmp_path, _doc())
    assert model_config.effective_path(tmp_path) == tmp_path / "model_config.json"
    assert model_config.load(tmp_path)["tiers"] == {"heavy": ["acme-small"]}  # a list now
    model_config.reset(tmp_path)
    assert model_config.effective_path(tmp_path) == model_config.DEFAULTS_PATH


def test_the_shipped_defaults_validate() -> None:
    model_config.validate(model_config.load(None))


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["providers"].update({"Bad Name": d["providers"]["acme"]}), "provider name"),
    (lambda d: d["providers"]["acme"].update(client="grpc"), "client must be"),
    (lambda d: d["providers"]["acme"].update(api_key_env="lower"), "api_key_env"),
    (lambda d: d["providers"]["acme"].update(base_url=""), "needs a base_url"),
    (lambda d: d["providers"]["acme"].update(base_url="file:///etc"), r"http\(s\) URL"),
    (lambda d: d["models"].append(dict(d["models"][0])), "listed twice"),
    (lambda d: d["models"][0].update(provider="nope"), "unknown provider"),
    (lambda d: d["models"][0].update(tier="huge"), "tier must be"),
    (lambda d: d["models"][0].update(cost_per_1k_input=-1), "out of range"),
    (lambda d: d["models"][0].update(cost_per_1k_input="cheap"), "must be a number"),
    (lambda d: d["tiers"].update(fast="missing-model"), "unknown model"),
])
def test_invalid_configs_are_refused_with_the_reason(mutate, message: str) -> None:
    doc = _doc()
    mutate(doc)
    with pytest.raises(ModelConfigError, match=message):
        model_config.validate(doc)


def test_keys_are_stored_owner_only_and_env_wins(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(ENV, raising=False)
    keys = KeyStore(tmp_path)
    provider = _doc()["providers"]["acme"]
    assert model_config.key_status(provider, "acme", keys) == "missing"
    keys.set("acme", "sk-stored-123")
    assert stat.S_IMODE(os.stat(keys.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(keys.path.parent).st_mode) == 0o700
    assert model_config.api_key(provider, "acme", keys) == "sk-stored-123"
    assert model_config.key_status(provider, "acme", keys) == "stored"
    monkeypatch.setenv(ENV, "sk-from-env")
    assert model_config.api_key(provider, "acme", keys) == "sk-from-env"
    assert model_config.key_status(provider, "acme", keys) == "env"
    monkeypatch.delenv(ENV)
    assert keys.clear("acme") and not keys.clear("acme")
    assert model_config.key_status(provider, "acme", keys) == "missing"
    with pytest.raises(ModelConfigError):
        keys.set("acme", "two words")


def test_registry_uses_tier_choices_stored_keys_and_cache_write_prices(tmp_path: Path,
                                                                       monkeypatch) -> None:
    monkeypatch.delenv(ENV, raising=False)
    model_config.save(tmp_path, _doc())
    registry = ModelRegistry.for_data_root(tmp_path)
    # No key yet: the tier choice is skipped, nothing else in the tier has a key either.
    assert registry.tier_model("heavy").id == "acme-small"   # estimates need no key
    KeyStore(tmp_path).set("acme", "sk-stored-123")
    registry = ModelRegistry.for_data_root(tmp_path)
    assert registry.resolve("heavy").id == "acme-small"      # the saved choice, not file order
    assert registry.api_key("acme") == "sk-stored-123"
    assert registry.get("acme-small").cache_write_price == pytest.approx(0.0011)
    assert registry.get("acme-big").cache_write_price == pytest.approx(0.003 * 1.25)


def test_a_tier_with_several_models_spreads_roles_and_reviewers_differ(tmp_path: Path,
                                                                        monkeypatch) -> None:
    doc = _doc()
    doc["tiers"] = {"heavy": ["acme-small", "acme-big", "acme-small"]}  # duplicates dropped
    model_config.save(tmp_path, doc)
    assert model_config.load(tmp_path)["tiers"]["heavy"] == ["acme-small", "acme-big"]
    KeyStore(tmp_path).set("acme", "sk-stored-123")
    registry = ModelRegistry.for_data_root(tmp_path)
    picks = {registry.resolve("heavy", seat=f"proj:role{i}").id for i in range(20)}
    assert picks == {"acme-small", "acme-big"}                      # roles spread across both
    assert registry.resolve("heavy", seat="proj:x").id == registry.resolve("heavy", seat="proj:x").id
    for i in range(10):                                             # a reviewer avoids the author's
        author = registry.resolve("heavy", seat=f"proj:a{i}").id
        assert registry.resolve("heavy", seat=f"proj:r{i}", avoid=[author]).id != author
    assert registry.resolve("heavy").id == "acme-small"            # no seat: the first choice
    assert registry.tier_model("heavy").id == "acme-small"          # estimates use the first



def test_working_context_and_long_prices_are_checked() -> None:
    from core.model_config import ModelConfigError, validate as validate_config
    base = {"providers": {"anthropic": {"client": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}},
            "models": [{"id": "m", "provider": "anthropic", "tier": "fast",
                        "max_context_tokens": 200000, "working_context_tokens": 120000,
                        "long_context_threshold": 100000, "long_input_per_1k": 0.0005,
                        "long_output_per_1k": 0.0025}]}
    model = validate_config(base)["models"][0]
    assert model["working_context_tokens"] == 120000 and model["long_input_per_1k"] == 0.0005
    too_big = {**base, "models": [{**base["models"][0], "working_context_tokens": 300000}]}
    with pytest.raises(ModelConfigError, match="above max_context_tokens"):
        validate_config(too_big)
    no_price = {**base, "models": [{**base["models"][0], "long_output_per_1k": None}]}
    with pytest.raises(ModelConfigError, match="long_output_per_1k is required"):
        validate_config(no_price)
