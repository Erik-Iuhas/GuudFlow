"""Persona — persistent specialist agents with three-layer memory.

A persona is the durable identity behind an agent slot. Profile, character,
and stake_framing are its prompt-engineering scaffold (§20). Episodes,
patterns, and style are its three-layer memory (§26). Patterns have
lifecycle states and recency-weighted confidence (§34). Reputation is
split into production and training aggregates (§35).

This module is the READ side: loading from `data/personas/{id}/`, computing
recency-weighted confidence at recall time, filtering by lifecycle status,
and rendering for prompt injection. The WRITE side (`memory_writer`)
lands in Stage 5.

Storage layout matches DESIGN §26:

    data/personas/{persona_id}/
    ├── profile.json
    ├── episodes.jsonl
    ├── patterns.json
    ├── style.json
    ├── reputation.json
    └── history.json
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal


# ── Constants from §34 ──────────────────────────────────────────────────────

STATUS_EMERGING = "emerging"
STATUS_ACTIVE = "active"
STATUS_DECLINING = "declining"
STATUS_ARCHIVED = "archived"
STATUS_SUPERSEDED = "superseded"

PROMPT_STATUSES: frozenset[str] = frozenset({STATUS_EMERGING, STATUS_ACTIVE, STATUS_DECLINING})

# Default recency half-life for confidence weighting (days). Configurable
# per-persona via profile.recency_half_life_days.
DEFAULT_HALF_LIFE_DAYS = 90.0
RECENCY_FACTOR = 0.7  # how much we trust recent over lifetime

# Episode staleness weights for retrieval scoring.
STALENESS_WEIGHTS: dict[str, float] = {"fresh": 1.0, "aged": 0.5, "stale": 0.25}


# ── Profile ────────────────────────────────────────────────────────────────


@dataclass
class ModelPreference:
    provider: str = "anthropic"
    model: str = ""


@dataclass
class PersonaProfile:
    """Identity layer — immutable after creation. Stored in profile.json."""

    persona_id: str
    name: str
    specialty: str
    description: str = ""
    base_role_type: str = "developer"
    character: str = ""
    stake_framing: str = ""
    preferred_model: ModelPreference = field(default_factory=ModelPreference)
    base_system_prompt: str = ""
    tools_required: list[str] = field(default_factory=list)
    # M2 — CrewAI-style one-sentence goal (≤120 chars). Used in teammate
    # brief (Layer 3.5) and studio worker card. Synthesized from specialty +
    # character on first load when absent; cached back to profile.json.
    goal: str = ""
    recency_half_life_days: float = DEFAULT_HALF_LIFE_DAYS
    created_at: str = ""
    created_by: str = "system"

    @classmethod
    def from_dict(cls, d: dict) -> "PersonaProfile":
        pm = d.get("preferred_model", {}) or {}
        return cls(
            persona_id=d.get("persona_id") or d.get("id") or "",
            name=d.get("name", ""),
            specialty=d.get("specialty", ""),
            description=d.get("description", ""),
            base_role_type=d.get("base_role_type", "developer"),
            character=d.get("character", ""),
            stake_framing=d.get("stake_framing", ""),
            goal=d.get("goal", ""),
            preferred_model=ModelPreference(
                provider=pm.get("provider", "anthropic"),
                model=pm.get("model", ""),
            ),
            base_system_prompt=d.get("base_system_prompt", ""),
            tools_required=list(d.get("tools_required", [])),
            recency_half_life_days=float(
                d.get("recency_half_life_days", DEFAULT_HALF_LIFE_DAYS)
            ),
            created_at=d.get("created_at", ""),
            created_by=d.get("created_by", "system"),
        )


# ── Memory layers ───────────────────────────────────────────────────────────


@dataclass
class Episode:
    """Layer 1: raw experience. Stored line-delimited in episodes.jsonl."""

    episode_id: str
    run_id: str = ""
    iteration: int = 0
    project_type: str = ""
    task_context: str = ""
    situation: str = ""
    what_i_tried: str = ""
    what_failed: str = ""
    what_worked: str = ""
    feedback_verbatim: str = ""
    iterations_to_resolve: int = 0
    model_used: str = ""
    confidence: str = "medium"  # legacy free-text on the episode itself
    tags: list[str] = field(default_factory=list)
    embedding: list[float] | None = None
    timestamp: str = ""
    staleness: str = "fresh"  # "fresh" | "aged" | "stale"
    training: bool = False  # §35 — segregates reputation, not memory recall
    # Phase F.1.3 — training-only fields. Empty / None on production
    # episodes; populated on training episodes so the memory_writer's
    # graduation logic and the ranking endpoint can query without
    # walking the run's board on disk.
    scenario_id: str = ""
    rubric_score: float | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Episode":
        return cls(
            episode_id=d.get("episode_id", ""),
            run_id=d.get("run_id", ""),
            iteration=int(d.get("iteration", 0)),
            project_type=d.get("project_type", ""),
            task_context=d.get("task_context", ""),
            situation=d.get("situation", ""),
            what_i_tried=d.get("what_i_tried", ""),
            what_failed=d.get("what_failed", ""),
            what_worked=d.get("what_worked", ""),
            feedback_verbatim=d.get("feedback_verbatim", ""),
            iterations_to_resolve=int(d.get("iterations_to_resolve", 0)),
            model_used=d.get("model_used", ""),
            confidence=d.get("confidence", "medium"),
            tags=list(d.get("tags", [])),
            embedding=d.get("embedding"),
            timestamp=d.get("timestamp", ""),
            staleness=d.get("staleness", "fresh"),
            training=bool(d.get("training", False)),
            scenario_id=d.get("scenario_id", ""),
            rubric_score=d.get("rubric_score"),
        )


@dataclass
class PatternApplication:
    run_id: str
    ts: str
    outcome: Literal["succeeded", "failed"]


@dataclass
class Pattern:
    """Layer 2: distilled lesson. §34 lifecycle fields included."""

    pattern_id: str
    title: str
    lesson: str
    applies_when: list[str] = field(default_factory=list)
    applies_when_excludes: list[str] = field(default_factory=list)
    confidence: float = 0.6
    status: str = STATUS_EMERGING
    applications: list[PatternApplication] = field(default_factory=list)
    lifetime_applications: int = 0
    lifetime_successes: int = 0
    source_episodes: list[str] = field(default_factory=list)
    superseded_by: str | None = None
    user_pinned: bool = False
    first_observed: str = ""
    last_state_change: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Pattern":
        apps = [
            PatternApplication(
                run_id=a.get("run_id", ""),
                ts=a.get("ts", ""),
                outcome=a.get("outcome", "succeeded"),
            )
            for a in d.get("applications", [])
        ]
        lifecycle = d.get("lifecycle", {}) or {}
        return cls(
            pattern_id=d.get("pattern_id", ""),
            title=d.get("title", ""),
            lesson=d.get("lesson", ""),
            applies_when=list(d.get("applies_when", [])),
            applies_when_excludes=list(d.get("applies_when_excludes", [])),
            confidence=float(d.get("confidence", 0.6)),
            status=d.get("status", STATUS_EMERGING),
            applications=apps,
            lifetime_applications=int(d.get("lifetime_applications", 0)),
            lifetime_successes=int(d.get("lifetime_successes", 0)),
            source_episodes=list(d.get("source_episodes", [])),
            superseded_by=d.get("superseded_by"),
            user_pinned=bool(d.get("user_pinned", False)),
            first_observed=lifecycle.get("first_observed", d.get("first_observed", "")),
            last_state_change=lifecycle.get("last_state_change", ""),
        )


@dataclass
class AntiPattern:
    """An inverted Pattern — what NOT to do, with a stated alternative.

    Shares the lifecycle/confidence interface of Pattern so the same
    `compute_confidence` works for both.
    """

    anti_pattern_id: str
    title: str
    lesson: str
    alternative: str = ""
    applies_when: list[str] = field(default_factory=list)
    applies_when_excludes: list[str] = field(default_factory=list)
    confidence: float = 0.6
    status: str = STATUS_EMERGING
    applications: list[PatternApplication] = field(default_factory=list)
    lifetime_applications: int = 0
    lifetime_successes: int = 0
    source_episodes: list[str] = field(default_factory=list)
    flipped_from: str | None = None
    flipped_at: str | None = None
    user_pinned: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "AntiPattern":
        apps = [
            PatternApplication(
                run_id=a.get("run_id", ""),
                ts=a.get("ts", ""),
                outcome=a.get("outcome", "succeeded"),
            )
            for a in d.get("applications", [])
        ]
        return cls(
            anti_pattern_id=d.get("anti_pattern_id", ""),
            title=d.get("title", ""),
            lesson=d.get("lesson", ""),
            alternative=d.get("alternative", ""),
            applies_when=list(d.get("applies_when", [])),
            applies_when_excludes=list(d.get("applies_when_excludes", [])),
            confidence=float(d.get("confidence", 0.6)),
            status=d.get("status", STATUS_EMERGING),
            applications=apps,
            lifetime_applications=int(d.get("lifetime_applications", 0)),
            lifetime_successes=int(d.get("lifetime_successes", 0)),
            source_episodes=list(d.get("source_episodes", [])),
            flipped_from=d.get("flipped_from"),
            flipped_at=d.get("flipped_at"),
            user_pinned=bool(d.get("user_pinned", False)),
        )


@dataclass
class WorkingStyle:
    """Layer 3: emergent tendencies and relationships."""

    tendencies: dict[str, float] = field(default_factory=dict)
    reviewer_relationships: dict[str, dict] = field(default_factory=dict)
    project_type_performance: dict[str, dict] = field(default_factory=dict)
    model_compatibility: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "WorkingStyle":
        return cls(
            tendencies=dict(d.get("tendencies", {})),
            reviewer_relationships=dict(d.get("reviewer_relationships", {})),
            project_type_performance=dict(d.get("project_type_performance", {})),
            model_compatibility=dict(d.get("model_compatibility", {})),
        )


@dataclass
class ReputationBucket:
    total_runs: int = 0
    completed_runs: int = 0
    failed_runs: int = 0
    average_iterations_to_approval: float = 0.0
    reviewer_first_pass_rate: float = 0.0
    by_task_type: dict[str, dict] = field(default_factory=dict)
    common_feedback_themes: list[str] = field(default_factory=list)


@dataclass
class TrainingReputation(ReputationBucket):
    """§35 — training-mode aggregate. Segregated from production."""

    graduation_status: str = "ungraduated"  # "ungraduated" | "graduated"
    scenarios_completed: list[str] = field(default_factory=list)
    average_rubric_score: float = 0.0


@dataclass
class Reputation:
    """§35 — production and training stats live side by side."""

    production: ReputationBucket = field(default_factory=ReputationBucket)
    training: TrainingReputation = field(default_factory=TrainingReputation)
    total_episodes: int = 0
    active_patterns: int = 0
    active_anti_patterns: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "Reputation":
        # Backward-compat: old reputation.json was flat (production only).
        if "production" in d or "training" in d:
            prod = d.get("production", {}) or {}
            train = d.get("training", {}) or {}
        else:
            prod = d
            train = {}

        def bucket(b: dict) -> ReputationBucket:
            return ReputationBucket(
                total_runs=int(b.get("total_runs", 0)),
                completed_runs=int(b.get("completed_runs", 0)),
                failed_runs=int(b.get("failed_runs", 0)),
                average_iterations_to_approval=float(b.get("average_iterations_to_approval", 0.0)),
                reviewer_first_pass_rate=float(b.get("reviewer_first_pass_rate", 0.0)),
                by_task_type=dict(b.get("by_task_type", {})),
                common_feedback_themes=list(b.get("common_feedback_themes", [])),
            )

        production = bucket(prod)
        training_b = bucket(train)
        training = TrainingReputation(
            **training_b.__dict__,
            graduation_status=train.get("graduation_status", "ungraduated"),
            scenarios_completed=list(train.get("scenarios_completed", [])),
            average_rubric_score=float(train.get("average_rubric_score", 0.0)),
        )
        return cls(
            production=production,
            training=training,
            total_episodes=int(d.get("total_episodes", 0)),
            active_patterns=int(d.get("active_patterns", 0)),
            active_anti_patterns=int(d.get("active_anti_patterns", 0)),
        )


# ── Recall result ──────────────────────────────────────────────────────────


@dataclass
class RecalledPattern:
    pattern: Pattern
    score: float
    caution: bool  # True for declining patterns — prompt frames carefully


@dataclass
class RecalledAntiPattern:
    anti_pattern: AntiPattern
    score: float


@dataclass
class MemoryRecall:
    """What `Persona.recall()` returns. Agents render this into the prompt
    via `render_for_prompt()` — they never read raw memory files."""

    episodes: list[tuple[Episode, float]] = field(default_factory=list)
    patterns: list[RecalledPattern] = field(default_factory=list)
    anti_patterns: list[RecalledAntiPattern] = field(default_factory=list)

    def has_entries(self) -> bool:
        return bool(self.episodes or self.patterns or self.anti_patterns)

    def render_for_prompt(self) -> str:
        if not self.has_entries():
            return ""
        lines = ["FROM YOUR EXPERIENCE WITH SIMILAR TASKS:", ""]

        if self.patterns:
            lines.append("Relevant lessons:")
            for rp in self.patterns:
                p = rp.pattern
                marker = "⚠ " if rp.caution else "- "
                conf = f"confidence {p.confidence:.2f}"
                applied = f"{p.lifetime_applications} applications"
                line = f"{marker}{p.title}: {p.lesson} ({applied}, {conf})"
                if rp.caution:
                    line += " — recent failures, apply with care"
                lines.append(line)
            lines.append("")

        if self.anti_patterns:
            lines.append("Avoid these patterns:")
            for ra in self.anti_patterns:
                ap = ra.anti_pattern
                line = f"- {ap.title}: {ap.lesson}"
                if ap.alternative:
                    line += f" → instead: {ap.alternative}"
                lines.append(line)
            lines.append("")

        if self.episodes:
            lines.append(f"You have {len(self.episodes)} relevant past episodes available; "
                         "request more context on any specific one if needed.")

        return "\n".join(lines).rstrip()


# ── Helpers — recency + similarity ─────────────────────────────────────────


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        # ISO-8601 with optional 'Z'
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_days(ts: str, now: datetime) -> float | None:
    parsed = _parse_ts(ts)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = now - parsed
    return max(0.0, delta.total_seconds() / 86400.0)


def _recency_weight(age_days: float | None, half_life: float) -> float:
    """exp(-age/half_life). Older → smaller weight."""
    if age_days is None or half_life <= 0:
        return 1.0
    return math.exp(-age_days / half_life)


def compute_confidence(p: Pattern | AntiPattern, half_life: float, now: datetime | None = None) -> float:
    """Recency-weighted confidence per §34.

    weighted_success_rate = Σ weight·success / Σ weight     (over recent applications)
    lifetime_rate         = lifetime_successes / lifetime_applications
    confidence = recency_factor·weighted + (1-recency_factor)·lifetime_rate
    """
    now = now or datetime.now(timezone.utc)
    if not p.applications and p.lifetime_applications == 0:
        return p.confidence  # nothing to compute from — keep stored value

    if p.applications:
        total_weight = 0.0
        weighted_succ = 0.0
        for app in p.applications:
            age = _age_days(app.ts, now)
            w = _recency_weight(age, half_life)
            total_weight += w
            if app.outcome == "succeeded":
                weighted_succ += w
        weighted_rate = weighted_succ / total_weight if total_weight > 0 else 0.0
    else:
        weighted_rate = (
            p.lifetime_successes / p.lifetime_applications
            if p.lifetime_applications > 0
            else p.confidence
        )

    if p.lifetime_applications > 0:
        lifetime_rate = p.lifetime_successes / p.lifetime_applications
    else:
        lifetime_rate = weighted_rate

    return RECENCY_FACTOR * weighted_rate + (1 - RECENCY_FACTOR) * lifetime_rate


def _tokenize(text: str) -> set[str]:
    """Lowercase identifier-ish tokens for keyword similarity."""
    import re
    out: set[str] = set()
    for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", text or ""):
        word = m.group(0).lower()
        if len(word) >= 2:
            out.add(word)
    return out


def _tag_overlap(query: set[str], tags: Iterable[str]) -> int:
    tag_set = {t.lower() for t in tags if t}
    return len(query & tag_set)


def _applies_filter(query_tags: set[str], applies_when: list[str], excludes: list[str]) -> bool:
    """Returns True if pattern is applicable given the query's tags.

    `applies_when` semantics:
      - empty list → applies to anything (but won't score well)
      - else → at least one tag must overlap
    `excludes` (with `!` prefix) → none of these may overlap
    """
    if applies_when:
        if not any(t.lower() in query_tags for t in applies_when):
            return False
    for ex in excludes:
        ex_clean = ex.lstrip("!").lower()
        if ex_clean in query_tags:
            return False
    return True


# ── Goal synthesis (M2) ────────────────────────────────────────────────────


def _synthesize_goal(profile: "PersonaProfile") -> str:
    """Derive a one-sentence goal (≤120 chars) from specialty + character.

    Imperative phrasing, no marketing words. This runs synchronously so it
    can be called inside Persona.load() without async context.
    """
    specialty = (profile.specialty or "").strip()
    character = (profile.character or "").strip()
    # Extract first sentence of character (likely the most relevant)
    first_sentence = character.split(".")[0].strip() if character else ""

    if specialty and first_sentence:
        raw = f"Deliver {specialty} solutions: {first_sentence.lower()}."
    elif specialty:
        raw = f"Deliver reliable {specialty} work."
    elif first_sentence:
        raw = first_sentence + "."
    else:
        raw = "Deliver quality work within your specialty."
    return raw[:120]


def _cache_goal(persona_dir: "Path", goal: str) -> None:
    """Write goal back to profile.json. Silently skips on any IO error."""
    import json as _json
    profile_path = persona_dir / "profile.json"
    try:
        data = _json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
        data["goal"] = goal
        profile_path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass


# ── Persona — the loaded entity ─────────────────────────────────────────────


@dataclass
class _PatternsFile:
    patterns: list[Pattern] = field(default_factory=list)
    anti_patterns: list[AntiPattern] = field(default_factory=list)
    archived: list[dict] = field(default_factory=list)  # raw dicts; not loaded actively


class Persona:
    """A loaded persona with its memory available for recall.

    Construct via `Persona.load(persona_id, root)` or `Persona.empty(...)`.
    `recall()` is the only method agents should use to access memory.
    """

    def __init__(
        self,
        profile: PersonaProfile,
        episodes: list[Episode],
        patterns: list[Pattern],
        anti_patterns: list[AntiPattern],
        style: WorkingStyle,
        reputation: Reputation,
        history: dict[str, Any] | None = None,
    ):
        self.profile = profile
        self.episodes = episodes
        self.patterns = patterns
        self.anti_patterns = anti_patterns
        self.style = style
        self.reputation = reputation
        self.history = history or {"runs": []}

    # ── Loaders ─────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, persona_id: str, root: Path | str) -> "Persona":
        root = Path(root)
        persona_dir = root / persona_id
        if not persona_dir.is_dir():
            raise FileNotFoundError(f"persona directory not found: {persona_dir}")

        profile = cls._load_profile(persona_dir)
        episodes = cls._load_episodes(persona_dir)
        patterns_file = cls._load_patterns(persona_dir)
        style = cls._load_style(persona_dir)
        reputation = cls._load_reputation(persona_dir)
        history = cls._load_history(persona_dir)

        persona = cls(
            profile=profile,
            episodes=episodes,
            patterns=patterns_file.patterns,
            anti_patterns=patterns_file.anti_patterns,
            style=style,
            reputation=reputation,
            history=history,
        )

        # M2: synthesize goal on first load if absent; cache back to profile.json.
        if not persona.profile.goal:
            persona.profile.goal = _synthesize_goal(persona.profile)
            _cache_goal(persona_dir, persona.profile.goal)

        return persona

    @classmethod
    def empty(cls, persona_id: str, name: str, specialty: str, **profile_kwargs) -> "Persona":
        """Construct a fresh persona with no memory. Used for tests and for
        the very first time a new specialist is created in the studio."""
        profile = PersonaProfile(
            persona_id=persona_id,
            name=name,
            specialty=specialty,
            created_at=datetime.now(timezone.utc).isoformat(),
            **profile_kwargs,
        )
        return cls(
            profile=profile,
            episodes=[],
            patterns=[],
            anti_patterns=[],
            style=WorkingStyle(),
            reputation=Reputation(),
            history={"runs": []},
        )

    @staticmethod
    def _load_profile(persona_dir: Path) -> PersonaProfile:
        path = persona_dir / "profile.json"
        if not path.exists():
            raise FileNotFoundError(f"profile.json missing in {persona_dir}")
        return PersonaProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def _load_episodes(persona_dir: Path) -> list[Episode]:
        path = persona_dir / "episodes.jsonl"
        if not path.exists():
            return []
        episodes: list[Episode] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                episodes.append(Episode.from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError):
                # Tolerate malformed lines — log in production, skip here.
                continue
        return episodes

    @staticmethod
    def _load_patterns(persona_dir: Path) -> _PatternsFile:
        path = persona_dir / "patterns.json"
        if not path.exists():
            return _PatternsFile()
        data = json.loads(path.read_text(encoding="utf-8"))
        patterns = [Pattern.from_dict(p) for p in data.get("patterns", [])]
        anti = [AntiPattern.from_dict(p) for p in data.get("anti_patterns", [])]
        archived = list(data.get("archived", []))
        return _PatternsFile(patterns=patterns, anti_patterns=anti, archived=archived)

    @staticmethod
    def _load_style(persona_dir: Path) -> WorkingStyle:
        path = persona_dir / "style.json"
        if not path.exists():
            return WorkingStyle()
        return WorkingStyle.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def _load_reputation(persona_dir: Path) -> Reputation:
        path = persona_dir / "reputation.json"
        if not path.exists():
            return Reputation()
        return Reputation.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def _load_history(persona_dir: Path) -> dict[str, Any]:
        path = persona_dir / "history.json"
        if not path.exists():
            return {"runs": []}
        return json.loads(path.read_text(encoding="utf-8"))

    # ── Identity layers (Layer 1-3 of effective_system_prompt) ──────────────

    def identity_layer(self) -> str:
        """Layer 1 — Expert identity framing."""
        p = self.profile
        bits = [f"You are {p.name}, a {p.specialty}."]
        if p.description:
            bits.append(p.description)
        return " ".join(bits)

    def character_layer(self) -> str:
        """Layer 2 — Character. Empty string if not set."""
        return self.profile.character

    def stake_layer(self) -> str:
        """Layer 3 — Stake framing. Empty string if not set."""
        return self.profile.stake_framing

    def has_memory(self) -> bool:
        return bool(self.episodes or self.patterns or self.anti_patterns)

    # ── Recall ──────────────────────────────────────────────────────────────

    async def recall(
        self,
        task_description: str,
        limit: int = 5,
        now: datetime | None = None,
    ) -> MemoryRecall:
        """Return contextually-relevant memory for the current task.

        Filters:
          - Patterns: status ∈ {emerging, active, declining}; declining
            flagged with `caution`. `applies_when` / `applies_when_excludes`
            checked against the task tags.
          - Anti-patterns: same status filter.
          - Episodes: weighted by staleness (fresh > aged > stale).

        Scoring:
          - Patterns: tag overlap + recency-weighted confidence.
          - Episodes: tag overlap + token similarity, scaled by staleness.
        """
        now = now or datetime.now(timezone.utc)
        half_life = self.profile.recency_half_life_days
        query_tokens = _tokenize(task_description)

        scored_patterns = self._score_patterns(query_tokens, half_life, now)
        scored_anti = self._score_anti_patterns(query_tokens, half_life, now)
        scored_episodes = self._score_episodes(query_tokens, task_description)

        return MemoryRecall(
            episodes=scored_episodes[:limit],
            patterns=scored_patterns[:limit],
            anti_patterns=scored_anti[:limit],
        )

    def _score_patterns(
        self,
        query_tokens: set[str],
        half_life: float,
        now: datetime,
    ) -> list[RecalledPattern]:
        out: list[RecalledPattern] = []
        for p in self.patterns:
            if p.status not in PROMPT_STATUSES:
                continue
            if not _applies_filter(query_tokens, p.applies_when, p.applies_when_excludes):
                continue

            conf = compute_confidence(p, half_life, now)
            tag_hits = _tag_overlap(query_tokens, p.applies_when)
            score = (1.0 + tag_hits) * conf
            if score <= 0:
                continue
            out.append(RecalledPattern(
                pattern=p,
                score=score,
                caution=(p.status == STATUS_DECLINING),
            ))
        out.sort(key=lambda rp: rp.score, reverse=True)
        return out

    def _score_anti_patterns(
        self,
        query_tokens: set[str],
        half_life: float,
        now: datetime,
    ) -> list[RecalledAntiPattern]:
        out: list[RecalledAntiPattern] = []
        for ap in self.anti_patterns:
            if ap.status not in PROMPT_STATUSES:
                continue
            if not _applies_filter(query_tokens, ap.applies_when, ap.applies_when_excludes):
                continue
            conf = compute_confidence(ap, half_life, now)
            tag_hits = _tag_overlap(query_tokens, ap.applies_when)
            score = (1.0 + tag_hits) * conf
            if score <= 0:
                continue
            out.append(RecalledAntiPattern(anti_pattern=ap, score=score))
        out.sort(key=lambda ra: ra.score, reverse=True)
        return out

    def _score_episodes(
        self,
        query_tokens: set[str],
        task_description: str,
    ) -> list[tuple[Episode, float]]:
        out: list[tuple[Episode, float]] = []
        task_tokens = query_tokens
        for ep in self.episodes:
            tag_hits = _tag_overlap(task_tokens, ep.tags)
            ep_text = " ".join([
                ep.task_context, ep.situation, ep.what_worked, ep.what_failed,
            ])
            text_overlap = len(_tokenize(ep_text) & task_tokens)
            staleness_w = STALENESS_WEIGHTS.get(ep.staleness, 1.0)
            score = (2.0 * tag_hits + 0.3 * text_overlap) * staleness_w
            if score <= 0:
                continue
            out.append((ep, score))
        out.sort(key=lambda pair: pair[1], reverse=True)
        return out
