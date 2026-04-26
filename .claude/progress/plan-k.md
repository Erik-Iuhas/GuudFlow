# Active plan — Phase K — Project context (continuous app building)

Status: planned. Estimated ~half a day across 3 chunks. Light-touch
extension to existing primitives — explicitly NOT a full Agile
framework rewrite.

## Decision context

Erik 2026-04-26: continuous app building is the desirable use case.
The "iterate on a project + team has understanding of goals" loop
is the goal. Most review parts already exist (reviewer
APPROVED/REJECTED, auto-tag review points G.5, USER_COMMENT,
approval gates C.0.5, continuation runs D-prime, persona memory).

Earlier discussion floated a full Agile framework (Sprint object +
Scrum Master archetype + Definition-of-Done formalism + planner +
backlog data model + sprint board UI). Erik pushed back: that's
overshooting. The actual gap is smaller — **a persistent place
where the project's bigger picture lives between runs, that the
team reads in.**

If a user later wants formal Agile ceremonies, they can encode them
in the roadmap.md themselves (markdown headings for milestones).
The platform doesn't need new data models for that.

## Goal

After K ships, a project has continuity. The team hired into run
3 of a project knows what runs 1 + 2 did and what's queued next,
because the roadmap.md is in their context window. The user
maintains the roadmap; the system reads it.

---

## K.0 — Roadmap document storage + API (~1 hr)

### What lands

- `data/projects/{id}/context/roadmap.md` — free-form markdown,
  user-owned. Created empty when a project is created.
- `GET /api/projects/{id}/roadmap` returns the file contents.
- `PUT /api/projects/{id}/roadmap` updates it. Bearer-auth gated.
- `studio/src/api.ts` — `projectsApi.getRoadmap(id)` +
  `projectsApi.updateRoadmap(id, content)`.

### Files

- `api/storage.py` — `ProjectStore.roadmap_path(project_id)` helper
- `api/routes/projects.py` — two new endpoints
- Tests: extend `tests/api/test_projects.py`

Commit: `feat(api): project roadmap storage + endpoints (Phase K.0)`

---

## K.1 — Runner reads roadmap into agent prompts (~1-2 hrs)

### What lands

The runner loads `roadmap.md` from the project directory at run
start and threads it into the agent prompt stack as a new layer
between project rules and persona memory. Layer name:
`project_context_layer`.

§20 prompt-stack ordering becomes:
1. Identity
2. Character
3. Stake framing
4. Project rules (existing per-persona overlay)
5. **Project context (new — `roadmap.md`)**
6. Memory recall (per-persona)
7. Base system prompt
8. CoT primer
9. Positive framing

### Implementation

- `core/runner.py` — read `data_root/projects/{id}/context/roadmap.md`
  at run start; cache on `self.project_context: str | None`
- `agents/persona.py` (or `agents/base.py`) — extend
  `effective_system_prompt()` to include the new layer when
  `project_context` is set
- Empty roadmap → layer is omitted (no noise).
- Tests: `tests/core/test_runner.py` extension verifying the
  prompt assembled by an agent includes the roadmap excerpt when
  the file exists, and doesn't when it's empty/missing.

Commit: `feat(runner): thread project roadmap into agent prompts (Phase K.1)`

---

## K.2 — Studio "Roadmap" tab on project page (~1-2 hrs)

### What lands

A new tab on the Studio view (per-project) that shows the roadmap
as a textarea editor. Save button calls `projectsApi.updateRoadmap`.

Empty-state copy:
> This project has no roadmap yet. Use this space to record the
> bigger picture — what we're building, what's done, what's next.
> Every run reads this; the team uses it as shared context.

### Files

- `studio/src/views/Studio.tsx` — add "Roadmap" tab next to existing
  tabs (Settings, Inputs, etc.)
- Or new `studio/src/views/Roadmap.tsx` if the Studio tab list is
  already crowded — embed it as a tab regardless

### Verify

- Open a project, switch to Roadmap tab, type, save, reload — content
  persists
- Run a project where roadmap is non-empty → confirm via the run's
  workspace prompts (or by inspecting the agent's effective prompt)
  that the roadmap is included

Commit: `feat(studio): roadmap tab on project page (Phase K.2)`

---

## What this does NOT do (intentionally)

- **No Sprint object.** A project either has a roadmap or doesn't.
  If the user wants sprints, they write `## Sprint 3` headings in
  the roadmap and the team reads them.
- **No Scrum Master archetype.** Standing facilitator personas across
  runs is interesting but adds complexity. The roadmap.md is the
  shared understanding; the existing tech_lead escalation pattern
  handles in-run coordination.
- **No formal Definition of Done per story.** Rubrics already exist
  (training-camp F.1.0). If a user wants story-level rubrics they
  can attach them via the existing scenario format. K doesn't
  formalize the link.
- **No backlog data model.** Markdown bullets in roadmap.md are the
  backlog. Re-ordering = edit the file. Status = annotate the line.
- **No auto-summarisation of runs into roadmap.** Tempting, but
  brittle — LLM might summarise wrong. User edits the roadmap
  manually after each run, informed by what landed (visible in the
  Run view's BoardFeed + git log).
- **No multi-project orchestration.** One roadmap per project. Cross-
  project planning is out of scope.

If Phase K's lightweight version lands and users keep asking for the
fuller Agile framework, the eight-chunk K-Heavy version stays in the
back pocket. Adding Sprint as a structured concept on top of an
existing project is a non-breaking extension.

## Verification at end of phase

```bash
python -m pytest                                  # full suite stays green
cd studio && npm run typecheck && npm run build  # frontend clean
```

Suite expected: ~890 passed (885 + ~5 new tests for the endpoints +
prompt-layer wiring).

### Acceptance flow

1. Create a project (existing path).
2. Open Studio → Roadmap tab. Type:
   ```
   ## Goal
   Build a small CLI for processing CSVs.

   ## What's done
   - Nothing yet.

   ## Next
   - Skeleton + arg parsing
   ```
3. Save, then run the project with goal "Add the skeleton + arg parsing."
4. Inspect the agent's effective prompt — roadmap excerpt is in it.
5. After the run, edit the roadmap to mark "skeleton + arg parsing"
   as done and add the next item. Run again. Team carries the
   context forward.

If steps 1-5 work cleanly without ceremony files, sprint objects, or
new archetypes — the phase achieved its goal.
