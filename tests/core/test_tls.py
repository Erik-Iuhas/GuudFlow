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
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)
    tls._reset()
    tls.set_data_root(tmp_path / "appdata")


def test_no_custom_ca_still_verifies() -> None:
    ctx = tls.httpx_verify()
    assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode is ssl.CERT_REQUIRED and ctx.check_hostname
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


def test_a_windows_path_means_the_wsl_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tls.os, "name", "posix")
    assert tls._native_path("C:\\certs\\corp.pem") == "/mnt/c/certs/corp.pem"
    assert tls._native_path('"D:/x/ca.pem"') == "/mnt/d/x/ca.pem"
    assert tls._native_path("/etc/ssl/corp.pem") == "/etc/ssl/corp.pem"
    assert tls._native_path("\\\\wsl.localhost\\Ubuntu\\home\\me\\ca.pem") == "/home/me/ca.pem"
    assert tls._native_path("//wsl$/Ubuntu-22.04/etc/ssl/ca.pem") == "/etc/ssl/ca.pem"


def test_a_der_certificate_works_and_an_intermediate_is_an_anchor(monkeypatch: pytest.MonkeyPatch,
                                                                   tmp_path: Path) -> None:
    first = Path(certifi.where()).read_text().split("-----END CERTIFICATE-----")[0]
    pem = first[first.index("-----BEGIN"):] + "-----END CERTIFICATE-----\n"
    der = tmp_path / "corp.cer"
    der.write_bytes(ssl.PEM_cert_to_DER_cert(pem))
    monkeypatch.setenv("GUUDFLOW_CA_BUNDLE", str(der))
    ctx = tls.httpx_verify()
    assert ctx.verify_mode is ssl.CERT_REQUIRED and ctx.check_hostname
    if hasattr(ssl, "VERIFY_X509_PARTIAL_CHAIN"):
        assert ctx.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
    assert "BEGIN CERTIFICATE" in (tls.apply_env(tmp_path / "data") or Path()).read_text()


def test_garbage_is_a_clear_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bad = tmp_path / "x.pem"
    bad.write_bytes(b"not a certificate")
    monkeypatch.setenv("GUUDFLOW_CA_BUNDLE", str(bad))
    with pytest.raises(ValueError, match="neither a PEM nor a DER"):
        tls.httpx_verify()


def test_the_studio_path_is_a_backup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first = Path(certifi.where()).read_text().split("-----END CERTIFICATE-----")[0]
    ca = tmp_path / "corp.pem"
    ca.write_text(first[first.index("-----BEGIN"):] + "-----END CERTIFICATE-----\n")
    with pytest.raises(FileNotFoundError):
        tls.save_app_setting(str(tmp_path / "nope.pem"))
    assert tls.save_app_setting(str(ca)) == ca          # nothing in .env: the Studio path
    assert tls.status()["source"] == "app" and tls.status()["certs"]
    monkeypatch.setenv("GUUDFLOW_CA_BUNDLE", "/no/such/ca.pem")
    assert tls.custom_bundle() == ca                    # .env path not found: falls back
    monkeypatch.setenv("GUUDFLOW_CA_BUNDLE", str(ca))
    assert tls.status()["source"] == "env"
    tls.save_app_setting("")
    monkeypatch.delenv("GUUDFLOW_CA_BUNDLE")
    assert tls.custom_bundle() is None
