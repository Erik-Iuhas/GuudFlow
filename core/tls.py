"""Trusting a custom certificate authority (a company proxy or an internal provider endpoint).

Set `GUUDFLOW_CA_BUNDLE` to a PEM file with the extra CA certificate(s). GuudFlow then trusts
them *in addition to* the public roots, everywhere it makes an outbound TLS connection:

- Python HTTP clients (model providers, the LLM gateway's upstream calls, web search and fetch,
  MCP servers, GitHub pull requests) get an `ssl.SSLContext` from `httpx_verify()`;
- subprocesses (git for project remotes, npm when installing opencode or packages, opencode
  itself on the host) get environment variables from `apply_env()`, pointing at a combined
  bundle (public roots + yours) written next to the data directory.

Verification is never turned off: there is no setting for that, and none is needed.
"""

from __future__ import annotations

import logging
import os
import re
import ssl
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

ENV = "GUUDFLOW_CA_BUNDLE"


def _native_path(raw: str) -> str:
    """A Windows path (`C:\\certs\\corp.pem`) means the same file under /mnt/c on WSL/Linux, so
    one .env works from both sides."""
    raw = raw.strip().strip('"').strip("'")
    if os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", raw):
        return f"/mnt/{raw[0].lower()}/" + raw[3:].replace("\\", "/")
    return raw


def custom_bundle() -> Path | None:
    """The extra CA file, or None when none is configured. A missing file is an error."""
    raw = _native_path(os.environ.get(ENV, ""))
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"{ENV} points to {path}, which is not a file")
    return path


@lru_cache(maxsize=4)
def _context(bundle: str | None) -> ssl.SSLContext:
    import certifi
    ctx = ssl.create_default_context(cafile=certifi.where())
    if bundle:
        ctx.load_verify_locations(cafile=bundle)
    return ctx


def httpx_verify() -> ssl.SSLContext | bool:
    """`verify=` for httpx clients: the public roots plus the custom CA, or True (httpx's
    own default) when none is configured."""
    bundle = custom_bundle()
    return _context(str(bundle)) if bundle else True


def apply_env(data_dir: Path) -> Path | None:
    """Point subprocesses at a combined bundle (public roots + the custom CA). Returns the
    combined file, or None when no custom CA is configured. Values the operator set are kept."""
    bundle = custom_bundle()
    if bundle is None:
        return None
    import certifi
    out = Path(data_dir) / "tls" / "ca-bundle.pem"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(Path(certifi.where()).read_text() + "\n" + bundle.read_text())
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "GIT_SSL_CAINFO",
                "NPM_CONFIG_CAFILE"):
        os.environ.setdefault(var, str(out))
    os.environ.setdefault("NODE_EXTRA_CA_CERTS", str(bundle))  # node adds it to its own roots
    log.info("trusting the extra CA certificates in %s", bundle)
    return out
