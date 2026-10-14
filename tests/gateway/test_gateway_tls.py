"""The certificate reaches agent model calls: opencode talks plain HTTP to the gateway on
localhost, and the gateway makes the real HTTPS call with GUUDFLOW_CA_BUNDLE (or the CA saved in
Studio). A real TLS server signed by a private CA, no mocks on the TLS side."""

from __future__ import annotations

import json
import shutil
import ssl
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from core import tls
from gateway.app import Gateway, Price, Upstream

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")

MODEL = "corp-model"
SSE = (b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
       b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":1}}\n\n'
       b"data: [DONE]\n\n")


def _openssl(*args: str, cwd: Path) -> None:
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def certs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("pki")
    _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem",
             "-days", "2", "-subj", "/CN=Test Corp Root CA",
             "-addext", "basicConstraints=critical,CA:TRUE",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign", cwd=d)
    _openssl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr",
             "-subj", "/CN=localhost", cwd=d)
    (d / "ext.cnf").write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\n")
    _openssl("x509", "-req", "-in", "srv.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
             "-CAcreateserial", "-out", "srv.pem", "-days", "2", "-extfile", "ext.cnf", cwd=d)
    return d


@pytest.fixture(scope="module")
def provider(certs: Path) -> Iterator[str]:
    """An OpenAI-compatible endpoint on https://localhost, signed by the private CA."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server API
            self.rfile.read(int(self.headers.get("content-length") or 0))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(SSE)))
            self.end_headers()
            self.wfile.write(SSE)

        def log_message(self, *_: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certs / "srv.pem", certs / "srv.key")
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"https://localhost:{server.server_address[1]}/v1"
    server.shutdown()


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GUUDFLOW_CA_BUNDLE", raising=False)
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    tls._reset()
    tls.set_data_root(tmp_path / "data")


async def _call(gw: Gateway) -> httpx.Response:
    key = gw.issue_key("run1", "dev", [MODEL])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app()),
                                 base_url="http://gateway") as c:
        return await c.post("/corp/chat/completions", headers={"authorization": f"Bearer {key}"},
                            json={"model": MODEL, "stream": True,
                                  "messages": [{"role": "user", "content": "hi"}]})


def _gateway(base: str) -> Gateway:
    return Gateway({"corp": Upstream("corp", "openai_compatible", base, "real-key")},
                   {MODEL: Price(input_per_1k=0.0, output_per_1k=0.0)}.get)


async def test_without_the_ca_the_agent_call_fails_with_the_certificate_reason(provider: str) -> None:
    gw = _gateway(provider)
    r = await _call(gw)
    await gw.aclose()
    assert r.status_code == 502 and "CERTIFICATE_VERIFY_FAILED" in r.json()["error"]["message"]


async def test_the_env_ca_reaches_the_agent_call(provider: str, certs: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUUDFLOW_CA_BUNDLE", str(certs / "ca.pem"))
    gw = _gateway(provider)
    r = await _call(gw)
    await gw.aclose()
    assert r.status_code == 200 and b"hi" in r.content


async def test_a_ca_saved_in_studio_reaches_a_running_gateway(provider: str, certs: Path) -> None:
    gw = _gateway(provider)                       # started before the CA was known
    assert (await _call(gw)).status_code == 502
    tls.save_app_setting(str(certs / "ca.pem"))   # Settings -> Certificate, no restart
    r = await _call(gw)
    await gw.aclose()
    assert r.status_code == 200 and json.loads(r.content.split(b"\n")[0][5:])["choices"]
