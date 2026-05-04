"""Client wrapper — verify URL composition, auth header, and error
mapping. We use httpx's MockTransport so no real socket is opened."""

from __future__ import annotations

import httpx
import pytest

from cli.client import APIError, Client


@pytest.fixture(autouse=True)
def _patch_request(monkeypatch):
    """Route every httpx.request call through a transport we control."""
    captured: dict[str, httpx.Request] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["last"] = request
        # Default: 200 OK with empty dict.
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(_handler)

    def _request(method, url, **kwargs):
        # Strip args httpx.Client doesn't accept.
        kwargs.pop("timeout", None)
        with httpx.Client(transport=transport) as inner:
            return inner.request(method, url, **kwargs)

    monkeypatch.setattr(httpx, "request", _request)
    return captured


def test_default_base_url_when_unset(monkeypatch):
    monkeypatch.delenv("GUUDFLOW_API_URL", raising=False)
    monkeypatch.delenv("GUUDFLOW_API_TOKEN", raising=False)
    c = Client()
    assert c.base_url == "http://127.0.0.1:8000"
    assert c.token is None


def test_env_picks_up_url_and_token(monkeypatch):
    monkeypatch.setenv("GUUDFLOW_API_URL", "https://example.com/")
    monkeypatch.setenv("GUUDFLOW_API_TOKEN", "secret")
    c = Client()
    assert c.base_url == "https://example.com"  # trailing slash stripped
    assert c.token == "secret"


def test_explicit_args_override_env(monkeypatch):
    monkeypatch.setenv("GUUDFLOW_API_URL", "https://example.com")
    monkeypatch.setenv("GUUDFLOW_API_TOKEN", "x")
    c = Client(base_url="http://host:9000", token="other")
    assert c.base_url == "http://host:9000"
    assert c.token == "other"


def test_health_path(_patch_request):
    Client(base_url="http://h").health()
    req = _patch_request["last"]
    assert req.url.path == "/health"
    assert req.method == "GET"


def test_bearer_token_header_when_set(_patch_request):
    Client(base_url="http://h", token="abc").list_projects()
    req = _patch_request["last"]
    assert req.headers.get("authorization") == "Bearer abc"


def test_no_auth_header_when_token_missing(monkeypatch, _patch_request):
    monkeypatch.delenv("GUUDFLOW_API_TOKEN", raising=False)
    Client(base_url="http://h").list_projects()
    req = _patch_request["last"]
    assert "authorization" not in {k.lower() for k in req.headers.keys()}


def test_runs_list_passes_project_filter(_patch_request):
    Client(base_url="http://h").list_runs(project_id="proj", limit=5)
    req = _patch_request["last"]
    assert req.url.path == "/api/runs"
    qs = dict(req.url.params)
    assert qs.get("project_id") == "proj"
    assert qs.get("limit") == "5"


def test_post_run_comment_body(_patch_request):
    Client(base_url="http://h", token="t").post_run_comment(
        "r1", "hello", priority="high"
    )
    req = _patch_request["last"]
    assert req.method == "POST"
    assert req.url.path == "/api/runs/r1/comment"
    import json

    body = json.loads(req.content.decode("utf-8"))
    assert body == {"text": "hello", "priority": "high"}


def test_4xx_response_raises_apierror(monkeypatch):
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "missing"})

    transport = httpx.MockTransport(_handler)

    def _request(method, url, **kwargs):
        kwargs.pop("timeout", None)
        with httpx.Client(transport=transport) as c:
            return c.request(method, url, **kwargs)

    monkeypatch.setattr(httpx, "request", _request)

    with pytest.raises(APIError) as exc:
        Client(base_url="http://h").get_project("nope")
    assert exc.value.status == 404
    assert "missing" in exc.value.detail


def test_connection_error_wrapped(monkeypatch):
    def _request(method, url, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "request", _request)

    with pytest.raises(APIError) as exc:
        Client(base_url="http://h").health()
    assert exc.value.status == 0
    assert "refused" in exc.value.detail


def test_204_returns_none(monkeypatch):
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    transport = httpx.MockTransport(_handler)

    def _request(method, url, **kwargs):
        kwargs.pop("timeout", None)
        with httpx.Client(transport=transport) as c:
            return c.request(method, url, **kwargs)

    monkeypatch.setattr(httpx, "request", _request)
    out = Client(base_url="http://h", token="t").delete_project("demo")
    assert out is None
