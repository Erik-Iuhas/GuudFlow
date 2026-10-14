"""Trusting a custom certificate authority (a company proxy or an internal provider endpoint).

Set `GUUDFLOW_CA_BUNDLE` to a PEM (or binary .cer) file with the extra CA certificate(s), in
.env, or as a backup in Studio (Settings -> Certificate, stored in `<data>/tls/settings.json`,
used when the .env path is unset or not found). GuudFlow then trusts
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
import json
from pathlib import Path

log = logging.getLogger(__name__)

ENV = "GUUDFLOW_CA_BUNDLE"
_data_root = Path(os.environ.get("GUUDFLOW_DATA_DIR", "./data"))


def _native_path(raw: str) -> str:
    """A Windows path (`C:\\certs\\corp.pem`) means the same file under /mnt/c on WSL/Linux, so
    one .env works from both sides."""
    raw = raw.strip().strip('"').strip("'")
    unc = re.match(r"^[\\/]{2}wsl(?:\.localhost|\$)[\\/][^\\/]+(.*)$", raw, re.IGNORECASE)
    if os.name != "nt" and unc:  # \\wsl.localhost\Ubuntu\home\x\ca.pem -> /home/x/ca.pem
        return unc.group(1).replace("\\", "/") or "/"
    if os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", raw):
        return f"/mnt/{raw[0].lower()}/" + raw[3:].replace("\\", "/")
    return raw


def set_data_root(path: Path) -> None:
    """Where the Studio setting lives (`<data>/tls/settings.json`); the API sets it at startup."""
    global _data_root
    _data_root = Path(path)


def _settings_file() -> Path:
    return _data_root / "tls" / "settings.json"


def app_setting() -> str:
    """The CA path saved in Studio, or ""."""
    try:
        return str(json.loads(_settings_file().read_text()).get("ca_bundle") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def save_app_setting(raw: str) -> Path | None:
    """Save the CA path from Studio (empty clears it). The file must exist and hold certificates;
    it is trusted from the next connection on, without a restart."""
    raw = _native_path(raw)
    if raw:
        path = Path(raw).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"{path} is not a file")
        pem_text(path)  # PEM or DER, or a clear error
    f = _settings_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"ca_bundle": raw}))
    bundle = custom_bundle()
    if bundle is not None:
        httpx_verify()
        apply_env(_data_root)
    return bundle


def custom_bundle() -> Path | None:
    """The CA file: GUUDFLOW_CA_BUNDLE, else (or when that path is not found) the Studio setting.
    None when neither is set. An .env path that is not found, with no working Studio path to fall
    back on, is an error."""
    raw = _native_path(os.environ.get(ENV, ""))
    saved = _native_path(app_setting())
    if raw:
        path = Path(raw).expanduser()
        if path.is_file():
            return path
        if saved and Path(saved).expanduser().is_file():
            log.warning("%s=%s is not a file; using the path saved in Studio: %s", ENV, path, saved)
            return Path(saved).expanduser()
        raise FileNotFoundError(f"{ENV} points to {path}, which is not a file")
    if saved:
        path = Path(saved).expanduser()
        if path.is_file():
            return path
        log.warning("the CA path saved in Studio (%s) is not a file; ignoring it", path)
    return None


def status() -> dict[str, object]:
    """For Studio: both paths, which one is used, and the certificates it holds."""
    out: dict[str, object] = {"env": os.environ.get(ENV, ""), "app": app_setting(),
                              "effective": None, "source": None, "certs": [], "error": None}
    try:
        bundle = custom_bundle()
    except (FileNotFoundError, ValueError) as e:
        out["error"] = str(e)
        return out
    if bundle is None:
        return out
    env_path = _native_path(os.environ.get(ENV, ""))
    out["effective"] = str(bundle)
    out["source"] = "env" if env_path and Path(env_path).expanduser() == bundle else "app"
    try:
        out["certs"] = cert_names(pem_text(bundle))
    except ValueError as e:
        out["error"] = str(e)
    return out


def cert_names(pem: str) -> list[str]:
    """'subject (issued by issuer)' for each certificate in a PEM text."""
    import tempfile
    names = []
    for chunk in pem.split("-----END CERTIFICATE-----")[:-1]:
        if "-----BEGIN CERTIFICATE-----" not in chunk:
            continue
        body = chunk[chunk.index("-----BEGIN CERTIFICATE-----"):] + "-----END CERTIFICATE-----\n"
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
            f.write(body)
        try:
            d = ssl._ssl._test_decode_cert(f.name)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - a name is only for display
            continue
        finally:
            os.unlink(f.name)
        def cn(parts: tuple) -> str:  # noqa: E306
            kv = {k: v for rdn in parts for k, v in rdn}
            return str(kv.get("commonName") or kv.get("organizationName") or "?")
        names.append(f"{cn(d['subject'])} (issued by {cn(d['issuer'])})")
    return names


def pem_text(path: Path) -> str:
    """The certificates in `path` as PEM text. A binary (DER) `.cer`/`.crt`, as Windows exports
    by default, is converted, so either format works."""
    data = path.read_bytes()
    if b"-----BEGIN CERTIFICATE-----" in data:
        return data.decode("utf-8", "replace").replace("\ufeff", "")
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=data)  # valid DER?
        return ssl.DER_cert_to_PEM_cert(data)
    except ssl.SSLError as e:
        raise ValueError(f"{ENV}: {path} is neither a PEM nor a DER certificate") from e


_ctx: ssl.SSLContext | None = None
_loaded: set[str] = set()


def _reset() -> None:
    """Forget the shared context (tests)."""
    global _ctx
    _ctx = None
    _loaded.clear()


def httpx_verify() -> ssl.SSLContext:
    """`verify=` for httpx clients: one shared context with the public roots (certifi) and the
    custom CA. Verification and the hostname check stay on. A CA saved later (Studio) is added to
    this same context, so clients already made trust it from their next connection. A custom CA
    that is an intermediate rather than a root is accepted as a trust anchor."""
    global _ctx
    if _ctx is None:
        import certifi
        _ctx = ssl.create_default_context(cafile=certifi.where())
    bundle = custom_bundle()
    if bundle is not None and str(bundle) not in _loaded:
        _ctx.load_verify_locations(cadata=pem_text(bundle))
        _ctx.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
        _loaded.add(str(bundle))
        log.info("trusting the CA certificates in %s", bundle)
    return _ctx


def apply_env(data_dir: Path) -> Path | None:
    """Point subprocesses at a combined bundle (public roots + the custom CA). Returns the
    combined file, or None when no custom CA is configured. Values the operator set are kept."""
    bundle = custom_bundle()
    if bundle is None:
        return None
    import certifi
    out = Path(data_dir) / "tls" / "ca-bundle.pem"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(Path(certifi.where()).read_text() + "\n" + pem_text(bundle))
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "GIT_SSL_CAINFO",
                "NPM_CONFIG_CAFILE"):
        os.environ.setdefault(var, str(out))
    os.environ.setdefault("NODE_EXTRA_CA_CERTS", str(bundle))  # node adds it to its own roots
    log.info("trusting the extra CA certificates in %s", bundle)
    return out


def describe_error(exc: BaseException) -> str:
    """A connection error in a sentence, with the certificate reason and what to do about it."""
    text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    if "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text:
        try:
            bundle = custom_bundle()
        except (FileNotFoundError, ValueError):
            bundle = None
        hint = (f"set {ENV} in .env, or the CA path in Settings -> Certificate, to your company's "
                "root CA (PEM or .cer)" if bundle is None else
                f"the CA in {bundle} is not the one that signed this certificate; use your "
                "company's root CA (the top of the chain)")
        return f"{text[:300]} ({hint})"
    return text[:300]
