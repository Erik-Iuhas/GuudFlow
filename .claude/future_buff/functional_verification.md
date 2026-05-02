# Functional Verification Gate — Automated "Does the Output Run?"

**Type:** Regression safety net.
**Token budget when picked up:** ~200–300k for the first reference project; subsequent reference projects cheap.
**Depends on:** Track 4 (`.flow/`-in-repo) makes this easier (per-run baseline ref makes "what changed" trivially queryable). Not a hard dep.
**When to elevate:** when a foundational change inadvertently degrades output quality and we don't catch it. (Goal of this track: catch it next time.)

## Why this is captured but deferred

I previously recommended this as Sprint Y based on the assumption that we couldn't trust the agents to produce working software. Manual inspection of `run_70dc78f9bb` proved that assumption wrong: the produced pacman game is professional-grade, well-architected, and meets spec.

So this track is **not urgent for current work**. The agents are producing real software. My eyes are the verification gate, and they've been working.

What it protects against: a future architectural change that *silently* hurts output quality. Token efficiency tests pass, the run completes, the metrics look great — but the produced game has a regression. Without an automated check, we'd only catch it on the next manual play-through.

## What

A reference project (or 2-3) that AgentFlow is run against on every meaningful infrastructure change. After the run, automated assertions:

1. **Imports clean.** `python -c "import src.main"` exits 0.
2. **Entry point runs.** `python -m src.main --headless` (or equivalent) exits 0 within N seconds.
3. **Tests pass.** If the project produces tests (it should — `code_reviewer` agent in the team), `pytest` in the workspace exits 0.
4. **Spec adherence.** A small per-project check: e.g. for the pacman, `grep -r "PowerPellet\|wall_break\|burst" src/` returns hits proving the requested mechanics exist in code.

If any assertion fails, the run is flagged as a quality regression and a CI alert fires (or in dev: a `[QUALITY_GATE_FAILED]` signal is posted to the board with details).

## Reference projects worth using

- **RougePack** (the pacman) — already proven to work. Excellent baseline.
- **A simple CLI tool** — different domain, no UI dependency. Tests entry-point + arg parsing + a couple of features.
- **A REST endpoint** — exercises the data layer, async, and HTTP server stack. Adds backend coverage.

Three reference projects covering UI / CLI / API gives broad signal at low maintenance cost.

## Files (when picked up)

- `tests/quality/__init__.py`
- `tests/quality/test_pacman_run.py` — runs the pacman flow against a frozen project config; runs the post-assertions.
- `tests/quality/fixtures/pacman/project.json` — canonical project definition.
- `tests/quality/conftest.py` — shared post-run assertion helpers.
- `scripts/run_quality_gate.sh` — CI entry point.

## What good looks like

After this lands:

- Every PR runs the quality gate against at least one reference project.
- A failing gate blocks merge.
- Cost per gate run is bounded (~$2–4 for pacman).
- We trust the system to produce working software because we *prove* it on every change, not just hope.

## Risks

- **Cost in CI.** Running real LLM-backed projects in CI costs money. Mitigate: only run on PRs that touch agent / runner / scheduler / tools code. Skip on doc-only PRs.
- **Flakiness.** LLMs are stochastic. The same project run twice can produce slightly different code. Mitigate: assertions check for *capabilities*, not exact code. "Does power_pellet exist as a symbol?" not "is line 42 of player.py exactly X?"
- **Reference-project staleness.** If we change the pacman goal, we update the assertions. Pinned to a goal-version.

## Out of scope

- Quality metrics beyond "does it run." E.g. code style, test coverage %, performance benchmarks. Future enhancement; v1 is just "doesn't break."
- Cross-model comparison (does Sonnet produce better output than Haiku on the same task). Useful research; not this track.

---

**Why deferred:** Currently unnecessary because manual quality checks succeed. Becomes valuable as insurance the moment we ship a track that *should* be quality-neutral but isn't.
