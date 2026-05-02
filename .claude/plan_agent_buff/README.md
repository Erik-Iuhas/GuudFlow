# plan_agent_buff — Execution Queue

Detailed plans for the GuudFlow multi-track roadmap. Parent strategy doc: `~/.claude/plans/i-think-right-now-linked-rabin.md`.

**Sister folder:** `.claude/future_buff/` — strategic items not on the immediate execution queue (cost ceiling, runner refactor, production sandbox, functional verification gate, recalibration notes). Pull from there when active queue is clear or when one becomes blocking.

Each file is **Sonnet-executable**: self-contained, code-cited, with acceptance criteria and a commit plan. Treat the parent roadmap as "why," and these as "how."

**Progress: 14 / 17 tracks queued.** ✅ Track 1, ✅ Track 1b, ✅ Track 1c, ✅ Track 1d, ✅ Track 2a, ✅ Track 2b, ✅ Track 3a, ✅ Track 3c, ✅ Track 3d.2 *(partial → completed dd7985e)*, ✅ Track 3d.4, ✅ Track 3e, ✅ Track 4, ✅ Track 4.2, ✅ Track 4.3, ✅ Track 4.4.

## Files

| File | Track | Type | Status | Depends on | Token est. |
|---|---|---|---|---|---|
| `track_1_iteration_metrics.md` | 1 | Foundation | ✅ **Shipped** (with logging gap — fixed by 1b) | — | 200–300k |
| `track_1b_logging_fix.md` | 1b | Logging truth-telling (Sprint 1.5) | ✅ **Shipped** | Track 1 | 80–150k |
| `track_1c_wire_repomap.md` | 1c | Wire repo_map into prompt (Sprint 1.6) | ✅ **Shipped** | Track 1b | 80–150k |
| `track_1d_repo_map_stability.md` | 1d | Repo_map cache stability + task-aware relevance (Sprint 1.7) | ✅ **Shipped** | Track 1c | 120–200k |
| `track_2a_orphan_question.md` | 2a | Bug fix | ✅ **Shipped** (verified 2026-05-02 — implementation predated this conversation) | — | 100–200k |
| `track_2b_no_op_rationale.md` | 2b | Bug fix | ✅ **Shipped** (verified 2026-05-02 — implementation predated this conversation) | — (pairs with 2a) | 100–200k |
| `track_3a_outline_funnel.md` | 3 (code-side) | Architecture | ✅ **Shipped** (most landed via 1c/3d.4; dead-diff-path cleanup in `0027069`) | Track 1 | 250–400k |
| `track_3c_output_compression.md` | 3c | Context economy (RTK-inspired) | ✅ **Shipped** | Track 1 | 200–350k |
| `track_3d_agent_io_hygiene.md` | 3d | Hygiene + drift + cost (4 sub-deliverables; 3d.3 superseded by 3e) | ⬜ Not started | Track 1 | 280–450k |
| `track_3e_batch_tool_calls.md` | 3e | Batch tools: fs-edit-batch, fs-write-batch, code-outline-batch | ✅ **Shipped** | Track 1 | 200–350k |
| `track_3d4_manifest_render.md` | 3d.4 | Manifest auto-render: priority + when_to_use on every tool | ✅ **Shipped** | Track 3e | 200–350k |
| `track_3f_cross_reference_graph.md` | 3f | Cross-reference graph (tree-sitter extension; Python/JS/TS/C++) | 🟦 **Deferred** — gated on real-run signals | Track 1, 1c | 550–850k (3 phases) |
| `track_4_flow_dir_design.md` | 4 | Design spec (history — superseded by `track_4_flow_dir_implementation.md`) | ✅ **Shipped** (storage-mode toggle + Settings drawer; commits 5c7dc9b → 824fde5) | — | 100–150k |
| `track_4_flow_dir_implementation.md` | 4 | Implementation plan (storage mode + Settings drawer) | ✅ **Shipped** | Track 1 | 600–900k |
| `track_4_2_pr_mode.md` | 4.2 | Per-project pr_mode (manual stops at story branch; auto merges to main) | ✅ **Shipped** (`d71ac2b` `ecd4b34`) | Track 4 | 200–350k |
| _Track 4.3 (no plan file)_ | 4.3 | In-place working tree via Source repo (Local path → In-place); inherits Storage root from Settings | ✅ **Shipped** (`94b7650` `2f5f74d` `79413dc`) | Track 4 | — |
| _Track 4.4 (no plan file)_ | 4.4 | Settings hot-reload (no uvicorn restart) + gitignore-aware fs-list/repo_map + Files/Git tabs respect in-place working dir | ✅ **Shipped** (`876cbfc` `b24b142` `2077441` `25266cf`) | Track 4.3 | — |
| `track_6_peer_rework.md` | 6 | Feature | ⬜ Not started | — | 150–250k |
| `track_11_worker_cognition_view.md` | 11 | Worker cognition view (live "current state of thinking" tab) | 🟦 **Deferred** — gated on Track 7 (subtask decomposition) | Track 7, pairs with Track 8 | 200–350k |
| `master_subtasks_dispatch_t7_t8_t12.md` | 7 + 8 + 12 | **Master plan** spanning sub-LLM dispatch (T12), subtask decomposition (T7), and context hot-swap (T8). Read this first before the per-track buff files. | 🟨 **In progress** (T12 shipped, dormant) | Track 1, registry | 800k–1.2M total |
| `track_12_sub_llm_dispatch.md` | 12 | **Phase 1 detail** — `llm-dispatch` primitive + `summarise-subtask` / `protocol-cleanup` wrappers + per-use-case + per-agent model picker | ✅ **Shipped** as dormant primitive (`cb3422e`, `6905348`, `1148bf1`); consumers in T8 / T9 | Master | 250–400k |
| `track_7_subtask_decomposition.md` | 7 | **Phase 2 detail** — synthetic root + `decompose-task` tool + `assign-task` extension + active subtask layer | ✅ **Shipped** (`4e8cc09` `0ef8ed3` `fce079a` `89a3bff` `6050e34` `7af5fab`) | Master | ~300k actual |
| `track_8_subtask_context_hotswap.md` | 8 | **Phase 3 detail** — stash + summary marker + `recall-subtask` tool. Primary consumer of T12's `summarise-subtask`. | ✅ **Shipped** (`dfa8958`) | T7, T12 | ~200k actual |

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

Sprint 1.10 (provider-portable cost reduction) — SHIPPED:
  Track 3c  ✅ tool output compression (RTK-inspired). Every tool result
            flows through a structure-aware compressor at the
            BaseAgent.call_tool boundary before being returned to the
            agent. Four compressors registered: code-exec (pytest banner
            strip + dedup), git-log (trailer strip + history truncate),
            git-diff (index-line strip + oversize cap), git-changed-files
            (directory grouping). Provider-portable: helps Anthropic,
            OpenAI, Groq, local models equally because it reduces the
            actual bytes sent to the LLM, not vendor-specific cache
            tricks.

Sprint 2 (parallel-safe) — SHIPPED:
  Track 2a  ✅ orphan question respawn (was already implemented before
            this conversation — verified 2026-05-02 with 6 passing tests).
  Track 2b  ✅ no-op rationale (also already shipped — emit_no_op,
            runner enforcement, NO_OP_REASON protocol block; 3 tests).
  Track 3a  ✅ outline-first funnel (mostly delivered via Track 1c repo_map
            injection + Track 3d.4 manifest auto-render; dead iteration-2
            wholesale-diff path removed in commit 0027069).

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
