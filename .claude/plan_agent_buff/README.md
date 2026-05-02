# plan_agent_buff — Execution Queue

Detailed plans for the GuudFlow multi-track roadmap. Parent strategy doc: `~/.claude/plans/i-think-right-now-linked-rabin.md`.

Each file is **Sonnet-executable**: self-contained, code-cited, with acceptance criteria and a commit plan. Treat the parent roadmap as "why," and these as "how."

**Progress: 6 / 16 tracks queued.** ✅ Track 1, ✅ Track 1b, ✅ Track 1c, ✅ Track 1d, ✅ Track 3e, ✅ Track 3d.4.

## Files

| File | Track | Type | Status | Depends on | Token est. |
|---|---|---|---|---|---|
| `track_1_iteration_metrics.md` | 1 | Foundation | ✅ **Shipped** (with logging gap — fixed by 1b) | — | 200–300k |
| `track_1b_logging_fix.md` | 1b | Logging truth-telling (Sprint 1.5) | ✅ **Shipped** | Track 1 | 80–150k |
| `track_1c_wire_repomap.md` | 1c | Wire repo_map into prompt (Sprint 1.6) | ✅ **Shipped** | Track 1b | 80–150k |
| `track_1d_repo_map_stability.md` | 1d | Repo_map cache stability + task-aware relevance (Sprint 1.7) | ✅ **Shipped** | Track 1c | 120–200k |
| `track_2a_orphan_question.md` | 2a | Bug fix | ⬜ Not started | — | 100–200k |
| `track_2b_no_op_rationale.md` | 2b | Bug fix | ⬜ Not started | — (pairs with 2a) | 100–200k |
| `track_3a_outline_funnel.md` | 3 (code-side) | Architecture | ⬜ Not started (partially adopted in personas) | Track 1 | 250–400k |
| `track_3c_output_compression.md` | 3c | Context economy (RTK-inspired) | ⬜ Not started | Track 1 | 200–350k |
| `track_3d_agent_io_hygiene.md` | 3d | Hygiene + drift + cost (4 sub-deliverables; 3d.3 superseded by 3e) | ⬜ Not started | Track 1 | 280–450k |
| `track_3e_batch_tool_calls.md` | 3e | Batch tools: fs-edit-batch, fs-write-batch, code-outline-batch | ✅ **Shipped** | Track 1 | 200–350k |
| `track_3d4_manifest_render.md` | 3d.4 | Manifest auto-render: priority + when_to_use on every tool | ✅ **Shipped** | Track 3e | 200–350k |
| `track_3f_cross_reference_graph.md` | 3f | Cross-reference graph (tree-sitter extension; Python/JS/TS/C++) | 🟦 **Deferred** — gated on real-run signals | Track 1, 1c | 550–850k (3 phases) |
| `track_4_flow_dir_design.md` | 4 | Design spec (needs sign-off) | ⬜ Pending review | — | 100–150k |
| `track_6_peer_rework.md` | 6 | Feature | ⬜ Not started | — | 150–250k |

### Deferred (intentionally not specced as files)

| Track | Why deferred |
|---|---|
| 2c — Kanban task display | Needs UI reproduction first; can't spec until rendered behavior is observed |
| 3b — Doc-diff over `.flow/docs/` | Depends on Track 4 landing the doc directory |
| 5 — Doc Manager (memory_writer extension) | Depends on Track 4 |
| 7 — Subtask decomposition | Composes on Tracks 1 + 3 + 4 |
| 8 — Subtask context hot-swap | Composes on Track 7. When an agent finishes a subtask, stash that subtask's read context (file bodies, tool results) so it's not re-fed into every later LLM call. Picks back up only if a later subtask references it. Long-term context economy lever. |
| 9 — Protocol cleaner sidecar | Bigger architectural pass. A small, fast model translates the heavy model's prose output into protocol blocks (`[COMMIT]`, `[VERIFY]`, etc.) so the heavy model focuses on work, not formatting. Addresses the persistent `PROTOCOL_PARSE_FAIL` retries seen across runs. |
| 10 — Operator settings UI | Settings page exposing: per-role default model (small-model defaults for low-stakes roles), per-run wall-clock deadlines, per-agent max-iterations, max-protocol-retries, max-tool-calls. Plus visible read of the same values during a live run so users can see what's enforcing them. |
| Test-writer/implementer split | Explicitly deferred per Erik |

## Recommended order

```
Sprint 1.5 (gates measurement) — SHIPPED:
  Track 1b  ✅ logging truth-telling. tool_token_breakdown +
            direct_file_read_tokens now populate.

Sprint 1.6 (gates token efficiency) — SHIPPED:
  Track 1c  ✅ wire workspace.repo_map into effective_system_prompt as
            Layer 4.5. CLAUDE.md §28 compliance restored.

Sprint 1.7 (cache stability + task-aware relevance) — SHIPPED:
  Track 1d  ✅ repo_map dirty-flag rebuilds (one per agent transition,
            not per commit) + new task-aware relevance section in the
            prompt (aider-style: PageRank structure + task-keyword
            relevance). Closes the architectural gap from run_ff1e92d43c.

Sprint 1.8 (turn-count reduction) — SHIPPED:
  Track 3e  ✅ batch tool calls. fs-edit-batch / fs-write-batch /
            code-outline-batch collapse N sequential round-trips into 1.
            Manifest piggyback rule: fs-edit grant auto-includes
            fs-edit-batch. No project-config changes needed.

Sprint 1.9 (drift fix — adoption nudge for batch tools) — SHIPPED:
  Track 3d.4  ✅ manifest auto-render. Every BaseTool now declares its
            `priority` (catalog ordering) and `when_to_use` (one-line
            hint). Tool catalog renders sorted by priority with the hint
            inline. Batch variants sit at priority 100 with "PREFER..."
            hints; singular write tools at 105. Closes the adoption gap
            from run_543a09eb6a.

Sprint 2 (parallel-safe):
  Track 2a  ←── bug, highest user value
  Track 2b  ←── bug, pairs with 2a in verification

Sprint 3:
  Track 3a  ←── outline funnel; pairs naturally with 3d.4 drift fix
  Track 3c  ←── RTK-inspired tool output compression
  Track 3d  ←── board signal compression, batch tools, drift fix, cost column
                (3d.2 is superseded by Track 1b — strike from 3d's scope)
  Track 6   ←── independent, can slot in anywhere

Sprint 4:
  Track 4   ←── DESIGN review with Erik (8 sign-off questions in the spec)
  (then Track 5 doc-manager, Track 3b doc-diff, Track 7 subtask-decomp — write specs after 4 lands)

Later (deferred — gated on observed need from real runs):
  Track 3f  ←── Cross-reference graph. Tree-sitter extension that gives
            agents "find references / trace variable" across Python, JS/TS,
            and C++ without depending on LSP. Inspired by 2026 Codebase-
            Memory paper. Pick up only when real runs show repeated
            cross-file reference failures that repo_map + grep can't
            satisfy. 550–850k tokens across 3 phases.
```

## Rules for executors

1. **One plan = one PR-sized chunk.** Do not bundle two tracks in one branch.
2. **Commits per the plan's commit list.** Do not squash. CLAUDE.md top-priority rule: commit after every code change.
3. **Verify against the plan's acceptance criteria** before declaring done — run the listed tests AND do the manual end-to-end check.
4. **If a citation is stale** (line number off), re-locate by symbol name and proceed. Do not pause and re-plan unless the symbol itself is gone.
5. **Out-of-scope sections are load-bearing.** Don't gold-plate. The next sprint will pick up adjacent work.
6. **CLAUDE.md is the contract.** Memory-of-the-project. Reread §31 (tool naming with hyphens), §33 (inputs/ read-only), §13 (FileWorkspace only) before touching agent or filesystem code.
