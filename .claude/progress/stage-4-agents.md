# Stage 4 — Agent foundation

**Budget:** 450-650k tokens
**Status:** done (2026-04-25)
**Exit criteria (CLAUDE.md):** BaseAgent instantiates with persona; recall() returns relevant episodes/patterns; protocol emit methods produce parseable output; effective_system_prompt() composes all 7 layers; tools run without errors in dev mode.

DESIGN sections: §3.4, §18, §20, §26, §30, §31, §32, §34, §36.

## Files

### Persona — three-layer memory + lifecycle
- [x] `agents/persona.py` ✅
  - [x] `PersonaProfile` (immutable identity), `Episode`, `Pattern`, `AntiPattern`, `WorkingStyle`, `Reputation` (production/training split per §35)
  - [x] §34 lifecycle fields: `status`, `applications` window, `applies_when_excludes`, `lifetime_*`, `superseded_by`, `user_pinned`, `lifecycle.first_observed/last_state_change`
  - [x] `compute_confidence()` — recency-weighted with 90d half-life default
  - [x] `Persona.load(persona_id, root)` and `Persona.empty()`
  - [x] `recall(task, limit)` filters by status (only emerging/active/declining surface), excludes via `applies_when_excludes`, scales episode score by staleness
  - [x] `MemoryRecall.render_for_prompt()` with caution markers for declining patterns
  - [x] tests: 24 (loader, lifecycle filtering, recency math, applies_when_excludes, render markers)

### Tool infrastructure
- [x] `agents/tools/base.py` ✅ — `BaseTool` ABC, `ToolError`
- [x] `core/tool_manifest.py` ✅
  - [x] `ToolDescriptor` (validates broker descriptors require scope)
  - [x] `ToolManifest` (find/names/iter/render — render groups by category)
  - [x] `ToolManifestBuilder` — intersection of declared ∩ RBAC ∩ project-registered
  - [x] `BASELINE_LOCAL_SCOPES` for read-side baseline
  - [x] tests: 20 (RoleRBAC.grants, manifest filtering, MCP verb scopes, baseline)
- [x] `core/credentials_broker.py` ✅
  - [x] `CredentialsBroker` with default-deny RBAC
  - [x] `register_handler` (specific / prefix resolution)
  - [x] `request(role, scope, operation, payload)` returns `CredentialResult`
  - [x] audit log persisted to disk; sensitive keys redacted in preview
  - [x] tests: 18 (RBAC enforcement, handler resolution, audit, redaction)

### Built-in tools
- [x] `agents/tools/fs.py` ✅ — read (read/list/exists/**read_range**/**grep**) + write (create/write/edit/delete/rename) with auto-claim/release; tests: 24 + 13 extras
- [x] `agents/tools/doc.py` ✅ — `doc:markdown` (heading extraction), `doc:html` (tag stripping); tests: 6. PDF/DOCX/XLSX deferred to Stage 5 (need optional deps)
- [x] `agents/tools/git.py` ✅ — `git:diff`, `git:log`, `git:read`, **`git:status`** wrapping GitWorkspace; tests: 5 + 4 extras
- [x] `agents/tools/web.py` ✅ — `web:search` and `web:fetch` with injectable handlers (stubs in dev); tests: 6
- [x] `agents/tools/code.py` ✅ — `code:exec` (dev-mode host runner with §29 warning) + **`code:outline`** (tree-sitter Python/JS skeleton); tests: 6 + 7 extras

### BaseAgent + Registry
- [x] `agents/base.py` ✅
  - [x] `BaseAgent` dataclass with persona/manifest/broker/context_manager handles
  - [x] `signal()` (auto-namespaces with persona name), `ask_human()`, `check_user_board()`, `request_visual_feedback()`, `request_package()`
  - [x] Protocol: `emit_commit/verify/delta/action/ask/answer`, `read_protocol()` (filters by type, marks seen)
  - [x] File coordination (§32): `claim_files()`, `release_files()`, `active_claims()`, `pre_write_check()` returning `ClaimAdvice`
  - [x] Tools (§31): `call_tool()` enforces manifest membership; routes local vs broker; raises `ToolNotAvailable` / `ToolDenied`; `invoke_mcp()` sugar
  - [x] Broker (§30): `request_credential()`
  - [x] Context (§28): `get_context()`, `search_codebase()`
  - [x] Memory (§26 / §34): `recall()`
  - [x] `effective_system_prompt()` — full layer order: identity → character → stake → teammate_brief (§23) → project_rules (§36) → memory (§26 / §34) → base prompt + tool catalog (§31) → CoT primer → positive framing
  - [x] tests: 19 (signals, namespacing, ask_human, protocol roundtrip, claims, tool dispatch, broker routing, layer ordering)
- [x] `agents/registry.py` ✅ — `_Registry` with register/get/has/all_role_types; module singleton; tests: 7

## Verification
- Full repo: **306/306 passing**
- Per file: persona 24, manifest 20, broker 18, fs 24, doc 6, git 5, web 6, code 6, base 19, registry 7, extras 29

## Post-Stage-4 additions (context savers + general purpose + hardening)

### Round 1: Context savers (4 read tools)
- `fs:read_range(path, start_line, end_line)` — partial file read (1-indexed inclusive, end clamped to file length)
- `fs:grep(query, regex?, path_glob?, max_matches?, context_lines?, case_sensitive?)` — text search with surrounding context, skips binary extensions
- `code:outline(path)` — tree-sitter outline (Python + JS/TS) returning imports / classes (with methods) / functions / constants, with line numbers
- `git:status` — staged / unstaged / untracked entries for current branch

### Round 2: General-purpose tools (9 tools)
- `text:diff(a, b | a_path, b_path)` — unified diff, raw strings or files
- `text:estimate_tokens(text | path)` — chars/4 approximation, matches context_manager
- `data:json(path, json_path?)` — JSON read with array-form OR dot-string path
- `data:csv(path, delimiter?, has_header?, max_rows?)` — CSV with column-aware row dicts
- `data:yaml(path)` — YAML via safe_load (no arbitrary object construction)
- `data:xml(path, max_depth?)` — XML via defusedxml (XXE-safe), capped depth
- `time:now()` — UTC ISO + epoch
- `time:diff(a, b)` — duration with days/hours/minutes/seconds breakdown
- `math:eval(expression)` — AST-allowlist arithmetic, no names/calls/attrs

### Round 3: Dev-mode `code:exec` hardening (DESIGN §29 extension)
- Mode enforcement (GUUDFLOW_MODE=production refuses host runner)
- Per-run quota (default 50 calls per CodeExec instance)
- Audit log to disk (timestamp / lang / code preview / exit / duration / error)
- Env scrubbing (curated allowlist + secret-name double-check)
- CWD pinning (configurable, runner pins to working/)
- POSIX rlimits (RLIMIT_CPU / RLIMIT_AS / RLIMIT_NPROC, Linux/macOS only)
- New `make_dev_host_runner(...)` factory in `agents/tools/code.py`

All read tools added to `BASELINE_LOCAL_SCOPES`. DESIGN §31 catalog tables updated for each.

Total post-Stage-4 tests added: 90 (29 context savers + 51 general tools + 13 hardening, with 2 POSIX-only skipped on Windows).

## Notes / decisions made during Stage 4
- `AntiPattern` shares `lifetime_applications`/`lifetime_successes` with `Pattern` so the single `compute_confidence` works for both.
- Anti-patterns also flow through `compute_confidence`'s recency weighting — they're confidence-aware too.
- `fs:edit` requires single-match by default; agent must opt in to `replace_all=True` with multiple occurrences. Prevents accidental whole-file rewrites.
- Tool tests use injectable handlers (`web:search`, `web:fetch`, `code:exec`) so production wiring can plug in real backends without changing tool contracts.
- `_dev_host_runner` in `code:exec` logs the §29 dev-mode warning on every call.
- Registry uses a module-level singleton (CLAUDE.md sanctions this for queue + registry).
