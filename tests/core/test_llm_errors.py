"""Tests for the LLM error taxonomy and provider error mapping."""
from __future__ import annotations

import pytest

from core.llm.errors import (
    AuthError,
    ContextLengthError,
    InvalidRequestError,
    LLMError,
    NetworkError,
    RateLimitError,
    ServerError,
    TimeoutError,
)


# ── Taxonomy structure ─────────────────────────────────────────────────────────


def test_all_errors_are_llm_error_subclasses():
    for cls in (
        RateLimitError, AuthError, ContextLengthError, InvalidRequestError,
        ServerError, NetworkError, TimeoutError,
    ):
        assert issubclass(cls, LLMError)


def test_rate_limit_error_stores_retry_after():
    err = RateLimitError("too many requests", retry_after_s=30.5)
    assert err.retry_after_s == 30.5
    assert "too many requests" in str(err)


def test_rate_limit_error_retry_after_defaults_none():
    err = RateLimitError("rate limited")
    assert err.retry_after_s is None


def test_context_length_error_stores_max_tokens():
    err = ContextLengthError("too long", max_tokens=100000)
    assert err.max_tokens == 100000


def test_context_length_error_max_tokens_defaults_none():
    err = ContextLengthError("too long")
    assert err.max_tokens is None


# ── Anthropic error mapping ────────────────────────────────────────────────────


def test_anthropic_map_rate_limit():
    from unittest.mock import MagicMock
    import anthropic
    from core.llm.anthropic import _map_error

    exc = MagicMock(spec=anthropic.RateLimitError)
    exc.response = MagicMock()
    exc.response.headers = {"retry-after": "45"}
    exc.__str__ = lambda self: "rate limited"
    # Force isinstance check to work
    exc.__class__ = anthropic.RateLimitError

    result = _map_error(anthropic.RateLimitError.__new__(anthropic.RateLimitError))
    assert isinstance(result, RateLimitError)


def test_anthropic_map_auth_error():
    import anthropic
    from core.llm.anthropic import _map_error

    exc = anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)
    result = _map_error(exc)
    assert isinstance(result, AuthError)


def test_anthropic_map_permission_denied():
    import anthropic
    from core.llm.anthropic import _map_error

    exc = anthropic.PermissionDeniedError.__new__(anthropic.PermissionDeniedError)
    result = _map_error(exc)
    assert isinstance(result, AuthError)


def test_anthropic_map_bad_request_context_length():
    import anthropic
    from core.llm.anthropic import _map_error

    class FakeBadRequest(anthropic.BadRequestError):
        def __str__(self):
            return "context window exceeded"

    exc = FakeBadRequest.__new__(FakeBadRequest)
    result = _map_error(exc)
    assert isinstance(result, ContextLengthError)


def test_anthropic_map_bad_request_other():
    import anthropic
    from core.llm.anthropic import _map_error

    class FakeBadRequest(anthropic.BadRequestError):
        def __str__(self):
            return "invalid tool schema"

    exc = FakeBadRequest.__new__(FakeBadRequest)
    result = _map_error(exc)
    assert isinstance(result, InvalidRequestError)


def test_anthropic_map_internal_server_error():
    import anthropic
    from core.llm.anthropic import _map_error

    exc = anthropic.InternalServerError.__new__(anthropic.InternalServerError)
    result = _map_error(exc)
    assert isinstance(result, ServerError)


def test_anthropic_map_connection_error():
    import anthropic
    from core.llm.anthropic import _map_error

    exc = anthropic.APIConnectionError.__new__(anthropic.APIConnectionError)
    result = _map_error(exc)
    assert isinstance(result, NetworkError)


# ── OpenAI (httpx) error mapping ──────────────────────────────────────────────


def test_openai_map_401_to_auth_error():
    import httpx
    from core.llm.openai import _map_error

    request = httpx.Request("POST", "http://x/v1/chat/completions")
    response = httpx.Response(401, json={"error": {"message": "unauthorized"}}, request=request)
    exc = httpx.HTTPStatusError("401", request=request, response=response)
    result = _map_error(exc)
    assert isinstance(result, AuthError)


def test_openai_map_429_to_rate_limit():
    import httpx
    from core.llm.openai import _map_error

    request = httpx.Request("POST", "http://x/v1/chat/completions")
    response = httpx.Response(
        429,
        json={"error": {"message": "rate limited"}},
        headers={"retry-after": "60"},
        request=request,
    )
    exc = httpx.HTTPStatusError("429", request=request, response=response)
    result = _map_error(exc)
    assert isinstance(result, RateLimitError)
    assert result.retry_after_s == 60.0


def test_openai_map_429_no_retry_after():
    import httpx
    from core.llm.openai import _map_error

    request = httpx.Request("POST", "http://x/v1/chat/completions")
    response = httpx.Response(429, json={}, request=request)
    exc = httpx.HTTPStatusError("429", request=request, response=response)
    result = _map_error(exc)
    assert isinstance(result, RateLimitError)
    assert result.retry_after_s is None


def test_openai_map_400_context_length():
    import httpx
    from core.llm.openai import _map_error

    request = httpx.Request("POST", "http://x/v1/chat/completions")
    response = httpx.Response(
        400,
        json={"error": {"code": "context_length_exceeded", "message": "too long"}},
        request=request,
    )
    exc = httpx.HTTPStatusError("400", request=request, response=response)
    result = _map_error(exc)
    assert isinstance(result, ContextLengthError)


def test_openai_map_400_other():
    import httpx
    from core.llm.openai import _map_error

    request = httpx.Request("POST", "http://x/v1/chat/completions")
    response = httpx.Response(
        400,
        json={"error": {"code": "invalid_api_key", "message": "bad schema"}},
        request=request,
    )
    exc = httpx.HTTPStatusError("400", request=request, response=response)
    result = _map_error(exc)
    assert isinstance(result, InvalidRequestError)


def test_openai_map_500_to_server_error():
    import httpx
    from core.llm.openai import _map_error

    request = httpx.Request("POST", "http://x/v1/chat/completions")
    response = httpx.Response(500, json={}, request=request)
    exc = httpx.HTTPStatusError("500", request=request, response=response)
    result = _map_error(exc)
    assert isinstance(result, ServerError)


def test_openai_map_timeout():
    import httpx
    from core.llm.openai import _map_error

    exc = httpx.ReadTimeout("timed out")
    result = _map_error(exc)
    assert isinstance(result, TimeoutError)


def test_openai_map_connect_error():
    import httpx
    from core.llm.openai import _map_error

    exc = httpx.ConnectError("connection refused")
    result = _map_error(exc)
    assert isinstance(result, NetworkError)
