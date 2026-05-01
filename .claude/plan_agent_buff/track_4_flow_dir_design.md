# Track 4 — `.flow/`-in-Repo and Branch-Scoped Runs (Design Spec)

**Status:** DESIGN SPEC, not executable. Erik must sign off on the layout before any code lands.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 100–150k for the design pass. Implementation budget will be specced after sign-off.

This file does NOT include a commit plan or test list. Once the design is approved, a follow-up `track_4_flow_dir_implementation.md` will be written with the executable steps.

---

## The shift

Today: runs are ephemeral artifacts under `data/runs/{run_id}/`. The repo being worked on knows nothing about prior runs. Continuation runs reload state from `data/runs/` (via [core/run_loader.py:75](core/run_loader.py:75)) but the user's repo doesn't carry any record of what GuudFlow did.

Tomorrow: runs are commits on a project branch with a `.flow/` directory that ships with the repo. Persistent state lives next to the code it operated on.

This unblocks five things:
- Per-run diff baselines that aren't "branch vs main."
- Roadmap and review carryover between runs (without needing to read prior `data/runs/` JSON).
- Doc-graph (Track 5) has a home.
- Project rules per CLAUDE.md §36 have a home.
- A **History tab** in the Studio project view that lists prior runs with their initial request, outcome, and completion record — without needing the live run UI.

---

## Design questions Erik needs to answer

These are the structural decisions that drive implementation. Defaults are recommendations, not commitments.

### Q1 — Layout
Recommended:
```
.flow/
  config.json              # project_id, default branch, run profile preset, schema version
  baseline_ref.txt         # commit SHA at most-recent run start (single line)
  roadmap.md               # carries between runs; agent-readable + editable
  docs/                    # populated by Track 5; empty or absent until then
    modules.md
    decisions.md
    cross-links.md
  runs/
    {run_id}/
      summary.json         # structured digest for the History tab (see Q9)
      proposal.json        # the meta-agent's team proposal at run start
      completion_record.md # human-readable summary written at run end
      iteration_metrics.jsonl   # copy of Track 1's metrics
  persona_rules/           # CLAUDE.md §36
    _shared.md
    {persona_id}.md
.flowignore                # paths to skip when computing baseline diffs (build artifacts, screenshots)
```

Open: should `runs/{run_id}/` mirror everything in `data/runs/{run_id}/`, or be a curated subset? Recommendation: **curated subset**. Server-side `data/runs/` is canonical for forensics; `.flow/runs/` is what ships in the repo for users / future agents reading history. Three files only: `proposal.json`, `completion_record.md`, `iteration_metrics.jsonl`.

### Q2 — Baseline ref semantics
What does `baseline_ref.txt` point to?

Options:
- **A. HEAD at run start** — most defensible. Per-run diffs become "diff from HEAD-at-run-start to current." Survives the run going through multiple commits.
- **B. Last main-branch sync** — cleaner conceptually (always relative to main) but loses meaning if user works on long-lived feature branches.
- **C. Tag created at run start** (`flow/run-{n}-baseline`) — git-native, reviewable in `git log`. Slight overhead.

Recommendation: **A**, with the SHA pinned at run start and never rewritten until the next run starts. Update timestamp: just before the meta-agent runs.

### Q3 — Branch-scoping
Runs need a branch to work on. Options:
- **A. Pre-created**: user creates the branch, attaches a team to it. GuudFlow refuses to run on `main` directly.
- **B. Auto-created**: GuudFlow creates `flow/run-{n}` from current HEAD on attach.
- **C. User choice at run start**.

Recommendation: **C with default = B**. UI offers a branch selector; "Create new flow/run-N branch" is the default. Refuse to run on the configured `default_branch` (typically `main` or `master`) without an explicit override flag.

### Q4 — Continuation flow
A continuation run is "another run on the same project, building on the prior one." How is it invoked?

Recommendation:
- **From CLI / API**: `POST /projects/{id}/runs` with `continue_from: <prior_run_id>`.
- **From Studio**: a "Continue this work" button on a finished run.
- **Behavior**:
  - Read `prior_run.completion_record.md` and `roadmap.md`. Inject into meta-agent context as "prior run summary."
  - Read prior run's persona reputation deltas. Inject into teammate brief.
  - Run on the same branch by default; offer to branch off.
  - Update `baseline_ref.txt` to current HEAD at continuation start.
- **What does NOT carry over**: in-flight signals, board state, scheduler state. Only durable artifacts (completion record, roadmap, persona memory which is global already).

### Q5 — Attach-to-existing-repo
What happens when a user points GuudFlow at an existing git repo with no `.flow/`?

Recommendation:
- GuudFlow detects missing `.flow/`, offers a one-time setup wizard:
  - Inspect repo: detect language, framework, package manager.
  - Read top-level `README.md` if present (per CLAUDE.md §33 — `inputs/README.md` first).
  - Generate `.flow/config.json` with detected defaults + `schema_version`.
  - Create `.flow/roadmap.md` from a template.
  - Commit `.flow/` setup as `flow: initialize project structure`.
- User can decline and run "ephemeral mode" (current behavior — no `.flow/`, runs go only to `data/runs/`).
- The runner has two paths from day one: "with .flow" and "ephemeral." Don't force migration.

### Q6 — `.flowignore`
Like `.gitignore` but scopes what GuudFlow's baseline-diff and doc-graph computations consider. Default contents:
```
node_modules/
.venv/
__pycache__/
*.pyc
.flow/runs/        # don't include the runs dir in baseline diffs
build/
dist/
.next/
*.log
screenshots/
```

User can override per-project.

### Q7 — Multi-team-per-repo
What if a repo has multiple flow-managed projects (e.g., monorepo with `apps/web` and `apps/api`)?

Recommendation: **out of scope for v1**. One `.flow/` per repo root. Document that monorepo support is a future track.

### Q8 — `.flow/` migration
If schema changes between GuudFlow versions, how does `.flow/` migrate?

Recommendation:
- `config.json` carries `schema_version: int`.
- Runner refuses to run if `schema_version > supported`. Prompts user to upgrade GuudFlow.
- For `schema_version < supported`, a one-shot migrator runs and bumps the version. Migration code lives in `core/flow_dir_migrations/`.

---

## Architectural decisions (not negotiable, derived from existing rules)

These are not Q&A — they fall out of CLAUDE.md and DESIGN.md.

- **All `.flow/` I/O goes through FileWorkspace** (CLAUDE.md §13). New helper `core/flow_dir.py` wraps FileWorkspace calls, never touches the FS directly.
- **`.flow/persona_rules/` is read at Layer 3.7** of `effective_system_prompt()` (CLAUDE.md §36). ABOVE memory.
- **Project rules trump persona memory** on conflict (CLAUDE.md §36).
- **Baseline ref updates are commits** to `.flow/baseline_ref.txt`, not silent file writes. This makes `git log .flow/baseline_ref.txt` a complete history of run starts.
- **`.flow/` is git-tracked**; `.flowignore` is purely an GuudFlow convention.
- **Continuation persona memory** is global (`data/personas/...`) per CLAUDE.md §35 — it crosses runs but not via `.flow/`.

---

## Project History tab (Studio)

Every project view in the Studio gains a **History** tab next to the live Run view. It reads `.flow/runs/{run_id}/` directly — no live WebSocket, no scheduler state. Pure read-over-disk.

**`summary.json` schema** (written at run end, read by the History tab):
```json
{
  "run_id": "run_2026_04_30_abc123",
  "started_at": "2026-04-30T14:22:00Z",
  "ended_at": "2026-04-30T14:48:31Z",
  "elapsed_seconds": 1591,
  "initial_request": "<the user's original goal text — full, not truncated>",
  "outcome": "complete | failed | stopped | deadline_exceeded",
  "outcome_summary": "<one-paragraph human-readable result, written by memory_writer at run end>",
  "branch": "flow/run-3",
  "baseline_ref": "<sha at run start>",
  "head_ref": "<sha at run end>",
  "personas": [{"role": "coder", "persona_id": "...", "model": "claude-sonnet-4-6"}, ...],
  "agent_count": 4,
  "iteration_count": 11,
  "commit_count": 7,
  "rejection_count": 1,
  "escalation_count": 0,
  "total_tokens": {"input": 142_300, "output": 18_400, "cache_read": 88_100},
  "continued_from": "<prior run_id or null>"
}
```

**Tab layout:**

1. **Run list** (left rail or table) — newest first. Each row shows:
   - Started-at (relative + absolute on hover).
   - Branch name.
   - Initial request (truncated to ~80 chars; full text on hover or in detail view).
   - Outcome badge (color-coded: complete / failed / stopped).
   - Agent count, iteration count, total tokens at a glance.
   - "Continued from →" arrow if `continued_from` is set, linking to the prior run row.

2. **Run detail view** (when a row is selected) — read-only, no live updates:
   - **Header**: full initial request, outcome, elapsed, branch + baseline/head SHAs (clickable to open a diff in the user's git tool of choice — or just copy-to-clipboard).
   - **Outcome summary**: rendered markdown from `outcome_summary`.
   - **Completion record**: rendered markdown from `completion_record.md` (full, not truncated).
   - **Team**: persona roster from `proposal.json` — same component the live Run view uses for the team panel, just sourced from disk.
   - **Analytics**: embed Track 1's Run Analytics panel reading `iteration_metrics.jsonl`.
   - **Board replay** (optional, defer to v2): static render of the run's signal board if available; otherwise a "View on disk" pointer to `data/runs/{run_id}/board.jsonl`.

**Continuation lineage**: render `continued_from` as a thread — clicking through walks the chain backwards. A small badge on the project header shows the current chain depth ("Run 4 of a 4-run thread").

**No mutations from this tab.** Read-only. To start a new continuation, the user clicks "Continue this work" from the run detail view, which opens the standard new-run flow with `continue_from` pre-filled.

**Source of truth**: this tab reads from `.flow/runs/{run_id}/` only. If the user manually deletes a `summary.json`, that run vanishes from the History tab — by design (`.flow/` is git-tracked, so anything they delete they did intentionally). The full `data/runs/` server-side record remains for forensics.

---

## Files that will change once approved

Listed for sizing only. Implementation plan to follow.

| Area | Files |
|---|---|
| New module | `core/flow_dir.py` — schema, read/write helpers, validation, migrations |
| Runner integration | [core/runner.py](core/runner.py) — init path, baseline writing, continuation logic |
| Loader | [core/run_loader.py](core/run_loader.py) — read `.flow/runs/{prior_id}/completion_record.md` on continuation |
| Persona prompt | [agents/persona.py](agents/persona.py) (or wherever `effective_system_prompt()` lives) — Layer 3.7 wiring for `persona_rules/` |
| API | [api/routes/runs.py](api/routes/runs.py) — `continue_from` parameter, attach-to-existing-repo endpoint |
| Studio | New "Setup `.flow/`" wizard component, "Continue this work" button on finished runs, **new History tab** on the project view (run list + run detail, reads `.flow/runs/{run_id}/summary.json` and siblings) |
| Run-end hook | [core/runner.py](core/runner.py) or memory_writer — write `.flow/runs/{run_id}/summary.json` at run end with the schema above |
| Tests | `tests/core/test_flow_dir.py`, integration test for continuation |

---

## Verification (once implemented)

The implementation plan will spec these in detail. Listed here for design completeness.

1. Init a fresh project against an existing git repo. Confirm `.flow/` is created with all defaults populated, baseline ref pinned to current HEAD.
2. Run a 3-agent flow. Confirm `.flow/runs/{run_id}/` is created with the three subset files.
3. Continuation: run again with `continue_from`. Confirm meta-agent's prompt includes prior run's completion record verbatim. Confirm `baseline_ref.txt` updated to new HEAD.
4. Diff scoping: assert agent diffs return only this run's changes, not the entire branch's history. (This is what Track 3a's `git-diff` `from_ref` default uses — feed it `baseline_ref.txt` once Track 4 lands.)
5. `.flowignore` honored: artifacts under ignored paths don't show in baseline diff context.
6. Attach to dirty existing repo: setup wizard runs, user accepts, repo gets a `flow: initialize project structure` commit. No other files touched.
7. Schema migration: bump `schema_version` in a fixture, run, confirm migrator ran and version bumped.
8. History tab: after two runs (the second continued from the first), open the project's History tab. Confirm both rows render with correct initial requests, outcome badges, and agent counts. Click row 2 → detail view shows full completion record, embedded analytics, and a "Continued from" link that navigates to row 1.

---

## What this unblocks

Listed for prioritization context, not for this plan to deliver:

- **Track 5 (Doc Manager)** — `.flow/docs/` exists, doc-manager has a write target.
- **Track 3b (Doc-Diff)** — `.flow/docs/` is git-tracked, so per-run diffs are trivial.
- **Track 3a refinement** — `git-diff`'s default `from_ref` becomes `baseline_ref.txt` content instead of `HEAD~1`. Better per-run scoping.
- **Roadmap-driven flows** — `.flow/roadmap.md` becomes the long-running project plan agents read on every run.

---

## Sign-off needed

Before writing the implementation plan, Erik should answer or acknowledge:

- [ ] Q1 layout — accept recommendation or revise?
- [ ] Q2 baseline semantics — accept "HEAD at run start"?
- [ ] Q3 branch-scoping — accept default = auto-created `flow/run-N`?
- [ ] Q4 continuation invocation — `continue_from` parameter shape OK?
- [ ] Q5 attach wizard — opt-in (with ephemeral fallback) acceptable?
- [ ] Q6 `.flowignore` defaults — additions/removals?
- [ ] Q7 monorepo deferral — confirm out-of-scope for v1?
- [ ] Q8 migration mechanic — schema_version + migrator pattern OK?

Once signed off, write `track_4_flow_dir_implementation.md` and queue.
