# plan_agent_buff — Execution Queue

Detailed plans for the GuudFlow multi-track roadmap. Parent strategy doc: `~/.claude/plans/i-think-right-now-linked-rabin.md`.

Each file is **Sonnet-executable**: self-contained, code-cited, with acceptance criteria and a commit plan. Treat the parent roadmap as "why," and these as "how."

**Progress: 1 / 9 tracks shipped.** ✅ Track 1.

## Files

| File | Track | Type | Status | Depends on | Token est. |
|---|---|---|---|---|---|
| `track_1_iteration_metrics.md` | 1 | Foundation | ✅ **Shipped** (with logging gap — fixed by 1b) | — | 200–300k |
| `track_1b_logging_fix.md` | 1b | Logging truth-telling (Sprint 1.5) | ⬜ Not started | Track 1 | 80–150k |
| `track_2a_orphan_question.md` | 2a | Bug fix | ⬜ Not started | — | 100–200k |
| `track_2b_no_op_rationale.md` | 2b | Bug fix | ⬜ Not started | — (pairs with 2a) | 100–200k |
| `track_3a_outline_funnel.md` | 3 (code-side) | Architecture | ⬜ Not started (partially adopted in personas) | Track 1 | 250–400k |
| `track_3c_output_compression.md` | 3c | Context economy (RTK-inspired) | ⬜ Not started | Track 1 | 200–350k |
| `track_3d_agent_io_hygiene.md` | 3d | Hygiene + drift + cost (5 sub-deliverables) | ⬜ Not started | Track 1 | 350–550k |
| `track_4_flow_dir_design.md` | 4 | Design spec (needs sign-off) | ⬜ Pending review | — | 100–150k |
| `track_6_peer_rework.md` | 6 | Feature | ⬜ Not started | — | 150–250k |

### Deferred (intentionally not specced as files)

| Track | Why deferred |
|---|---|
| 2c — Kanban task display | Needs UI reproduction first; can't spec until rendered behavior is observed |
| 3b — Doc-diff over `.flow/docs/` | Depends on Track 4 landing the doc directory |
| 5 — Doc Manager (memory_writer extension) | Depends on Track 4 |
| 7 — Subtask decomposition | Composes on Tracks 1 + 3 + 4 |
| Test-writer/implementer split | Explicitly deferred per Erik |

## Recommended order

```
Sprint 1.5 (gates everything else):
  Track 1b  ←── logging truth-telling. Two consecutive runs show context-token
                fields stuck at 0. Without this, every Sprint 2 track ships
                blind. Small (80–150k tokens) and standalone.

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
```

## Rules for executors

1. **One plan = one PR-sized chunk.** Do not bundle two tracks in one branch.
2. **Commits per the plan's commit list.** Do not squash. CLAUDE.md top-priority rule: commit after every code change.
3. **Verify against the plan's acceptance criteria** before declaring done — run the listed tests AND do the manual end-to-end check.
4. **If a citation is stale** (line number off), re-locate by symbol name and proceed. Do not pause and re-plan unless the symbol itself is gone.
5. **Out-of-scope sections are load-bearing.** Don't gold-plate. The next sprint will pick up adjacent work.
6. **CLAUDE.md is the contract.** Memory-of-the-project. Reread §31 (tool naming with hyphens), §33 (inputs/ read-only), §13 (FileWorkspace only) before touching agent or filesystem code.
