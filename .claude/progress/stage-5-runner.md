# Stage 5 — Runner & builtin agents

**Budget:** 600-850k tokens
**Status:** done (2026-04-25) — exit criteria met
**Exit criteria (CLAUDE.md):** A 2-agent run completes end-to-end with protocol messages on the board, git commits in workspace, quips in quip log; memory_writer creates a new episode in episodes.jsonl, updates pattern confidence if applicable, and refreshes reputation.json post-run.

DESIGN sections: §3.3, §5, §7, §17, §19, §20, §23, §29, §31, §32, §34, §35.

## Files

### Foundation
- [x] `core/llm.py` ✅ — `LLMMessage`, `LLMResponse`, `ToolCall`, `LLMClient` Protocol, `MockLLMClient` (script-driven for tests), `AnthropicClient` adapter
- [x] `core/runner.py` ✅ — `AgentRunner` class
  - workspace + file_workspace + git_workspace + repo_map setup
  - per-agent ToolManifest via builder
  - per-agent local tool instantiation (claim emitters wired to BaseAgent.claim_files/release_files)
  - tier→model mapping
  - generic tool-use loop for prompt-defined agents
  - auto-commit on agent completion (commits any uncommitted changes on agent's branch)
  - reviewer merges upstream branches into main on APPROVED
  - GIT_COMMIT triggers repo_map rebuild
  - memory_writer + quip_engine triggered post-run
- [x] `core/quip_engine.py` ✅ — secondary FAST-tier LLM call writing to `data/runs/{run_id}/quip_log.json`
- [x] BaseAgent updated with `_llm`, `_model`, `llm()` method, `_tool_schemas_for_llm()`

### Builtin agents
- [x] `agents/builtin/it_agent.py` ✅ — `ITAgent` + `PackagePolicy` (allowlist + block_list, version pinning required)
- [x] `agents/builtin/code_reviewer.py` ✅ — reviewer with §18-aware system prompt
- [x] `agents/builtin/test_runner.py` ✅ — wraps `code:exec`, parses pytest/jest output
- [x] `agents/builtin/tech_lead.py` ✅ — escalation handler shell, HEAVY tier prompt
- [x] `agents/builtin/meta.py` ✅ — proposal-generator shell, `TeamProposal` dataclass
- [x] `agents/builtin/visual_checker.py` ✅ — request_visual_feedback wrapper shell
- [x] `agents/builtin/rubric_scorer.py` ✅ — auto-grader for `files_present` and `text_match` checks (training mode per §35)
- [x] `agents/builtin/memory_writer.py` ✅ — full §34 reconciliation pass:
  - episode recording with auto-tag extraction
  - recency-weighted confidence rebuild
  - reputation refresh (production/training split)
  - contradiction reconciliation (FIRST in reconciliation order so overlapping ACTIVE patterns supersede before lifecycle transitions)
  - lifecycle transitions (emerging → active → declining → archived)
  - episode staleness recomputation
  - atomic JSON/JSONL rewrites via tempfile + shutil.move
  - user_pinned patterns excluded from auto-transitions

## Tests
- [x] `tests/core/test_llm.py` — 6 (mock script, tool calls, exhaustion guard)
- [x] `tests/core/test_runner.py` — 7 (init, manifest building, tier→model, no-agents, factory)
- [x] `tests/core/test_runner_e2e.py` — 1 e2e (developer + reviewer, file written, commit, merge, episodes, reputation)
- [x] `tests/core/test_quip_engine.py` — 6 (persistence, FAST tier, capping, failure handling, accumulation)
- [x] `tests/agents/builtin/test_it_agent.py` — 6 (approve/reject/version/blocklist/multiple)
- [x] `tests/agents/builtin/test_test_runner.py` — 4 (pytest pass/fail, jest, unknown)
- [x] `tests/agents/builtin/test_rubric_scorer.py` — 5 (files_present, text_match, weighted, unsupported, zero-weight)
- [x] `tests/agents/builtin/test_memory_writer.py` — 10 (episodes, reputation, training split, lifecycle transitions, contradictions, persistence, missing _dir guard)

## Verification
- Full repo: **413 passed, 2 skipped (POSIX-only)**
- E2E test verifies all Stage 5 exit criteria in one go

## Notes
- Generic tool-use loop in runner handles prompt-defined agents; subclasses can override `run()` for special behavior (TestRunner, ITAgent, etc.).
- The reviewer's `is_reviewer=True` triggers merge-on-completion. Agent's branch merges into main only when the reviewer's run() returns successfully.
- MemoryWriter persistence relies on `persona._dir` being set by the runner (or test) — this is a runtime convention, not a profile field.
- Quip engine FAILS GRACEFULLY — if the LLM call raises, the quip log records "(quip failed: ...)" rather than propagating to the run.
- Stage 5 ships hooks/shells for `tech_lead`, `meta`, `visual_checker` — they have system prompts and registered classes but their domain-specific logic (escalation detection in runner, proposal synthesis, real Playwright wiring) lands in Stage 6+ as the API surfaces emerge.
- `agents/builtin/credentials_broker.py` deliberately not added — `core/credentials_broker.py` already provides the broker class; agents call it directly via `request_credential`. The §30 "broker as event-driven agent" framing was redesigned during Stage 4 to a class with handler registration, which is simpler and equivalent.
