# Track 4.2 — Per-Project `pr_mode` (manual stops at story branch; auto merges to main)

**Status:** Ready for implementation. Sonnet-executable.
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 200–350k. Stop and reassess if over 450k.
**Sprint slot:** Sprint 4 (immediately after Track 4 storage-mode toggle, which shipped in commits `5c7dc9b` … `824fde5`).
**Depends on:** Track 4 (storage mode + Settings drawer). Doesn't strictly require it — could ship standalone — but they pair naturally because in-directory mode + manual PR mode is the canonical "real repo" workflow.

---

## Why

Today, when a multi-agent run completes, the runner **auto-merges the leaf agent's branch into `main`** ([core/runner.py:2932](core/runner.py:2932)). For sandbox/throwaway projects that's fine. For real repos pointed at the user's actual codebase (the in-directory mode use case from Track 4), it's wrong — the user wants a PR to review, not a fait-accompli merge.

Per-project toggle:

- **`auto`** (today's behavior): runner merges the leaf branch into `main` directly. Sandbox-friendly.
- **`manual`** (default for new projects): runner merges agent branches into a **story/integration branch** (`flow/story-{slug}` if a story is open, else `flow/run-{run_id_short}`) and **stops there**. Completion summary surfaces a paste-ready `gh pr create --base main --head <branch> --title <suggestion>` command.

The user does the PR themselves from that branch — never auto-merge to main on a real repo.

---

## Scope

**In scope:**
- New `pr_mode: Literal["manual", "auto"] = "manual"` field on `ProjectConfig`.
- Runner branch logic: respect `pr_mode` at the two merge sites (`_land_leaf_on_main` at [core/runner.py:2875](core/runner.py:2875), and the reviewer merge at [core/runner.py:1796](core/runner.py:1796)).
- Integration branch naming: `flow/story-{slug}` when a story is open, else `flow/run-{run_id_short}`.
- Completion summary (artifact + new signal) includes the integration branch name and a paste-ready `gh pr create` command in `manual` mode.
- Studio: per-project setting in the project edit form (radio or dropdown with one-line explanations).
- Studio: Run completion view shows the integration branch + copy-to-clipboard button for the `gh pr create` command in `manual` mode.
- Tests for: field default, `auto` keeps today's behavior byte-identically, `manual` stops at integration branch, `manual` posts the PR command in the artifact.

**Out of scope:**
- Auto-PR via `gh` CLI invocation. Surface the command, let the user run it. (Per Erik's earlier call: per-project toggle, manual stops at branch, auto merges to main — no auto-PR third option.)
- Conflict detection on the integration branch beyond what `git.merge` already does.
- Multi-PR workflows (e.g. one PR per agent, stacked PRs). v1 is one integration branch with all agent work.
- Reviewer agent integration with the PR — the reviewer's VERIFY runs the same way regardless of `pr_mode`. Just where the merge lands changes.

---

## Files to modify

| File | Change |
|---|---|
| [core/workspace.py](core/workspace.py) `ProjectConfig` | Add `pr_mode: Literal["manual", "auto"] = "manual"` field. |
| [core/runner.py](core/runner.py) `_land_leaf_on_main` (line 2875) | Branch on `self.project.pr_mode`. `auto` keeps today's path. `manual` computes integration branch name, merges leaf into it (not main), posts a `PR_READY` signal with the branch + suggested gh command, stops. |
| [core/runner.py](core/runner.py) reviewer-merge site (line 1796) | Same branching: `auto` merges to main; `manual` merges into the integration branch. |
| [core/signals.py](core/signals.py) | New `SignalType.PR_READY` (or reuse a STATUS-shaped signal — call out in implementation). Carries `branch`, `gh_command`, `head_sha`, `base_branch`. |
| [core/runner.py](core/runner.py) completion summary | When `pr_mode=manual`, include integration branch + gh command in the run's `summary.json` / completion record. |
| [api/storage.py](api/storage.py) `ProjectStore` | No change — just stores/reads the new field as part of project config. |
| `studio/src/views/Propose.tsx` (or wherever project edit form lives) | Add `pr_mode` selector. |
| `studio/src/views/Run.tsx` (or run-completion component) | When `pr_mode=manual` and run completed, show integration branch + copy-button for gh command. Read from `PR_READY` signal or completion record. |
| Tests: `tests/core/test_runner_pr_mode.py` (new) | Field default, auto path, manual path, signal emission, integration branch naming. |

---

## Implementation steps

### Step 1 — `pr_mode` field
File: [core/workspace.py](core/workspace.py)

```python
@dataclass
class ProjectConfig:
    ...
    pr_mode: Literal["manual", "auto"] = "manual"
```

`manual` is the default: real-repo safety. Existing projects without the field deserialize as `manual` automatically (dataclass default).

### Step 2 — Integration branch naming
Files: [core/runner.py](core/runner.py), helper in same file.

```python
def _integration_branch_name(self) -> str:
    """Name of the branch leaf-work gets merged into when pr_mode=manual.

    Prefers the active story slug if one is open; otherwise uses the run_id.
    Returns a fully-qualified ref like `flow/story-auth-redesign` or
    `flow/run-543a09eb6a`.
    """
    # Check workspace.active_story for any role; pick the first non-None.
    for role, story_id in (self.workspace.active_story or {}).items():
        story = self.workspace.stories.get(story_id) if self.workspace.stories else None
        if story and not getattr(story, "closed", False):
            slug = story_id.lower().replace(" ", "-").replace("_", "-")
            return f"flow/story-{slug}"
    return f"flow/run-{self.run_id[:10]}"
```

### Step 3 — Branch the merge in `_land_leaf_on_main`
File: [core/runner.py:2875](core/runner.py:2875)

After the leaf-branch detection (existing code at lines 2884–2917), but before the `is_ancestor` check, branch on `pr_mode`:

```python
target = "main" if self.project.pr_mode == "auto" else self._integration_branch_name()

# Skip-if-already-contained check now uses `target` instead of "main".
try:
    repo = Repo(self.git.working_dir)
    target_ref = repo.heads[target] if target in [h.name for h in repo.heads] else None
    if target_ref is not None:
        leaf_commit = repo.commit(leaf_branch)
        if repo.is_ancestor(leaf_commit, target_ref.commit) or target_ref.commit == leaf_commit:
            return
except Exception:
    pass

async with self._git_lock:
    # Create the integration branch if it doesn't exist (manual mode).
    if self.project.pr_mode == "manual" and target != "main":
        if target not in [h.name for h in repo.heads]:
            repo.create_head(target, "main")
    ok = self.git.merge(leaf_branch, into=target)
    ...
    if self.project.pr_mode == "manual" and ok:
        await self._emit_pr_ready_signal(target, leaf_branch)
```

### Step 4 — `PR_READY` signal
File: [core/signals.py](core/signals.py)

```python
class SignalType(str, Enum):
    ...
    PR_READY = "PR_READY"
```

Detail JSON shape:
```json
{
  "integration_branch": "flow/story-auth-redesign",
  "base_branch": "main",
  "head_sha": "abc123",
  "gh_command": "gh pr create --base main --head flow/story-auth-redesign --title \"...\"",
  "title_suggestion": "..."
}
```

Title suggestion: pull from project.goal (truncated to ~60 chars).

### Step 5 — Same branching at reviewer-merge site
File: [core/runner.py:1796](core/runner.py:1796)

```python
target = "main" if self.project.pr_mode == "auto" else self._integration_branch_name()
ok = self.git.merge(reviewer_branch, into=target)
```

Use the same helper. Reviewer-related merges respect the same toggle.

### Step 6 — Completion summary surfaces the gh command
File: [core/runner.py](core/runner.py) — wherever the run's completion artifact / summary.json is written.

When `pr_mode == "manual"`:
- Append a `pr_command` field to summary.json
- Append the same to the completion_record.md as a final section

This way the user sees it in the Studio run view and on disk.

### Step 7 — Studio: project edit form

File: `studio/src/views/Propose.tsx` (or the project-edit form file).

Add a section:

```jsx
<fieldset>
  <legend>PR mode (when run completes)</legend>
  <label>
    <input type="radio" name="pr_mode" value="manual" checked={prMode === "manual"} onChange={...} />
    Manual — stop at an integration branch; you open the PR yourself (recommended for real repos)
  </label>
  <label>
    <input type="radio" name="pr_mode" value="auto" checked={prMode === "auto"} onChange={...} />
    Auto — merge into main directly (sandbox / throwaway projects only)
  </label>
</fieldset>
```

### Step 8 — Studio: run-completion view

When the run is in `manual` mode and finished, show:

```
🌿 Branch ready: flow/story-auth-redesign
   PR command: gh pr create --base main --head flow/story-... [Copy]
```

Read from `PR_READY` signal on the board OR from `summary.json`.

### Step 9 — Tests

`tests/core/test_runner_pr_mode.py`:

1. `test_pr_mode_default_is_manual` — newly-created `ProjectConfig` has `pr_mode == "manual"`.
2. `test_pr_mode_auto_merges_into_main` — set `pr_mode="auto"`, run a 1-agent flow, assert leaf branch was merged into main (today's behavior preserved).
3. `test_pr_mode_manual_merges_into_integration_branch` — set `pr_mode="manual"`, run, assert merge target is `flow/run-<id>` and main is unchanged.
4. `test_pr_mode_manual_with_story_uses_story_slug` — open a story, run, assert integration branch is `flow/story-<slug>`.
5. `test_pr_mode_manual_emits_pr_ready_signal` — assert `PR_READY` signal posted with correct branch + gh command.
6. `test_pr_mode_manual_summary_includes_pr_command` — assert summary.json carries `pr_command` field.

---

## Acceptance criteria

1. `pytest tests/core/test_runner_pr_mode.py` — green.
2. `pytest tests/` — full suite green; existing tests that asserted main was modified after a run continue to pass when those projects are explicitly set to `pr_mode=auto` (audit needed).
3. Manual: create a new project (default `pr_mode=manual`), run a 1-agent flow against an in-directory mode storage. Confirm `main` is NOT touched, `flow/run-<id>` exists, and the run's completion view shows the gh command.
4. Manual: existing project with `pr_mode=auto` runs the same as today.
5. Studio shows the PR mode setting in the project edit form. Toggling it persists.

---

## Risks / things to watch

- **Existing test fixtures expecting auto-merge to main.** Some integration tests may construct a `ProjectConfig` without specifying `pr_mode` — they'll now default to `manual` and break. Audit needed: either pass `pr_mode="auto"` explicitly in those fixtures (preserves test intent — they're testing the old auto-merge behavior) or update the assertions.
- **Integration branch already exists across runs.** If a user runs the project twice in manual mode, `flow/run-<id>` will be different per run (good). But `flow/story-<slug>` is shared across runs in the same story — second run will merge on top of the existing branch. That's actually the right behavior (story branch accumulates across runs) but document it.
- **Reviewer-merge site at line 1796.** The context around that merge is reviewer-driven; double-check that branching to integration in manual mode doesn't break the reviewer's view of "is this work landed?" The reviewer should still be able to verify; just the landing target shifts.
- **gh CLI not installed on user machine.** The command we surface is text — no validation. If they don't have `gh`, the command fails when they paste it. Acceptable — just a copy-paste prompt, not an automation.
- **Default flip from `auto` to `manual`.** Existing projects on disk get `manual` after upgrade because the dataclass default is `manual`. This means any user with existing projects will see behavior change after this lands. Document in changelog. If concerning, default to `auto` for backward-compat and require opt-in to manual — but that defeats the safety story. Recommend default `manual`, accept the change.

---

## Commit plan

```
feat(workspace): pr_mode field on ProjectConfig (default 'manual')
feat(signals): PR_READY signal type
feat(runner): _integration_branch_name helper (story slug or run id)
feat(runner): pr_mode branches the leaf-merge — manual lands on integration branch
feat(runner): pr_mode branches the reviewer-merge site
feat(runner): manual completion surfaces gh pr create command in summary + signal
feat(studio): project edit form — pr_mode radio with explanations
feat(studio): run-completion view shows integration branch + copy gh command
test(runner): pr_mode default, auto preserves behavior, manual stops at integration
test(integration): end-to-end manual mode emits PR_READY + summary command
```

---

## After this lands

The two-axis matrix becomes:

| Storage mode | PR mode | Use case |
|---|---|---|
| global | auto | Today's default — sandbox, internal experiments |
| global | manual | Sandbox project but user wants explicit PR control |
| in-directory | auto | Real repo but user genuinely wants auto-merge (rare) |
| in-directory | manual | **The "AgentFlow on a real repo" sweet spot** |

Track 4 + Track 4.2 together deliver that sweet-spot workflow: `guudflow` opens at a real repo's directory, agents work on branches, runner stops at the integration branch, user reviews and PRs through their normal GitHub workflow. No auto-merge anxiety.
