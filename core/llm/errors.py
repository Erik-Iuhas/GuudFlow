"""Normalized LLM error taxonomy.

All LLM clients map provider exceptions to these classes so callers
never import provider SDKs just to write error handlers.
"""
from __future__ import annotations


class LLMError(Exception):
    """Base class for all normalized LLM errors."""


class RateLimitError(LLMError):
    """HTTP 429 or provider rate-limit signal. Retry with backoff."""

    def __init__(self, message: str = "", retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class AuthError(LLMError):
    """HTTP 401/403. Invalid API key or insufficient permissions. Fail fast."""


class ContextLengthError(LLMError):
    """Request exceeds model context window. Caller must compact and retry."""

    def __init__(self, message: str = "", max_tokens: int | None = None):
        super().__init__(message)
        self.max_tokens = max_tokens


class InvalidRequestError(LLMError):
    """HTTP 400 schema/payload error. Fail fast — do not retry."""


class ServerError(LLMError):
    """HTTP 5xx transient server error. Retry with backoff."""


class NetworkError(LLMError):
    """Connection-level failure (DNS, disconnect). Retry with backoff."""


class TimeoutError(LLMError):
    """Request exceeded configured timeout. Retry once."""
