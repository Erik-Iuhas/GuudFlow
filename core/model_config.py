"""Model providers, models, prices and tier choices (the Studio "Models & providers" page).

The shipped defaults live in `data/models.json` (committed). An install's own choices are
saved to `{data_root}/model_config.json`, which replaces the defaults as a whole once it
exists; "reset" deletes it. Provider API keys never live in either file: a provider names
an environment variable (`api_key_env`), and a key typed into Studio is stored on the host
in `{data_root}/secrets/provider_keys.json` (directory 0700, file 0600). An environment
variable wins over a stored key. Keys are never returned by the API, logged, or passed
into a sandbox: the LLM gateway holds them (plan §5.4).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from core.json_store import read_json, write_json

DEFAULTS_PATH = Path(__file__).resolve().parent.parent / "data" / "models.json"
CONFIG_FILE = "model_config.json"
KEYS_FILE = Path("secrets") / "provider_keys.json"

TIERS = ("heavy", "solid", "fast")
CLIENTS = ("anthropic", "openai_compatible")
KeyStatus = Literal["env", "stored", "local", "missing"]

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$")
_PRICE_FIELDS = ("cost_per_1k_input", "cost_per_1k_output", "cache_read_per_1k",
                 "cache_write_per_1k")


class ModelConfigError(ValueError):
    """The submitted configuration is invalid (the message says where)."""


# ── the configuration document ───────────────────────────────────────────────


def config_path(data_root: Path) -> Path:
    """Where an install's own configuration is saved."""
    return Path(data_root) / CONFIG_FILE


def effective_path(data_root: Path | None) -> Path:
    """The file the model registry should load: the install's config, else the defaults."""
    if data_root is not None and config_path(data_root).is_file():
        return config_path(data_root)
    return DEFAULTS_PATH


def load(data_root: Path | None) -> dict[str, Any]:  # Any: a JSON document
    """The effective configuration document (validated on save, read leniently)."""
    doc = read_json(effective_path(data_root), {})
    return doc if isinstance(doc, dict) else {}


def is_custom(data_root: Path) -> bool:
    """True once the install has saved its own configuration."""
    return config_path(data_root).is_file()


def save(data_root: Path, doc: dict[str, Any]) -> dict[str, Any]:  # Any: a JSON document
    """Validate and save a full configuration; returns the normalised document."""
    clean = validate(doc)
    write_json(config_path(data_root), clean)
    return clean


def reset(data_root: Path) -> None:
    """Drop the install's configuration: the shipped defaults apply again."""
    config_path(data_root).unlink(missing_ok=True)


def validate(doc: dict[str, Any]) -> dict[str, Any]:  # Any: caller-supplied JSON
    """Check providers, models and tiers; return a normalised copy or raise ModelConfigError."""
    if not isinstance(doc, dict):
        raise ModelConfigError("the configuration must be an object")
    providers_in = doc.get("providers")
    if not isinstance(providers_in, dict) or not providers_in:
        raise ModelConfigError("at least one provider is required")
    providers: dict[str, dict[str, str]] = {}
    for name, p in providers_in.items():
        if not isinstance(name, str) or not _NAME.match(name):
            raise ModelConfigError(f"provider name {name!r}: use lowercase letters, digits, - or _")
        if not isinstance(p, dict):
            raise ModelConfigError(f"provider {name}: must be an object")
        client = p.get("client")
        if client not in CLIENTS:
            raise ModelConfigError(f"provider {name}: client must be one of {', '.join(CLIENTS)}")
        env = str(p.get("api_key_env") or "")
        if not _ENV.match(env):
            raise ModelConfigError(f"provider {name}: api_key_env must look like MY_PROVIDER_API_KEY")
        base_url = str(p.get("base_url") or "").strip()
        if base_url and not re.match(r"^https?://[^\s]+$", base_url):
            raise ModelConfigError(f"provider {name}: base_url must be an http(s) URL")
        if client == "openai_compatible" and not base_url:
            raise ModelConfigError(f"provider {name}: an OpenAI-compatible provider needs a base_url")
        entry = {"client": client, "api_key_env": env}
        if base_url:
            entry["base_url"] = base_url
        providers[name] = entry

    models_in = doc.get("models")
    if not isinstance(models_in, list) or not models_in:
        raise ModelConfigError("at least one model is required")
    models: list[dict[str, Any]] = []  # Any: JSON values
    seen: set[str] = set()
    for m in models_in:
        if not isinstance(m, dict):
            raise ModelConfigError("each model must be an object")
        mid = str(m.get("id") or "")
        if not _MODEL_ID.match(mid):
            raise ModelConfigError(f"model id {mid!r} is not valid")
        if mid in seen:
            raise ModelConfigError(f"model {mid} is listed twice")
        seen.add(mid)
        if m.get("provider") not in providers:
            raise ModelConfigError(f"model {mid}: unknown provider {m.get('provider')!r}")
        if m.get("tier") not in TIERS:
            raise ModelConfigError(f"model {mid}: tier must be one of {', '.join(TIERS)}")
        clean: dict[str, Any] = {  # Any: JSON values
            "id": mid, "provider": m["provider"], "tier": m["tier"],
            "supports_tools": bool(m.get("supports_tools", True)),
            "supports_images": bool(m.get("supports_images", False)),
            "max_context_tokens": _positive_int(m, "max_context_tokens", 200000, mid),
            "max_output_tokens": _positive_int(m, "max_output_tokens", 8192, mid),
            "deprecated": bool(m.get("deprecated", False)),
            "replacement": m.get("replacement") or None,
        }
        for field in _PRICE_FIELDS:
            value = m.get(field)
            if value is None and field == "cache_write_per_1k":
                continue  # defaults to 1.25x input when priced
            try:
                price = float(value if value is not None else 0.0)
            except (TypeError, ValueError):
                raise ModelConfigError(f"model {mid}: {field} must be a number") from None
            if price < 0 or price > 1000:
                raise ModelConfigError(f"model {mid}: {field} is out of range")
            clean[field] = price
        models.append(clean)

    tiers_in = doc.get("tiers") or {}
    if not isinstance(tiers_in, dict):
        raise ModelConfigError("tiers must be an object")
    # A tier may use several models (model diversity): roles are spread across them.
    tiers: dict[str, list[str]] = {}
    by_id = {m["id"]: m for m in models}
    for tier, chosen in tiers_in.items():
        if tier not in TIERS:
            raise ModelConfigError(f"unknown tier {tier!r}")
        ids = tier_list(chosen)
        for mid in ids:
            if mid not in by_id:
                raise ModelConfigError(f"tier {tier}: unknown model {mid!r}")
        if ids:
            tiers[tier] = ids
    return {"providers": providers, "models": models, "tiers": tiers}


def tier_list(value: object) -> list[str]:
    """A tier's models as a de-duplicated list (an older config stores one id as a string)."""
    items = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    out: list[str] = []
    for item in items:
        mid = str(item or "").strip()
        if mid and mid not in out:
            out.append(mid)
    return out


def _positive_int(m: dict[str, Any], key: str, default: int, mid: str) -> int:  # Any: JSON
    try:
        value = int(m.get(key, default))
    except (TypeError, ValueError):
        raise ModelConfigError(f"model {mid}: {key} must be a whole number") from None
    if value <= 0:
        raise ModelConfigError(f"model {mid}: {key} must be positive")
    return value


# ── provider keys (host only) ────────────────────────────────────────────────


@dataclass(frozen=True)
class KeyStore:
    """API keys typed into Studio, stored on the host only (never returned or logged)."""

    data_root: Path

    @property
    def path(self) -> Path:
        return Path(self.data_root) / KEYS_FILE

    def _read(self) -> dict[str, str]:
        doc = read_json(self.path, {})
        return {k: v for k, v in doc.items() if isinstance(v, str)} if isinstance(doc, dict) else {}

    def get(self, provider: str) -> str | None:
        """The stored key for a provider, if any."""
        return self._read().get(provider) or None

    def set(self, provider: str, key: str) -> None:
        """Store (or replace) a provider's key; the file is readable by its owner only."""
        key = key.strip()
        if not key or len(key) > 512 or any(c.isspace() for c in key):
            raise ModelConfigError("the key must be a single line without spaces")
        keys = self._read()
        keys[provider] = key
        self._write(keys)

    def clear(self, provider: str) -> bool:
        """Forget a stored key; True if one was stored."""
        keys = self._read()
        if keys.pop(provider, None) is None:
            return False
        self._write(keys)
        return True

    def _write(self, keys: dict[str, str]) -> None:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(keys, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)


def is_local(base_url: str) -> bool:
    """A local endpoint (Ollama, LM Studio, vLLM) needs no real key."""
    return "localhost" in base_url or "127.0.0.1" in base_url


def api_key(provider: dict[str, Any], name: str, keys: KeyStore | None) -> str | None:  # Any: JSON
    """The key the host uses for a provider: its environment variable, else a stored key."""
    env = provider.get("api_key_env") or ""
    return os.environ.get(env) or (keys.get(name) if keys is not None else None) or None


def key_status(provider: dict[str, Any], name: str, keys: KeyStore | None) -> KeyStatus:  # Any: JSON
    """Where a provider's key comes from (the key itself is never exposed)."""
    if os.environ.get(provider.get("api_key_env") or ""):
        return "env"
    if keys is not None and keys.get(name):
        return "stored"
    if is_local(str(provider.get("base_url") or "")):
        return "local"
    return "missing"
