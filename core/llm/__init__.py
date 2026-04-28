"""LLM abstraction package.

Re-exports all public symbols so `from core.llm import X` continues to work
identically after the single-file → package split.
"""
from core.llm.client import (
    LLMClient,
    LLMMessage,
    LLMResponse,
    MockLLMClient,
    MockTurn,
    ToolCall,
)
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
from core.llm.anthropic import AnthropicClient
from core.llm.openai import (
    OpenAIClient,
    _from_openai_response,
    _to_openai_messages,
    _to_openai_tool,
    ollama,
    openai_compatible,
)
from core.llm.registry import ModelInfo, ModelRegistry, NoModelAvailableError, ProviderInfo

__all__ = [
    # Protocol + dataclasses
    "LLMClient",
    "LLMMessage",
    "LLMResponse",
    "ToolCall",
    # Mock
    "MockLLMClient",
    "MockTurn",
    # Errors
    "LLMError",
    "RateLimitError",
    "AuthError",
    "ContextLengthError",
    "InvalidRequestError",
    "ServerError",
    "NetworkError",
    "TimeoutError",
    # Clients
    "AnthropicClient",
    "OpenAIClient",
    # Factories
    "ollama",
    "openai_compatible",
    # Private helpers re-exported for test backward compat
    "_from_openai_response",
    "_to_openai_messages",
    "_to_openai_tool",
    # Registry
    "ModelInfo",
    "ModelRegistry",
    "NoModelAvailableError",
    "ProviderInfo",
]
