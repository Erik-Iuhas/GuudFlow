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
from core.llm import LLMClient
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

# Phase F.3.1 distillation thresholds.
DISTILL_RECENT_EPISODE_LIMIT = 50  # consider only the last N non-stale episodes
DISTILL_MIN_CLUSTER_SIZE = 3       # cluster must contain ≥3 episodes
DISTILL_MIN_SHARED_TAGS = 2        # cluster members share ≥2 tags
MAX_DISTILL_CALLS_PER_RUN = 3      # cap LLM calls per persona per run
EMERGING_SEED_CONFIDENCE = 0.6     # §34 — emerging patterns start at 0.6
MAX_FLIP_CALLS_PER_RUN = 3         # cap LLM calls for flip detection per run
ANTI_PATTERN_SEED_CONFIDENCE = 0.78  # §34 example — anti-pattern initial confidence
STYLE_DRIFT_ALPHA = 0.1            # per-run nudge weight for style tendencies
STYLE_TENDENCY_DEFAULT = 0.5       # neutral starting point for new tendencies


class MemoryWriter(BaseAgent):
    """Post-run memory synthesizer. Constructed by the runner; the runner
    calls `on_run_complete` after the scheduler finishes.

    When constructed with an `llm` client (Phase F.3), the writer
    additionally performs LLM-driven pattern distillation and pattern→
    anti-pattern flip detection. With `llm=None`, behaviour collapses
    to the deterministic Stage-5 path (no synthesis, no flips).
    """

    def __init__(self, llm: LLMClient | None = None, **kwargs):
        super().__init__(
            role=kwargs.pop("role", "memory_writer"),
            description="Post-run persona memory synthesizer.",
            **kwargs,
        )
        self._llm = llm

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
                await self._update_persona(persona, role, workspace, run_id, project)
            except Exception:
                # Memory write must never fail the run.
                continue

    # ── Per-persona update ─────────────────────────────────────────────────

    async def _update_persona(
        self, persona: Persona, role: str, workspace, run_id: str, project=None
    ) -> None:
        # 1. Record episode. Episodes from training-mode runs are
        #    tagged so reputation aggregates can split production vs
        #    training (DESIGN §35) — both still feed recall().
        is_training = bool(project is not None and getattr(project, "mode", "production") == "training")
        episode = self._build_episode(persona, role, workspace, run_id, is_training, project)
        persona.episodes.append(episode)

        # 2. Recency-weighted confidence (read-side already computes this;
        #    on write we rebuild the stored `confidence` field from the
        #    application window so the stored snapshot is current).
        now = datetime.now(timezone.utc)
        for p in persona.patterns:
            p.confidence = round(compute_confidence(p, persona.profile.recency_half_life_days, now), 4)
        for ap in persona.anti_patterns:
            ap.confidence = round(compute_confidence(ap, persona.profile.recency_half_life_days, now), 4)

        # 3. LLM-driven pattern distillation from recent episode clusters
        #    (Phase F.3.1). Skipped entirely when no LLM was injected, so
        #    `MemoryWriter()` retains its deterministic Stage-5 contract.
        if self._llm is not None:
            try:
                await self._distill_patterns(persona, now)
            except Exception:
                # Synthesis must never fail the run; fall through with
                # whatever distillation produced before the failure.
                pass

        # 4. Style drift from this run's evidence (Phase F.3.3). Pure
        #    deterministic tendency updates from board signals — no LLM
        #    needed; the deeper LLM-driven style synthesis stays deferred
        #    until a real user request surfaces.
        self._drift_style(persona, role, workspace)

        # 5. Update reputation aggregates.
        self._refresh_reputation(persona)

        # 6. Reconciliation pass.
        await self._reconcile(persona, now)

        # Persist atomically.
        self._save(persona, run_id)

        # M2: append to history.json for this run.
        self._append_history(persona, role, workspace, run_id, project)

    # ── 1. Episode ──────────────────────────────────────────────────────────

    def _build_episode(
        self, persona: Persona, role: str, workspace, run_id: str,
        is_training: bool = False, project=None,
    ) -> Episode:
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
            training=is_training,
            scenario_id=(
                getattr(project, "training_scenario_id", "") or ""
                if is_training and project is not None else ""
            ),
            rubric_score=_extract_rubric_score(workspace) if is_training else None,
        )

    # ── 3. LLM-driven pattern distillation (§34, Phase F.3.1) ──────────────

    async def _distill_patterns(self, persona: Persona, now: datetime) -> None:
        """Find recent episode clusters that share themes and ask the LLM
        whether each cluster supports a new emerging pattern.

        Bounded by `MAX_DISTILL_CALLS_PER_RUN` so a chatty persona doesn't
        spend tokens explosively. Falls back silently on any per-cluster
        error — the broader reconciliation pass still runs.
        """
        clusters = _build_episode_clusters(persona)
        if not clusters:
            return

        for cluster in clusters[:MAX_DISTILL_CALLS_PER_RUN]:
            tool_args = await self._call_distill_llm(persona, cluster)
            if tool_args is None:
                continue
            new_pattern = _build_pattern_from_tool_args(
                tool_args, cluster, now
            )
            if new_pattern is None:
                continue
            if _duplicates_existing_pattern(new_pattern, persona):
                continue
            persona.patterns.append(new_pattern)

    async def _call_distill_llm(
        self, persona: Persona, cluster: "_EpisodeCluster"
    ) -> dict[str, Any] | None:
        """Render the prompt and call the LLM. Returns the structured
        `emit_pattern` arguments dict, or None if the LLM declined / the
        call failed / the response was malformed.
        """
        try:
            from core.llm import LLMMessage  # local to keep import cheap
            response = await self._llm.complete(  # type: ignore[union-attr]
                system=DISTILL_SYSTEM_PROMPT,
                messages=[LLMMessage(role="user", content=_render_distill_user(persona, cluster))],
                tools=[EMIT_PATTERN_TOOL, EMIT_NO_PATTERN_TOOL],
                max_tokens=1024,
            )
        except Exception:
            return None

        if not response.tool_calls:
            return None
        call = response.tool_calls[0]
        if call.name != "emit_pattern":
            return None
        if not isinstance(call.arguments, dict):
            return None
        return call.arguments

    # ── 4. Style drift (§26 Layer 3, Phase F.3.3) ──────────────────────────

    def _drift_style(self, persona: Persona, role: str, workspace) -> None:
        """Drift the persona's working-style tendencies toward what was
        observed this run.

        Three lightweight observations from the board:
          - `incremental_commits` — multiple GIT_COMMITs by role (vs one
            big drop)
          - `iterates_on_feedback` — run completed in iteration > 1 (the
            agent had to revise after rejection or scheduler iteration)
          - `tests_referenced` — at least one GIT_COMMIT by role mentions
            tests (summary or detail contains "test")

        Drift formula: `t = (1-α)·t + α·observation`, α=0.1. Each
        observation is 1.0 (true) or 0.0 (false), so a single run nudges
        the tendency by at most 0.1. Tendencies converge over ~10 runs.
        """
        role_commits = [
            s for s in workspace.board
            if s.type.value == "GIT_COMMIT" and s.from_agent == role
        ]
        incremental = len(role_commits) >= 2
        iterates = bool(getattr(workspace, "iteration", 0) and workspace.iteration > 1)
        tests_referenced = any(
            "test" in (s.summary or "").lower() or "test" in (s.detail or "").lower()
            for s in role_commits
        )

        _drift_tendency(persona.style.tendencies, "incremental_commits", incremental)
        _drift_tendency(persona.style.tendencies, "iterates_on_feedback", iterates)
        _drift_tendency(persona.style.tendencies, "tests_referenced", tests_referenced)

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
        training_episodes = [ep for ep in persona.episodes if ep.training]
        train.total_runs = len(training_episodes)
        # Graduation logic (DESIGN §35, Phase F.1.3). The persona graduates
        # when it has covered breadth (≥3 distinct scenarios) AND shown
        # consistent quality on its recent training (avg rubric ≥ 0.7
        # across the last 5 training episodes). The §35 third criterion
        # ("survived a tournament") is implicit: meeting (1)+(2) requires
        # ≥5 training runs covering ≥3 scenarios, which already
        # represents meaningful exposure.
        train.graduation_status = _compute_graduation_status(training_episodes)

    # ── 6. Reconciliation pass (§34) ────────────────────────────────────────

    async def _reconcile(self, persona: Persona, now: datetime) -> None:
        # 6a. Contradiction reconciliation FIRST — checks ACTIVE patterns
        #     for overlapping applies_when and supersedes the loser.
        #     Must run before lifecycle transitions so we compare against
        #     stable status; otherwise a soon-to-decline pattern would
        #     drop out of ACTIVE before contradiction check fires.
        self._reconcile_contradictions(persona, now)

        # 6b. Context narrowing — examine recent failures by tag.
        self._narrow_contexts(persona)

        # 6c. Pattern → anti-pattern flipping (Phase F.3.2) runs BEFORE
        #     lifecycle transitions: a pattern with 5+ consecutive failures
        #     would otherwise archive in this same run, freezing it before
        #     the LLM gets a chance to extract a "what worked" alternative.
        #     Flip-or-archive — flip wins if the LLM can name an alternative.
        if self._llm is not None:
            try:
                await self._detect_flips(persona, now)
            except Exception:
                pass

        # 6d. Lifecycle state transitions on whatever remains in
        #     emerging/active/declining after supersedence + flips.
        for p in persona.patterns:
            if p.user_pinned:
                continue
            self._transition_state(p, now)
        for ap in persona.anti_patterns:
            if ap.user_pinned:
                continue
            self._transition_state(ap, now)

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

    async def _detect_flips(self, persona: Persona, now: datetime) -> None:
        """Patterns with FLIP_FAILURE_STREAK consecutive failed applications
        are candidates for becoming anti-patterns. Ask the LLM to confirm
        the flip and supply the 'what worked' alternative; on success,
        create an AntiPattern and supersede the original.

        Bounded by `MAX_FLIP_CALLS_PER_RUN` for the same reason
        distillation is bounded — token spend control on chatty personas.
        """
        candidates: list[Pattern] = []
        for p in persona.patterns:
            if p.status in (STATUS_ARCHIVED, STATUS_SUPERSEDED):
                continue
            if p.user_pinned:
                continue
            consecutive_failures = 0
            for app in reversed(p.applications):
                if app.outcome == "failed":
                    consecutive_failures += 1
                else:
                    break
            if consecutive_failures >= FLIP_FAILURE_STREAK:
                candidates.append(p)

        for pattern in candidates[:MAX_FLIP_CALLS_PER_RUN]:
            tool_args = await self._call_flip_llm(persona, pattern)
            if tool_args is None:
                continue
            anti = _build_anti_pattern_from_flip(tool_args, pattern, now)
            if anti is None:
                continue
            persona.anti_patterns.append(anti)
            _set_status(pattern, STATUS_SUPERSEDED)
            pattern.superseded_by = anti.anti_pattern_id

    async def _call_flip_llm(
        self, persona: Persona, pattern: Pattern
    ) -> dict[str, Any] | None:
        try:
            from core.llm import LLMMessage
            response = await self._llm.complete(  # type: ignore[union-attr]
                system=FLIP_SYSTEM_PROMPT,
                messages=[LLMMessage(
                    role="user",
                    content=_render_flip_user(persona, pattern),
                )],
                tools=[EMIT_FLIP_TOOL, EMIT_NO_FLIP_TOOL],
                max_tokens=1024,
            )
        except Exception:
            return None
        if not response.tool_calls:
            return None
        call = response.tool_calls[0]
        if call.name != "emit_flip":
            return None
        if not isinstance(call.arguments, dict):
            return None
        return call.arguments

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

    def _save(self, persona: Persona, run_id: str = "") -> None:
        """Atomic rewrite of patterns.json and episodes.jsonl; updates profile.json last_active fields."""
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

        # style.json (Phase F.3.3 — drifted tendencies + relationship maps)
        style_path = persona_dir / "style.json"
        style_payload = {
            "tendencies": dict(persona.style.tendencies),
            "reviewer_relationships": dict(persona.style.reviewer_relationships),
            "project_type_performance": dict(persona.style.project_type_performance),
            "model_compatibility": dict(persona.style.model_compatibility),
        }
        style_path.write_text(
            json.dumps(style_payload, indent=2, default=str), encoding="utf-8"
        )

        # Update profile.json with last_active metadata so the persona is
        # always findable in /roster after a run, even if it was newly created.
        profile_path = persona_dir / "profile.json"
        try:
            profile_data = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
        except json.JSONDecodeError:
            profile_data = {}
        profile_data["persona_id"] = profile_data.get("persona_id") or persona.profile.persona_id
        profile_data["name"] = profile_data.get("name") or persona.profile.name
        profile_data["last_active_run_id"] = run_id
        profile_data["last_active_ts"] = datetime.now(timezone.utc).isoformat()
        profile_path.write_text(json.dumps(profile_data, indent=2, default=str), encoding="utf-8")

    def _append_history(
        self, persona: Persona, role: str, workspace: Any, run_id: str, project: Any
    ) -> None:
        """Append one entry to data/personas/{id}/history.json (M2 §7).

        Atomic write: read → append → write to .tmp → rename.
        """
        persona_dir = getattr(persona, "_dir", None)
        if persona_dir is None:
            return
        persona_dir = Path(persona_dir)

        # Determine outcome from board signals.
        approved = any(
            s.from_agent == role and getattr(s.type, "value", "") == "APPROVED"
            for s in getattr(workspace, "board", [])
        )
        sat_out = (
            role in (workspace.agent_states or {})
            and getattr(workspace.agent_states.get(role), "value", "") == "sat_out"
        )
        failed = (
            role in (workspace.agent_states or {})
            and getattr(workspace.agent_states.get(role), "value", "") == "failed"
        )
        if approved:
            outcome = "approved"
        elif sat_out:
            outcome = "sat_out"
        elif failed:
            outcome = "failed"
        else:
            outcome = "completed"

        project_id = getattr(project, "project_id", "") if project is not None else ""
        # Determine branch name (convention: agent/{role}).
        branch = f"agent/{role}"

        entry: dict[str, Any] = {
            "run_id": run_id,
            "project_id": project_id,
            "role": role,
            "outcome": outcome,
            "iteration_count": getattr(workspace, "iteration", 0),
            "started_ts": None,  # runner doesn't expose per-agent start time yet
            "ended_ts": datetime.now(timezone.utc).timestamp(),
            "branch": branch,
        }

        history_path = persona_dir / "history.json"
        try:
            if history_path.exists():
                existing = json.loads(history_path.read_text(encoding="utf-8"))
                if not isinstance(existing, dict) or "entries" not in existing:
                    existing = {"entries": []}
            else:
                existing = {"entries": []}
        except (json.JSONDecodeError, OSError):
            existing = {"entries": []}

        entries: list[dict] = existing["entries"]
        entries.append(entry)

        # Rotation: keep last 1000 entries inline, archive older to history.archive.jsonl
        if len(entries) > 1000:
            archive_path = persona_dir / "history.archive.jsonl"
            overflow = entries[:-1000]
            try:
                with archive_path.open("a", encoding="utf-8") as f:
                    for e in overflow:
                        f.write(json.dumps(e, default=str) + "\n")
            except OSError:
                pass
            entries = entries[-1000:]

        payload = json.dumps({"entries": entries}, indent=2, default=str)
        tmp_path = history_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(history_path)
        except OSError:
            pass


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


# ── Phase F.3.1 — pattern distillation helpers ─────────────────────────────


from dataclasses import dataclass as _dataclass
from itertools import combinations as _combinations


@_dataclass
class _EpisodeCluster:
    """Recent episodes that co-occur on a shared pair of tags. The
    LLM is asked whether the cluster supports a single new pattern."""

    shared_tags: tuple[str, str]
    episodes: list[Episode]


DISTILL_SYSTEM_PROMPT = (
    "You are the memory_writer for a software-engineering persona. You "
    "review small clusters of past episodes that share themes and decide "
    "whether they justify a new emerging pattern in the persona's memory.\n\n"
    "Be conservative. Emit a pattern ONLY when the episodes share a clear, "
    "actionable lesson — not just the same topic. If the episodes are "
    "merely related (same area of the codebase, same kind of task) but the "
    "lesson is fuzzy or the outcomes are mixed, call emit_no_pattern with "
    "the reason instead. A weak pattern hurts the persona more than no "
    "pattern at all.\n\n"
    "Patterns are seeded as 'emerging' (low weight) — they will be "
    "promoted only after the persona applies them in real future runs. "
    "Your job is to spot the lesson, not to score it."
)


EMIT_PATTERN_TOOL: dict[str, Any] = {
    "name": "emit_pattern",
    "description": "Record a new emerging pattern distilled from this episode cluster.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short imperative title — what the persona should do.",
            },
            "lesson": {
                "type": "string",
                "description": "One-paragraph explanation of WHY and WHEN, grounded in the episodes shown.",
            },
            "applies_when": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags / contexts this pattern applies to. Reuse cluster tags where appropriate.",
            },
        },
        "required": ["title", "lesson", "applies_when"],
    },
}


EMIT_NO_PATTERN_TOOL: dict[str, Any] = {
    "name": "emit_no_pattern",
    "description": "Decline to emit a pattern from this cluster.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Why this cluster doesn't support a pattern (mixed outcomes, fuzzy lesson, etc).",
            },
        },
        "required": ["reason"],
    },
}


def _build_episode_clusters(persona: Persona) -> list[_EpisodeCluster]:
    """Surface candidate clusters: pairs of tags that co-occur in
    `DISTILL_MIN_CLUSTER_SIZE`+ recent non-stale episodes whose
    member episodes are not already covered by a non-archived pattern.

    Clusters are returned largest-first so the per-run cap picks the
    strongest signals.
    """
    recent = [
        ep for ep in reversed(persona.episodes)
        if ep.staleness != "stale" and not ep.training
    ][:DISTILL_RECENT_EPISODE_LIMIT]
    if len(recent) < DISTILL_MIN_CLUSTER_SIZE:
        return []

    covered = _covered_episode_ids(persona)

    # Tag-pair → episode list.
    pair_to_eps: dict[tuple[str, str], list[Episode]] = {}
    for ep in recent:
        if ep.episode_id in covered:
            continue
        tags = sorted(set(ep.tags))
        for pair in _combinations(tags, DISTILL_MIN_SHARED_TAGS):
            pair_to_eps.setdefault(pair, []).append(ep)

    clusters: list[_EpisodeCluster] = []
    seen_signatures: set[frozenset[str]] = set()
    for pair, eps in pair_to_eps.items():
        if len(eps) < DISTILL_MIN_CLUSTER_SIZE:
            continue
        # Dedupe overlapping clusters by member-id signature.
        signature = frozenset(ep.episode_id for ep in eps)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        clusters.append(_EpisodeCluster(shared_tags=pair, episodes=eps))

    clusters.sort(key=lambda c: len(c.episodes), reverse=True)
    return clusters


def _covered_episode_ids(persona: Persona) -> set[str]:
    """Episodes already cited as `source_episodes` of a non-archived
    pattern or anti-pattern shouldn't be re-distilled — the LLM has
    already extracted whatever signal lived there."""
    out: set[str] = set()
    for p in persona.patterns:
        if p.status not in (STATUS_ARCHIVED, STATUS_SUPERSEDED):
            out.update(p.source_episodes)
    for ap in persona.anti_patterns:
        if ap.status not in (STATUS_ARCHIVED, STATUS_SUPERSEDED):
            out.update(ap.source_episodes)
    return out


def _render_distill_user(persona: Persona, cluster: _EpisodeCluster) -> str:
    lines: list[str] = []
    lines.append(
        f"Persona: {persona.profile.name} ({persona.profile.specialty})."
    )
    lines.append(
        f"Cluster shared tags: {', '.join(cluster.shared_tags)}."
    )
    lines.append("")
    lines.append("Existing patterns this persona already knows (skip duplicating):")
    if persona.patterns:
        for p in persona.patterns:
            if p.status in (STATUS_ARCHIVED, STATUS_SUPERSEDED):
                continue
            lines.append(f"- {p.title}: {p.lesson}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append(f"Cluster episodes ({len(cluster.episodes)}):")
    for ep in cluster.episodes:
        lines.append(f"--- episode {ep.episode_id} ---")
        if ep.task_context:
            lines.append(f"task: {ep.task_context}")
        if ep.what_i_tried:
            lines.append(f"tried: {ep.what_i_tried[:300]}")
        if ep.what_worked:
            lines.append(f"worked: {ep.what_worked[:200]}")
        if ep.what_failed:
            lines.append(f"failed: {ep.what_failed[:200]}")
        if ep.feedback_verbatim:
            lines.append(f"feedback: {ep.feedback_verbatim[:200]}")
    return "\n".join(lines)


def _build_pattern_from_tool_args(
    args: dict[str, Any],
    cluster: _EpisodeCluster,
    now: datetime,
) -> Pattern | None:
    title = (args.get("title") or "").strip()
    lesson = (args.get("lesson") or "").strip()
    if not title or not lesson:
        return None

    raw_applies = args.get("applies_when") or []
    if not isinstance(raw_applies, list):
        return None
    applies_when = [
        str(t).strip().lower() for t in raw_applies if isinstance(t, (str, int))
    ]
    applies_when = [t for t in applies_when if t]
    if not applies_when:
        # Default to the cluster's shared tags so retrieval can still hit.
        applies_when = list(cluster.shared_tags)

    now_iso = now.isoformat()
    return Pattern(
        pattern_id=f"pat_{uuid4().hex[:10]}",
        title=title,
        lesson=lesson,
        applies_when=applies_when,
        applies_when_excludes=[],
        confidence=EMERGING_SEED_CONFIDENCE,
        status=STATUS_EMERGING,
        applications=[],
        lifetime_applications=0,
        lifetime_successes=0,
        source_episodes=[ep.episode_id for ep in cluster.episodes],
        superseded_by=None,
        user_pinned=False,
        first_observed=now_iso,
        last_state_change=now_iso,
    )


def _duplicates_existing_pattern(new_pattern: Pattern, persona: Persona) -> bool:
    """Belt-and-suspenders dedupe: if the LLM proposes a lesson identical
    to an existing pattern's lesson, drop the duplicate. The system
    prompt already shows existing patterns; this catches the rare case
    where the LLM ignores the hint."""
    target = new_pattern.lesson.strip().lower()
    if not target:
        return False
    for p in persona.patterns:
        if p.status in (STATUS_ARCHIVED, STATUS_SUPERSEDED):
            continue
        if p.lesson.strip().lower() == target:
            return True
    return False


# ── Phase F.3.2 — pattern→anti-pattern flip helpers ────────────────────────


FLIP_SYSTEM_PROMPT = (
    "You are reviewing a pattern that has accumulated repeated recent "
    "failures. Decide whether the pattern should be flipped into an "
    "anti-pattern (an explicit rule for what NOT to do, paired with a "
    "concrete alternative).\n\n"
    "Flip ONLY when the failures show a clear inverse lesson — i.e. you "
    "can name what would have worked instead. If failures are scattered "
    "or the alternative is fuzzy, call emit_no_flip and the lifecycle "
    "transitions will let the pattern decline naturally.\n\n"
    "An anti-pattern needs both a `lesson` (why this approach fails now) "
    "and an `alternative` (what to do instead). Without both, decline."
)


EMIT_FLIP_TOOL: dict[str, Any] = {
    "name": "emit_flip",
    "description": "Flip the failing pattern into an anti-pattern with a stated alternative.",
    "input_schema": {
        "type": "object",
        "properties": {
            "lesson": {
                "type": "string",
                "description": "Why this approach now fails — grounded in the recent failure evidence.",
            },
            "alternative": {
                "type": "string",
                "description": "What the persona should do instead (the 'what worked' counter-evidence).",
            },
        },
        "required": ["lesson", "alternative"],
    },
}


EMIT_NO_FLIP_TOOL: dict[str, Any] = {
    "name": "emit_no_flip",
    "description": "Decline to flip — the pattern should decline naturally instead.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Why this isn't a clean flip (no clear alternative, scattered failures, etc).",
            },
        },
        "required": ["reason"],
    },
}


def _render_flip_user(persona: Persona, pattern: Pattern) -> str:
    lines: list[str] = []
    lines.append(
        f"Persona: {persona.profile.name} ({persona.profile.specialty})."
    )
    lines.append("")
    lines.append("Pattern under review:")
    lines.append(f"- title: {pattern.title}")
    lines.append(f"- lesson: {pattern.lesson}")
    lines.append(f"- applies_when: {', '.join(pattern.applies_when)}")
    lines.append(f"- confidence: {pattern.confidence}")
    lines.append(f"- status: {pattern.status}")
    lines.append("")
    lines.append("Recent applications (most recent last):")
    for app in pattern.applications[-10:]:
        lines.append(f"  {app.ts} {app.run_id} → {app.outcome}")
    lines.append("")

    # Surface source episodes — these contain the 'what worked' evidence
    # the LLM needs to name a concrete alternative.
    source_ids = set(pattern.source_episodes)
    relevant_eps = [
        ep for ep in persona.episodes if ep.episode_id in source_ids
    ][:8]
    if relevant_eps:
        lines.append("Source episodes (look for 'what_worked' alternatives):")
        for ep in relevant_eps:
            lines.append(f"--- episode {ep.episode_id} ---")
            if ep.what_failed:
                lines.append(f"failed: {ep.what_failed[:300]}")
            if ep.what_worked:
                lines.append(f"worked: {ep.what_worked[:300]}")
            if ep.feedback_verbatim:
                lines.append(f"feedback: {ep.feedback_verbatim[:300]}")
    return "\n".join(lines)


def _build_anti_pattern_from_flip(
    args: dict[str, Any], pattern: Pattern, now: datetime
) -> AntiPattern | None:
    lesson = (args.get("lesson") or "").strip()
    alternative = (args.get("alternative") or "").strip()
    if not lesson or not alternative:
        return None
    now_iso = now.isoformat()
    return AntiPattern(
        anti_pattern_id=f"anti_{uuid4().hex[:10]}",
        title=pattern.title,
        lesson=lesson,
        alternative=alternative,
        applies_when=list(pattern.applies_when),
        applies_when_excludes=list(pattern.applies_when_excludes),
        confidence=ANTI_PATTERN_SEED_CONFIDENCE,
        status=STATUS_EMERGING,
        applications=[],
        lifetime_applications=0,
        lifetime_successes=0,
        source_episodes=list(pattern.source_episodes),
        flipped_from=pattern.pattern_id,
        flipped_at=now_iso,
        user_pinned=False,
    )


# ── Phase F.3.3 — style drift helpers ──────────────────────────────────────


# ── Phase F.1.3 — graduation + rubric extraction ──────────────────────────


GRADUATION_MIN_SCENARIOS = 3
GRADUATION_RECENT_WINDOW = 5
GRADUATION_AVG_THRESHOLD = 0.7


def _extract_rubric_score(workspace) -> float | None:
    """Pull the score out of the latest RUBRIC_SCORE signal on the
    board. Returns None when the rubric_scorer didn't run (production
    runs, scoring failures with no detail JSON, etc)."""
    for signal in reversed(workspace.board):
        # SignalType compared by value to stay decoupled from the
        # SignalType import here.
        if str(getattr(signal.type, "value", signal.type)) != "RUBRIC_SCORE":
            continue
        detail_raw = signal.detail or ""
        try:
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else detail_raw
        except json.JSONDecodeError:
            continue
        score = detail.get("score") if isinstance(detail, dict) else None
        if isinstance(score, (int, float)):
            return float(score)
    return None


def _compute_graduation_status(training_episodes: list[Episode]) -> str:
    """Apply §35's soft-gate criteria to the persona's training
    episodes. Returns "graduated" or "ungraduated"."""
    if len(training_episodes) < GRADUATION_RECENT_WINDOW:
        return "ungraduated"

    distinct_scenarios = {ep.scenario_id for ep in training_episodes if ep.scenario_id}
    if len(distinct_scenarios) < GRADUATION_MIN_SCENARIOS:
        return "ungraduated"

    # Sort by timestamp so "last 5" actually reflects chronology.
    by_time = sorted(
        (ep for ep in training_episodes if ep.rubric_score is not None),
        key=lambda ep: ep.timestamp,
    )
    recent = by_time[-GRADUATION_RECENT_WINDOW:]
    if len(recent) < GRADUATION_RECENT_WINDOW:
        return "ungraduated"
    avg = sum(ep.rubric_score for ep in recent) / len(recent)
    if avg < GRADUATION_AVG_THRESHOLD:
        return "ungraduated"
    return "graduated"


def _drift_tendency(
    tendencies: dict[str, float], key: str, observation: bool
) -> None:
    """Nudge a tendency score toward `observation` (1.0 if True else 0.0).

    Initialised to STYLE_TENDENCY_DEFAULT on first observation so a
    persona without prior style data starts neutral and drifts from
    there. Result clamped to [0.0, 1.0] and rounded to 4 places to
    keep the persisted JSON readable.
    """
    target = 1.0 if observation else 0.0
    current = tendencies.get(key, STYLE_TENDENCY_DEFAULT)
    drifted = (1.0 - STYLE_DRIFT_ALPHA) * current + STYLE_DRIFT_ALPHA * target
    tendencies[key] = round(max(0.0, min(1.0, drifted)), 4)
