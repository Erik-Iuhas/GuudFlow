---
name: Common Bug Patterns to Watch For
triggers: [bug, issue, mistake, async, race, leak, regression]
priority: 5
kind: reference
tags: [reviewer, bugs]
---

# Common Bug Patterns

## Async / Concurrency
- **Missing await**: `result = async_fn()` returns a coroutine, not the value. Look for unhandled coroutine warnings.
- **asyncio.gather without error handling**: one task failing silently lets others succeed; use `return_exceptions=True` or handle individually.
- **Lock held across await**: deadlock risk if anything tries to re-acquire the same lock in the awaited code.
- **asyncio.gather OUTSIDE scheduler.py**: violates hard constraint §2. Flag immediately.

## File I/O
- **Path concatenation instead of `/`**: `path + "/file"` fails on Windows, use `path / "file"`.
- **No atomic write**: writing directly to the target file leaves it corrupt on crash. Should use temp file + rename.
- **Not closing file handles**: use `with` blocks, never bare `open()`.

## State Management
- **Mutable default argument**: `def f(x=[])` shares the list across calls. Use `None` default with `if x is None: x = []`.
- **Dict modified during iteration**: raises `RuntimeError`. Iterate over `list(d.items())` or collect changes first.
- **Stale closure**: lambda in a loop captures the loop variable by reference, not value.

## Agent Protocol
- **FAIL detail is empty string**: raises ValueError in `emit_verify()`, per design constraint.
- **Agent reads raw board text**: must use `read_protocol()`, not string parsing.
- **Same USER_COMMENT processed twice**: check `seen_by` before absorbing.

## Memory / History
- **Pattern without source_episodes**: invalid per design §26. `source_episodes` must be a non-empty list.
- **Confidence not clamped**: should stay in [0.0, 1.0]. Check arithmetic operations that could exceed range.
