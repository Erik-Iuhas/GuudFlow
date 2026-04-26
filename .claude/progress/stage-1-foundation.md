# Stage 1 — Foundation primitives ✅ DONE

**Budget:** 150-250k tokens
**Actual:** ~95k tokens
**Status:** done (2026-04-25)
**Exit criteria:** All signal types defined; protocol parse/render roundtrips cleanly in tests; ProtocolMessage dataclass complete.

## Files
- [x] `requirements.txt` — pinned core deps (gitpython, docker, tree-sitter, fastapi, anthropic, playwright, pytest)
- [x] `.gitignore`, `.env.example`, `pytest.ini`
- [x] `core/__init__.py`, `tests/__init__.py`, `tests/core/__init__.py`
- [x] `core/signals.py`
  - [x] implementation — SignalType (20 types incl. PROTOCOL/GIT_*/USER_*/SCREENSHOT_READY/PACKAGE_*)
  - [x] `Signal` dataclass with origin validation (user vs agent), `mark_seen_by()`
  - [x] tests in `tests/core/test_signals.py` (10 tests)
  - [x] all green
- [x] `core/protocol.py`
  - [x] `FileChange`, `VerifyResult` (FAIL requires detail), `ProtocolMessage` dataclasses
  - [x] render_*: commit/verify/delta/action/ask/answer/status
  - [x] strict `parse(raw)` with dispatch table
  - [x] tests in `tests/core/test_protocol.py` (19 tests, full roundtrips)
  - [x] all green

## Verification
`python -m pytest tests/core/ -v` → **29/29 passing in 0.05s**

## Notes
- Strict parser chosen over lenient — see decisions.md.
- ASK/ANSWER parser accepts both `→` and `->` to be robust against arrow-stripping copy paste.
- VerifyResult validates in `__post_init__` so render_verify can never produce an invalid FAIL line.
