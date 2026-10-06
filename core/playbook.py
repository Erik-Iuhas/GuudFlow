"""Per-project playbook — lessons that carry across runs of one project.

OPENCODE_HARNESS_PLAN §11.4. The playbook lives at
`<data_root>/projects/{project_id}/playbook.json` and is deliberately separate
from persona memory (personas cross projects; the playbook belongs to exactly
one). It follows the same rules as persona memory (CLAUDE.md hard constraints
#16 and #22):

  * every entry needs evidence runs — an entry without evidence is invalid;
  * confidence is recency-weighted using the persona recency function
    (`exp(-age / half_life)`, 90-day default) and drops on contradiction;
  * entries move through emerging -> active -> declining -> archived.

Lifecycle thresholds (confidence based, with hysteresis so an entry that has
earned `active` is not demoted until it falls below the declining line):

    archived   confidence < 0.2   (terminal)
    declining  was active/declining and confidence < 0.4
    active     confidence >= 0.6, or already active/declining and >= 0.4
    emerging   everything else (new entries start here at 0.5)

These differ from persona's thresholds (0.55 / 0.30), which are coupled to
application counts and failure streaks that a playbook entry does not have;
see `docs/OPENCODE_HARNESS_PLAN.md` §11.4. The persona constants, status names,
half-life default and recency functions are reused from `agents.persona`.

This module only STORES. Nothing here applies a playbook change to a team —
playbook-driven changes go through the approval gate (hard constraint #24).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, get_args

from agents.persona import (
    DEFAULT_HALF_LIFE_DAYS,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_DECLINING,
    STATUS_EMERGING,
    STATUS_SUPERSEDED,
    _age_days,
    _recency_weight,
)

Section = Literal[
    "team_adjustments",
    "brief_addenda",
    "known_pitfalls",
    "review_rounds",
    "capability_hints",
    "task_templates",
]
SECTIONS: tuple[str, ...] = get_args(Section)

LifecycleState = Literal["emerging", "active", "declining", "archived", "superseded"]

# Confidence deltas — same as persona patterns (CLAUDE.md "Persona Memory Rules").
REINFORCE_DELTA = 0.05
CONTRADICT_DELTA = -0.15
REVIEW_FLAG_BELOW = 0.5  # contradicted entries below this are flagged for review

ACTIVE_THRESHOLD = 0.6
DECLINING_THRESHOLD = 0.4
ARCHIVE_THRESHOLD = 0.2

MAX_RUN_METRICS = 50
PLAYBOOK_VERSION = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def make_entry_id(section: str, key: str, value: Any) -> str:
    """Stable 12-hex-char id from section + key + canonical JSON of the value."""
    payload = json.dumps([section, key, value], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def next_state(previous: str, confidence: float) -> str:
    """Lifecycle state for `confidence`, given the entry's `previous` state.

    Archived and superseded are terminal. See the module docstring for the
    thresholds.
    """
    if previous in (STATUS_ARCHIVED, STATUS_SUPERSEDED):
        return previous
    if confidence < ARCHIVE_THRESHOLD:
        return STATUS_ARCHIVED
    if confidence >= ACTIVE_THRESHOLD:
        return STATUS_ACTIVE
    if previous in (STATUS_ACTIVE, STATUS_DECLINING):
        return STATUS_DECLINING if confidence < DECLINING_THRESHOLD else previous
    return STATUS_EMERGING


@dataclass
class PlaybookEntry:
    """One evidence-backed lesson. `evidence_runs` must be non-empty."""

    id: str
    section: Section
    key: str
    value: Any  # any JSON value; shape depends on the section (see plan §11.4)
    evidence_runs: list[str]
    confidence: float = 0.5
    state: LifecycleState = "emerging"
    created_at: str = ""
    updated_at: str = ""
    last_evidence_at: str = ""
    # Confidence at `last_evidence_at`, before recency decay. `confidence` is
    # always derived from this, which keeps `decay()` idempotent.
    base_confidence: float = -1.0
    contradicted_runs: list[str] = field(default_factory=list)
    needs_review: bool = False

    def __post_init__(self) -> None:
        if self.section not in SECTIONS:
            raise ValueError(f"unknown playbook section: {self.section!r}")
        if not self.evidence_runs or not all(
            isinstance(r, str) and r for r in self.evidence_runs
        ):
            raise ValueError("playbook entries require non-empty evidence_runs")
        if self.section == "capability_hints" and not isinstance(self.value, dict):
            raise ValueError("capability_hints values must be dicts")
        try:
            json.dumps(self.value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"playbook value is not JSON-serialisable: {exc}") from exc
        if not self.created_at:
            self.created_at = _iso(_now())
        if not self.updated_at:
            self.updated_at = self.created_at
        if not self.last_evidence_at:
            self.last_evidence_at = self.created_at
        if self.base_confidence < 0:
            self.base_confidence = self.confidence
        if not self.id:
            self.id = make_entry_id(self.section, self.key, self.value)

    @classmethod
    def create(
        cls,
        section: Section,
        key: str,
        value: Any,
        run_id: str,
        confidence: float = 0.5,
        now: datetime | None = None,
    ) -> "PlaybookEntry":
        """Build a fresh entry evidenced by `run_id`."""
        ts = _iso(now or _now())
        conf = _clamp(confidence)
        return cls(
            id=make_entry_id(section, key, value),
            section=section,
            key=key,
            value=value,
            evidence_runs=[run_id],
            confidence=conf,
            state=next_state(STATUS_EMERGING, conf),  # type: ignore[arg-type]
            created_at=ts,
            updated_at=ts,
            last_evidence_at=ts,
            base_confidence=conf,
        )

    def to_json(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "PlaybookEntry":
        """Rebuild from `to_json()` output; raises ValueError if invalid."""
        try:
            return cls(
                id=str(data.get("id", "")),
                section=data["section"],
                key=str(data.get("key", "")),
                value=data.get("value"),
                evidence_runs=list(data.get("evidence_runs", [])),
                confidence=float(data.get("confidence", 0.5)),
                state=data.get("state", "emerging"),
                created_at=str(data.get("created_at", "")),
                updated_at=str(data.get("updated_at", "")),
                last_evidence_at=str(data.get("last_evidence_at", "")),
                base_confidence=float(data.get("base_confidence", -1.0)),
                contradicted_runs=list(data.get("contradicted_runs", [])),
                needs_review=bool(data.get("needs_review", False)),
            )
        except KeyError as exc:
            raise ValueError(f"playbook entry missing field: {exc}") from exc


def _render_value(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


class Playbook:
    """The per-project playbook: evidence-backed entries plus run metrics."""

    def __init__(
        self,
        project_id: str,
        path: Path | None = None,
        half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    ) -> None:
        self.project_id = project_id
        self.path = path
        self.half_life_days = half_life_days
        self._entries: dict[str, PlaybookEntry] = {}
        self._runs: list[dict[str, Any]] = []

    # ── persistence ──

    @staticmethod
    def path_for(root: Path, project_id: str) -> Path:
        """`<root>/projects/{project_id}/playbook.json` (root is the data root)."""
        if not project_id or "/" in project_id or "\\" in project_id or project_id in (".", ".."):
            raise ValueError(f"invalid project_id: {project_id!r}")
        return root / "projects" / project_id / "playbook.json"

    @classmethod
    def load(cls, root: Path, project_id: str) -> "Playbook":
        """Load the project's playbook; a missing file yields an empty one."""
        path = cls.path_for(root, project_id)
        if not path.exists():
            return cls(project_id, path)
        pb = cls.from_json(json.loads(path.read_text(encoding="utf-8")), project_id)
        pb.path = path
        return pb

    def save(self) -> None:
        """Atomically write the playbook (temp file in the same dir + replace)."""
        if self.path is None:
            raise ValueError("playbook has no path; create it with Playbook.load()")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".playbook.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.to_json(), fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def to_json(self) -> dict[str, Any]:
        """Serialise the whole playbook."""
        return {
            "version": PLAYBOOK_VERSION,
            "project_id": self.project_id,
            "half_life_days": self.half_life_days,
            "entries": [e.to_json() for e in self._entries.values()],
            "runs": list(self._runs),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any], project_id: str = "") -> "Playbook":
        """Rebuild from `to_json()` output."""
        pb = cls(
            str(data.get("project_id") or project_id),
            half_life_days=float(data.get("half_life_days", DEFAULT_HALF_LIFE_DAYS)),
        )
        for raw in data.get("entries", []):
            entry = PlaybookEntry.from_json(raw)
            pb._entries[entry.id] = entry
        pb._runs = [dict(r) for r in data.get("runs", [])][-MAX_RUN_METRICS:]
        return pb

    # ── queries ──

    def get(self, entry_id: str) -> PlaybookEntry:
        """Return one entry; KeyError if unknown."""
        return self._entries[entry_id]

    def entries(
        self, section: Section | None = None, include_archived: bool = False
    ) -> list[PlaybookEntry]:
        """Entries in insertion order, optionally one section; archived and
        superseded entries are hidden unless `include_archived`."""
        out = []
        for e in self._entries.values():
            if section is not None and e.section != section:
                continue
            if not include_archived and e.state in (STATUS_ARCHIVED, STATUS_SUPERSEDED):
                continue
            out.append(e)
        return out

    def active_for_role(self, role: str) -> dict[str, Any]:
        """Brief addenda and capability hints for `role` (emerging/active only).

        Returns `{"brief_addenda": [str, ...], "capability_hints": {...}}`;
        addenda are highest confidence first and, on a hint key conflict, the
        higher-confidence entry wins.
        """
        usable = [
            e
            for e in self._entries.values()
            if e.key == role
            and e.section in ("brief_addenda", "capability_hints")
            and e.state in (STATUS_EMERGING, STATUS_ACTIVE)
        ]
        usable.sort(key=lambda e: (-e.confidence, e.created_at))
        addenda = [str(e.value) for e in usable if e.section == "brief_addenda"]
        hints: dict[str, Any] = {}
        for e in reversed([e for e in usable if e.section == "capability_hints"]):
            hints.update(e.value)  # lowest confidence first, best overwrites
        return {"brief_addenda": addenda, "capability_hints": hints}

    def render_for_meta(self) -> str:
        """Compact markdown for the meta-agent prompt: active entries only,
        grouped by section, highest confidence first, with evidence counts."""
        lines: list[str] = []
        for section in SECTIONS:
            rows = [e for e in self.entries(section) if e.state == STATUS_ACTIVE]  # type: ignore[arg-type]
            if not rows:
                continue
            rows.sort(key=lambda e: (-e.confidence, e.created_at))
            lines.append(f"### {section}")
            for e in rows:
                label = f"{e.key}: " if e.key else ""
                lines.append(
                    f"- {label}{_render_value(e.value)} "
                    f"(confidence {e.confidence:.2f}, {len(e.evidence_runs)} runs)"
                )
        if not lines:
            return ""
        return "## Project playbook\n" + "\n".join(lines)

    # ── mutation ──

    def add(
        self,
        section: Section,
        key: str,
        value: Any,
        run_id: str,
        confidence: float = 0.5,
        now: datetime | None = None,
    ) -> PlaybookEntry:
        """Add an entry; an identical one (same id) is reinforced instead."""
        entry_id = make_entry_id(section, key, value)
        if entry_id in self._entries:
            return self.reinforce(entry_id, run_id, now=now)
        entry = PlaybookEntry.create(section, key, value, run_id, confidence, now)
        self._entries[entry.id] = entry
        return entry

    def reinforce(
        self, entry_id: str, run_id: str, now: datetime | None = None
    ) -> PlaybookEntry:
        """+0.05 on the (decayed) confidence, record `run_id` as evidence."""
        return self._adjust(entry_id, run_id, REINFORCE_DELTA, now)

    def contradict(
        self, entry_id: str, run_id: str, now: datetime | None = None
    ) -> PlaybookEntry:
        """-0.15 on the (decayed) confidence; flags the entry for review below
        0.5 and archives it below the archive threshold."""
        return self._adjust(entry_id, run_id, CONTRADICT_DELTA, now)

    def _adjust(
        self, entry_id: str, run_id: str, delta: float, now: datetime | None
    ) -> PlaybookEntry:
        if not run_id:
            raise ValueError("run_id is required")
        entry = self._entries[entry_id]
        moment = now or _now()
        self._decay_entry(entry, moment)
        entry.confidence = round(_clamp(entry.confidence + delta), 4)
        entry.base_confidence = entry.confidence
        entry.last_evidence_at = entry.updated_at = _iso(moment)
        if delta >= 0:
            if run_id not in entry.evidence_runs:
                entry.evidence_runs.append(run_id)
        else:
            if run_id not in entry.contradicted_runs:
                entry.contradicted_runs.append(run_id)
            entry.needs_review = entry.confidence < REVIEW_FLAG_BELOW
        entry.state = next_state(entry.state, entry.confidence)  # type: ignore[assignment]
        return entry

    def _decay_entry(self, entry: PlaybookEntry, now: datetime) -> None:
        if entry.state in (STATUS_ARCHIVED, STATUS_SUPERSEDED):
            return
        age = _age_days(entry.last_evidence_at, now)
        weight = _recency_weight(age, self.half_life_days)
        entry.confidence = round(entry.base_confidence * weight, 4)
        entry.state = next_state(entry.state, entry.confidence)  # type: ignore[assignment]

    def decay(self, now: datetime | None = None) -> list[str]:
        """Apply recency weighting to every entry; returns ids whose state changed.

        Idempotent: confidence is always `base_confidence * weight(age since
        last evidence)`, so calling it twice with the same `now` is a no-op.
        """
        moment = now or _now()
        changed: list[str] = []
        for entry in self._entries.values():
            before = entry.state
            self._decay_entry(entry, moment)
            if entry.state != before:
                entry.updated_at = _iso(moment)
                changed.append(entry.id)
        return changed

    # ── run metrics ──

    def record_run_metrics(self, run_id: str, metrics: dict[str, float]) -> None:
        """Store per-run metrics (stall_minutes, interventions,
        review_rounds_used, cost_per_completed_role, ...). Re-recording a run
        replaces it; only the last 50 runs are kept."""
        if not run_id:
            raise ValueError("run_id is required")
        self._runs = [r for r in self._runs if r.get("run_id") != run_id]
        self._runs.append(
            {"run_id": run_id, "recorded_at": _iso(_now()), "metrics": dict(metrics)}
        )
        self._runs = self._runs[-MAX_RUN_METRICS:]

    def metrics_history(self) -> list[dict[str, Any]]:
        """Recorded runs, oldest first: `{run_id, recorded_at, metrics}`."""
        return [dict(r) for r in self._runs]


@dataclass
class PlaybookDiff:
    """A retrospective's proposed playbook update (produced by the overseer).

    `apply()` only stores it; nothing here changes a team (constraint #24).
    """

    added: list[PlaybookEntry] = field(default_factory=list)
    reinforced: list[str] = field(default_factory=list)
    contradicted: list[str] = field(default_factory=list)

    def apply(self, playbook: Playbook, run_id: str) -> list[PlaybookEntry]:
        """Apply to `playbook` in memory (caller saves); returns touched entries.

        Added entries are credited to `run_id`; unknown reinforced/contradicted
        ids raise KeyError.
        """
        touched: list[PlaybookEntry] = []
        for e in self.added:
            touched.append(playbook.add(e.section, e.key, e.value, run_id, e.confidence))
        for entry_id in self.reinforced:
            touched.append(playbook.reinforce(entry_id, run_id))
        for entry_id in self.contradicted:
            touched.append(playbook.contradict(entry_id, run_id))
        return touched
