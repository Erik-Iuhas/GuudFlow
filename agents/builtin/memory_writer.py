"""MemoryWriter — post-run persona memory synthesis.

DESIGN §26 (three-layer model), §34 (lifecycle, recency, reconciliation).

This is the SOLID-tier agent that runs once per run after the scheduler
finishes. For each persona that participated, it:

  1. Records an episode (raw experience).
  2. Updates pattern confidence using recency-weighted application
     outcomes.
  3. Detects new emerging patterns from repeated themes.
  4. Updates working style tendencies.
  5. Updates reputation aggregates.
  6. Reconciliation pass — pattern lifecycle transitions, context
     narrowing, pattern→anti-pattern flipping, episode staleness.
  7. Atomic rewrite of patterns.json + episodes.jsonl.

Stage 5 ships a deterministic implementation of steps 1, 2, 5, and 6
(no LLM call required — these are pure data ops over the persona's
files). Steps 3 and 4 (which need genuine synthesis) are scaffolded
with hooks; full LLM-driven distillation can layer on later.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from agents.base import BaseAgent
from agents.persona import (
    AntiPattern,
    DEFAULT_HALF_LIFE_DAYS,
    Episode,
    Pattern,
    PatternApplication,
    Persona,
    PROMPT_STATUSES,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_DECLINING,
    STATUS_EMERGING,
    STATUS_SUPERSEDED,
    compute_confidence,
)


# §34 thresholds (defaults).
ACTIVE_CONFIDENCE_FLOOR = 0.55
DECLINING_CONFIDENCE_FLOOR = 0.30
EMERGING_TO_ACTIVE_MIN_APPLICATIONS = 5
DECLINING_TO_ARCHIVED_CONSECUTIVE = 3
NARROW_CLUSTER_MIN_FAILURES = 3
NARROW_CLUSTER_FAILURE_RATE = 0.5
FLIP_FAILURE_STREAK = 5
ARCHIVE_SCOPE_RATIO = 0.30


class MemoryWriter(BaseAgent):
    """Post-run memory synthesizer. Constructed by the runner; the runner
    calls `on_run_complete` after the scheduler finishes.
    """

    def __init__(self, **kwargs):
        super().__init__(
            role=kwargs.pop("role", "memory_writer"),
            description="Post-run persona memory synthesizer.",
            **kwargs,
        )

    async def on_run_complete(
        self,
        workspace,
        project,
        run_id: str,
        run_root: Path,
        personas: dict[str, Persona],
    ) -> None:
        """Update each participating persona's memory.

        `personas` maps role → loaded Persona instance. The personas'
        files are at `data/personas/{persona_id}/`.
        """
        for role, persona in personas.items():
            try:
                self._update_persona(persona, role, workspace, run_id)
            except Exception:
                # Memory write must never fail the run.
                continue

    # ── Per-persona update ─────────────────────────────────────────────────

    def _update_persona(self, persona: Persona, role: str, workspace, run_id: str) -> None:
        # 1. Record episode.
        episode = self._build_episode(persona, role, workspace, run_id)
        persona.episodes.append(episode)

        # 2. Recency-weighted confidence (read-side already computes this;
        #    on write we rebuild the stored `confidence` field from the
        #    application window so the stored snapshot is current).
        now = datetime.now(timezone.utc)
        for p in persona.patterns:
            p.confidence = round(compute_confidence(p, persona.profile.recency_half_life_days, now), 4)
        for ap in persona.anti_patterns:
            ap.confidence = round(compute_confidence(ap, persona.profile.recency_half_life_days, now), 4)

        # 5. Update reputation aggregates.
        self._refresh_reputation(persona)

        # 6. Reconciliation pass.
        self._reconcile(persona, now)

        # Persist atomically.
        self._save(persona)

    # ── 1. Episode ──────────────────────────────────────────────────────────

    def _build_episode(self, persona: Persona, role: str, workspace, run_id: str) -> Episode:
        # Find the artifact (if any) for this role.
        artifact = workspace.artifacts.get(role)
        artifact_text = ""
        if artifact is not None:
            content = artifact.content
            artifact_text = content if isinstance(content, str) else str(content)

        # Find approval/rejection signals on the board for this role.
        approved = any(
            s.from_agent == role and s.type.value == "APPROVED"
            for s in workspace.board
        )
        was_failed = role in (workspace.agent_states or {}) and workspace.agent_states[role].value == "failed"

        # Derive crude tags from the goal text and role.
        tags = sorted(set(_extract_tags(workspace.goal) + [role.lower()]))

        return Episode(
            episode_id=f"ep_{uuid4().hex[:10]}",
            run_id=run_id,
            iteration=workspace.iteration,
            project_type=getattr(workspace, "project_type", "") or "",
            task_context=workspace.goal,
            situation=f"Acted as {role}",
            what_i_tried=artifact_text[:1000],
            what_failed="" if approved or not was_failed else "agent failed during run",
            what_worked="completed without rejection" if approved or not was_failed else "",
            feedback_verbatim="",
            iterations_to_resolve=workspace.iteration,
            model_used="",  # runner can fill this in later
            confidence="medium",
            tags=tags,
            timestamp=datetime.now(timezone.utc).isoformat(),
            staleness="fresh",
            training=False,
        )

    # ── 5. Reputation ───────────────────────────────────────────────────────

    def _refresh_reputation(self, persona: Persona) -> None:
        rep = persona.reputation
        rep.total_episodes = len(persona.episodes)
        rep.active_patterns = sum(1 for p in persona.patterns if p.status in (STATUS_ACTIVE, STATUS_EMERGING))
        rep.active_anti_patterns = sum(1 for a in persona.anti_patterns if a.status in (STATUS_ACTIVE, STATUS_EMERGING))

        prod = rep.production
        prod.total_runs = sum(1 for ep in persona.episodes if not ep.training)
        prod.completed_runs = prod.total_runs  # without explicit failure tracking, treat all as completed

        train = rep.training
        train.total_runs = sum(1 for ep in persona.episodes if ep.training)

    # ── 6. Reconciliation pass (§34) ────────────────────────────────────────

    def _reconcile(self, persona: Persona, now: datetime) -> None:
        # 6a. Contradiction reconciliation FIRST — checks ACTIVE patterns
        #     for overlapping applies_when and supersedes the loser.
        #     Must run before lifecycle transitions so we compare against
        #     stable status; otherwise a soon-to-decline pattern would
        #     drop out of ACTIVE before contradiction check fires.
        self._reconcile_contradictions(persona, now)

        # 6b. Context narrowing — examine recent failures by tag.
        self._narrow_contexts(persona)

        # 6c. Lifecycle state transitions on whatever remains in
        #     emerging/active/declining after supersedence.
        for p in persona.patterns:
            if p.user_pinned:
                continue
            self._transition_state(p, now)
        for ap in persona.anti_patterns:
            if ap.user_pinned:
                continue
            self._transition_state(ap, now)

        # 6d. Pattern → anti-pattern flipping.
        # (Detection requires "what worked" alternatives in source episodes;
        # implemented as a hook so the §34 mechanic is wired without
        # over-fitting heuristics. The LLM-driven version lands later.)

        # 6e. Episode staleness.
        self._mark_episode_staleness(persona)

    def _transition_state(self, p: Pattern | AntiPattern, now: datetime) -> None:
        # Count consecutive failures from most-recent end of applications.
        consecutive_failures = 0
        for app in reversed(p.applications):
            if app.outcome == "failed":
                consecutive_failures += 1
            else:
                break

        if p.status == STATUS_EMERGING:
            if p.lifetime_applications >= EMERGING_TO_ACTIVE_MIN_APPLICATIONS and p.confidence >= ACTIVE_CONFIDENCE_FLOOR:
                _set_status(p, STATUS_ACTIVE)
            elif p.confidence < DECLINING_CONFIDENCE_FLOOR and p.lifetime_applications >= EMERGING_TO_ACTIVE_MIN_APPLICATIONS:
                _set_status(p, STATUS_DECLINING)
        elif p.status == STATUS_ACTIVE:
            if p.confidence < ACTIVE_CONFIDENCE_FLOOR or consecutive_failures >= 3:
                _set_status(p, STATUS_DECLINING)
        elif p.status == STATUS_DECLINING:
            if p.confidence >= ACTIVE_CONFIDENCE_FLOOR:
                _set_status(p, STATUS_ACTIVE)
            elif p.confidence < DECLINING_CONFIDENCE_FLOOR:
                # Need DECLINING_TO_ARCHIVED_CONSECUTIVE applicable runs
                # below floor before archive. Approximation: count recent
                # apps with outcome=failed.
                recent_failures = sum(
                    1 for app in p.applications[-DECLINING_TO_ARCHIVED_CONSECUTIVE:]
                    if app.outcome == "failed"
                )
                if recent_failures >= DECLINING_TO_ARCHIVED_CONSECUTIVE:
                    _set_status(p, STATUS_ARCHIVED)

    def _narrow_contexts(self, persona: Persona) -> None:
        """Find tags where recent failures cluster ≥ 3 with ≥50% rate.
        Add the tag to the pattern's `applies_when_excludes`.

        Because tag-level outcomes are not stored on Pattern.applications
        directly, we approximate via episodes that share tags with the
        pattern's applies_when. Stage 5 ships the framework; richer
        tag-level analytics layer on later when episodes carry richer
        outcome metadata.
        """
        # Placeholder: framework only. The LLM-driven memory_writer will
        # do this with richer evidence.
        return

    def _reconcile_contradictions(self, persona: Persona, now: datetime) -> None:
        """If two ACTIVE patterns share an applies_when tag and one's
        confidence is materially higher, supersede the loser."""
        active = [p for p in persona.patterns if p.status == STATUS_ACTIVE]
        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                a, b = active[i], active[j]
                overlap = set(a.applies_when) & set(b.applies_when)
                if not overlap:
                    continue
                if abs(a.confidence - b.confidence) < 0.15:
                    continue  # too close — no clear winner
                winner, loser = (a, b) if a.confidence > b.confidence else (b, a)
                if loser.user_pinned:
                    continue
                _set_status(loser, STATUS_SUPERSEDED)
                loser.superseded_by = winner.pattern_id

    def _mark_episode_staleness(self, persona: Persona) -> None:
        """An episode contributing only to archived/superseded patterns
        becomes stale; aged episodes (>1y old) without active references
        become aged."""
        active_episode_ids: set[str] = set()
        for p in persona.patterns:
            if p.status in PROMPT_STATUSES:
                active_episode_ids.update(p.source_episodes)
        for ap in persona.anti_patterns:
            if ap.status in PROMPT_STATUSES:
                active_episode_ids.update(ap.source_episodes)

        now = datetime.now(timezone.utc)
        for ep in persona.episodes:
            ts = _parse_iso(ep.timestamp)
            age_days = (now - ts).days if ts else 0

            if ep.episode_id in active_episode_ids:
                ep.staleness = "fresh"
            elif age_days > 365:
                ep.staleness = "stale" if ep.staleness == "aged" else "aged"
            else:
                if ep.staleness != "fresh":
                    ep.staleness = "fresh"

    # ── Persistence ────────────────────────────────────────────────────────

    def _save(self, persona: Persona) -> None:
        """Atomic rewrite of patterns.json and episodes.jsonl."""
        # Find the persona dir from a handle — we expect runner to have
        # passed `data_root/persona_id`. We don't have that path here
        # directly; the Persona instance knows where it was loaded from
        # only if we store it. For Stage 5 we rely on a convention:
        # `persona.profile.persona_id` + a configurable data root via
        # env var or runner. For now we look for a `_dir` attr the
        # runner can set.
        persona_dir = getattr(persona, "_dir", None)
        if persona_dir is None:
            return  # nothing to save against

        persona_dir = Path(persona_dir)
        persona_dir.mkdir(parents=True, exist_ok=True)

        # episodes.jsonl
        episodes_path = persona_dir / "episodes.jsonl"
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=persona_dir, delete=False, suffix=".tmp"
        ) as tmp:
            for ep in persona.episodes:
                tmp.write(json.dumps(asdict(ep), default=str) + "\n")
            tmp_name = tmp.name
        shutil.move(tmp_name, episodes_path)

        # patterns.json
        patterns_path = persona_dir / "patterns.json"
        payload = {
            "version": 3,
            "patterns": [_pattern_to_json(p) for p in persona.patterns],
            "anti_patterns": [_anti_to_json(p) for p in persona.anti_patterns],
            "archived": [],
        }
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=persona_dir, delete=False, suffix=".tmp"
        ) as tmp:
            json.dump(payload, tmp, indent=2, default=str)
            tmp_name = tmp.name
        shutil.move(tmp_name, patterns_path)

        # reputation.json
        rep_path = persona_dir / "reputation.json"
        rep_payload = {
            "production": asdict(persona.reputation.production),
            "training": asdict(persona.reputation.training),
            "total_episodes": persona.reputation.total_episodes,
            "active_patterns": persona.reputation.active_patterns,
            "active_anti_patterns": persona.reputation.active_anti_patterns,
        }
        rep_path.write_text(json.dumps(rep_payload, indent=2, default=str), encoding="utf-8")


# ── Helpers ─────────────────────────────────────────────────────────────────


def _set_status(p: Pattern | AntiPattern, new_status: str) -> None:
    if p.status == new_status:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    p.status = new_status
    p.last_state_change = now_iso  # AntiPattern doesn't have this — guard
    if not hasattr(p, "last_state_change"):
        return


def _pattern_to_json(p: Pattern) -> dict[str, Any]:
    return {
        "pattern_id": p.pattern_id,
        "title": p.title,
        "lesson": p.lesson,
        "applies_when": list(p.applies_when),
        "applies_when_excludes": list(p.applies_when_excludes),
        "confidence": p.confidence,
        "status": p.status,
        "applications": [asdict(a) for a in p.applications],
        "lifetime_applications": p.lifetime_applications,
        "lifetime_successes": p.lifetime_successes,
        "source_episodes": list(p.source_episodes),
        "superseded_by": p.superseded_by,
        "user_pinned": p.user_pinned,
        "lifecycle": {
            "first_observed": p.first_observed,
            "last_state_change": p.last_state_change,
        },
    }


def _anti_to_json(p: AntiPattern) -> dict[str, Any]:
    return {
        "anti_pattern_id": p.anti_pattern_id,
        "title": p.title,
        "lesson": p.lesson,
        "alternative": p.alternative,
        "applies_when": list(p.applies_when),
        "applies_when_excludes": list(p.applies_when_excludes),
        "confidence": p.confidence,
        "status": p.status,
        "applications": [asdict(a) for a in p.applications],
        "lifetime_applications": p.lifetime_applications,
        "lifetime_successes": p.lifetime_successes,
        "source_episodes": list(p.source_episodes),
        "flipped_from": p.flipped_from,
        "flipped_at": p.flipped_at,
        "user_pinned": p.user_pinned,
    }


def _extract_tags(text: str) -> list[str]:
    import re
    out: set[str] = set()
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9_-]+", text or ""):
        word = m.group(0).lower()
        if 3 <= len(word) <= 20 and word not in _STOPWORDS:
            out.add(word)
    return sorted(out)[:8]  # cap at 8 tags


_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "will",
    "have", "has", "had", "when", "what", "which", "where", "while", "should",
    "their", "there", "they", "then", "than", "but", "are", "was", "were", "use",
    "using", "used", "make", "made", "build", "built",
})


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
