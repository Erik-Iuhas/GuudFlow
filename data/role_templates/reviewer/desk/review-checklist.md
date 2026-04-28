---
name: Code Review Checklist
triggers: [review, checklist, pr, diff, quality, feedback]
priority: 6
kind: checklist
tags: [reviewer, quality]
---

# Code Review Checklist

Work through these in order. Stop at the first blocking issue and emit VERIFY FAIL with a specific detail string.

## Correctness (blocking)
- [ ] Logic matches the stated intent of the task
- [ ] Edge cases handled: empty input, null/None, zero, negative, max values
- [ ] Concurrent writes protected (lock, atomic op, or optimistic check)
- [ ] No off-by-one errors in loops, slices, pagination
- [ ] Error paths return/raise — no silent swallowing

## Security (blocking)
- [ ] No raw file paths from untrusted input — must go through `_safe_path()`
- [ ] External content wrapped in delimiters before LLM injection
- [ ] No secrets, tokens, or keys in code or log output
- [ ] SQL/command injection: parameterized queries only

## Protocol Compliance (blocking)
- [ ] Board posts use emit helpers, not raw strings
- [ ] VERIFY FAIL includes non-empty `detail`
- [ ] `check_user_board()` called between major steps

## Code Quality (non-blocking, flag but don't fail)
- [ ] No function longer than ~50 lines without clear justification
- [ ] Type hints on all public functions
- [ ] No commented-out code
- [ ] No print() — use logger.*
- [ ] Variable names describe what they hold, not how they're used

## Tests
- [ ] New behavior has test coverage
- [ ] Tests cover the failure path, not just the happy path
- [ ] No mocking of the database when a real integration test is possible

## Review Output Format
```
PASS — [one sentence summary of what was reviewed]

OR

FAIL — [specific issue, file:line if applicable]
  Details: [what is wrong and why it matters]
  Suggestion: [concrete fix, not "maybe consider"]
```
