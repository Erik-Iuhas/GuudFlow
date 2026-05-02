# Track 9 — Protocol cleaner sidecar (auto-fire on PROTOCOL_PARSE_FAIL)

**Status:** In progress. Sonnet-executable.
**Parent plan:** previously deferred in `master_subtasks_dispatch_t7_t8_t12.md`; spec finalised here.
**Token budget:** 100–200k. Stop and reassess if over 250k.
**Depends on:** Track 12 (sub-LLM dispatch — provides `protocol-cleanup` wrapper, currently dormant).
**Unblocks:** Track 12's `protocol-cleanup` becomes the Track 9 sidecar's primitive — dormant → live.

---

## Why

Today's parse-fail handler ([core/runner.py:3081-3146](core/runner.py:3081)):
1. Posts `PROTOCOL_PARSE_FAIL` signal.
2. Nudges the agent ("end your response with EXACTLY [COMMIT]…").
3. Re-runs the agent's HEAVY model with the nudge appended → another full LLM round-trip
   (~10K-25K input tokens at Sonnet/Opus rates).
4. Up to `max_protocol_retries` (default 3) before giving up with synthetic VERIFY FAIL.

Each retry costs full agent rate. The codebase_analyst on QuickRefs-web
(run_f7d1422d5b) burned ~120s on 2 retries before the user stopped it.

The agent doesn't NEED to reason again — it already produced the answer
in prose. It just needs the prose translated into the protocol format.
That's exactly what Track 12's `protocol-cleanup` wrapper does, with a
cheap model. Wiring it before the nudge turns a 25K-token Sonnet retry
into a 500-token Haiku dispatch.

## Outcome

- Before posting the existing nudge + re-running the agent, the runner
  invokes `protocol-cleanup` against the agent's raw response with the
  expected_kind matching the nudge's example (`VERIFY` for reviewers,
  `COMMIT` otherwise).
- The cleanup result is parsed via `protocol.extract_trailing`. If
  parse succeeds, we **skip the retry entirely** and return that block.
  Run continues without burning a heavy-tier round-trip.
- If cleanup itself fails (LLM error, parse-fail on the cleaned text),
  fall through to today's nudge logic — strictly additive, never
  regresses behaviour.
- Auto-cleanup attempts are tracked in a separate counter so they
  don't consume `protocol_retries_used`. Cap at 1 attempt per
  parse-fail (don't loop on a stuck cleanup model).
- A new signal `PROTOCOL_CLEANUP_AUTO` lands on the board so the user
  can see when the sidecar saved a retry.

## Files to modify

| File | Change |
|---|---|
| `core/runner.py` parse-fail handler | After `parse_error` is set + before the existing PROTOCOL_PARSE_FAIL signal, invoke a new `_try_auto_protocol_cleanup(role, text, is_reviewer)` helper. On success, return the parsed block. On failure or skip, continue to the existing nudge path. |
| `core/runner.py` `_try_auto_protocol_cleanup` | **New method.** Pulls the agent's `protocol-cleanup` tool from `self.agents[role]._local_tools`. Calls it with `{raw_response: text, expected_kind: "VERIFY"|"COMMIT"}`. Parses returned block via `protocol.extract_trailing`. Returns `(block, summary_text)` on success, `(None, error_text)` on failure. Capped at one attempt per call site (no retry on cleanup-of-cleanup failure). |
| `core/signals.py` | New `SignalType.PROTOCOL_CLEANUP_AUTO`. Posted by the helper on success so the board surfaces the rescue. |
| `tests/integration/test_protocol_cleanup_sidecar.py` | **New.** (a) Cleanup succeeds → skips the heavy retry; (b) Cleanup fails → falls through to existing nudge; (c) cleanup is tried once per parse-fail (no nested retry); (d) PROTOCOL_CLEANUP_AUTO signal lands; (e) sub_llm_breakdown reflects the cleanup cost. |

## Implementation order (fine-grained)

1. Add `SignalType.PROTOCOL_CLEANUP_AUTO`.
2. Add `_try_auto_protocol_cleanup` helper in `core/runner.py`.
3. Wire the helper into the parse-fail branch — after `parse_error` is
   detected, before the existing PROTOCOL_PARSE_FAIL signal post.
4. Tests covering the four scenarios above.
5. Full sweep + commit.

## Acceptance criteria

1. **Sidecar success short-circuits the retry.** When the agent emits
   prose without a protocol block AND `protocol-cleanup` returns a
   parseable block, the runner returns that block instead of nudging.
   `protocol_retries_used` doesn't increment.
2. **Sidecar failure is silent.** If `protocol-cleanup` errors (LLM
   error, registry empty) OR returns text that doesn't parse, the
   runner falls through to today's nudge path. Existing tests still
   pass byte-identically.
3. **One attempt per parse-fail.** Multiple consecutive parse-fails
   each trigger ONE cleanup attempt. No nested retry on the cleanup
   itself.
4. **Observability.** `PROTOCOL_CLEANUP_AUTO` signal posted on
   success with detail naming the agent + the kind of block recovered.
   `IterationMetrics.sub_llm_breakdown["protocol-cleanup"]` accumulates
   the cleanup cost (already wired by Track 12's metrics sink).
5. **No-op for agents without the tool.** Some agents may have
   `protocol-cleanup` excluded from their RBAC scopes (the scope is
   non-baseline). When the tool isn't in `_local_tools`, the helper
   returns None silently and the existing nudge fires.

## Risks

- **Cleanup model fabricates content.** A small model could rewrite
  the agent's prose into a `[COMMIT]` block whose summary is
  inaccurate. Mitigation: the `PROTOCOL_CLEANUP_SYSTEM` prompt is
  locked (Track 12) and forbids inventing task ids; reviewer
  pre-merge VERIFY still gates real correctness via `git diff`.
  This sidecar is a *format* fixer, not a *content* fixer.
- **Cleanup adds latency on the happy path.** No — it only fires
  when parse already failed. Happy path (block parses first try)
  costs zero.
- **Cleanup cost compounds if every iteration parses fails.**
  Bounded by the project's per-iteration cleanup-call cap. Use the
  same envelope as `summarise-subtask`: at most one cleanup per
  parse-fail, max `max_protocol_retries` parse-fails per iteration.
  At cheapest-tool-capable rates that's negligible.
- **Cleanup tool isn't granted.** Helper returns None silently.
  Ship docs noting `protocol-cleanup` should be a baseline RBAC
  scope so this works out of the box. (Decision: add to baseline
  in this same commit.)

## Commit plan

```
feat(track-9): PROTOCOL_CLEANUP_AUTO signal type
feat(track-9): _try_auto_protocol_cleanup helper in runner
feat(track-9): wire sidecar into PROTOCOL_PARSE_FAIL handler
feat(tool_manifest): protocol-cleanup is baseline (Track 9 needs it)
test(track-9): sidecar success/failure paths + signal + metrics
```

## After this lands

- Track 12's `protocol-cleanup` wrapper is no longer dormant — fires
  automatically on every parse failure.
- Codebase_analyst-style stalls become 1-cleanup-call instead of
  multi-retry. Empirical win measurable as `(sub_llm_breakdown["protocol-cleanup"].calls
  > 0 in iterations where protocol_retries_used remained 0)`.
- Track 12's two opinionated wrappers now both have live consumers
  (T8 still uses M5.5's path for marker text — see deferred notes in
  `track_8_subtask_context_hotswap.md`).
