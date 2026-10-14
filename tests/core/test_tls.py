"""A custom CA is trusted on top of the public roots, never instead of verification."""

from __future__ import annotations

import ssl
from pathlib import Path

import certifi
import pytest

from core import tls

_VARS = ("GUUDFLOW_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
         "GIT_SSL_CAINFO", "NPM_CONFIG_CAFILE", "NODE_EXTRA_CA_CERTS")


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)


def test_no_custom_ca_keeps_the_default() -> None:
    assert tls.httpx_verify() is True
    assert tls.apply_env(Path("/nonexistent")) is None


def test_a_custom_ca_gives_a_verifying_context(monkeypatch: pytest.MonkeyPatch,
                                               tmp_path: Path) -> None:
    ca = tmp_path / "corp.pem"
    ca.write_text(Path(certifi.where()).read_text().split("-----END CERTIFICATE-----")[0]
                  + "-----END CERTIFICATE-----\n")
    monkeypatch.setenv("GUUDFLOW_CA_BUNDLE", str(ca))
    ctx = tls.httpx_verify()
    assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode is ssl.CERT_REQUIRED
    out = tls.apply_env(tmp_path / "data")
    assert out is not None and out.read_text().endswith(ca.read_text())
    import os
    assert os.environ["GIT_SSL_CAINFO"] == str(out) and os.environ["NODE_EXTRA_CA_CERTS"] == str(ca)


def test_a_missing_file_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUUDFLOW_CA_BUNDLE", "/no/such/ca.pem")
    with pytest.raises(FileNotFoundError, match="GUUDFLOW_CA_BUNDLE"):
        tls.httpx_verify()
