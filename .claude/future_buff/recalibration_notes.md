# Recalibration Notes — Token Efficiency ≠ Quality

**Date:** 2026-05-02
**Trigger:** Strategic discussion after `run_70dc78f9bb`. Erik pushed back on a recommendation that assumed output quality was unverified.

## The lesson

Every metric I'd been celebrating across Tracks 1–3d.4 measured **internal efficiency**:

- Token counts (input/output/cache)
- LLM call counts
- Wall-clock per agent
- Tool-call breakdown
- Cache hit ratios

None of those measure whether the produced software **actually works**.

I extrapolated from "we don't have an automated quality test" to "we don't know if quality is good." That extrapolation was wrong. Erik had been verifying quality manually all along — the produced pacman game is a real, playable, well-architected pygame project with 9 ADRs documenting design decisions. The system was meeting the highest expectations of it; I just wasn't looking at the output.

## Concrete evidence from `run_70dc78f9bb`

Files produced (sample):
- `src/main.py` — clean entry point, idiomatic.
- `src/game/wall_break.py` — `EffectBus` value-object pattern crossing the game/render boundary cleanly.
- `src/game/`, `src/renderer/`, `src/scenes/` — three-layer separation as requested.
- `docs/design/adr/001-009.md` — 9 Architecture Decision Records the architect agent wrote unprompted.
- `TYPE_CHECKING` imports used correctly to avoid circular references.

This is senior-engineer code. For $2/iteration on Sonnet.

## The rule going forward

**Before recommending any "we need to measure quality" track, look at the output first.**

The relevant question isn't "do we have automated quality verification?" — it's "is the output actually bad?" If quality is good (verified by human inspection of recent runs), automated verification is *insurance*, not *correction*. It belongs in `future_buff`, not the active sprint queue.

Token efficiency improvements remain valuable (they reduce cost and latency), but they are **independent of** quality. Don't conflate the two:

- Cost-reduction tracks → reduce $ per iteration.
- Quality-verification tracks → catch regressions in output.

These serve different goals. Both are valuable. Neither implies the other.

## What this changes about the sprint queue

- **Removed:** Sprint Y (functional verification gate) from active recommendation. Captured in `future_buff/functional_verification.md` for later.
- **Reframed:** The current cost-reduction work (3a, 3c, 3d remainder, 3e adoption follow-ups) is the right priority. Quality is good; making good quality cheaper is the goal.
- **Reinforced:** The architectural disciplines are working. RBAC, manifest, signal protocol, branch-per-agent, ADR habit — all visible in the deliverable.

## Quote worth remembering

From the discussion:

> "We're going to see more gains to reduce cost soon. That's the real goal here. High quality, low cost."

That's the framing. **High quality (already achieved), low cost (open work).** Don't drift into "what if quality slips" without evidence that it has.
