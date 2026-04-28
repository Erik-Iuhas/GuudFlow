---
name: Error Handling & Retry Patterns
triggers: [error, exception, retry, timeout, fallback, failure]
priority: 4
kind: reference
tags: [backend, reliability]
---

# Error Handling Patterns

## Exception Hierarchy
Define domain exceptions, not generic ones:
```python
class AgentFlowError(Exception): ...          # base
class WorkspaceError(AgentFlowError): ...     # file ops
class SchedulerError(AgentFlowError): ...     # orchestration
class ToolError(AgentFlowError): ...          # tool call failures
```
Catch specific types; let unexpected exceptions propagate to the runner.

## Retry Strategy
Use exponential backoff with jitter for transient errors:
```python
base = 1.0       # seconds
max_wait = 30.0
jitter = random.uniform(0, 0.5)
wait = min(base * (2 ** attempt) + jitter, max_wait)
```
Retryable: network timeouts, 429, 503.
Non-retryable: 400, 401, 403, 404, 409, 422.

## Async Timeouts
Always set explicit timeouts on external calls:
```python
async with asyncio.timeout(30):
    result = await call_external_service()
```
Never await without a timeout in production paths.

## Structured Logging on Errors
```python
logger.error(
    "Tool call failed",
    extra={"tool": name, "run_id": run_id, "attempt": n, "error": str(e)}
)
```
Include enough context to reproduce the failure without extra investigation.

## Circuit Breaker (for repeated LLM/external failures)
- Open after N consecutive failures (default: 5)
- Half-open after cooldown (default: 60s)
- Reset on success in half-open
- Expose state in health check endpoint
