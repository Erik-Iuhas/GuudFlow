# Track 3f — Cross-Reference Graph (tree-sitter extension)

**Status:** Queued, **deferred**. Spec-only at this point. Pick up after several real runs validate that earlier tracks (3e, then 2a/2b/3c) deliver as expected.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 550–850k across phases (split below). Stop and reassess if total exceeds 1M.
**Sprint slot:** late — after the Sprint-2 bug fixes and at least one of {3c, 3d (slimmed)} ship.
**Depends on:** Track 1 (metrics), Track 1c (repo_map injection — same tree-sitter integration extends). No hard dep on 3a or 3e.

---

## Why this track exists (and why it's deferred)

**The architectural question Erik raised:** can we give agents a "find references / trace variable" capability across languages without depending on LSP servers being installed on the user's machine?

**2026 SOTA research validates the answer is yes** — specifically the *Codebase-Memory* paper (arXiv 2603.27277) demonstrates that tree-sitter + per-language semantic resolution passes can hit ~80% accuracy on cross-file reference resolution, including C++ with dedicated type-resolution passes. Their lightweight model (single binary, zero runtime deps, SQLite persistence) maps directly onto what we already have (Python + tree-sitter + stdlib SQLite).

**Why deferred:** this is meaningful work (550–850k tokens). Before committing to it we need:
1. To verify Track 3e's batch-tool wins materialise in real runs.
2. To see whether agents actually need cross-file reference data, or whether repo_map + outline + grep is already sufficient. Tracks 3a (already shipped via 1c's repo_map injection) and Track 3e may have raised the floor enough that this is gold-plating.

If after 3-5 production runs we see agents repeatedly failing on questions like "where is this function called from?" or "what calls would break if I change this signature?", queue this track. Otherwise leave it deferred.

---

## Scope

**Three phases, each independently shippable:**

- **Phase 1 — Python + JS/TS reference graph.** Extend `core/repo_map.py` with the *6-strategy cascade* from Codebase-Memory: import maps, same-module, unique-name, fuzzy match, type-aware (where we can infer), final fuzzy fallback. Per-reference confidence score. Persisted to SQLite.
- **Phase 2 — C++ type-resolution passes.** Per-file TypeRegistry built from declarations + auto-generated standard-library stubs. Resolves method receivers, pointer indirection, template-dependent calls.
- **Phase 3 — Incremental updates.** Content-hash-based change detection so commits don't trigger full rebuilds. Hooks into the existing `_repo_map_dirty` mechanism from Track 1d.

**Out of scope:**
- Languages beyond Python/JS/TS/C++ in v1. Adding more is mechanical once the framework exists.
- Type inference for Python (e.g. running `pyright` or building a real type system). The TypeRegistry approach for C++ doesn't generalise to Python's dynamic typing — accept the ~50% method-resolution accuracy on Python and let agents fall back to grep.
- Shipping a separate binary or external service. Pure-Python in-process, same as repo_map.
- Stack-graphs library integration (Rust crate). The 6-strategy cascade gets us 80%; Stack Graphs gets ~90% but adds Rust dependency. Defer that compromise unless 80% proves insufficient in practice.
- An MCP server interface. Future track if needed; for now a regular tool is enough.

---

## What the agent-facing tool looks like

New tool: `code-references` (or `code-trace` — name negotiable).

**Input:**
```python
{"symbol": "User",       "from_file": "src/auth.py"}     # find all references to User
{"path": "src/auth.py", "line": 42, "column": 8}          # find all references to whatever is at this position
```

**Output:**
```python
{
  "ok": True,
  "symbol": "User",
  "definition": {"path": "src/auth.py", "line": 12, "column": 7, "kind": "class"},
  "references": [
    {"path": "src/api/users.py",   "line": 8,  "column": 16, "kind": "import",      "confidence": 1.0},
    {"path": "src/api/users.py",   "line": 34, "column": 24, "kind": "instantiate", "confidence": 1.0},
    {"path": "tests/test_auth.py", "line": 15, "column": 30, "kind": "use",         "confidence": 0.85},
    ...
  ],
  "low_confidence_count": 2,
  "total": 14,
}
```

**Confidence:** any reference with confidence < 0.8 marked explicitly. Agents can verify low-confidence hits via grep when it matters.

---

## Files to create / modify (high level)

| File | Phase | Purpose |
|---|---|---|
| `core/refgraph/__init__.py` | 1 | Module entry point. |
| `core/refgraph/cascade.py` | 1 | 6-strategy resolution cascade with confidence scoring. |
| `core/refgraph/symbol_table.py` | 1 | Per-module declaration index built from tree-sitter. |
| `core/refgraph/imports.py` | 1 | Import resolution per language (Python, JS/TS). |
| `core/refgraph/persistence.py` | 1 | SQLite schema: symbols, references, files (mtime + hash). |
| `core/refgraph/cpp_types.py` | 2 | TypeRegistry + stdlib stub generation for C++. |
| `core/refgraph/incremental.py` | 3 | Hash-based change detection; rebuild only changed-file slices. |
| `agents/tools/code_references.py` | 1 | The agent-facing tool. |
| `tests/core/test_refgraph_cascade.py` | 1 | Each strategy isolated + confidence math. |
| `tests/core/test_refgraph_python.py` | 1 | Python fixtures with known cross-references. |
| `tests/core/test_refgraph_jsts.py` | 1 | JS/TS fixtures including ESM + CommonJS. |
| `tests/core/test_refgraph_cpp.py` | 2 | C++ fixtures with templates, virtual methods, stdlib usage. |
| `tests/core/test_refgraph_incremental.py` | 3 | Commit → rebuild only changed slices. |
| `tests/agents/tools/test_code_references.py` | 1 | Tool input/output shapes + low-confidence reporting. |

---

## Implementation phases (sketch only — full plans written when track is picked up)

### Phase 1 — Python + JS/TS reference graph (250–400k tokens)

1. Extend tree-sitter parsing in `repo_map.py` to extract not just symbols and imports, but also *call sites* and *attribute accesses* with their source locations.
2. Build a per-language symbol table: file → exported symbols + their kinds (class, function, variable, constant).
3. Implement import resolution per language:
   - Python: `from foo.bar import Baz` → walk `foo/bar.py` or `foo/bar/__init__.py`. Handle relative imports (`from .sibling import X`).
   - JS/TS: ESM (`import { X } from './foo'`) and CommonJS (`require('./foo').X`). Handle `package.json` mains, `tsconfig.json` paths.
4. Implement the 6-strategy cascade per Codebase-Memory:
   1. Direct import map lookup (confidence 1.0).
   2. Same-module symbol (confidence 0.95).
   3. Unique name across repo (confidence 0.85).
   4. Type-hinted match (confidence 0.75 — Python type hints, TS types).
   5. Receiver-shape match (confidence 0.6).
   6. Fuzzy name match (confidence 0.4 — last resort).
5. Persist to `data/runs/{run_id}/refgraph.sqlite` with file-mtime tracking. SQLite schema: `files(path, mtime, hash)`, `symbols(path, line, name, kind)`, `references(path, line, target_path, target_line, kind, confidence)`.
6. Tool entry: `code-references` reads from SQLite (which is built lazily on first call).

### Phase 2 — C++ type-resolution passes (200–300k tokens)

1. Add C++ to tree-sitter parsers list (`tree-sitter-cpp` already exists; extend `repo_map.py` to handle it).
2. After AST extraction, run a TypeRegistry pass:
   - Per-file: collect class/struct/enum/typedef declarations with their member function signatures.
   - Cross-file: merge using existing import resolution (`#include` directive parsing).
   - Auto-generate standard-library type stubs: pre-canned data for `std::vector`, `std::string`, `std::unique_ptr`, `std::shared_ptr`, etc. Hand-curated, ~50 stubs gets you most stdlib coverage.
3. Resolution strategy for C++ method calls:
   - Identify receiver expression's static type (declared variable, return type, or fail).
   - Look up class in TypeRegistry.
   - Find matching member by name + arity.
   - For virtual methods: resolve to all overrides (return all matches with confidence weighted by inheritance distance).
4. Template instantiation: best-effort. Track common cases (`std::vector<T>::push_back`); accept misses on heavy template metaprogramming.

### Phase 3 — Incremental updates (100–150k tokens)

1. Hook into `_rebuild_repo_map_if_dirty()` (Track 1d). When dirty:
   - Compute hashes of all source files.
   - Compare against stored hashes in SQLite.
   - For changed files: re-parse, re-extract, re-resolve their references *and* references *into* them from other files (computed from the prior reverse index).
2. Rebuild from scratch only when SQLite is missing or schema version changed.

---

## Borrowable from Codebase-Memory paper

These are the techniques worth lifting wholesale, with attribution in the code comments:

- **6-strategy cascade with confidence scoring** ([cascade.py](core/refgraph/cascade.py)). Acknowledge the paper.
- **C++ TypeRegistry pattern** with per-file scope merged at query time ([cpp_types.py](core/refgraph/cpp_types.py)).
- **Auto-generated stdlib stubs** to avoid mis-resolving `std::*` member calls.
- **SQLite single-file persistence** for minimal-deps state.

What we'd skip:
- Louvain community detection — overkill for our needs.
- C-binary distribution — we're Python.
- Their full MCP integration shape — we already have a tool dispatcher.

---

## Acceptance criteria (per phase, ship-when-met)

**Phase 1:**
- ≥80% reference resolution accuracy on a fixture-based Python repo (e.g. AgentFlow itself — pre-curated ground truth of 50 known references).
- ≥80% on a JS/TS fixture.
- `code-references` tool returns results with confidence scores; <1s latency on a 1000-file repo after initial build.
- Initial build of AgentFlow (~300 Python files) completes in <30s.
- Full test suite green; new tests cover each cascade strategy.

**Phase 2:**
- ≥70% reference resolution accuracy on a C++ fixture (lower target — C++ is genuinely harder).
- Standard-library member calls (`std::vector::push_back`, etc.) resolve correctly via stdlib stubs.
- Template-heavy code documented as known-low-confidence.

**Phase 3:**
- Incremental update of a 1-file change completes in <500ms (vs ~30s full rebuild).
- Hash-based skip of unchanged files verified in tests.

---

## Risks / things to watch

- **Accuracy regression vs LSP:** an LSP-backed solution would hit ~95% on the same queries. Our 80% target is a deliberate trade-off for portability. Document the gap so users with LSP available aren't surprised.
- **SQLite contention:** if multiple agents query simultaneously, SQLite read-locks should be fine but write-paths during incremental updates need attention. Use `wal` mode.
- **Stale graph on disk:** if a run is killed mid-build, `refgraph.sqlite` could be partial. Schema version check on read; rebuild on mismatch.
- **Phase 2 (C++) underdelivery:** if the hand-curated stdlib stubs prove insufficient and template support is too weak, fall back to documenting C++ as best-effort and recommend clangd via LSP for serious C++ work.
- **Maintenance cost:** language semantics drift (Python type-hint syntax evolves, TS module resolution gets new features, C++ standards). Each phase has ongoing maintenance. If maintenance is heavy, we may eventually want to switch to Stack Graphs as the upstream and contribute language specs there instead.

---

## Decision gates before starting

Don't begin Phase 1 until at least three of these are answered "yes":

1. Have we run ≥3 real projects since shipping Track 3e and 3c, and observed agents struggling with cross-file reference questions?
2. Did `tool_token_breakdown` show repeated `fs-grep` patterns trying to answer "where is X used?" that the relevance section in Track 1c didn't satisfy?
3. Have we explored whether the existing repo_map's symbols + grep is genuinely insufficient — vs. whether it's just persona-prompt drift (Track 3d.4 territory)?
4. Is C++ specifically a language users are running AgentFlow against? (If only Python/JS, defer Phase 2 indefinitely.)

If most answers are "no," this track stays deferred — we'd be solving a hypothetical problem at meaningful cost. If most are "yes," queue Phase 1 and revisit gates before Phase 2.

---

## Sources

- *Codebase-Memory: Tree-Sitter-Based Knowledge Graphs for LLM Code Exploration via MCP* — arXiv 2603.27277. Primary technical inspiration.
- *Stack Graphs: Name Resolution at Scale* (Creager, 2022) — arXiv 2211.01224. Formal grounding; we're not using the full machinery but the ideas inform the cascade design.
- [github/stack-graphs](https://github.com/github/stack-graphs) — reference Rust implementation if Phase 1's accuracy proves insufficient.
- [oraios/serena](https://github.com/oraios/serena) — counter-example showing the LSP-dependent path; validates that NOT going LSP is a deliberate trade.
- [kiro.dev code intelligence docs](https://kiro.dev/docs/cli/code-intelligence/) — production confirmation that pure tree-sitter caps at within-file navigation.
